"""Match the visible printed name and floor of a surveyed, vacant physical bay."""
from zipfile import BadZipFile

import cv2
import numpy as np

from .registration import expected_ids


def load_empty_board(path, *, partial=False):
    if path is None or not path.is_file():
        return {}
    try:
        with np.load(path, allow_pickle=False) as data:
            keys = [(lot, sid) for lot in ('A', 'B') for sid in expected_ids(lot)]
            available=set(data.files)
            allowed={lot + sid for lot, sid in keys}
            if not available.issubset(allowed) or (not partial and available!=allowed):
                return {}
            references = {key: data[''.join(key)] for key in keys if ''.join(key) in available}
            if any(img.dtype != np.uint8 or img.shape != (112, 80, 3) for img in references.values()):
                return {}
            return references
    except (ValueError, OSError, EOFError, BadZipFile):
        return {}


def matches_printed_floor(crop, ref):
    """Tolerate small registration/lighting shifts but require the central marking and clear floor."""
    current = cv2.resize(crop, (80, 112), interpolation=cv2.INTER_AREA)
    gray = cv2.cvtColor(current, cv2.COLOR_BGR2GRAY)
    marker = cv2.cvtColor(ref[37:78, 17:63], cv2.COLOR_BGR2GRAY)
    if int(marker.max()) - float(np.median(marker)) < 24:
        return False
    scores = cv2.matchTemplate(gray[29:86, 9:71], marker, cv2.TM_CCOEFF_NORMED)
    _, peak, _, (x, y) = cv2.minMaxLoc(scores)
    if peak < .68:
        return False
    dx, dy = x - 8, y - 8
    # Skip the bordering lines, which can shift between surveyed camera poses.
    now = current[12+dy:100+dy, 12+dx:68+dx]
    floor = ref[12:100, 12:68]
    def lab(img):
        return cv2.cvtColor(cv2.GaussianBlur(img, (5, 5), 0), cv2.COLOR_BGR2LAB).astype(np.int16)
    delta = lab(now) - lab(floor)
    shift = np.median(delta, axis=(0, 1))
    ordinary = bool(np.all(np.abs(shift) <= [50, 13, 13]))
    # A lamp may brighten one row while leaving the other in shadow. Permit a
    # larger luminance offset only with a well-aligned, high-correlation name
    # and nearly unchanged chroma; still inspect the entire interior by tiles.
    exposure = (peak >= .84 and max(abs(dx),abs(dy)) <= 3 and abs(shift[0]) >= 35
                and np.all(np.abs(shift) <= [75, 7, 7]))
    if not (ordinary or exposure):
        return False
    # The desk lamp casts a smooth gradient. Estimate its luma slope from
    # broad strips so a small hand or roof cannot explain away its own pixels.
    residual = delta - shift
    luma = residual[:, :, 0]
    height, width = luma.shape
    slope_x = np.median(luma[:, -14:]) - np.median(luma[:, :14])
    slope_y = np.median(luma[-22:]) - np.median(luma[:22])
    y, x = np.ogrid[:height, :width]
    gradient = slope_x * (x - (width-1)/2) / (width-14) + slope_y * (y - (height-1)/2) / (height-22)
    luma -= np.clip(gradient, -22, 22)
    def clear_floor(tolerances):
        changed = np.any(np.abs(residual) > tolerances, axis=2)
        tiles = changed.reshape(4, 22, 4, 14).mean(axis=(1, 3))
        return changed.mean() <= .065 and tiles.max() <= .35
    return (ordinary and clear_floor([22,10,10])) or (exposure and clear_floor([25,9,9]))
