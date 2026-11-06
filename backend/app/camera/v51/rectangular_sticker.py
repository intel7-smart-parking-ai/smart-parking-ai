"""Localize rectangular paper labels and read only their original pixels."""
import cv2
import numpy as np


def proposals(image,polygon,ignored=()):
    from static_markings import overlaps_static
    x,y,w,h=cv2.boundingRect(np.int32(polygon))
    crop=image[y:y+h,x:x+w]
    hue,sat,val=cv2.split(cv2.cvtColor(crop,cv2.COLOR_BGR2HSV))
    masks=((sat<65)&(val>140),(hue>=35)&(hue<89)&(sat>=13)&(val>55),
           (hue>=90)&(hue<135)&(sat>=50)&(val>50),
           (hue>=90)&(hue<135)&(sat>=50)&(sat<205)&(val>120))
    found=[];bay=np.float32(polygon);bay_area=cv2.contourArea(bay)
    for raw in masks:
        mask=cv2.morphologyEx(raw.astype(np.uint8)*255,cv2.MORPH_CLOSE,np.ones((3,3),np.uint8))
        for contour in cv2.findContours(mask,cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)[0]:
            area=cv2.contourArea(contour)
            if not max(65,bay_area*.004)<area<bay_area*.38:continue
            rect=cv2.minAreaRect(contour);rw,rh=rect[1]
            if min(rw,rh)<14 or max(rw,rh)/min(rw,rh)>1.85 or area/(rw*rh)<.80:continue
            corners=cv2.approxPolyDP(contour,.045*cv2.arcLength(contour,True),True)
            if len(corners)!=4 or not cv2.isContourConvex(corners) or cv2.contourArea(corners)<.84*area:continue
            points=cv2.boxPoints(rect)+[x,y]
            center=points.mean(axis=0)
            points=points[np.argsort(np.arctan2(points[:,1]-center[1],points[:,0]-center[0]))]
            points=np.rint(center+(points-center)*.97).astype(np.int32)
            if any(cv2.pointPolygonTest(bay,tuple(map(float,p)),False)<0 for p in points) or overlaps_static(points,ignored):continue
            target=np.float32([[0,0],[95,0],[95,95],[0,95]])
            patch=cv2.warpPerspective(image,cv2.getPerspectiveTransform(points.astype(np.float32),target),(96,96))
            border=np.ones((96,96),bool);border[19:77,19:77]=False
            border[:7]=False;border[-7:]=False;border[:,:7]=False;border[:,-7:]=False
            ph,ps,pv=np.moveaxis(cv2.cvtColor(patch,cv2.COLOR_BGR2HSV)[border],1,0)
            votes={'green':np.mean((ph>=35)&(ph<89)&(ps>=13)&(pv>55)),
                   'blue':np.mean((ph>=90)&(ph<135)&(ps>=50)&(pv>50)),
                   'white':np.mean((ps<55)&(pv>137))}
            color=max(('green','blue'),key=votes.get)
            if votes[color]<.68:color='white'
            if votes[color]<.68:continue
            gray=cv2.cvtColor(patch,cv2.COLOR_BGR2GRAY)
            background=float(np.median(gray[border]));ink=gray[19:77,19:77]<background-35
            if not .045<float(ink.mean())<.65:continue
            _,_,parts,_=cv2.connectedComponentsWithStats(ink.astype(np.uint8))
            printed=[part for part in parts[1:] if part[4]>=20 and part[3]>=10]
            if not 1<=len(printed)<=4:continue
            candidate={'polygon':points.tolist(),'color':color,'shape':'rectangle',
                       'center':center.tolist(),'radius':min(rw,rh)/2}
            if any(cv2.intersectConvexConvex(points.astype(np.float32),np.float32(other['polygon']))[0]>
                   .75*min(cv2.contourArea(points),cv2.contourArea(np.float32(other['polygon']))) for other in found):continue
            found.append(candidate)
    return found


def identify(image,slot,reader,cfg,ignored=()):
    candidates=proposals(image,slot['polygon'],ignored)
    if not candidates:return None
    if len(candidates)>1:
        return None,{'status':'multiple_sticker_candidates','selected':None,'candidates':[],
                     'reason':'한 칸에 사각형 번호 라벨 후보가 여러 개 있습니다. 원본을 확인하세요.'}
    label=candidates[0]
    if not callable(getattr(reader,'recognize',None)):
        return label,{'status':'sticker_number_unread','selected':None,'candidates':[]}
    read=read_rectangle(image,label['polygon'],reader,cfg)
    if read['status']!='candidate':
        support={}
        for entry in read['candidates']:
            if entry['status']=='candidate':
                key=(entry['number_candidate'],entry['rotation_degrees'])
                support.setdefault(key,set()).add(entry['method'])
        if not any(len(methods)>=2 for methods in support.values()):return None
    return label,read


