"""Positive identification of a reviewed trim pattern in the team board reference."""
import hashlib

import cv2
import numpy as np
from .printed_numbers import same_box


# Coordinates below describe the reviewed ornament in this exact reference photo.
REFERENCE_SHA256 = '62d2bc50d9e04247cec5b169c429ba755f10651897f79ff8dfb78d80aa6b1c92'


def load_reference(team_directory):
    try:
        data = (team_directory / 'samples' / 'B_rectified_sample.jpg').read_bytes()
    except OSError:
        return None
    if hashlib.sha256(data).hexdigest() != REFERENCE_SHA256:
        return None
    image = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_GRAYSCALE)
    if image is None or image.shape != (1080, 1920):
        return None
    return cv2.resize(image[125:230, 380:560], (96, 96), interpolation=cv2.INTER_AREA)


def decoration_match(frame, candidates, reference, *, recheck_paper=None):
    """Return the positively matched extra box only; unresolved pairs stay together."""
    if reference is None or len(candidates) != 2:
        return None
    printed = [d for d in candidates if d.get('print_read')]
    if len(printed) != 1:
        return None
    roof = printed[0]
    hit = roof['print_read']
    needs_paper = hit['margin'] < .04
    if hit['score'] < .94 or (needs_paper and (not recheck_paper or hit['score'] < .96 or hit['margin'] < .03)):
        return None
    other = next(d for d in candidates if d is not roof)
    if other['sticker_color'] != 'white':
        return None
    x, y, u, v = other['bbox_xyxy']
    left, top, right, bottom = roof['bbox_xyxy']
    if min(u, right) > max(x, left) and min(v, bottom) > max(y, top):
        return None
    if x < 0 or y < 0 or u > frame.shape[1] or v > frame.shape[0] or min(u-x, v-y) < 24:
        return None
    gray = cv2.cvtColor(frame[y:v, x:u], cv2.COLOR_BGR2GRAY)
    if gray.std() < 20:
        return None
    # Compare the entire extra box, in four orientations, with a crop spanning
    # the reference hood. Local agreement prevents a single similar edge from
    # explaining an otherwise different paper sticker.
    for turns in range(4):
        rotated = np.rot90(gray, turns)
        for width in (80, 72, 88):
            for height in (64, 56, 72):
                patch = cv2.resize(rotated, (width, height), interpolation=cv2.INTER_AREA)
                scores = cv2.matchTemplate(reference, patch, cv2.TM_CCOEFF_NORMED)
                _, score, _, (cx, cy) = cv2.minMaxLoc(scores)
                if score < .78:
                    continue
                region = reference[cy:cy+height, cx:cx+width]
                quadrants = [(slice(i, i+height//2), slice(j, j+width//2))
                             for i in (0, height//2) for j in (0, width//2)]
                if all(np.std(patch[q]) >= 8 and np.std(region[q]) >= 8 and
                       cv2.matchTemplate(region[q], patch[q], cv2.TM_CCOEFF_NORMED)[0, 0] >= .50
                       for q in quadrants):
                    if needs_paper:
                        # A detector crop can include part of the roof. Read
                        # independently located paper outlines, including any
                        # elsewhere in this bay, before excluding the trim.
                        sheets = recheck_paper()
                        if not sheets or any(not same_box(s['bbox_xyxy'], roof['bbox_xyxy'])
                                or s['print_read']['number'] != hit['number']
                                or s['sticker_color'] != roof['sticker_color'] for s in sheets):
                            return None
                        if not any(s['print_read']['score'] >= .96 and s['print_read']['margin'] >= .035 for s in sheets):
                            return None
                    return other, round(score, 4)
    return None
