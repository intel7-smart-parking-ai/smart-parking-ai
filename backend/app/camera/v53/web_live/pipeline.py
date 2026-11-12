"""Frame evidence only: registration, per-space occupancy, sticker/color and OCR."""
import base64
import io
import logging
import os
import time
import threading
from concurrent.futures import ThreadPoolExecutor, wait
from zipfile import BadZipFile
from functools import lru_cache
import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from .inference import StickerModel, LABELS, KOREAN
from .models import Classifier, STICKER_LABELS, NUMBERS, unique_choice
from .registration import BoardRegistry, overlap_slot, dedicated,assign_sticker
from .policy import confirmed_number, CATALOG, duplicate_identities, valid_score, parking_rule_status, on_vehicle_roof
from .number_reader import NumberReader
from .printed_numbers import PrintedNumbers, same_box, same_print_area, paper_color_matches
from .body_decoration import decoration_match, load_reference
from .sticker_geometry import rectify_sticker
from .fire_led import FireWatch, LensModel, project_box
from .glow import GlowModel, localize
from .empty_board import load_empty_board, matches_printed_floor

DEST = np.float32([[0,0],[159,0],[159,223],[0,223]])


def frame_quality(frame):
    """Diagnose only extreme whole-frame faults. Mixed lighting stays eligible."""
    sample = cv2.resize(frame, (160, 90), interpolation=cv2.INTER_AREA)
    luma = cv2.cvtColor(sample, cv2.COLOR_BGR2GRAY)
    low, high = int(luma.min()), int(luma.max())
    state = 'inspect'
    if high <= 24:
        state = 'dark'
    elif low >= 245:
        state = 'overexposed'
    return {'state': state, 'min_luma': low, 'max_luma': high}


def read_current_sticker(printed, frame, box, nearby):
    """Reuse only the search area. Derive identity and paper color from this frame."""
    if any(not same_box(box, item['bbox_xyxy']) for item in nearby):
        return None
    observed_colors = {item['sticker_color'] for item in nearby}
    matches = []
    for label, (color, kind) in LABELS.items():
        if observed_colors and observed_colors != {color}:
            continue
        candidate = dict(bbox_xyxy=box, bbox_target='sticker', label=label, color_name=KOREAN[label],
                         sticker_color=color, vehicle_type_from_sticker=kind)
        read = printed.read_box(frame, candidate)
        if read and read['score'] >= .94 and read['margin'] >= .04:
            matches.append(dict(candidate, print_read=read, confidence=read['score'],
                                confidence_method='print_shape_similarity',
                                localization_source='current_print_at_reviewed_box'))
    return matches[0] if len(matches) == 1 else None


def reread_detector_crop(printed, frame, detection):
    """Test small registration shifts when glare or a tight detector crop clips ink."""
    x, y, u, v = detection['bbox_xyxy']
    dx, dy = max(1, round((u-x)*.05)), max(1, round((v-y)*.05))
    fine_x, fine_y = max(1, round((u-x)*.035)), max(1, round((v-y)*.035))
    coarse = ((0,-dy), (-dx,-dy), (dx,-dy), (0,dy), (-dx,0), (dx,0))
    fine = ((a,b) for a in (-fine_x,0,fine_x) for b in (-fine_y,0,fine_y) if a or b)
    reads = []
    tried = set()
    for group in (coarse, fine):
        for ox, oy in group:
            if (ox, oy) in tried:
                continue
            tried.add((ox, oy))
            box = [x+ox, y+oy, u+ox, v+oy]
            if box[0]<0 or box[1]<0 or box[2]>frame.shape[1] or box[3]>frame.shape[0]:
                continue
            hit = printed.read_box(frame, dict(detection, bbox_xyxy=box))
            if hit:
                reads.append(hit)
        if len({r['number'] for r in reads}) > 1:
            return None
        if len(reads) >= 3:
            best = max(reads, key=lambda r:r['score'])
            if best['score'] >= .94:
                return best

    # Require original paper color and agreeing current-frame glyphs at two
    # contrast levels. These two views count as one frame for temporal voting.
    if not paper_color_matches(frame[y:v, x:u], detection['sticker_color']):
        return None
    pad = max(8, round(.16 * max(u-x, v-y)))
    left, top = max(0, x-pad), max(0, y-pad)
    tile = frame[top:v+pad, left:u+pad]
    local = dict(detection, bbox_xyxy=[x-left,y-top,u-left,v-top])
    blurred = cv2.GaussianBlur(tile, (0,0), 2.)
    enhanced_reads = []
    for gain in (.6, 1.2):
        view = cv2.addWeighted(tile, 1+gain, blurred, -gain, 0)
        hit = printed.read_box(view, local)
        if not hit or hit['score']<.94 or hit['margin']<.04:
            return None
        enhanced_reads.append(hit)
    if len({r['number'] for r in reads+enhanced_reads}) != 1:
        return None
    best = max(enhanced_reads, key=lambda r:r['score'])
    return best if best['score'] >= .96 else None


def corroborated_paper(printed, frame, detected, paper):
    """Check whether a differently colored detector box encloses one printed sheet."""
    hit = paper.get('print_read')
    if (detected.get('localization_source') or detected.get('print_read')
            or paper.get('localization_source') != 'paper_contour_and_print'
            or detected['sticker_color'] == paper['sticker_color']
            or not hit or hit['score'] < .96):
        return False
    a, b = detected['bbox_xyxy'], paper['bbox_xyxy']
    w, h = b[2]-b[0], b[3]-b[1]
    if min(w,h) <= 0:
        return False
    overlap = max(0,min(a[2],b[2])-max(a[0],b[0])) * max(0,min(a[3],b[3])-max(a[1],b[1]))
    centered = abs(a[0]+a[2]-b[0]-b[2]) <= .30*w and abs(a[1]+a[3]-b[1]-b[3]) <= .30*h
    if same_print_area(a,b) and hit['margin']>=.022:
        x,y,u,v=a
        tile=frame[y:v,x:u]
        if (overlap >= .90*w*h and centered and a!=b
                and paper_color_matches(tile,paper['sticker_color']) and not paper_color_matches(tile,detected['sticker_color'])):
            middle=[round((p+q)/2) for p,q in zip(a,b)]
            if middle!=a and middle!=b:
                reads=[printed.read_box(frame,dict(paper,bbox_xyxy=box)) for box in (middle,a)]
                if (all(r and r['number']==hit['number'] and r['score']>=.96 and r['margin']>=.022 for r in reads)
                        and max(r['score'] for r in [hit,*reads])>=.97):
                    return True
    if hit['margin'] < .04:return False
    if not same_box(a,b):
        return False
    if (overlap < .92*w*h or abs(a[0]+a[2]-b[0]-b[2]) > .25*w
            or abs(a[1]+a[3]-b[1]-b[3]) > .25*h):
        return False
    reread = printed.read_box(frame, dict(paper, bbox_xyxy=a))
    return bool(reread and reread['number']==hit['number'] and reread['score']>=.96 and reread['margin']>=.04)


def crop_quality(image):
    gray = cv2.cvtColor(image,cv2.COLOR_BGR2GRAY)
    if np.percentile(gray,95)<65 or np.std(gray)<10:
        return '어둡거나 명암 부족'
    if np.mean(gray>248)>.50:
        return '과노출·가림 의심'
    return None