def read_rectangle(image,polygon,reader,cfg):
    from analysis import number
    from sticker_ocr import rectified_views,overlap_fraction
    views,inverse=rectified_views(image,polygon,rectangular=True)
    h,w=views[0][1].shape[:2]
    left=20+max(1,round((w-40)*.06));top=20+max(1,round((h-40)*.06))
    right=w-left;bottom=h-top
    gray=cv2.cvtColor(views[0][1][top:bottom,left:right],cv2.COLOR_BGR2GRAY)
    _,ink=cv2.threshold(cv2.GaussianBlur(gray,(3,3),0),0,255,cv2.THRESH_BINARY_INV+cv2.THRESH_OTSU)
    _,labels,parts,_=cv2.connectedComponentsWithStats(ink)
    keep=[i for i,part in enumerate(parts[1:],1) if part[4]>=max(8,ink.size*.004)]
    ys,xs=np.nonzero(np.isin(labels,keep))
    if not len(xs):return {'status':'sticker_number_unread','selected':None,'candidates':[]}
    ink_points=np.stack((xs+left,ys+top),axis=-1).astype(np.float32)
    original=cv2.perspectiveTransform(ink_points[None],inverse)[0]
    lower=np.maximum(0,np.floor(original.min(axis=0))).astype(int)
    upper=np.minimum(image.shape[1::-1],np.ceil(original.max(axis=0))+1).astype(int)
    box=lower.tolist()+upper.tolist()
    evidence=[];threshold=max(.90,cfg['ocr_min_score'])
    for method,pixels in views:
        if pixels.ndim==3:pixels=cv2.cvtColor(pixels,cv2.COLOR_BGR2GRAY)
        core=cv2.copyMakeBorder(pixels[top:bottom,left:right],20,20,20,20,cv2.BORDER_CONSTANT,value=255)
        for turn in range(4):
            oriented=np.ascontiguousarray(np.rot90(core,turn));height,width=oriented.shape
            hits=reader.recognize(oriented,horizontal_list=[[0,width,0,height]],free_list=[],
                                  allowlist='0123456789',detail=1,paragraph=False)
            if not isinstance(hits,(list,tuple)) or len(hits)!=1:continue
            _,text,score=hits[0]
            if not isinstance(text,str) or not isinstance(score,(int,float,np.floating)) or not np.isfinite(score) or not 0<=score<=1:continue
            value=number(text,cfg)
            status='invalid_number' if value is None else 'low_score' if score<threshold else 'candidate'
            if overlap_fraction(box,polygon)<.60:status='outside_sticker'
            evidence.append({'text':text,'number_candidate':value,'score':float(score),'text_bbox':box,
                             'status':status,'method':method,'rotation_degrees':turn*90,'selected_in_view':False})
    good=[e for e in evidence if e['status']=='candidate']
    values={e['number_candidate'] for e in good}
    if len(values)==1:
        for angle in (0,90,180,270):
            agreeing=[e for e in good if e['rotation_degrees']==angle]
            if len(agreeing)<2:continue
            for e in agreeing:e['selected_in_view']=True
            selected=max(agreeing,key=lambda e:e['score'])
            return {'status':'candidate','number_candidate':selected['number_candidate'],'selected':selected,
                    'candidates':evidence,'method':'rectangular_sticker_multi_view'}
    reason='사각형 라벨의 서로 다른 번호·방향 근거 충돌' if len(values)>1 else '사각형 라벨 번호 합의 부족'
    if len(values)<=1 and callable(getattr(reader,'readtext',None)):
        from sticker_ocr import read_sticker
        detected=read_sticker(image,polygon,reader,cfg,rectangular=True)
        if detected.get('selected') and values.issubset({detected['number_candidate']}):
            return {**detected,'candidates':evidence+detected['candidates']}
    return {'status':'sticker_number_unread','selected':None,'candidates':evidence,
            'reason':reason+' · 원본 숫자와 방향을 확인하세요.'}
