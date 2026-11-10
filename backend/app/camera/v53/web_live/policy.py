"""D-54 catalog and evidence rules, independent of any camera/model runtime."""
import math
import re
from datetime import datetime, timezone
import uuid
from collections import Counter
from .registration import expected_ids

CATALOG = {n:t for t,numbers in {
    'ev':'11 23 42 57 85', 'accessible':'14 32 49 55 77',
    'general':'10 12 13 20 21 22 24 30 31 33 34 40 41 43 44 58 75 78 87 88'
}.items() for n in numbers.split()}


def valid_score(value):
    return type(value) in (int,float) and math.isfinite(value) and 0<=value<=1


# Team rule 6: an LED is a mock fire and travels only as mock_fire_led_observed.
FIRE_EVENTS = ('mock_fire_led_observed',)
# Camera-only marker for an LED confirmed on a parked car's roof. Keys that
# start with "_" stay in this process; wire_event() removes them before HTTP.
VEHICLE_FIRE = '_vehicle_fire'


def is_vehicle_fire(event):
    return event.get('event_type') == 'mock_fire_led_observed' and event.get(VEHICLE_FIRE) is True


def wire_event(event):
    return {key: value for key, value in event.items() if not key.startswith('_')}


def on_vehicle_roof(row, light_box):
    sticker = row.get('sticker_bbox')
    if row.get('observation') != 'occupied' or not sticker or not light_box:
        return False
    x,y,u,v = sticker
    a,b,c,d = light_box
    return (u>x and v>y and c>a and d>b and
            math.hypot(((a+c)-(x+u))/(2*(u-x)), ((b+d)-(y+v))/(2*(v-y))) <= 2.0)


def parking_rule_status(row, confirmed):
    """Project rule for the A/B models: all known vehicle types may park in a general bay."""
    if row.get('observation')!='occupied':
        return None
    kinds=('general','ev','accessible')
    score=row.get('confidence')
    if (not confirmed or not row.get('sendable') or not valid_score(score) or score<.8
            or row.get('zone_type') not in kinds or row.get('vehicle_type') not in kinds):
        return 'unverified'
    return 'matched' if row['zone_type'] in ('general', row['vehicle_type']) else 'mismatch'


def confirmed_number(ocr, model_candidate, color, *, model_classes=None):
    """Never relabel 28 as 23 or use color to invent an identity."""
    number = (ocr or {}).get('number')
    if not isinstance(number,str) or not re.fullmatch(r'[1-9][0-9]',number) or number not in CATALOG:
        return None
    score = (ocr or {}).get('confidence')
    if not valid_score(score) or score<.8:
        return None
    if color and CATALOG[number] != color:
        return None
    # A model trained on six labels cannot reject an unseen number such as 23.
    # But a conflicting prediction among its *valid* known labels is held.
    if (model_classes is None or number in model_classes) and model_candidate in CATALOG and model_candidate != number:
        return None
    return number


def duplicate_identities(results):
    counts = Counter()
    duplicates = set()
    for result in results.values():
        # Single-board analysis has already nulled identities that collided locally.
        duplicates.update(result.get('_duplicate_ids', ()))
        counts.update(row['car_id'] for row in result.get('spaces', ()) if row.get('car_id'))
    return duplicates | {number for number, count in counts.items() if count > 1}


def pin_reads(result, duplicates=()):
    """{car: {(lot, bay)}} for numbers currently read on occupied bays (including pin-held reads)."""
    reads = {}
    lot = result.get('lot_id')
    for row in result.get('spaces', ()):
        car = row.get('car_id')
        if car is None and str(row.get('number_reason', '')).startswith('칸 번호 고정'):
            car = row.get('number_candidate')
        if row.get('sendable') and row.get('observation') == 'occupied' and car in CATALOG and car not in duplicates:
            reads.setdefault(car, set()).add((lot, row.get('space_id')))
    return reads


# A different number read this many consecutive frames in the same occupied bay replaces
# the pin even when the old car is not visible anywhere (~5 s at 1.6 fps). 11/10: cars
# 11/23 moved to Busan A-04/A-06 were unreadable there, so 88/21 in their old bays
# stayed null for 10 minutes. Number reads did not flip on their own (0 of 28 bays).
PIN_RELEASE_FRAMES = 8


