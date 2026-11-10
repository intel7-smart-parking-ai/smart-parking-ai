"""Photographic red-emitter classifier and per-bay off-to-on verification."""
from dataclasses import dataclass
from datetime import datetime
import cv2
import numpy as np


def features(patch):
    """Describe the lens, excluding the background color of the studio photograph."""
    b, g, r = cv2.split(patch.astype(np.float32) / 255)
    red = (r > .45) & (r - np.maximum(g, b) > .12)
    if not red.any():
        return np.zeros(7)
    r, g, b = r[red], g[red], b[red]
    # Light escaping through a red lens can produce a yellow or white-hot core.
    return np.array([r.mean(), np.quantile(r, .90), (r > .94).mean(),
                     np.quantile(g-b, .90), (g-b > .12).mean(),
                     np.quantile(g, .85), np.std(g)])


class LensModel:
    def __init__(self, weights, center, scale, bias):
        self.weights, self.center, self.scale, self.bias = weights, center, scale, bias

    @classmethod
    def load(cls, path):
        with np.load(path, allow_pickle=False) as data:
            vectors = [data[key] for key in ('weights', 'center', 'scale')]
            bias = float(data['bias'].item())
            if (any(v.shape != (7,) or not np.isfinite(v).all() for v in vectors)
                    or np.any(vectors[2] <= 0) or not np.isfinite(bias)
                    or data['version'].item() != 1):
                raise ValueError('LED 모델 규격이 다릅니다.')
            return cls(*vectors, bias)

    def score(self, patch):
        z = (features(patch) - self.center) / self.scale @ self.weights + self.bias
        return float(1 / (1 + np.exp(-np.clip(z, -40, 40))))

    def candidates(self, crop):
        b, g, r = cv2.split(crop.astype(np.int16))
        mask = np.uint8((r >= 155) & (r - np.maximum(g, b) >= 42))
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        height, width = mask.shape
        found = []
        for contour in contours:
            x, y, w, h = cv2.boundingRect(contour)
            if not (3 <= min(w, h) and max(w, h) <= 40 and w*h <= 1100 and .32 <= w/h <= 3.1
                    and cv2.contourArea(contour) >= max(3, w*h*.30)
                    and 4 <= x and 4 <= y and x+w < width-4 and y+h < height-4):
                continue
            score = self.score(crop[y:y+h, x:x+w])
            if score >= .65:
                found.append((score, (x, y, x+w, y+h)))
        return sorted(found, reverse=True)


@dataclass
class Bay:
    reference: np.ndarray
    sequence: int
    stamp: float
    box: tuple | None = None
    hits: int = 0
    first: int = 0
    first_time: float = 0


class FireWatch:
    def __init__(self, model):
        self.model = model
        self.bays = {}

    def clear(self, camera=None):
        for key in list(self.bays):
            if camera is None or key[0] == camera:
                del self.bays[key]

    def observe(self, key, crop, *, eligible, sequence, observed_at, roof_region=None):
        if not eligible or not observed_at:
            self.bays.pop(key, None)
            return {'state': 'unverified'}
        try:
            stamp = datetime.fromisoformat(observed_at)
            if stamp.utcoffset() is None:
                raise ValueError
            now = stamp.timestamp()
        except (ValueError, TypeError, OverflowError):
            self.bays.pop(key, None)
            return {'state': 'unverified'}
        candidates = self.model.candidates(crop)
        if roof_region is not None:
            left, top, right, bottom = roof_region
            candidates = [(score, box) for score, box in candidates
                          if left <= (box[0]+box[2])/2 <= right and top <= (box[1]+box[3])/2 <= bottom]
        tentative = {'state': 'candidate' if candidates else 'off'}
        bay = self.bays.get(key)
        if bay and (sequence <= bay.sequence or now <= bay.stamp):
            return {'state': 'unverified'}
        if bay is None or now-bay.stamp > 2:
            self.bays[key] = Bay(crop.copy(), sequence, now)
            return tentative
        delta = cv2.absdiff(crop, bay.reference)
        if np.mean(np.max(delta, axis=2) > 32) > .09:
            # Vehicle movement, changed exposure or an occluder invalidates the off reference.
            self.bays[key] = Bay(crop.copy(), sequence, now)
            return tentative
        bay.sequence, bay.stamp = sequence, now
        for score, box in candidates:
            x, y, u, v = box
            current = crop[y:v, x:u].astype(np.int16)
            reference = bay.reference[y:v, x:u].astype(np.int16)
            red_gain = current[:,:,2] - reference[:,:,2]
            chroma_gain = red_gain - (np.maximum(current[:,:,0], current[:,:,1])
                                      - np.maximum(reference[:,:,0], reference[:,:,1]))
            # Include illumination of a lens whose red channel was already saturated.
            warm_gain = current[:,:,1] - reference[:,:,1]
            if max(red_gain.max(), warm_gain.max())<=25:
                continue
            brightened = ((red_gain > 38) & (chroma_gain > 18)) | ((warm_gain > 48) & (current[:,:,2] > 225))
            # A tinted unpowered lens can already have saturated red pixels.
            # Measure the rise over the visible red surface. A slanted lens
            # occupies different fractions of its bounding rectangle on A and B.
            hot=(current[:,:,2]>185) & (current[:,:,2]-np.maximum(current[:,:,0],current[:,:,1])>30)
            was_red=reference[:,:,2]-np.maximum(reference[:,:,0],reference[:,:,1])>35
            ref_score=self.model.score(bay.reference[y:v,x:u])
            # Relative odds compare the observed lens to its own off image.
            # This evidence score is not a calibrated probability of fire.
            positive=score*(1-ref_score)
            opposite=(1-score)*ref_score
            transition_score=positive/max(positive+opposite,1e-8)
            lens_changed=(np.mean(was_red)>.20 and transition_score>=.80
                          and np.count_nonzero((warm_gain>25) & hot)>=max(3, .12*np.count_nonzero(hot)))
            direct=score>=.80 and np.mean(brightened)>=.22
            if not (direct or lens_changed):
                continue
            if bay.box is None or max(abs(a-b) for a,b in zip(box, bay.box)) > max(3, min(u-x,v-y)*.45):
                bay.hits, bay.first, bay.first_time = 0, sequence, now
            bay.box, bay.hits = box, bay.hits + 1
            if bay.hits >= 3 and now-bay.first_time >= .18:
                return dict(state='confirmed', confidence=score if direct else transition_score,
                            confidence_method='lens_and_brightness' if direct else 'relative_lens_odds_and_brightness',
                            appearance_score=score, reference_score=ref_score, crop_box=box, since_seq=bay.first)
            return dict(state='candidate', method='lens_brightening', crop_box=box)
        bay.box, bay.hits = None, 0
        bay.reference = crop.copy()
        return tentative


def project_box(box, mapping, image_shape):
    x, y, u, v = box
    points = np.float32([[[x,y], [u,y], [u,v], [x,v]]])
    corners = cv2.perspectiveTransform(points, np.linalg.inv(mapping))[0]
    h, w = image_shape[:2]
    lower = np.clip(np.floor(corners.min(0)), 0, (w,h)).astype(int)
    upper = np.clip(np.ceil(corners.max(0)), 0, (w,h)).astype(int)
    return [*lower.tolist(), *upper.tolist()]
