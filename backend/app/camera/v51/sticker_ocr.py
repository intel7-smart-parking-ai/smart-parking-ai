"""Read a localized sticker; preserve original coordinates and disagreeing evidence."""
import math
import cv2
import numpy as np
from analysis import number,validate_sticker_polygon,color_candidate

MIN_NUMBER_STICKER_OVERLAP = 0.60
# Distinct legal two-digit labels that common round 6, 8 and 9 glyphs can
# turn into after 180 degrees. The order of the two digits reverses as well.
HALF_TURN_OTHER={'66':'99','99':'66','68':'89','89':'68','86':'98','98':'86'}


def overlap_fraction(box,polygon):
    x1,y1,x2,y2=box
    rect=np.asarray([[x1,y1],[x2,y1],[x2,y2],[x1,y2]],np.float32)
    overlap,_=cv2.intersectConvexConvex(rect,np.asarray(polygon,np.float32))
    return float(overlap/max((x2-x1)*(y2-y1),1.))


def rectified_views(image,polygon,rectangular=False):
    points=validate_sticker_polygon(polygon,image.shape[1::-1]).astype(np.float32)
    center=points.mean(axis=0)
    points=points[np.argsort(np.arctan2(points[:,1]-center[1],points[:,0]-center[0]))]
    points=np.roll(points,-int(np.argmin(points.sum(axis=1))),axis=0)
    width=max(np.linalg.norm(points[1]-points[0]),np.linalg.norm(points[2]-points[3]))
    height=max(np.linalg.norm(points[3]-points[0]),np.linalg.norm(points[2]-points[1]))
    scale=min(4.,max(1.,160./max(height,1.)),1024./max(width,height,1.))
    w=max(12,int(round(width*scale)));h=max(12,int(round(height*scale)));pad=20
    destination=np.float32([[pad,pad],[pad+w-1,pad],[pad+w-1,pad+h-1],[pad,pad+h-1]])
    transform=cv2.getPerspectiveTransform(points,destination)
    color=cv2.warpPerspective(image,transform,(w+pad*2,h+pad*2),flags=cv2.INTER_CUBIC,borderValue=(255,255,255))
    # Only the sticker enters OCR. Mask all neighboring scene pixels to white.
    interior=np.zeros(color.shape[:2],np.uint8);cv2.fillConvexPoly(interior,destination.astype(np.int32),255)
    color[interior==0]=255
    hsv=cv2.cvtColor(color,cv2.COLOR_BGR2HSV);hue,sat,value=cv2.split(hsv)
    name=color_candidate(image,polygon)['name']
    masks={'white':(sat<65)&(value>155),'green':(hue>=40)&(hue<90)&(sat>65)&(value>55),
           'blue':(hue>=90)&(hue<=135)&(sat>65)&(value>55)}
    if name in masks and not rectangular:
        colored=(masks[name]&(interior>0)).astype(np.uint8)*255
        contours,_=cv2.findContours(colored,cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)
        if contours:
            contour=max(contours,key=cv2.contourArea)
            if cv2.contourArea(contour)>.45*w*h:
                silhouette=np.zeros_like(interior);cv2.drawContours(silhouette,[contour],-1,255,-1)
                # Fill printed-digit holes, but remove dark car corners around a circular sticker.
                silhouette=cv2.erode(silhouette,np.ones((3,3),np.uint8))
                color[silhouette==0]=255
    value=cv2.cvtColor(color,cv2.COLOR_BGR2HSV)[:,:,2]
    # Normalize the sticker background itself, excluding the added white margin.
    level=float(np.percentile(value[interior>0],70))
    normalized=np.clip(value.astype(np.float32)*255/max(level,1),0,255).astype(np.uint8)
    _,binary=cv2.threshold(value,0,255,cv2.THRESH_BINARY+cv2.THRESH_OTSU)
    return [('sticker_color',color),('sticker_value_normalized',normalized),('sticker_value_binary',binary)],np.linalg.inv(transform)


