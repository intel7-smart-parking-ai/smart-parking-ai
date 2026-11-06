"""Re-read faint printed names in small windows located from independently read bays."""
import re

import cv2
import numpy as np


ORIGINAL_MODES = ('gray', 'contrast_wide', 'contrast_wider')
BAND_MODES = ('band_inverse', 'band_toned')
# Gamma brightens the gray fringe around dark ink. The ellipse trims connected strokes.
STROKE_VIEWS = (('stroke_medium', .60, 2/3), ('stroke_fine', .35, 1.0),
                ('stroke_plain', 1.0, 1.0), ('stroke_soft', .35, 2/3))
STROKE_MODES = {name for name, _, _ in STROKE_VIEWS}


def full_name(text):
    if isinstance(text, str):
        match = re.fullmatch(r'([AB])\s*[-_. ]?\s*([0-9]{2})', text.strip().upper())
        if match:
            return f'{match[1]}-{match[2]}'
    return None


def numeric_prefix(text, sid):
    match = re.fullmatch(r'[0-9]\s*-\s*([0-9]{2})', text.strip()) if isinstance(text,str) else None
    return match is not None and match[1] == sid[2:]


def same_print(first, second):
    low = np.maximum(first[:2], second[:2])
    high = np.minimum(first[2:], second[2:])
    intersection = np.prod(np.maximum(0, high - low))
    larger = max((box[2] - box[0]) * (box[3] - box[1]) for box in (first, second))
    return larger > 0 and intersection >= .65 * larger


def validate_local_reads(row, size, min_score):
    reads = row.get('local_reads')
    if row.get('localized_retry') is not True or not isinstance(reads, list) or len(reads) != 2:
        raise ValueError('칸 이름 재판독 근거 2개가 필요합니다.')
    modes = set()
    for read in reads:
        box, score, factors = read['bbox'], read['score'], read['scale_xy']
        if (full_name(read['text']) != row['space_id'] or (read['mode'] not in ORIGINAL_MODES+BAND_MODES and read['mode'] not in STROKE_MODES)
                or type(score) not in (int,float) or not min_score <= score <= 1
                or len(box) != 4 or any(type(v) is not int for v in box)
                or not (0 <= box[0] < box[2] <= size[0] and 0 <= box[1] < box[3] <= size[1])
                or len(factors) != 2 or any(type(v) not in (int,float) or not np.isfinite(v) or v <= 0 for v in factors)):
            raise ValueError('칸 이름 재판독 문자·점수·원본 좌표 오류')
        modes.add(read['mode'])
    best = max(reads, key=lambda item: item['score'])
    if (len(modes) != 2 or not same_print(reads[0]['bbox'], reads[1]['bbox'])
            or row['score'] != min(item['score'] for item in reads)
            or row['bbox'] != best['bbox'] or row['text'] != best['text'] or row.get('view_scale') != best['scale_xy'][1]):
        raise ValueError('두 재판독 결과가 서로 일치하지 않습니다.')
    if modes & STROKE_MODES or 'numeric_prefix_read' in row:
        prior = row.get('numeric_prefix_read')
        if (not modes & STROKE_MODES or not isinstance(prior,dict)
                or not numeric_prefix(prior.get('text'),row['space_id']) or prior.get('mode') not in ORIGINAL_MODES
                or type(prior.get('score')) not in (int,float) or not min_score <= prior['score'] <= 1):
            raise ValueError('획 분리 전 문자 판독 근거 오류')
        box = prior.get('bbox')
        if (not isinstance(box,list) or len(box)!=4 or any(type(v) is not int for v in box)
                or not (0<=box[0]<box[2]<=size[0] and 0<=box[1]<box[3]<=size[1])
                or not all(same_print(box,r['bbox']) for r in reads)):
            raise ValueError('획 분리 전후의 원본 문자 위치 불일치')


def centers_to_image(names):
    ids = sorted(names)
    if any(sum(s.startswith(row + '-') for s in ids) < 3 for row in 'AB'):
        return None
    grid = np.float32([[int(s[2:]), int(s.startswith('B-'))] for s in ids])
    centers = np.float32([(np.array(names[s]['bbox'][:2]) + names[s]['bbox'][2:]) / 2 for s in ids])
    matrix, _ = cv2.findHomography(grid, centers, 0)
    if matrix is None or not np.isfinite(matrix).all():
        return None
    predicted = cv2.perspectiveTransform(grid[None], matrix)[0]
    if np.max(np.linalg.norm(predicted - centers, axis=1)) > 8:
        return None
    return matrix


def original_box(points, bounds, turns, factors):
    points = np.asarray(points, np.float32)
    if points.shape != (4, 2) or not np.isfinite(points).all():
        return None
    a, b, c, d = bounds
    u, v = (points / factors).T
    if turns == 1:
        u, v = v, d - b - 1 - u
    elif turns == 2:
        u, v = c - a - 1 - u, d - b - 1 - v
    elif turns == 3:
        u, v = c - a - 1 - v, u
    low = np.floor([u.min() + a, v.min() + b]).astype(int)
    high = np.ceil([u.max() + a, v.max() + b]).astype(int)
    if np.any(low < [a, b]) or np.any(high > [c, d]) or np.any(high <= low):
        return None
    return low.tolist() + high.tolist()


