"""Original-coordinate OCR candidates; no occupancy or ground-truth claims."""
import math
import re
import cv2
import numpy as np


def number(text, config):
    # Never repair "1" to "01"; preserve the original OCR text separately.
    if not isinstance(text,str): return None
    value=text.strip()
    mode=config.get('number_mode','physical_sticker')
    if mode=='physical_sticker':
        if not re.fullmatch(r'[0-9]{2}',value) or not 10<=int(value)<=99: return None
    elif mode=='virtual_plate':
        plate=re.fullmatch(r'[0-9]{2,3}[가-힣]([0-9]{4})',value)
        if plate: value=plate.group(1)
        elif not re.fullmatch(r'[0-9]{4}',value): return None
    else: raise ValueError('Unknown number mode')
    allowed=config.get('allowed_numbers')
    return value if allowed is None or value in allowed else None


def validate_sticker_polygon(polygon,size):
    points=np.asarray(polygon)
    w,h=size
    if (points.shape!=(4,2) or not np.issubdtype(points.dtype,np.integer)
            or (points<0).any() or (points[:,0]>=w).any() or (points[:,1]>=h).any()):
        raise ValueError('스티커 네 모서리는 원본 안의 정수 좌표여야 합니다.')
    points=points.astype(np.int32)
    if not cv2.isContourConvex(points) or cv2.contourArea(points)<=10:
        raise ValueError('스티커 모서리를 테두리 순서로 지정하세요.')
    return points


def color_candidate(image, sticker_polygon=None):
    """Only samples an explicitly supplied per-image sticker interior mask."""
    result={'name':'unknown','ratios':{},'method':'sticker_interior_hsv_v1',
            'status':'sticker_region_required','polygon':None,'sample_pixels':0}
    if sticker_polygon is None: return result
    points=validate_sticker_polygon(sticker_polygon,image.shape[1::-1])
    # A quadrilateral mask (not its bounding rectangle) excludes a slanted floor.
    # HSV and masks only need the sticker bounding area. One real-image pixel
    # of padding preserves the full-frame erosion behaviour at polygon edges.
    x,y,w,h=cv2.boundingRect(points)
    x0=max(0,x-1);y0=max(0,y-1);x1=min(image.shape[1],x+w+1);y1=min(image.shape[0],y+h+1)
    crop=image[y0:y1,x0:x1]
    mask=np.zeros(crop.shape[:2],np.uint8)
    cv2.fillConvexPoly(mask,points-[x0,y0],255)
    mask=cv2.erode(mask,np.ones((3,3),np.uint8),iterations=1)
    hsv=cv2.cvtColor(crop,cv2.COLOR_BGR2HSV)
    inside=mask>0;interior_count=int(inside.sum())
    hue,sat,val=cv2.split(hsv)
    usable=inside & (val>=55)  # dark digits/shadows excluded, never exterior pixels
    count=int(usable.sum())
    coverage=count/max(1,interior_count)
    result.update(polygon=points.tolist(),sample_pixels=count,usable_coverage=coverage,status='insufficient_pixels')
    if count<20 or coverage<.30: return result
    masks={'white':(sat<55)&(val>=160),
           'blue':(hue>=90)&(hue<=135)&(sat>=70)&(val>=60),
           'green':(hue>=40)&(hue<90)&(sat>=70)&(val>=60)}
    ratios={key:float((value&usable).sum()/count) for key,value in masks.items()}
    ordered=sorted(ratios,key=ratios.get,reverse=True);best=ordered[0]
    if ratios[best]<.55 or ratios[best]-ratios[ordered[1]]<.15: best='unknown'
    result.update(name=best,ratios=ratios,status='candidate' if best!='unknown' else 'ambiguous')
    return result


def analyze(image, spaces, reader, config):
    outputs=[]
    for space in spaces:
        polygon=np.array(space['polygon'],np.int32)
        x,y,w,h=cv2.boundingRect(polygon)
        crop=image[y:y+h,x:x+w].copy()
        mask=np.zeros((h,w),np.uint8)
        cv2.fillPoly(mask,[polygon-[x,y]],255)
        crop[mask==0]=255
        candidates=[]
        # EasyOCR reports coordinates in this input image even with rotation_info.
        for points,text,score in reader.readtext(crop,detail=1,allowlist='0123456789' if config.get('number_mode','physical_sticker')=='physical_sticker' else None,
                rotation_info=[90,180,270],mag_ratio=1.5,paragraph=False):
            points=np.asarray(points,dtype=float)
            score=float(score)
            if points.shape!=(4,2) or not np.isfinite(points).all() or not math.isfinite(score): continue
            a,b=np.floor(points.min(axis=0)).astype(int)
            c,d=np.ceil(points.max(axis=0)).astype(int)
            a,b,c,d=max(0,int(a)),max(0,int(b)),min(w,int(c)),min(h,int(d))
            if c<=a or d<=b: continue
            if np.mean(mask[b:d,a:c]>0)<.75: continue
            box=[x+a,y+b,x+c,y+d]
            normalized=number(text,config)
            reason='candidate'
            if normalized is None: reason='invalid_number'
            elif score<config['ocr_min_score']: reason='low_score'
            elif normalized.lstrip('0') in ('6','9'): reason='rotation_ambiguous'
            elif a<=1 or b<=1 or c>=w-1 or d>=h-1: reason='clipped_text'
            color=color_candidate(image)  # OCR text boxes do not prove sticker boundaries.
            candidates.append({'text':str(text),'number_candidate':normalized,'score':score,'text_bbox':box,
                'status':reason,'color_candidate':color,'vehicle_type_candidate':config['color_to_type'].get(color['name'])})
        good=[v for v in candidates if v['status']=='candidate']
        chosen=good[0] if len(candidates)==1 and len(good)==1 else None
        outputs.append({'space_type':space.get('space_type','general'),'space_id':space['space_id'],'occupancy':'unknown','review_status':'pending',
            'number_candidate':chosen['number_candidate'] if chosen else None,
            'color_candidate':chosen['color_candidate']['name'] if chosen else 'unknown',
            'color_status':'sticker_region_required',
            'vehicle_type_candidate':chosen['vehicle_type_candidate'] if chosen else None,
            'status':'candidate' if chosen else 'ambiguous' if candidates else 'no_text',
            'candidates':candidates})
    nums=[r['number_candidate'] for r in outputs if r['number_candidate'] is not None]
    for row in outputs:
        if row['number_candidate'] is not None and nums.count(row['number_candidate'])>1:
            row.update(number_candidate=None,status='duplicate_number')
    return outputs


def make_reader(project, config):
    from intel_ocr import build_reader
    return build_reader(project,config)