def apply_bay_pins(result, pins, duplicates=(), elsewhere=None, streaks=None):
    """Keep one car number per occupied bay until that bay is confirmed empty.

    pins maps (lot_id, space_id) to the first confirmed number. A different
    number read while the bay stays occupied is sent as null, so an active
    server session never sees its number flip (409). The pinned number is never
    sent without a current reading; an unreadable number stays null.

    A pin is also released when its car is being read in another bay right now
    (this frame or the other camera's current frame): that car has left. 11/10
    classroom: cars swapped without a visible empty frame stayed null for good.
    """
    lot = result.get('lot_id')
    if lot not in ('A', 'B') or not result.get('registration_ok') or not result.get('roi_confirmed'):
        return result
    reads = pin_reads(result, duplicates)
    for car, bays in (elsewhere or {}).items():
        reads.setdefault(car, set()).update(bays)
    for row in result.get('spaces', ()):
        key = (lot, row.get('space_id'))
        pinned = pins.get(key)
        car = row.get('car_id')
        steady = (pinned is not None and car in CATALOG and car != pinned and car not in duplicates
                  and row.get('sendable') and row.get('observation') == 'occupied')
        if streaks is not None:
            previous = streaks.get(key)
            if steady:
                count = previous[1] + 1 if previous and previous[0] == car else 1
                streaks[key] = (car, count)
            elif row.get('sendable'):
                streaks.pop(key, None)
        if steady and (reads.get(pinned, set()) - {key}
                       or (streaks is not None and streaks[key][1] >= PIN_RELEASE_FRAMES)):
            pins[key] = car
            if streaks is not None:
                streaks.pop(key, None)
    for row in result.get('spaces', ()):
        key = (lot, row.get('space_id'))
        if not row.get('sendable'):
            continue
        if row.get('observation') == 'empty' and row.get('empty_evidence_ok'):
            pins.pop(key, None)
            continue
        car = row.get('car_id')
        if row.get('observation') != 'occupied' or car not in CATALOG or car in duplicates:
            continue
        pinned = pins.setdefault(key, car)
        if pinned != car:
            row.update(car_id=None, number_candidate=car,
                       number_reason=f'칸 번호 고정({pinned}) 중 다른 번호 판독 · 빈칸 확인 전까지 null')
    return result


def drop_non_led_fire(result, pins, led_cars, elsewhere=None):
    """Bay identity for the LED-car list when this frame's number is unread.

    The pipeline filters red lights on a read non-LED car. When the number is momentarily
    null (OCR wait, glare, pin hold), fall back to the pin-held read, then to the bay's pinned
    number. 11/10 19:00: car 21 (red) at Gaebalwon A-03 confirmed a light for 3 null frames.
    A listed LED car moved into a pinned bay is read as the pin-held candidate, so it keeps its light.
    A pinned car that is being read in another bay right now has left; its pin is not used
    (an unreadable LED car that replaced it keeps its light).
    """
    if not led_cars:
        return result
    lot = result.get('lot_id')
    reads = pin_reads(result)
    for car, bays in (elsewhere or {}).items():
        reads.setdefault(car, set()).update(bays)
    rows = {row.get('space_id'): row for row in result.get('spaces', ())}
    kept = []
    for light in result.get('fire_lights') or []:
        row = rows.get(light.get('space_id'))
        identity = None
        if row is not None and light.get('location', 'space') == 'space' and row.get('observation') == 'occupied':
            identity = row.get('car_id')
            if identity is None and str(row.get('number_reason', '')).startswith('칸 번호 고정'):
                identity = row.get('number_candidate')
            if identity is None:
                pinned = pins.get((lot, row.get('space_id')))
                if pinned and not (reads.get(pinned, set()) - {(lot, row.get('space_id'))}):
                    identity = pinned
        if identity and identity not in led_cars:
            result.setdefault('filtered_lights', []).append(dict(
                reason='not_led_car_pinned', space_id=light.get('space_id'), location=light.get('location'),
                bbox_xyxy=light.get('bbox_xyxy'), confidence=round(float(light.get('confidence', 0)), 3),
                bay_area_fraction=round(float(light.get('bay_area_fraction', 0)), 4)))
            if (row.get('fire_led') or {}).get('bbox_xyxy') == light.get('bbox_xyxy'):
                row['fire_led'] = {'state': 'unverified'}
            continue
        kept.append(light)
    if 'fire_lights' in result:
        result['fire_lights'] = kept
    return result


def without_duplicate_identities(results):
    """Resolve identity conflicts across both cameras, preserving occupancy."""
    duplicates = duplicate_identities(results)
    output={}
    for camera,result in results.items():
        rows=[]
        for row in result.get('spaces',[]):
            row=dict(row)
            if row.get('car_id') in duplicates:
                row.update(car_id=None,number_reason='두 카메라/칸에서 번호 중복 · null')
            rows.append(row)
        by_space={row['space_id']:row for row in rows}
        detections=[]
        for detection in result.get('detections',[]):
            detection=dict(detection)
            row=by_space.get(detection.get('space_id'))
            if row is not None:
                detection['car_id']=row.get('car_id')
                if row.get('number_reason'):
                    detection['number_reason']=row['number_reason']
            elif detection.get('car_id') in duplicates:
                detection.update(car_id=None,number_reason='두 카메라/칸에서 번호 중복 · null')
            detections.append(detection)
        output[camera]=dict(result,spaces=rows,detections=detections)
    return output