def stroke_views(inverse, size):
    scale = int(np.clip(round(120/min(size)), 1, 3))
    enlarged = cv2.resize(inverse,None,fx=scale,fy=scale,interpolation=cv2.INTER_CUBIC)
    for name, gamma, radius_fraction in STROKE_VIEWS:
        radius = max(1,round(scale*radius_fraction))
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE,(2*radius+1,2*radius+1))
        toned = np.rint(255*(enlarged.astype(float)/255)**gamma).astype(np.uint8)
        yield cv2.dilate(toned,kernel), name


def localized_read(image, reader, sid, center, size, turns, min_score):
    height, width = image.shape[:2]
    x, y = center
    bw, bh = size
    a, b = max(0, int(x-bw)), max(0, int(y-bh))
    c, d = min(width, int(x+bw)+1), min(height, int(y+bh)+1)
    if c-a < bw or d-b < bh:
        return None
    crop = np.ascontiguousarray(np.rot90(image[b:d, a:c], -turns))
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    dark, light = np.percentile(gray, [5, 99])
    if light - dark < 12:
        return None
    inverse = (255 - 255*np.clip((gray.astype(float)-dark)/(light-dark), 0, 1)).astype(np.uint8)
    scale = int(np.clip(round(90 / min(bw, bh)), 1, 4))
    readings = []
    weak_name_modes = set()
    prior = None

    def views():
        for pixels,fx,mode in ((gray,scale,'gray'),(inverse,scale*4/3,'contrast_wide'),
                               (inverse,scale*5/3,'contrast_wider')):
            yield cv2.resize(pixels,None,fx=fx,fy=scale,interpolation=cv2.INTER_CUBIC), mode, 0
        if prior is not None:
            for pixels,mode in stroke_views(inverse,size):
                yield pixels,mode,0
        elif readings or len(weak_name_modes)>=2:
            # Special bays can have another line of print immediately below the ID.
            # Trim in upright coordinates, retaining both axes of the original mapping.
            for mode, fraction in zip(BAND_MODES,(.64,.85)):
                trim = round(gray.shape[0]*(1-fraction)/2)
                band = 255-gray[trim:gray.shape[0]-trim]
                if mode=='band_toned':
                    band = np.rint(255*(band/255.)**.9).astype(np.uint8)
                yield cv2.resize(band,None,fx=scale,fy=scale,interpolation=cv2.INTER_CUBIC), mode, trim

    for enlarged, mode, trim in views():
        factors = np.array(enlarged.shape[::-1]) / [gray.shape[1],gray.shape[0]-2*trim]
        hits = reader.readtext(enlarged, detail=1, paragraph=False, rotation_info=None,
                               allowlist='AB-0123456789', canvas_size=1024, mag_ratio=1.)
        matches = []
        for points, label, score in hits:
            if not 0 <= score <= 1 or not isinstance(label, str):
                continue
            observed = full_name(label)
            ambiguous = mode in ORIGINAL_MODES and numeric_prefix(label,sid)
            if not observed and not ambiguous:
                continue
            points = np.asarray(points,np.float32)
            if points.shape != (4,2):
                continue
            box = original_box(points + [0, trim*factors[1]], (a,b,c,d), turns, factors)
            if box is None:
                continue
            midpoint = (np.array(box[:2]) + box[2:])/2
            if np.any(np.abs(midpoint - center) > size/2):
                continue
            if score < min_score:
                if mode in ORIGINAL_MODES and observed==sid and score>=.35:
                    weak_name_modes.add(mode)
                continue
            if ambiguous:
                if prior is None or score > prior['score']:
                    prior = {'text':label, 'score':float(score), 'bbox':box, 'mode':mode}
                continue
            if observed != sid:
                return None
            if mode in STROKE_MODES and not same_print(prior['bbox'],box):
                continue
            matches.append({'text':label, 'score':float(score), 'bbox':box, 'mode':mode,
                            'scale_xy':factors.tolist()})
        if len(matches) > 1:
            return None
        readings.extend(matches)
        if len(readings) == 2:
            first, second = [r['bbox'] for r in readings]
            if not same_print(first, second):
                return None
            best = max(readings, key=lambda r:r['score'])
            result = {'space_id':sid, 'text':best['text'], 'score':min(r['score'] for r in readings),
                    'bbox':best['bbox'], 'view_rotation_clockwise':turns*90,
                    'view_scale':best['scale_xy'][1], 'localized_retry':True, 'local_reads':readings}
            if mode in STROKE_MODES:
                if not all(same_print(prior['bbox'],r['bbox']) for r in readings):
                    return None
                result['numeric_prefix_read'] = prior
            return result
    return None


def retry_names(image, lot, reader, names, conflicts, min_score):
    from layouts import layout
    if lot not in ('A','B') or conflicts or len(names) == layout(lot)['space_count']:
        return
    matrix = centers_to_image(names)
    if matrix is None:
        return
    for row in 'AB':
        peers = [r for sid,r in names.items() if sid.startswith(row+'-')]
        size = np.median([np.array(r['bbox'][2:])-r['bbox'][:2] for r in peers], axis=0)
        turns = peers[0]['view_rotation_clockwise']//90
        for slot in layout(lot)['spaces']:
            sid = slot['space_id']
            if sid in names or not sid.startswith(row+'-'):
                continue
            grid = np.float32([[[int(sid[2:]), int(row=='B')]]])
            center = cv2.perspectiveTransform(grid, matrix)[0,0]
            if not np.isfinite(center).all():
                continue
            entry = localized_read(image, reader, sid, center, size, turns, min_score)
            if entry is not None:
                names[sid] = entry