def read_sticker(image,polygon,reader,cfg,rectangular=False):
    views,inverse=rectified_views(image,polygon,rectangular=rectangular);candidates=[];decisions=[]
    for method,pixels in views:
        decision=None;h,w=pixels.shape[:2]
        # EasyOCR's automatic rotation chooses the highest score even if it is
        # an invalid one-digit reading. Validate each orientation before choosing.
        for rotation in range(4):
            current=[]
            for points,text,score in reader.readtext(np.ascontiguousarray(np.rot90(pixels,rotation)),detail=1,
                    allowlist='0123456789',rotation_info=None,mag_ratio=1.,paragraph=False):
                points=np.asarray(points,np.float32)
                if points.shape!=(4,2) or not np.isfinite(points).all():continue
                if not isinstance(text,str) or not isinstance(score,(int,float,np.floating)) or not math.isfinite(float(score)) or not 0<=float(score)<=1:continue
                x=points[:,0].copy();y=points[:,1].copy()
                if rotation==1:points[:,0]=w-1-y;points[:,1]=x
                elif rotation==2:points[:,0]=w-1-x;points[:,1]=h-1-y
                elif rotation==3:points[:,0]=y;points[:,1]=h-1-x
                original=cv2.perspectiveTransform(points[None],inverse)[0]
                x1,y1=np.floor(original.min(axis=0)).astype(int);x2,y2=np.ceil(original.max(axis=0)).astype(int)
                x1=max(0,int(x1));y1=max(0,int(y1));x2=min(image.shape[1],int(x2));y2=min(image.shape[0],int(y2))
                if x2<=x1 or y2<=y1:continue
                box=[x1,y1,x2,y2];normalized=number(text,cfg)
                status='candidate' if normalized is not None and score>=cfg['ocr_min_score'] else 'invalid_number' if normalized is None else 'low_score'
                if overlap_fraction(box,polygon)<.60:status='outside_sticker'
                entry={'text':text,'number_candidate':normalized,'score':float(score),'text_bbox':box,
                       'status':status,'method':method,'rotation_degrees':rotation*90,'selected_in_view':False}
                current.append(entry);candidates.append(entry)
            # Multiple pieces must not be silently concatenated or guessed into a car number.
            if len(current)>1:continue
            if len(current)==1 and current[0]['status']=='candidate':
                if decision is None:
                    decision=current[0];decision['selected_in_view']=True
                if decision['number_candidate'] not in HALF_TURN_OTHER:break
        decisions.append(decision)
    selected=None;status='unread'
    valid=[c for c in decisions if c is not None]
    values={c['number_candidate'] for c in valid}
    if not values.isdisjoint(HALF_TURN_OTHER):
        values.update(c['number_candidate'] for c in candidates if c['status']=='candidate')
    if len(values)>1:status='conflicting_views'
    elif len(valid)>=2:
        selected=max(valid,key=lambda c:c['score']);status='candidate'
    elif valid:status='single_view_unconfirmed'
    elif candidates:status='ambiguous'
    if not selected and status!='conflicting_views' and cfg.get('number_mode')=='physical_sticker':
        from sticker_digits import recognize_digits,METHODS
        extra=recognize_digits(image,polygon,reader,cfg)
        candidates.extend(extra)
        good=[e for e in extra if e['status']=='candidate']
        values={e['number_candidate'] for e in valid+good}
        if len(values)>1:
            status='conflicting_views'
        elif good:
            for angle in {e['rotation_degrees'] for e in good}:
                same=[e for e in good if e['rotation_degrees']==angle]
                if {e['method'] for e in same}==set(METHODS):
                    for e in same:e['selected_in_view']=True
                    selected=max(same,key=lambda e:e['score'])
                    status='candidate'
                    break
    return {'number_candidate':selected['number_candidate'] if selected else None,'selected':selected,
            'status':status,'candidates':candidates,'method':'sticker_multi_view_agreement'}