def to_events(result, packet, camera_id, *, now=None):
    if packet.get('source')!='live_camera' or not result.get('registration_ok') or not result.get('roi_confirmed'):
        return []
    if result.get('stream_epoch') != packet.get('epoch') or result.get('frame_sequence') != packet.get('sequence'):
        return []
    try:timestamp = datetime.fromisoformat(packet['observed_at'])
    except (KeyError,TypeError,ValueError):return []
    if timestamp.tzinfo is None:
        return []
    age = ((now or datetime.now(timezone.utc))-timestamp).total_seconds()
    if not -.5<=age<=2 or result.get('observed_at') != packet['observed_at']:
        return []
    lot = result.get('lot_id')
    if lot not in ('A','B') or camera_id != {'A':'camera_a','B':'camera_b'}[lot]:
        return []
    rows = result.get('spaces',[])
    if sorted(r.get('space_id','') for r in rows) != sorted(expected_ids(lot)):
        return []
    events = []
    duplicate_numbers = duplicate_identities({camera_id: result})
    for row in rows:
        occupied = row.get('observation')=='occupied'
        empty = row.get('observation')=='empty'
        score = row.get('confidence')
        if not (occupied or empty) or not row.get('sendable') or not valid_score(score) or score<.8:
            continue
        if empty and not row.get('empty_evidence_ok'):
            continue
        car_id = row.get('car_id') if occupied else None
        if car_id not in CATALOG or car_id in duplicate_numbers:
            car_id = None
        kind = row.get('vehicle_type') if occupied else None
        if kind not in ('general','ev','accessible'):
            kind = None
        if car_id and kind and CATALOG[car_id] != kind:
            car_id = None;kind = None
        events.append({'event_id':str(uuid.uuid4()),'event_type':'vehicle_observed' if occupied else 'space_empty_observed',
            'source':'vision','lot_id':lot,'camera_id':camera_id,'space_id':row['space_id'],
            'car_id':car_id,'vehicle_type':kind,'confidence':float(score),
            # A sticker box does not measure the vehicle's footprint.
            'bbox_xyxy':None,'observed_at':packet['observed_at'],
            'frame_id':f"{camera_id}:{packet['epoch']}:{packet['sequence']}:{packet['observed_at']}"})
        fire=row.get('fire_led') or {}
        if occupied and fire.get('method')!='red_emission' and fire.get('state')=='confirmed' and valid_score(fire.get('confidence')) and fire['confidence']>=.8:
            box=fire.get('bbox_xyxy')
            if (isinstance(box,list) and len(box)==4 and all(type(v) is int and v>=0 for v in box)
                    and box[0]<box[2] and box[1]<box[3]):
                events.append(dict(events[-1], event_type='mock_fire_led_observed', event_id=str(uuid.uuid4()),
                                   car_id=None, vehicle_type=None, confidence=fire['confidence'], bbox_xyxy=box))
    # The LED can be lit on a vacant bay or in the aisle. Slot occupancy is
    # independent. The team accepts a lot-level LED alert with space_id=null.
    used=set()
    for light in sorted(result.get('fire_lights',()),key=lambda h:-h['confidence']):
        sid=light.get('space_id')
        if (light.get('state')=='candidate' or light.get('location') not in ('space','boundary','aisle') or sid in used
                or not valid_score(light.get('confidence')) or light['confidence']<.85):
            continue
        if (light['location']=='space')!=(sid in expected_ids(lot)) or (light['location']!='space' and sid is not None):
            continue
        box=light.get('bbox_xyxy')
        if not (isinstance(box,list) and len(box)==4 and all(type(v) is int and v>=0 for v in box)
                and box[0]<box[2] and box[1]<box[3]):continue
        used.add(sid)
        events.append(dict(event_id=str(uuid.uuid4()),event_type='mock_fire_led_observed',source='vision',
                           lot_id=lot,camera_id=camera_id,space_id=sid,car_id=None,vehicle_type=None,
                           confidence=light['confidence'],bbox_xyxy=box,observed_at=packet['observed_at'],
                           frame_id=f"{camera_id}:{packet['epoch']}:{packet['sequence']}:{packet['observed_at']}"))
    occupied = {e['space_id']: e for e in events if e['event_type']=='vehicle_observed'}
    confirmed_lights = {(light.get('space_id'),tuple(light.get('bbox_xyxy') or ()))
                        for light in result.get('fire_lights',()) if light.get('state')=='confirmed'}
    by_slot = {row['space_id']:row for row in rows}
    for event in events:
        sid = event['space_id']
        if event['event_type']!='mock_fire_led_observed' or sid not in occupied:continue
        fire=by_slot[sid].get('fire_led') or {}
        confirmed=((sid,tuple(event['bbox_xyxy'])) in confirmed_lights or
                   (fire.get('state')=='confirmed' and fire.get('bbox_xyxy')==event['bbox_xyxy']))
        if confirmed and on_vehicle_roof(by_slot[sid],event['bbox_xyxy']):
            event.update(car_id=occupied[sid]['car_id'], vehicle_type=occupied[sid]['vehicle_type'],
                         **{VEHICLE_FIRE: True})
    return events