def empty_similarity(current, reference):
    a=cv2.cvtColor(cv2.GaussianBlur(current,(5,5),0),cv2.COLOR_BGR2YCrCb)
    b=cv2.cvtColor(cv2.GaussianBlur(reference,(5,5),0),cv2.COLOR_BGR2YCrCb)
    delta=a.astype(np.int16)-b
    shift=np.median(delta,axis=(0,1))
    if np.any(np.abs(shift)>[18,10,10]):
        return False
    changed=np.any(np.abs(delta-shift)>[25,15,15],axis=2).astype(np.float32)
    if changed.mean()>=.04:
        return False
    # A small roof, hand or bright reflection can occupy less than 4% of the bay.
    h,w=changed.shape
    region=(max(1,w//5),max(1,h//5))
    local=cv2.boxFilter(changed,-1,region,anchor=(0,0))
    return float(local[:h-region[1]+1,:w-region[0]+1].max())<.30


def same_pose(a,b,shape):
    return a is not None and b is not None and float(np.max(np.linalg.norm(a-b,axis=1)))<=max(shape[:2])*.008


# How long an approved board may stay hidden or off-pose and still be restored automatically.
# Restoring still needs two newer frames whose fixed landmarks verify the approved pose,
# so a moved camera or board never restores. 11/10 classroom: hands placing several cars
# hid a board for 12-60 s about 20 times; at 12 s each one needed a manual re-approval.
APPROVAL_RECHECK_SECONDS = 90
# 11/11 15:47: moving cars nudged the Busan board 2-3 px; the full search then landed >15 px
# off at a corner, the approval was released and the pixel-exact recheck never matched, so
# the lot sent nothing (and white-car LEDs went unjudged) until a person re-approved. While
# an approval is suspended, a board found again in the same lot within SHIFT_ADOPT_FACTOR of
# the frame size (~31 px on 1920) of the reviewed pose and steady for SHIFT_ADOPT_FRAMES
# frames is adopted as the new pose. A bay is ~100 px wide, so this cannot change which bay a
# sticker falls in; bigger moves still need '칸 위치 맞음'.
SHIFT_ADOPT_FACTOR = .016
SHIFT_ADOPT_FRAMES = 5
STARTUP_APPROVAL_SECONDS = 180  # camera warm-up after an app start can take ~30 s


def probable_red_body(light):
    """A weak red-emission box spread over a large part of the bay is red car paint, not an LED.

    Field 11/10 (lights on, LEDs off/on): car 33 at A/B-01 (red body, white sticker)
    produced a 81x81 px box covering 17% of the bay at 0.850-0.860, enough to confirm a
    mock fire and raise a level-2 alert. Real lit roofs measured 1.3-15.5% at 0.874-0.978,
    and >=0.94 whenever they covered 11% or more. Body colour is never fire evidence.
    """
    if light.get('method')!='red_emission':
        return False
    return light.get('bay_area_fraction',0)>=.12 and light.get('confidence',1)<.88


def glow_detect(glow,frame):
    """(lights, rejected) from the red-light model; older models give no rejections."""
    full=getattr(glow,'detect_with_rejections',None)
    if full is None:return glow.detect(frame),[]
    lights,rejected=full(frame)
    return lights,rejected


def led_debug_bays(value=None):
    """V53_LED_DEBUG_BAYS='A-04,A-05' limits rejection logs to those bays; unset logs bays only."""
    value=os.environ.get('V53_LED_DEBUG_BAYS','') if value is None else value
    return frozenset(x.strip().upper() for x in value.split(',') if x.strip())


def mock_fire_car_list(value):
    """`mock_fire_cars` in assets.local.json: numbers of the cars that carry an LED. Empty = no list."""
    if value in (None,'',[]):return frozenset()
    if not isinstance(value,(list,tuple)) or not all(isinstance(v,str) and len(v)==2 and v.isdigit() for v in value):
        raise ValueError('mock_fire_cars는 두 자리 번호 문자열 목록이어야 합니다.')
    return frozenset(value)


def not_led_car(light, row, led_cars):
    """A red light on a car whose number is read and is not an LED car is paint, wherever it is parked.

    Field 11/10 (collection.db, 50,873 frames with a light): every one of 1,300 false runs
    sat on a read, non-LED car (33, 21, 22, 88, 30, ...), many stable for 100-300 frames at
    0.85-0.97 in fixed bays, so neither score nor repetition separates them. Real runs were
    all on cars 11/23/42. Identity comes from the sticker number, never the body colour.
    Unread numbers (null) keep the optical rules.
    """
    if not led_cars or not row or light.get('location','space')!='space':return False
    car=row.get('car_id')
    return bool(car) and row.get('observation')=='occupied' and car not in led_cars


def boundary_not_led_car(light, rows_by_id, led_cars):
    """A boundary light whose every touched bay holds a read, non-LED car is that car's paint.

    11/11 demo site: a 0.91 light 82.7% inside Busan A-02 (red car 21) fell below the 0.85
    bay overlap, became 'boundary' and was sent as a lot-level mock fire.
    """
    touching=light.get('touching') or ()
    if not led_cars or light.get('location')!='boundary' or not touching:return False
    return all(not_led_car(dict(location='space'),rows_by_id.get(sid),led_cars) for sid in touching)


def split_filtered_lights(lights, rows_by_id, led_cars=frozenset()):
    """Keep LED candidates; return the ones excluded as paint, with the measured values.

    The exclusion thresholds were set under the 11/10 lab lighting. Recording each
    excluded box (bay, reason, size, score) lets a rehearsal under different lighting
    show at once whether a real LED was filtered out.
    """
    kept,filtered=[],[]
    for light in lights:
        row=rows_by_id.get(light.get('space_id'))
        # A listed LED car's own roof light can cover 12% of a bay (11/10 classroom:
        # car 42 at Busan B-03, 11.94% at 0.878-0.912); the red-body guess is for other cars.
        led_car=bool(led_cars and row and row.get('car_id') in led_cars)
        if not_led_car(light,row,led_cars):reason='not_led_car'
        elif boundary_not_led_car(light,rows_by_id,led_cars):reason='not_led_car_boundary'
        elif probable_paint_glint(light,row):reason='paint_glint'
        elif not led_car and probable_red_body(light):reason='red_body'
        else:
            kept.append(light)
            continue
        filtered.append(dict(reason=reason,space_id=light.get('space_id'),location=light.get('location'),
                             bbox_xyxy=light.get('bbox_xyxy'),confidence=round(float(light.get('confidence',0)),3),
                             bay_area_fraction=round(float(light.get('bay_area_fraction',0)),4)))
    return kept,filtered


def probable_paint_glint(light, row):
    """Small high-scoring paint highlights seen beside the white fleet stickers."""
    if not row or row.get('sticker_color')!='white' or row.get('observation')!='occupied':
        return False
    if light.get('method')!='red_emission' or not 0<light.get('bay_area_fraction',0)<.0035:
        return False
    return light['confidence']<.99


def same_emitter(light,old):
    """The same optical source: same bay/location and a nearby box centre."""
    x,y,u,v=light['bbox_xyxy']
    a,b,c,d=old['bbox_xyxy']
    dx=(a+c-x-u)/2;dy=(b+d-y-v)/2
    return (light['space_id']==old['space_id'] and light['location']==old['location']
            and np.hypot(dx,dy)<=max(4,.55*min(u-x,v-y,c-a,d-b)))


# Frames a confirmed LED may be missing and still continue as confirmed when it reappears.
# 11/11 Busan A-04·05: a listed LED car with a white body saturates like the LED, so
# the core/red-ring test never fires. Its LED instead tints the white roof pink.
# Measured at 960px (A4 shade): 42 on 466-499px / off 0-1px of 7322; 11 on 306-344 / off 0 of 7495;
# red cars 24·30 about 2000+ (28%), hence the upper limit.
LED_GLOW_MIN_FRACTION = .01
LED_GLOW_MAX_FRACTION = .15
LED_GLOW_FRAMES = 3


def pink_glow(frame, polygon):
    """(fraction of bay, bbox of the largest pink blob) for LED light tinting a white body."""
    pts=np.int32(np.round(polygon))
    x,y,w,h=cv2.boundingRect(pts)
    H,W=frame.shape[:2]
    x0,y0,x1,y1=max(0,x),max(0,y),min(W,x+w),min(H,y+h)
    if x1<=x0 or y1<=y0:return 0.,None
    crop=frame[y0:y1,x0:x1].astype(np.int16)
    mask=np.zeros(crop.shape[:2],np.uint8);cv2.fillPoly(mask,[pts-[x0,y0]],1)
    area=int(mask.sum())
    if area<=0:return 0.,None
    b,g,r=crop[:,:,0],crop[:,:,1],crop[:,:,2]
    pink=((r-np.maximum(g,b)>25)&(r>150)&(mask>0)).astype(np.uint8)
    count=int(pink.sum())
    if not count:return 0.,None
    n,labels,stats,_=cv2.connectedComponentsWithStats(pink,connectivity=8)
    k=1+int(np.argmax(stats[1:,cv2.CC_STAT_AREA]))
    bx,by,bw,bh=stats[k,:4]
    return count/area,[int(x0+bx),int(y0+by),int(x0+bx+bw),int(y0+by+bh)]


def led_identity(row, memory, key, now, led_cars):
    """The bay's listed LED car: this frame's number, or the bay's own earlier read (like the #84 pin).

    A lit LED glares over car 42's sticker (11/11: sticker lost 165/165 frames), so the read
    is held until the bay is seen empty or another number is read there. The demo rule
    empties a bay for 5 s between cars; a held red car still fails the 15% pink limit.
    """
    if not led_cars or row.get('observation')=='empty':
        memory.pop(key,None);return None
    car,other=row.get('car_id'),row.get('number_candidate')
    if car:
        if car in led_cars:memory[key]=(car,now)
        else:memory.pop(key,None)
    elif other and key in memory and other!=memory[key][0]:
        memory.pop(key,None)
    held=memory.get(key)
    return held[0] if held and row.get('observation')=='occupied' else None


LED_UNSEEN_SECONDS = 10


def led_car_glow_lights(frame, rows, polygons, lights, memory, streaks, scope, now, led_cars, seen=None):
    """Confirm after 3 consecutive frames; a frame without the glow resets the count.

    seen maps each LED-list car to the last time its number was read in any bay (both
    cameras share it). 11/11 15:55: car 11 carried to Busan B-01 with its LED already on
    never had its number read there (the glow covers the sticker), so the bay had no LED
    identity and the fire was missed. An occupied bay whose number is unread, with LED-range
    pink glow, may stand for an LED car that has not been read anywhere for 10 s. A read
    non-LED number still excludes the bay; a red body exceeds the 15% limit.
    """
    seen={} if seen is None else seen
    for row in rows:
        if row.get('car_id') in led_cars:seen[row['car_id']]=now
    unseen=sorted(c for c in (led_cars or ()) if now-seen.get(c,float('-inf'))>LED_UNSEEN_SECONDS)
    lit={l.get('space_id') for l in lights if l.get('state')=='confirmed'}
    found=[]
    for row in rows:
        sid=row['space_id'];key=(*scope,sid)
        car=led_identity(row,memory,key,now,led_cars)
        if (not car and unseen and row.get('observation')=='occupied' and not row.get('car_id')
                and row.get('number_candidate') in (None,*unseen)):
            car='unread:'+'/'.join(unseen)
        fraction,box=pink_glow(frame,polygons[sid]) if car and sid in polygons else (0.,None)
        if not car or box is None or not LED_GLOW_MIN_FRACTION<=fraction<=LED_GLOW_MAX_FRACTION:
            streaks.pop(key,None);continue
        count=streaks[key]=streaks.get(key,0)+1
        if sid in lit:continue
        found.append(dict(bbox_xyxy=box,confidence=.9,method='red_emission',detail='led_car_glow',space_id=sid,location='space',
            bay_area_fraction=round(fraction,4),led_car=car,consecutive_frames=min(LED_GLOW_FRAMES,count),
            state='confirmed' if count>=LED_GLOW_FRAMES else 'candidate'))
    return found


LED_MISSED_FRAMES = 3


def carry_missed_lights(current,previous):
    """Remember a confirmed emitter that is missing from this frame, for up to LED_MISSED_FRAMES frames.

    11/10 classroom, Busan A-02 (car 23): the LED scored 0.93-0.95 whenever seen but vanished
    for 1-3 frames at a time; each miss reset the repeat count, so the next sighting was only a
    candidate and the mock fire stopped for 2-6 s (51 gaps over 2 s in 10 min). The remembered
    box is never reported or sent (no fire while unseen); it only lets the same emitter seen
    again continue as confirmed. A longer absence starts over.
    """
    return [dict(old,_missed=old.get('_missed',0)+1) for old in previous
            if old.get('state')=='confirmed' and old.get('_missed',0)<LED_MISSED_FRAMES
            and not any(same_emitter(light,old) for light in current)]


def repeat_lights(current,previous):
    """Only count repeated optical evidence at the same position and location."""
    for light in current:
        prior=max((old.get('consecutive_frames',1) for old in previous if same_emitter(light,old)),default=0)
        light['consecutive_frames']=min(3,prior+1)
        # Broad, short-lived highlights on car paint can recur in two frames. Weak red
        # emission (<0.88; real LEDs p5 0.879, paint median 0.863 on 11/10) needs a third.
        weak=light.get('method')=='red_emission' and light.get('confidence',1)<.88
        required=3 if light.get('bay_area_fraction',0)>.12 or weak else 2
        light['state']='confirmed' if light['consecutive_frames']>=required else 'candidate'


def rotated_proposals(detector, frame):
    """Run the fallback once per frame and restore half-open source-pixel coordinates."""
    h,w=frame.shape[:2]
    result=detector.infer_bgr(cv2.rotate(frame,cv2.ROTATE_180),.35,render=False)
    proposals=[]
    for item in result['detections']:
        x,y,u,v=item['bbox_xyxy']
        proposals.append(dict(item,bbox_xyxy=[w-u,h-v,w-x,h-y]))
    return proposals,result['inference_ms']


def recover_rotated_print(frame, sid, polygons, proposals, original, printed):
    """Require print and paper agreement. Separate or conflicting detections veto recovery."""
    recovered=[]
    for candidate in proposals:
        box=candidate['bbox_xyxy']
        if assign_sticker(box,polygons)!=sid:continue
        hit=printed.read_box(frame,candidate)
        if not hit or hit['margin']<.04:continue
        corroborated=any(d['sticker_color']==candidate['sticker_color'] and same_box(box,d['bbox_xyxy'])
                         and max(d['confidence'],candidate['confidence'])>=.75 for d in original)
        # The normal detector operating point also applies to its rotated view.
        # A weaker box needs exceptional print evidence or a second localization.
        if candidate['confidence']<.85 and hit['score']<.94 and not corroborated:continue
        recovered.append(dict(candidate,space_id=sid,print_read=hit,localization_source='rotated_proposal_and_print'))
    if len(recovered)==1:
        box=recovered[0]['bbox_xyxy']
        for d in (*proposals,*original):
            if d['confidence']<.85 or overlap_slot(d['bbox_xyxy'],polygons[sid])<=.10:continue
            if not same_box(box,d['bbox_xyxy']) or d['sticker_color']!=recovered[0]['sticker_color']:
                return []
    return recovered


class ParkingPipeline:
    fire_watch = None
    glow = None
    glow_worker = None
    printed = None
    decoration_reference = None

    def __init__(self, cfg):
        cv2.setNumThreads(2)
        self.detector=StickerModel(cfg['detector'])
        self.registry=BoardRegistry(cfg['team_directory'],cfg.get('legacy_board_directory'),cfg.get('landmark_directory'))
        self.occupancy=Classifier(cfg['team_directory']/'occupancy/model.xml',('empty','occupied'))
        self.number_model=Classifier(cfg['team_directory']/'sticker/model.xml',STICKER_LABELS)
        print_path=cfg.get('printed_numbers')
        self.printed=PrintedNumbers.load(print_path) if print_path and print_path.is_file() else None
        self.decoration_reference=load_reference(cfg['team_directory'])
        led_path=cfg.get('fire_led_model')
        led_error=None
        try:
            self.fire_watch=FireWatch(LensModel.load(led_path)) if led_path and led_path.is_file() else None
        except (OSError, ValueError, KeyError, EOFError, BadZipFile):
            led_error='LED 모델 파일을 읽을 수 없습니다.'
            self.fire_watch=None
        glow_path=cfg.get('glow_model')
        try:
            self.glow=GlowModel(glow_path) if glow_path and glow_path.is_file() else None
        except (OSError,ValueError,KeyError,EOFError,BadZipFile,TypeError):
            led_error='즉시 점등 모델을 읽을 수 없습니다.'
        self.mock_fire_cars=mock_fire_car_list(cfg.get('mock_fire_cars'))
        self.numbers=NumberReader(cfg.get('ocr_python',''),cfg.get('ocr_models',''))
        self.print_history={}
        self.fixed_views={}
        self.interrupted_views={}
        self.board_tracking={}
        self.start_views={c:ref for c in ('1','2') if (ref:=self.registry.load_start_view(c)) is not None}
        self.light_history={}
        self.empty_boards=load_empty_board(cfg.get('empty_bays'))
        self.lit_empty_boards=load_empty_board(cfg.get('empty_bays_lit'))
        self.local_floor=load_empty_board(cfg.get('reviewed_empty_bays'),partial=True)
        self.floor_history={}
        self.lock=threading.RLock();self.photo_lock=threading.Lock();self.baselines={};self.confirmed={};self.search_order={}
        if self.glow is not None:
            self.glow_worker=ThreadPoolExecutor(max_workers=1,thread_name_prefix='v53-red-light')
        self.metadata={'product_version':'V53','camera_resolution':[1920,1080],
            'detector':self.detector.metadata,'number_model_classes':list(NUMBERS),
            'printed_number_count':len(self.printed.labels) if self.printed else 0,
            'body_decoration_reference_ready':self.decoration_reference is not None,
            'fire_led_ready':self.glow is not None,
            'lens_transition_ready':self.fire_watch is not None,
            'surveyed_empty_bays':len(self.empty_boards),
            'surveyed_lit_empty_bays':len(self.lit_empty_boards),
            'fire_led_error':led_error,
            'number_policy':'D-54 print matching or OCR, color consistency, two live frame readings',
            'roi_method':'per-frame SIFT homography to surveyed drawing polygons',
            'occupancy_model':'Geti multilabel empty/occupied classification',
            'vehicle_body_detection':False,'input_scope':'two known printed boards only'}

    def invalidate(self, camera=None, *, disconnected=False):
        with self.lock:
            # 11/11 USB drill: a re-plugged camera released both approvals until a person
            # re-approved. Keep the reviewed pose for APPROVAL_RECHECK_SECONDS so two
            # consecutive frames of the new stream that verify the same pose restore it.
            # A moved camera never verifies, and nothing else (identity, empty baseline)
            # carries over.
            reconnect=None
            if disconnected and camera is not None:
                approved=self.confirmed.get(camera)
                held=getattr(self,'interrupted_views',{}).get(camera)
                view=getattr(self,'fixed_views',{}).get(camera)
                if held is not None:
                    reconnect=((None,)+tuple(held[0][1:]),held[1],held[2],None)
                elif isinstance(approved,tuple) and len(approved)==4 and view and view[1] is not None:
                    reconnect=((None,)+tuple(approved[1:]),view[1],time.monotonic()+APPROVAL_RECHECK_SECONDS,None)
            if self.fire_watch:self.fire_watch.clear(camera)
            if camera is None:self.baselines.clear();self.confirmed.clear();self.search_order.clear()
            else:self.baselines.pop(camera,None);self.confirmed.pop(camera,None);self.search_order.pop(camera,None)
            self.numbers.invalidate(camera)
            interrupted=getattr(self,'interrupted_views',{})
            if camera is None:interrupted.clear()
            elif reconnect is not None:interrupted[camera]=reconnect
            else:interrupted.pop(camera,None)
            history=getattr(self,'light_history',{})
            floor=getattr(self,'floor_history',{})
            if camera is None:floor.clear()
            else:floor.pop(camera,None)
            if camera is None:history.clear()
            else:history.pop(camera,None)
            fixed=getattr(self,'fixed_views',{})
            tracking=getattr(self,'board_tracking',{})
            if camera is None:tracking.clear()
            else:tracking.pop(camera,None)
            reviewed=self.__dict__.setdefault('approved_views',{})
            if camera is None:reviewed.clear()
            elif not disconnected:reviewed.pop(camera,None)
            if camera is None:fixed.clear()
            elif disconnected and camera in fixed:
                # Only an appearance reference survives a channel reconnect.
                # No epoch, approval, empty baseline or identity carries over.
                fixed[camera]=(None,fixed[camera][1])
            else:fixed.pop(camera,None)
            if self.printed:
                if camera is None:self.print_history.clear()
                else:self.print_history.pop(camera,None)

    def approve_roi(self,camera,result,frame,save_start=True):
        with self.lock:
            if not result.get('registration_ok') or result.get('_outline') is None:
                raise ValueError('현재 도면 정합이 먼저 확인되어야 합니다.')
            interrupted=getattr(self,'interrupted_views',{}).pop(camera,None)
            first_approval=camera not in self.confirmed and interrupted is None
            history=getattr(self,'floor_history',{})
            prior=history.get(camera)
            # On first approval, the reviewed photo can be the first of two
            # independent floor observations. Dispatch still requires a newer
            # USB frame, another positive floor match and an approved pose.
            if not (first_approval and prior
                    and prior[0]==(result['stream_epoch'],result['lot_id'])
                    and prior[1]==result.get('frame_sequence') and time.monotonic()-prior[2]<=2
                    and np.array_equal(prior[3],result['_outline'])):
                history.pop(camera,None)
            # A reviewed light is one optical sample. A different USB frame
            # must locate it again on the same board before any fire is sent.
            lights=getattr(self,'light_history',{})
            sample=lights.get(camera)
            if not (first_approval and sample and sample[:3]==(
                    result['stream_epoch'],result['lot_id'],result.get('frame_sequence'))
                    and time.monotonic()-sample[3]<=2 and np.array_equal(sample[4],result['_outline'])):
                lights.pop(camera,None)
            self.confirmed[camera]=(result['stream_epoch'],result['lot_id'],result['_outline'].copy(),frame.shape)
            # The pose a person approved; automatic small-shift adoption is measured from it.
            self.__dict__.setdefault('human_poses',{})[camera]=(result['lot_id'],result['_outline'].copy())
            if self.fire_watch:self.fire_watch.clear(camera)
            pose=result.get('_pose')
            if (hasattr(self,'fixed_views') and pose and pose['lot_id']==result['lot_id']
                    and np.array_equal(result['_outline'],pose['outline'])):
                # Register the reviewed image with its own geometry.
                self.fixed_views[camera]=(result['stream_epoch'],self.registry.fixed_view(frame,pose))
                ref=self.fixed_views[camera][1]
                # The fixed view adapts its exposure while approved; keep the reviewed
                # appearance too so a recheck still verifies after the light returns.
                if ref:self.__dict__.setdefault('approved_views',{})[camera]=ref
                if ref and save_start and camera in ('1','2') and hasattr(self,'start_views'):
                    self.start_views[camera]=ref
                    self.registry.save_start_view(camera,ref)

    def validate_empty(self,result):
        if any(light.get('location') in ('space','boundary') for light in result.get('fire_lights',())):
            raise ValueError('주차칸에 빨간 LED가 보입니다. 물체를 치운 뒤 빈 기준을 등록하세요.')
        if (not result.get('roi_confirmed') or not result.get('registration_ok') or not result.get('spaces') or
            any(r.get('model_observation')!='empty' or r.get('model_score',0)<.65 or r.get('quality_reason') for r in result['spaces']) or
            result.get('weak_sticker_count',0)):
            raise ValueError('모든 칸의 빈 상태·도면 위치·스티커 없음이 확인되어야 빈 기준을 등록할 수 있습니다.')

    def register_empty(self,camera,result,frame):
        with self.lock:
            self.validate_empty(result)
            self.baselines[camera]={'epoch':result['stream_epoch'],'lot':result['lot_id'],
                'outline':result['_outline'].copy(),'crops':{k:v.copy() for k,v in result['_crops'].items()},
                'reviewed_frame':{'sequence':result['frame_sequence'],'observed_at':result['observed_at']}}

    def analyze(self,frame,*,camera='photo',epoch=0,sequence=0,observed_at=None,lot_hint='auto',threshold=.85):
        started=time.perf_counter()
        # The live loop has one inference owner; this also serializes sample requests.
        with self.lock:
            job=self.glow_worker.submit(glow_detect,self.glow,frame) if self.glow_worker else None
            try:
                return self._analyze(frame,camera,epoch,sequence,observed_at,lot_hint,threshold,started,glow_job=job)
            finally:
                if job is not None and not job.done():
                    wait((job,))

    def analyze_photo(self,frame,*,camera='photo',lot_hint='auto',wait_seconds=6):
        """One photo request waits for bounded OCR completion, including final nulls."""
        if not self.photo_lock.acquire(blocking=False):
            raise ValueError('다른 사진을 분석 중입니다.')
        started=time.monotonic()
        try:
            # A different upload may reuse the same camera key, bay and crop.
            # Keep OCR retries within this request, clear evidence from earlier uploads.
            self.invalidate(camera)
            result=self.analyze(frame,camera=camera,lot_hint=lot_hint)
            while result.get('ocr_pending') and time.monotonic()-started<wait_seconds:
                time.sleep(.1)
                result=self.analyze(frame,camera=camera,lot_hint=lot_hint)
            result['photo_request_ms']=round((time.monotonic()-started)*1000,2)
            if result.get('ocr_pending'):
                result['note']='OCR 대기 제한에 도달했습니다. 일부 번호는 처리 대기이며 최종 판독이 아닙니다.'
            return result
        finally:self.photo_lock.release()

    def _log_rejected_lights(self,camera,rejected,*,now=None,every=60):
        """Diagnostics only (11/11 A-04·05): per-bay counts of rejected LED candidates once a minute."""
        bays=led_debug_bays()
        counts=self.__dict__.setdefault('_rejected_counts',{})
        for item in rejected:
            bay=item.get('space_id')
            if item.get('location')!='space' or not bay or (bays and bay.upper() not in bays):continue
            key=(bay,item['reason'])
            n,best=counts.get(key,(0,0.))
            counts[key]=(n+1,max(best,item['confidence']))
        now=time.monotonic() if now is None else now
        last=self.__dict__.setdefault('_rejected_logged',{}).get(camera)
        if last is None:self._rejected_logged[camera]=now;return []
        if now-last<every or not counts:return []
        self._rejected_logged[camera]=now
        lines=[f'{bay} {reason} {n}회 최고 {best:.2f}' for (bay,reason),(n,best) in sorted(counts.items())]
        counts.clear()
        logging.getLogger(__name__).info('LED 탈락 후보 camera=%s %s',camera,' / '.join(lines))
        return lines

    def _log_filtered_lights(self,camera,filtered,*,now=None,every=60):
        """Log each (camera, bay, reason) exclusion when first seen, then at most once a minute."""
        seen=self.__dict__.setdefault('_filtered_logged',{})
        now=time.monotonic() if now is None else now
        logged=[]
        for item in filtered:
            key=(camera,item['space_id'],item['reason'])
            if now-seen.get(key,-every)<every:continue
            seen[key]=now
            logged.append(key)
            logging.getLogger(__name__).info(
                'LED candidate excluded camera=%s bay=%s reason=%s confidence=%.3f bay_area=%.4f box=%s',
                camera,item['space_id'],item['reason'],item['confidence'],item['bay_area_fraction'],item['bbox_xyxy'])
        return logged

    def arm_saved_approvals(self,seconds=None):
        """Re-verify the last operator-approved pose saved on disk after an app restart.

        11/11: after a restart nobody pressed '칸 위치 맞음', so white-car LEDs (42·11) were
        never judged and nothing was delivered for 40 minutes. The saved view is written only
        when a person approves; two consecutive frames that verify exactly that pose restore
        the approval (same rule as a USB reconnect). A moved camera or board never verifies.
        """
        seconds=STARTUP_APPROVAL_SECONDS if seconds is None else seconds
        armed=[]
        with self.lock:
            views=self.__dict__.setdefault('interrupted_views',{})
            for camera in ('1','2'):
                load=getattr(self.registry,'load_start_view',None)
                ref=load(camera) if load else None
                if not ref or camera in self.confirmed or camera in views:continue
                h,w=ref['shape']
                self.__dict__.setdefault('human_poses',{})[camera]=(ref['pose']['lot_id'],np.float32(ref['pose']['outline']))
                views[camera]=((None,ref['pose']['lot_id'],np.float32(ref['pose']['outline']),(h,w,3)),
                               ref,time.monotonic()+seconds,None)
                self.__dict__.setdefault('approved_views',{})[camera]=ref
                armed.append(camera)
        if armed:logging.getLogger(__name__).info('Saved board approvals armed for recheck: cameras %s',','.join(armed))
        return armed

    def _suspend_mismatched_approval(self,camera,epoch,shape,observed_at):
        """A full search that lands off the reviewed pose suspends, not erases, the review.

        Field 11/10: the A board's full search is unreliable. One frame where the
        fixed landmarks missed and the search returned a shifted outline erased an
        approval for the rest of the run. Keep the reviewed fixed view for
        APPROVAL_RECHECK_SECONDS; only two consecutive newer frames that verify the
        original pose restore it, and a real move never verifies.
        """
        approved=self.confirmed.get(camera)
        fixed=getattr(self,'fixed_views',{}).get(camera)
        views=getattr(self,'interrupted_views',None)
        if (views is None or not observed_at or camera in views or not isinstance(approved,tuple) or len(approved)!=4
                or approved[0]!=epoch or tuple(approved[3])!=tuple(shape) or not fixed or fixed[0]!=epoch or fixed[1] is None):
            return False
        views[camera]=(approved,fixed[1],time.monotonic()+APPROVAL_RECHECK_SECONDS,None)
        return True

    def _adopt_small_shift(self,camera,epoch,lot,outline,registration,frame,paused):
        """Adopt a steady, slightly shifted pose of the same board for a suspended approval."""
        streaks=self.__dict__.setdefault('_shift_streaks',{})
        limit=max(frame.shape[:2])*SHIFT_ADOPT_FACTOR
        # Review 11/11: measure from the pose a person approved, never from an earlier
        # adoption, so repeated small shifts cannot add up beyond the limit.
        human=getattr(self,'human_poses',{}).get(camera)
        reviewed=np.float32(human[1]) if human and human[0]==lot else np.float32(paused[0][2])
        shift=float(np.max(np.linalg.norm(reviewed-np.float32(outline),axis=1)))
        if (shift>limit or registration.get('clipped_spaces') or tuple(frame.shape)!=tuple(paused[0][3])):
            streaks.pop(camera,None);return False
        prior=streaks.get(camera)
        count=prior[0]+1 if prior and prior[1]==epoch and same_pose(prior[2],outline,frame.shape) else 1
        streaks[camera]=(count,epoch,np.float32(outline).copy())
        if count<SHIFT_ADOPT_FRAMES:return False
        ref=self.registry.fixed_view(frame,registration) if hasattr(self.registry,'fixed_view') else None
        self.confirmed[camera]=(epoch,lot,np.float32(outline).copy(),frame.shape)
        verified=None
        if ref:
            # The saved start view stays the person-approved one (not overwritten here).
            self.__dict__.setdefault('fixed_views',{})[camera]=(epoch,ref)
            self.__dict__.setdefault('approved_views',{})[camera]=ref
            verify=getattr(self.registry,'verify_fixed_view',None)
            verified=bool(verify(frame,ref)) if verify else None
        getattr(self,'interrupted_views',{}).pop(camera,None);streaks.pop(camera,None)
        logging.getLogger(__name__).info('Camera %s approval adopted after a small board shift (%.1f px from the approved pose, lot %s, new reference verifies: %s)',
                                         camera,shift,lot,verified)
        return True

    def _recheck_interrupted(self,frame,camera,epoch,sequence,lot_hint,inspect):
        """Revalidate an operator-reviewed pose after a brief obstruction on the same stream."""
        views=getattr(self,'interrupted_views',{})
        saved=views.get(camera)
        if saved is None:return None
        approval,ref,deadline,previous=saved
        if approval[0] is None:
            # First frame after a reconnect: the review may only resume on this new stream.
            approval=(epoch,)+tuple(approval[1:]);previous=None
            views[camera]=(approval,ref,deadline,previous)
        if (epoch!=approval[0] or lot_hint not in ('auto',approval[1]) or time.monotonic()>deadline
                or tuple(frame.shape)!=tuple(approval[3])):
            views.pop(camera,None)
            return None
        pose=self.registry.verify_fixed_view(frame,ref) if inspect else None
        reviewed=getattr(self,'approved_views',{}).get(camera)
        if pose is None and inspect and reviewed is not None and reviewed is not ref:
            # 11/11 ceiling-light drill: the exposure-adapted reference followed the dark
            # room; the reviewed appearance matches again once the light is back.
            pose=self.registry.verify_fixed_view(frame,reviewed)
        why=None if pose is not None else ('reference_not_matched' if inspect else 'frame_quality')
        if (pose is not None and pose.get('outline') is not None
                and not same_pose(approval[2],pose['outline'],frame.shape)):
            pose=None;why='moved'
        if why:
            # Diagnostics only (11/11 lighting drill): why an interrupted approval is not restored.
            seen=self.__dict__.setdefault('_recheck_logged',{})
            now=time.monotonic()
            if now-seen.get(camera,0)>=1:
                seen[camera]=now
                logging.getLogger(__name__).info('Camera %s approval recheck failed: %s (%.0fs left)',camera,why,deadline-now)
        if pose is not None and previous is not None and sequence>previous:
            self.confirmed[camera]=approval
            self.fixed_views[camera]=(epoch,ref)
            views.pop(camera,None)
        else:
            views[camera]=(approval,ref,deadline,sequence if pose is not None else None)
        return pose

    def _analyze(self,frame,camera,epoch,sequence,observed_at,lot_hint,threshold,started,*,glow_job=None):
        if not isinstance(frame,np.ndarray) or frame.dtype!=np.uint8 or frame.ndim!=3 or frame.shape[2]!=3 or min(frame.shape[:2])<32:
            raise ValueError('32px 이상의 uint8 BGR 프레임이 필요합니다.')
        quality_report = frame_quality(frame)
        # A small powered LED can be the only bright part of a dark frame.
        # Board quality gates its location and transmission, not its visibility.
        previous_registration = self.search_order.get(camera)
        registration = None
        detection = {'detections': [], 'inference_ms': 0.0}
        fixed=getattr(self,'fixed_views',{}).get(camera)
        tracking=getattr(self,'board_tracking',None) if observed_at else None
        interrupted=getattr(self,'interrupted_views',{})
        if camera in interrupted and observed_at:
            registration=self._recheck_interrupted(frame,camera,epoch,sequence,lot_hint,quality_report['state']=='inspect')
        if quality_report['state'] == 'inspect':
            if not fixed and tracking is not None and camera not in tracking:
                hint=getattr(self,'start_views',{}).get(camera)
                if hint and lot_hint in ('auto',hint['pose']['lot_id']):
                    tracking[camera]=(epoch,hint)
            if fixed and fixed[0] in (None,epoch) and fixed[1] and lot_hint in ('auto',fixed[1]['pose']['lot_id']):
                registration=self.registry.verify_fixed_view(frame,fixed[1])
                reviewed=getattr(self,'approved_views',{}).get(camera)
                if registration is None and reviewed is not None and reviewed is not fixed[1]:
                    registration=self.registry.verify_fixed_view(frame,reviewed)
            if registration is None and not fixed and tracking is not None and camera in tracking:
                tracked_epoch,reference=tracking[camera]
                if tracked_epoch==epoch and lot_hint in ('auto',reference['pose']['lot_id']):
                    registration=self.registry.verify_fixed_view(frame,reference)
                    if registration:registration['method']='unapproved_fixed_landmarks'
                if registration is None:tracking.pop(camera,None)
            if registration is None:
                registration = self.registry.locate(frame, lot_hint, previous_registration)
                if (registration and fixed and fixed[0]==epoch and fixed[1] and observed_at
                        and camera in self.confirmed and camera not in interrupted):
                    self.fixed_views[camera]=(epoch,self.registry.refresh_fixed_exposure(frame,fixed[1],registration))
                if registration and not fixed and tracking is not None:
                    reference=self.registry.fixed_view(frame,registration)
                    if reference:
                        tracking[camera]=(epoch,reference)
                        if hasattr(self,'start_views') and camera in ('1','2') and not registration.get('clipped_spaces'):
                            self.start_views[camera]=reference
                if registration is not None and fixed and fixed[0] is None:
                    self.fixed_views.pop(camera,None)
            # Keep the full-frame input distribution used by the trained detector.
            detection = self.detector.infer_bgr(frame,.05,observed_at=observed_at,render=False)
        lights,rejected_lights=glow_job.result() if glow_job is not None else (glow_detect(self.glow,frame) if self.glow is not None else ([],[]))
        self.search_order[camera]=registration
        # Very weak candidates are diagnostic evidence only. Do not promote
        # them to occupancy, OCR, type, or server observations.
        diagnostic=detection['detections']
        weak=[d for d in diagnostic if d['confidence']>=.35]
        detections=[dict(d) for d in weak if d['confidence']>=threshold]
        if self.printed:
            for d in detections:
                d['print_read']=self.printed.read_box(frame,d)
                if not d['print_read']:
                    d['print_read']=reread_detector_crop(self.printed,frame,d)
        ocr_waiting=False
        result={'product_version':'V53','source':'live_camera' if observed_at else 'saved_image',
            'camera_id':camera,'stream_epoch':epoch,'frame_sequence':sequence,'observed_at':observed_at,
            'image_size':list(frame.shape[1::-1]),'registration_ok':registration is not None,
            'roi_confirmed':False,'lot_id':None,'spaces':[],'detections':detections,'candidates':[],
            'weak_sticker_count':len(weak),'ocr_status':self.numbers.status,'empty_reference':False,
            'note':'도면을 찾지 못했습니다. 판 전체와 칸 이름이 보이도록 비춰 주세요.',
            'frame_quality':quality_report,'detector_ms':detection['inference_ms'],'_outline':None,'_crops':{},
            'fire_lights':localize(lights,None,{}),'filtered_lights':[]}
        if registration is None:
            approved=self.confirmed.get(camera)
            if (observed_at and fixed and fixed[0]==epoch and isinstance(approved,tuple) and len(approved)==4
                    and fixed[1] is not None and camera not in interrupted):
                interrupted[camera]=(approved,fixed[1],time.monotonic()+APPROVAL_RECHECK_SECONDS,None)
            getattr(self,'floor_history',{}).pop(camera,None)
            getattr(self,'light_history',{}).pop(camera,None)
            if tracking is not None:tracking.pop(camera,None)
            if self.fire_watch:self.fire_watch.clear(camera)
            if fixed and fixed[0] is not None:self.fixed_views.pop(camera,None)
            self.baselines.pop(camera,None);self.confirmed.pop(camera,None)
            if previous_registration is not None:
                # A new pose must earn fresh number evidence, even on the same USB connection.
                self.numbers.invalidate(camera)
            if self.printed:self.print_history.pop(camera,None)
            if quality_report['state'] == 'dark':
                result['note'] = '화면 전체가 어둡습니다. 렌즈 가림과 조명을 확인하세요. 칸 관측 보류.'
            elif quality_report['state'] == 'overexposed':
                result['note'] = '화면 전체가 과노출입니다. 반사와 노출을 확인하세요. 칸 관측 보류.'
            elif detections:
                result['note'] = f'스티커 {len(detections)}개 검출 · 도면 위치 미확인으로 칸·번호·전송 보류. 판 네 모서리를 확인하세요.'
        else:
            lot=registration['lot_id'];outline=registration['outline'];matrix=registration['matrix']
            paused=interrupted.get(camera)
            # A full search can land a few pixels off the reviewed pose (11/10, 11/11). Only a
            # different board cancels the recheck; a real move never verifies and expires.
            if paused and lot!=paused[0][1]:
                interrupted.pop(camera,None)
            elif paused and observed_at and quality_report['state']=='inspect':
                self._adopt_small_shift(camera,epoch,lot,outline,registration,frame,paused)
            result.update(lot_id=lot,_outline=outline,_pose=registration,registration={'inliers':registration['inliers'],
                'residual_px':round(registration['residual_px'],3),'line_support':round(registration['line_support'],3),
                'method':registration.get('method','team_reference')},note='도면 위치 확인 필요 · 화면의 칸 이름과 실제 칸을 대조하세요.')
            approved=self.confirmed.get(camera)
            if approved and (approved[0]!=epoch or approved[1]!=lot or not same_pose(approved[2],outline,frame.shape)):
                self._suspend_mismatched_approval(camera,epoch,frame.shape,observed_at)
                if self.fire_watch:self.fire_watch.clear(camera)
                getattr(self,'fixed_views',{}).pop(camera,None)
                self.confirmed.pop(camera,None);self.baselines.pop(camera,None);approved=None
                self.numbers.invalidate(camera)
                if self.printed:self.print_history.pop(camera,None)
            result['roi_confirmed']=bool(approved)
            baseline=self.baselines.get(camera)
            if baseline and (baseline['epoch']!=epoch or baseline['lot']!=lot or not same_pose(baseline['outline'],outline,frame.shape)):
                self.baselines.pop(camera,None);baseline=None
            result['empty_reference']=baseline is not None
            board_refs=getattr(self,'empty_boards',{})
            lit_refs=getattr(self,'lit_empty_boards',{})
            local_floor=getattr(self,'local_floor',{})
            result['empty_board_reference']=bool(board_refs or lit_refs or local_floor)
            history_floor=getattr(self,'floor_history',{})
            previous_floor=history_floor.pop(camera,None)
            stable_floor=set()
            floor_time=time.monotonic()
            if (observed_at and previous_floor and previous_floor[0]==(epoch,lot)
                    and sequence>previous_floor[1] and floor_time-previous_floor[2]<=3
                    and same_pose(previous_floor[3],outline,frame.shape)):
                stable_floor=previous_floor[4]
            current_floor=set()
            coverage=np.full(frame.shape[:2],255,np.uint8)
            clipped_spaces = registration.get('clipped_spaces', [])
            polygons=registration.get('polygons') or {s['space_id']:cv2.perspectiveTransform(np.float32(s['points'])[None],registration['inverse'])[0]
                      for s in self.registry.boards[lot]['spaces']}
            for d in detections:
                sid=assign_sticker(d['bbox_xyxy'],polygons)
                if sid:d.update(space_id=sid,lot_id=lot)
            localize(lights,outline,polygons)
            localize(rejected_lights,outline,polygons)
            result['rejected_lights']=rejected_lights
            self._log_rejected_lights(camera,rejected_lights)
            history=getattr(self,'light_history',None)
            if history is not None and observed_at:
                prior=history.pop(camera,None)
                now=time.monotonic()
                carried=[]
                if approved:
                    continuous=(prior and prior[0]==epoch and prior[1]==lot and sequence>prior[2]
                                and now-prior[3]<=4 and same_pose(prior[4],outline,frame.shape))
                    previous=list(prior[5])+list(prior[6] if len(prior)>6 else ()) if continuous else []
                    repeat_lights(lights,previous)
                    carried=carry_missed_lights(lights,previous)
                history[camera]=(epoch,lot,sequence,now,outline,lights,carried)
            # Weak detector hits can locate an ink patch; keep their original
            # scores. Only independent, high-quality printed digits and measured
            # paper color are eligible to corroborate such a location.
            if self.printed:
                lit={p['space_id']:p['bbox_xyxy'] for p in lights if p.get('space_id')}
                for d in detections:
                    lens=lit.get(d.get('space_id'))
                    if lens and not d.get('print_read'):
                        recovered=self.printed.read_beside_light(frame,d['bbox_xyxy'],lens)
                        if recovered and recovered['sticker_color']==d['sticker_color']:
                            d['print_read']=recovered['print_read']
                strong_slots={d.get('space_id') for d in detections}
                additional_proposals={}
                for candidate in diagnostic:
                    if candidate['confidence']>=threshold:continue
                    sid=assign_sticker(candidate['bbox_xyxy'],polygons)
                    if sid is None or sid in clipped_spaces:continue
                    if sid in strong_slots:
                        x,y,u,v=candidate['bbox_xyxy']
                        if any(d.get('space_id')==sid and min(u,d['bbox_xyxy'][2])>max(x,d['bbox_xyxy'][0])
                               and min(v,d['bbox_xyxy'][3])>max(y,d['bbox_xyxy'][1]) for d in detections):continue
                        additional_proposals.setdefault(sid,[]).append(candidate['bbox_xyxy'])
                        continue
                    hit=self.printed.read_box(frame,candidate)
                    if not hit:
                        if sid in lit:
                            found=self.printed.read_beside_light(frame,candidate['bbox_xyxy'],lit[sid])
                            if found and not any(d.get('space_id')==sid and d.get('print_read') and
                                    d['print_read']['number']==found['print_read']['number'] and same_box(
                                    found['bbox_xyxy'],d['bbox_xyxy']) for d in detections):
                                detections.append(dict(found,space_id=sid,lot_id=lot))
                    if hit and hit['score']>=.94 and hit['margin']>=.04:
                        if any(d.get('print_read') and d['print_read']['number']==hit['number']
                               and same_box(d['bbox_xyxy'],candidate['bbox_xyxy']) for d in detections):continue
                        detections.append(dict(candidate,space_id=sid,lot_id=lot,print_read=hit,
                                               localization_source='detector_proposal_and_print'))
                result['weak_sticker_count']=max(result['weak_sticker_count'],len(detections))
            previous_print=self.print_history.get(camera,{}) if self.printed else {}
            current_print={}
            active_ocr=set()
            rotated=None
            # Cache only searches of the very same pixels in this analysis.
            # Each caller can attach its own result metadata without affecting another.
            paper_searches={}
            def papers_in_bay(sid):
                if sid not in paper_searches:
                    paper_searches[sid]=self.printed.locate_in_bay(frame,polygons[sid])
                return paper_searches[sid]
            for space in self.registry.boards[lot]['spaces']:
                polygon=polygons[space['space_id']]
                quad=polygon[[1,2,3,0]] if space.get('crop_rotate') else polygon
                mapping=cv2.getPerspectiveTransform(quad,DEST)
                crop=cv2.warpPerspective(frame,mapping,(160,224))
                visible=cv2.warpPerspective(coverage,mapping,(160,224))
                clipped = space['space_id'] in clipped_spaces or np.mean(visible>250)<.98
                quality='칸 일부가 화면 밖' if clipped else crop_quality(crop)
                scores={} if quality else self.occupancy.scores(crop)
                # This Geti cosine head is clamped [-1,1] then sigmoid in Model API:
                # raw scores cannot reach .8. Preserve these scores, do not relabel as .9.
                label=unique_choice(scores,.65,.45)
                row={'space_id':space['space_id'],'zone_type':dedicated(lot,space['space_id']),
                    'polygon':polygon.round(2).tolist(),'model_observation':label or 'unknown',
                    'observation':label or 'unknown','scores':scores,'model_score':scores.get(label,0.),
                    'confidence':scores.get(label,0.),'confidence_method':'model_score_only',
                    'car_id':None,'number_candidate':None,'vehicle_type':None,'sticker_color':None,
                    'sendable':False,'empty_evidence_ok':False,'quality_reason':quality,
                    'reason':quality or ('모델 판단 보류' if label is None else '')}
                result['_crops'][space['space_id']]=crop
                intersecting=[d for d in weak if overlap_slot(d['bbox_xyxy'],polygon)>.10]
                assigned=[d for d in detections if d.get('space_id')==space['space_id']]
                if self.printed and not any(d.get('print_read') for d in assigned) and label=='occupied' and not quality:
                    for item in papers_in_bay(space['space_id']):
                        found=dict(item)
                        if assign_sticker(found['bbox_xyxy'],polygons)!=space['space_id']:continue
                        matching=next((d for d in assigned if same_box(found['bbox_xyxy'],d['bbox_xyxy'])
                                       and d['sticker_color']==found['sticker_color']),None)
                        if matching is not None and not matching.get('print_read'):
                            matching['print_read']=found['print_read']
                            continue
                        if len(assigned)==1 and corroborated_paper(self.printed,frame,assigned[0],found):
                            original=assigned.pop()
                            detections.remove(original)
                            result['candidates'].append(dict(kind='same_sticker_color_rechecked',
                                space_id=space['space_id'],bbox_xyxy=original['bbox_xyxy'],
                                detector_color=original['sticker_color'],paper_color=found['sticker_color']))
                        found.update(space_id=space['space_id'],lot_id=lot)
                        detections.append(found);assigned.append(found)
                    if not assigned and label=='occupied' and space['space_id'] in lit:
                        for found in self.printed.papers_beside_light(frame,polygon,lit[space['space_id']]):
                            if assign_sticker(found['bbox_xyxy'],polygons)==space['space_id']:
                                found.update(space_id=space['space_id'],lot_id=lot)
                                detections.append(found)
                                assigned.append(found)
                    if not assigned:
                        if rotated is None:
                            rotated,extra_ms=rotated_proposals(self.detector,frame)
                            result['detector_ms']+=extra_ms
                        for found in recover_rotated_print(frame,space['space_id'],polygons,rotated,weak,self.printed):
                            found['lot_id']=lot
                            detections.append(found);assigned.append(found)
                    if not assigned and observed_at:
                        # A recently measured box is only a search window. A
                        # wrong color, changed number or covered sheet must not
                        # inherit anything from the previous observation.
                        prior=previous_print.get((epoch,lot,space['space_id']))
                        if (prior and len(prior)>5 and 0<sequence-prior[1] and time.monotonic()-prior[2]<=3
                                and same_pose(prior[3],outline,frame.shape)):
                            box=prior[5]['bbox_xyxy']
                            if assign_sticker(box,polygons)==space['space_id']:
                                found=read_current_sticker(self.printed,frame,box,intersecting)
                                if found:
                                    found.update(lot_id=lot,space_id=space['space_id'])
                                    detections.append(found);assigned.append(found)
                    if assigned:result['weak_sticker_count']+=len(assigned)
                if self.printed and label=='occupied' and not quality:
                    match=decoration_match(frame,assigned,self.decoration_reference,
                        recheck_paper=lambda:papers_in_bay(space['space_id']))
                    if match:
                        extra,similarity=match
                        assigned.remove(extra)
                        detections.remove(extra)
                        result['candidates'].append(dict(kind='matched_body_decoration',space_id=space['space_id'],
                            bbox_xyxy=extra['bbox_xyxy'],detector_confidence=extra['confidence'],
                            reference_similarity=similarity))
                    if len(assigned)==1 and assigned[0].get('print_read'):
                        possible=self.printed.secondary_paper(frame,polygon,assigned[0],match[0]['bbox_xyxy'] if match else None)
                        extra_boxes=additional_proposals.get(space['space_id'],[])
                        if possible is None and extra_boxes:
                            # A weak second proposal can have the wrong Geti color.
                            # Require a separate paper contour with a matched print.
                            possible=next((dict(d) for d in papers_in_bay(space['space_id'])
                                           if any(same_box(d['bbox_xyxy'],b) for b in extra_boxes)
                                           and not same_box(d['bbox_xyxy'],assigned[0]['bbox_xyxy'])),None)
                        if possible and assign_sticker(possible['bbox_xyxy'],polygons)==space['space_id']:
                            possible.update(space_id=space['space_id'],lot_id=lot)
                            detections.append(possible)
                            assigned.append(possible)
                if not assigned and label=='occupied':
                    row['unconfirmed_sticker_candidates']=[
                        {k:d[k] for k in ('bbox_xyxy','confidence','sticker_color')}
                        for d in diagnostic if d['confidence']<threshold and assign_sticker(d['bbox_xyxy'],polygons)==space['space_id']]
                if len(assigned)==1 and label=='occupied' and not quality:
                    d=assigned[0];x1,y1,x2,y2=d['bbox_xyxy'];sticker=frame[y1:y2,x1:x2]
                    if sticker.size and min(sticker.shape[:2])>=12:
                        hit=d.get('print_read')
                        raw={} if hit else self.number_model.scores(sticker)
                        number=unique_choice({k:v for k,v in raw.items() if k.startswith('number_')},.55,.50)
                        candidate=number.removeprefix('number_') if number else None
                        color=unique_choice({k:v for k,v in raw.items() if k.startswith('color_')},.55,.50)
                        color_conflict=color is not None and color.removeprefix('color_')!=d['sticker_color']
                        kind=None if color_conflict else d['vehicle_type_from_sticker']
                        reading_crop,rectified=rectify_sticker(sticker,d['sticker_color'])
                        ocr=None
                        if hit:
                            key=(epoch,lot,space['space_id'])
                            prior=previous_print.get(key)
                            now=time.monotonic()
                            independent=1
                            if (prior and prior[0]==hit['number'] and now-prior[2]<=3
                                    and prior[5]['sticker_color']==d['sticker_color']
                                    and same_pose(prior[3],outline,frame.shape)):
                                if sequence>=prior[1]:independent=min(2,prior[4]+int(sequence>prior[1]))
                            locator={k:d[k] for k in ('bbox_xyxy','sticker_color','vehicle_type_from_sticker','label','color_name','bbox_target') if k in d}
                            current_print[key]=(hit['number'],sequence,now,outline,independent,locator)
                            ocr=dict(number=hit['number'],confidence=hit['score'],independent_reads=independent,
                                     reason='인쇄 숫자 형태·종이색 일치')
                        elif not color_conflict:
                            key=(camera,epoch,lot,space['space_id'],d['sticker_color'])
                            active_ocr.add(key)
                            ocr=self.numbers.query(key,reading_crop,
                                frame_id=f'{epoch}:{sequence}' if observed_at is not None else None)
                        # A job can finish between query() and has_pending(). The
                        # returned row still needs another pass in that case.
                        if ocr is None and self.numbers.worker and self.numbers.worker.is_alive():ocr_waiting=True
                        classifier_numbers={k.removeprefix('number_') for k in self.number_model.labels if k.startswith('number_')}
                        # A reliable contradictory number is a conflict, not a
                        # reason to retain the detector's possibly wrong type.
                        ocr_number=(ocr or {}).get('number');ocr_score=(ocr or {}).get('confidence')
                        if (ocr_number in CATALOG and valid_score(ocr_score) and ocr_score>=.8
                                and kind is not None and CATALOG[ocr_number]!=kind):
                            color_conflict=True;kind=None
                        car_id=confirmed_number(ocr,candidate,kind,model_classes=classifier_numbers) if not color_conflict else None
                        independent=(ocr or {}).get('independent_reads',0)
                        awaiting_recheck=observed_at is not None and car_id is not None and independent<2
                        if awaiting_recheck:car_id=None
                        row.update(number_candidate=candidate,car_id=car_id,vehicle_type=kind,
                            sticker_color=None if color_conflict else d['sticker_color'],number_scores=raw,
                            number_method='printed_digits' if hit else 'ocr',
                            number_reason=('다음 프레임의 인쇄 번호 재확인 대기' if hit and awaiting_recheck else
                                           '서로 다른 프레임의 OCR 재확인 대기' if awaiting_recheck
                                           else (ocr or {}).get('reason','OCR 대기 / 미설정')),
                            ocr_independent_reads=independent,sticker_bbox=d['bbox_xyxy'],ocr_rectified=rectified)
                        d.update(space_id=space['space_id'],lot_id=lot,car_id=car_id,number_candidate=candidate)
                        if color_conflict:row['reason']='색 모델 간 충돌 · 종류/번호 null'
                elif len(assigned)>1:
                    row.update(observation='unknown',reason='한 칸 복수 스티커')
                    if label=='occupied' and not quality:
                        row['display_occupancy']='occupied'
                    printed_candidates=sorted({d['print_read']['number'] for d in assigned if d.get('print_read')})
                    if printed_candidates:
                        row['reason']+=' · 인쇄 후보 '+', '.join(printed_candidates)
                        if len(printed_candidates)==1:row['number_candidate']=printed_candidates[0]
                if label=='occupied' and not quality and len(assigned)<=1:
                    if len(assigned)==1 and not assigned[0].get('localization_source') and assigned[0]['confidence']>=.85:
                        row.update(sendable=True,confidence=min(.90,assigned[0]['confidence'],registration['line_support']+.20),
                            confidence_method='review_heuristic: occupancy + sticker detector; not calibrated probability')
                    else:
                        row.update(sendable=False,reason=('점유 후보 · 스티커 미확인 · 번호·차종 보류'
                            if not assigned else '점유 후보 · 스티커 판독 재확인 대기'))
                        if (len(assigned)==1 and assigned[0].get('localization_source') in
                                ('detector_proposal_and_print','paper_contour_and_print','rotated_proposal_and_print',
                                 'current_print_at_reviewed_box','led_adjacent_paper_and_print')
                                and row['car_id'] and row['vehicle_type']):
                            row.update(sendable=True,reason='인쇄 번호·종이색·점유 근거 일치',
                                confidence=min(.90,registration['line_support']+.20),
                                confidence_method='review_heuristic: occupied + corroborated printed identity')
                elif label=='empty':
                    sid=space['space_id']
                    if any(light.get('space_id')==sid or (light.get('location')=='boundary'
                           and overlap_slot(light['bbox_xyxy'],polygon)>.02) for light in lights):
                        row.update(observation='unknown',reason='칸에 점등 후보 · 빈칸 확인 보류')
                    elif intersecting or assigned:
                        row.update(observation='unknown',reason='빈칸 모델과 스티커 검출 충돌')
                    elif baseline and empty_similarity(crop,baseline['crops'][space['space_id']]):
                        row.update(empty_evidence_ok=True,sendable=True,confidence=min(.90,registration['line_support']+.20),
                            confidence_method='review_heuristic: empty model + current verified empty reference; not calibrated probability')
                    elif (baseline is None and observed_at and any(
                            (lot,sid) in refs and matches_printed_floor(crop,refs[lot,sid])
                            for refs in (board_refs,lit_refs,local_floor))):
                        current_floor.add(sid)
                        if sid in stable_floor and approved:
                            row.update(empty_evidence_ok=True,sendable=True,
                                confidence=min(.90,registration['line_support']+.20),
                                confidence_method='review_heuristic: empty model + repeated visible printed floor',
                                reason='차가 비어 있음 · 칸 이름과 바닥 연속 확인')
                        elif sid in stable_floor:
                            row.update(observation='unknown',display_occupancy='empty',
                                       reason='칸 이름·바닥 연속 일치 · 위치 승인 전')
                        else:
                            row.update(observation='unknown',reason='빈칸 바닥 확인 · 다음 프레임 대기')
                    else:
                        reason='빈 기준 미등록' if baseline is None and not result['empty_board_reference'] else '빈 기준과 다름 · 가림/조명/물체 확인'
                        row.update(observation='unknown',reason=reason)
                if not approved:row['sendable']=False
                if row['sendable'] and row['confidence']<.8:
                    row.update(sendable=False,reason='전송 점수 0.80 미만 · 도면 선 근거 확인')
                result['spaces'].append(row)
            if self.printed:self.print_history[camera]=current_print
            self.numbers.retain_keys(camera, active_ocr)
            for d in detections:
                if not d.get('space_id'):
                    overlaps=[r['space_id'] for r in result['spaces'] if overlap_slot(d['bbox_xyxy'],r['polygon'])>.1]
                    result['candidates'].append({'kind':'sticker_multiple_spaces' if len(overlaps)>1 else 'sticker_outside_or_boundary',
                        'bbox_xyxy':d['bbox_xyxy'],'spaces':overlaps,'scope':'sticker only; not a confirmed vehicle violation'})
                    for row in result['spaces']:
                        if row['space_id'] in overlaps:
                            current_floor.discard(row['space_id'])
                            row.pop('display_occupancy',None)
                            if row['observation']=='empty':
                                row.update(observation='unknown',empty_evidence_ok=False)
                            row.update(sendable=False,reason='스티커 경계 후보 · 칸 배정 보류')
            if observed_at and current_floor:
                history_floor[camera]=((epoch,lot),sequence,floor_time,outline,current_floor)
            # Resolve sticker/slot boundary holds before counting any fire evidence.
            led_cars=getattr(self,'mock_fire_cars',frozenset())
            watch_filtered=[]
            if self.fire_watch:
                for row,space in zip(result['spaces'], self.registry.boards[lot]['spaces']):
                    sid=row['space_id']
                    poly=polygons[sid]
                    quad=poly[[1,2,3,0]] if space.get('crop_rotate') else poly
                    transform=cv2.getPerspectiveTransform(quad,DEST)
                    sticker=row.get('sticker_bbox')
                    region=None
                    if sticker:
                        x,y,u,v=sticker
                        points=cv2.perspectiveTransform(np.float32([[[x,y],[u,y],[u,v],[x,v]]]),transform)[0]
                        lo,hi=points.min(0),points.max(0)
                        region=(*tuple(lo-(hi-lo)),*tuple(hi+(hi-lo)))
                    fire=self.fire_watch.observe((camera,epoch,lot,sid), result['_crops'][sid],
                        eligible=bool(approved and sticker and row['sendable'] and row['observation']=='occupied'),
                        sequence=sequence, observed_at=observed_at, roof_region=region)
                    if fire['state']=='confirmed' or fire.get('method')=='lens_brightening':
                        box=project_box(fire.pop('crop_box'), transform, frame.shape)
                        if assign_sticker(box,polygons)==sid and overlap_slot(box,poly)>.95:
                            fire['bbox_xyxy']=box
                        else:
                            fire={'state':'unverified'}
                    if fire.get('bbox_xyxy') and not_led_car(dict(location='space'),row,led_cars):
                        watch_filtered.append(dict(reason='not_led_car',space_id=sid,location='space',
                            bbox_xyxy=fire['bbox_xyxy'],confidence=round(float(fire.get('confidence',0)),3),bay_area_fraction=0.))
                        fire={'state':'unverified'}
                    row['fire_led']=fire
            rows_by_id={s['space_id']:s for s in result['spaces']}
            lights[:],result['filtered_lights']=split_filtered_lights(lights,rows_by_id,led_cars)
            result['filtered_lights']+=watch_filtered
            if led_cars and observed_at and approved:
                glow_scope=(camera,epoch,lot)
                streaks=self.__dict__.setdefault('led_glow_streaks',{})
                for key in [k for k in streaks if k[:3]!=glow_scope and k[0]==camera]:streaks.pop(key)
                extra=led_car_glow_lights(frame,result['spaces'],polygons,lights,
                    self.__dict__.setdefault('led_identity',{}),streaks,glow_scope,time.monotonic(),led_cars,
                    self.__dict__.setdefault('led_seen',{}))
                if extra:
                    glowing={l['space_id'] for l in extra}
                    lights[:]=[l for l in lights if l.get('space_id') not in glowing]+extra
            self._log_filtered_lights(camera,result['filtered_lights'])
            for light in lights:
                if light['space_id']:
                    row=next(s for s in result['spaces'] if s['space_id']==light['space_id'])
                    row['fire_led']=dict(light)
                    if row['observation']=='empty':
                        row.update(sendable=False,reason='칸에 점등 확인 · 빈칸 전송 보류')
            for row in result['spaces']:
                fire = row.get('fire_led')
                if fire and (fire['state']=='confirmed' or fire.get('method') in ('red_emission','lens_brightening')):
                    fire['on_vehicle'] = on_vehicle_roof(row, fire.get('bbox_xyxy'))
            result['_duplicate_ids'] = tuple(sorted(duplicate_identities({camera: result})))
            for row in result['spaces']:
                if row['car_id'] in result['_duplicate_ids']:row.update(car_id=None,number_reason='장면 내 번호 중복')
            for d in detections:
                if d.get('space_id'):
                    row=next(r for r in result['spaces'] if r['space_id']==d['space_id'])
                    d['car_id']=row['car_id']
            if approved:result['note']='칸별 관측 중 · 입출차 확정은 수신 서버에서 처리합니다.'
            if clipped_spaces:
                result['note'] += f" 화면에 일부만 보이는 {', '.join(clipped_spaces)} 관측 보류."
        for row in result['spaces']:
            row['parking_rule_status']=parking_rule_status(row,result['roi_confirmed'])
        spaces=result['spaces']
        if result['roi_confirmed'] and spaces:
            empty=sum(s['observation']=='empty' and s['sendable'] and s['empty_evidence_ok'] for s in spaces)
            occupied=sum(s['observation']=='occupied' and s['sendable'] for s in spaces)
            result['occupancy_summary']=dict(empty=empty,occupied=occupied,unconfirmed=len(spaces)-occupied-empty,
                all_empty=empty==len(spaces))
        result['ocr_pending']=ocr_waiting or self.numbers.has_pending(camera)
        result['annotated_image']=annotate(frame,result)
        result['total_ms']=round((time.perf_counter()-started)*1000,2)
        return result

    def close(self):
        if self.glow_worker:
            self.glow_worker.shutdown(wait=True)
        self.numbers.close()


def public_result(result):
    return {k:v for k,v in result.items() if not k.startswith('_')}


@lru_cache(maxsize=8)
def annotation_font(size):
    try:return ImageFont.truetype('malgun.ttf',size)
    except OSError:return ImageFont.load_default(size=size)


def annotate(frame,result):
    photo=Image.fromarray(cv2.cvtColor(frame,cv2.COLOR_BGR2RGB));draw=ImageDraw.Draw(photo)
    size=max(16,round(frame.shape[1]/85))
    font=annotation_font(size)
    for row in result['spaces']:
        color='#f59e0b' if not row['sendable'] else '#ef4444' if row['observation']=='occupied' else '#10b981'
        points=[tuple(p) for p in row['polygon']];draw.line(points+[points[0]],fill=color,width=3)
        x,y=points[0];x=max(0,min(photo.width-100,x));y=max(0,min(photo.height-35,y))
        text=row['space_id'];draw.rectangle((x,y,x+92,y+size+8),fill='#152238');draw.text((x+3,y+2),text,font=font,fill='white')
    for d in result['detections']:
        x1,y1,x2,y2=d['bbox_xyxy'];color={'white':'#64748b','green':'#16a34a','blue':'#2563eb'}[d['sticker_color']]
        draw.rectangle((x1,y1,x2,y2),outline=color,width=4)
        # Cross-camera identity conflicts are resolved after this image is drawn.
        # Keep the authoritative number in the synchronized table, not this image.
        score=d.get('confidence')
        evidence=f'{score:.2f}' if score is not None else '추가 종이 후보'
        text=f"{d['color_name']} {d.get('space_id','칸 보류')} · 탐지 {evidence}"
        x=min(x1,max(0,photo.width-250));y=max(0,y1-size-10)
        draw.rectangle((x,y,x+250,y+size+8),fill=color);draw.text((x+3,y+2),text,font=font,fill='white')
    for row in result['spaces']:
        led=row.get('fire_led',{})
        if led.get('method')=='lens_brightening' or (led.get('state')=='confirmed' and led.get('method')!='red_emission'):
            x,y,u,v=led['bbox_xyxy']
            draw.rectangle((x-2,y-2,u+2,v+2),outline='#ff2828',width=3)
            draw.text((x,max(0,y-size-12)),f"모의 화재 · {result['lot_id']} / {row['space_id']}",font=font,
                      fill='white',stroke_width=2,stroke_fill='#ab1515')
    for light in result.get('fire_lights',[]):
        if light.get('state')!='confirmed' and not (light.get('state')=='candidate' and light.get('method')=='red_emission'):
            continue
        x,y,u,v=light['bbox_xyxy']
        draw.rectangle((x,y,u,v),outline='#ff2828',width=3)
        location=light.get('space_id') or {'aisle':'통로','boundary':'칸 경계','outside':'판 밖',
                                           'unregistered':'위치 미확인'}.get(light.get('location'),'')
        # An unconfirmed candidate is never sent; do not label it as a fire on screen.
        word='모의 화재' if light.get('state')=='confirmed' else 'LED 확인 중'
        draw.text((x,max(0,y-size-12)),f"{word} · {location}",font=font,fill='white',stroke_width=2,stroke_fill='#ab1515')
    photo.thumbnail((1280,960));output=io.BytesIO();photo.save(output,format='JPEG',quality=85)
    return 'data:image/jpeg;base64,'+base64.b64encode(output.getvalue()).decode('ascii')
