"""Locate round labels inside one registered bay. All color samples come from the disc."""
import cv2
import numpy as np
from sticker_ocr import HALF_TURN_OTHER


def proposals(image, polygon, ignored=()):
    from static_markings import overlaps_static
    x,y,w,h=cv2.boundingRect(np.int32(polygon))
    scale=min(3.,440/max(w,h))
    bgr=cv2.resize(image[y:y+h,x:x+w],None,fx=scale,fy=scale,
                   interpolation=cv2.INTER_AREA if scale<1 else cv2.INTER_CUBIC)
    hsv=cv2.cvtColor(bgr,cv2.COLOR_BGR2HSV)
    hue,sat,val=cv2.split(hsv)
    gray=cv2.cvtColor(bgr,cv2.COLOR_BGR2GRAY)
    short=min(bgr.shape[:2])
    bay_width=min(cv2.minAreaRect(np.float32(polygon))[1])*scale
    low=max(5,int(bay_width*.08)); high=max(low+2,int(short*.23))
    # Both color blobs and intensity edges can describe a sticker with washed-out ink.
    circles=[]
    for m in ((sat<53)&(val>140),(hue>=36)&(hue<89)&(sat>13)&(val>55),
              (hue>=90)&(hue<135)&(sat>50)&(val>50)):
        mask=cv2.morphologyEx(m.astype('uint8')*255,cv2.MORPH_CLOSE,np.ones((3,3),'uint8'))
        for c in cv2.findContours(mask,cv2.RETR_LIST,cv2.CHAIN_APPROX_SIMPLE)[0]:
            area=cv2.contourArea(c)
            if len(c)<5 or not 2.1*low**2<area<3.8*high**2:continue
            (cx,cy),(a,b),ang=cv2.fitEllipse(c)
            if min(a,b)<1 or min(a,b)/max(a,b)<.55 or not .65<area/(np.pi*a*b/4)<1.20:continue
            circles.append((cx,cy,a/2,b/2,ang))
    raw=cv2.HoughCircles(cv2.GaussianBlur(gray,(5,5),1),cv2.HOUGH_GRADIENT,1,max(5,low),
                         param1=90,param2=18,minRadius=low,maxRadius=high)
    if raw is not None:
        circles.extend((float(a),float(b),float(r),float(r),0.) for a,b,r in raw[0])
    # Pale green paper on silver paint can have very little luminance contrast.
    blue,green,red=cv2.split(bgr.astype('float32'))
    chroma=np.clip(128+4*(green-(red+blue)*.5),0,255).astype('uint8')
    chroma_circles=cv2.HoughCircles(cv2.GaussianBlur(chroma,(5,5),1),cv2.HOUGH_GRADIENT,1,max(5,low),
                                   param1=60,param2=17,minRadius=low,maxRadius=high)
    if chroma_circles is not None:
        circles.extend((float(a),float(b),float(r),float(r),0.) for a,b,r in chroma_circles[0])
    # Canonical disc, rim and background use separate samples. Reject uniform paint,
    # straight body panels, and highlights lacking two-tone printed detail.
    axis=np.linspace(-1.35,1.35,55,dtype='float32')
    u,v=np.meshgrid(axis,axis); radial=np.hypot(u,v);theta=np.arctan2(v,u)
    ring=(radial>.70)&(radial<.88); outer=(radial>1.09)&(radial<1.28); center=radial<.64
    sectors=np.floor((theta+np.pi)*(8/(2*np.pi))).astype(int)%8
    choices=[]
    poly=np.float32(polygon)
    for cx,cy,ra,rb,angle in circles:
        if min(ra,rb)<low*.70 or max(ra,rb)>high*1.3:continue
        rad=np.deg2rad(angle);cs,sn=np.cos(rad),np.sin(rad)
        mx=(cx+ra*cs*u-rb*sn*v).astype('float32'); my=(cy+ra*sn*u+rb*cs*v).astype('float32')
        if mx.min()<0 or my.min()<0 or mx.max()>=bgr.shape[1] or my.max()>=bgr.shape[0]:continue
        px,py=cx/scale+x,cy/scale+y
        if cv2.pointPolygonTest(poly,(px,py),True)<min(ra,rb)*.72/scale:continue
        patch=cv2.remap(bgr,mx,my,cv2.INTER_LINEAR)
        sample=cv2.cvtColor(patch,cv2.COLOR_BGR2HSV)
        rh,rs,rv=np.moveaxis(sample[ring],1,0)
        # Green may be pale under a ceiling light. Consistent hue across the
        # inner rim separates pale green ink stock from white paper.
        votes={'green':np.mean((rh>=35)&(rh<89)&(rs>=13)&(rv>55)),
               'blue':np.mean((rh>=90)&(rh<135)&(rs>=50)&(rv>50))}
        votes['white']=np.mean((rs<55)&(rv>137))
        color=max(('green','blue'),key=votes.get)
        if votes[color]<.59:color='white'
        if votes[color]<.59:continue
        change=[]
        for sector in range(8):
            inside=np.median(patch[ring&(sectors==sector)],axis=0)
            outside=np.median(patch[outer&(sectors==sector)],axis=0)
            change.append(float(np.max(np.abs(inside-outside))))
        if np.count_nonzero(np.array(change)>13)<5:continue
        light=cv2.cvtColor(patch,cv2.COLOR_BGR2GRAY)
        background=np.median(light[ring])
        if color=='white' and np.percentile(light[ring],15)<.68*background:continue
        contrast=background-np.percentile(light[center],10)
        if contrast<12 or np.mean(light[center]<background-max(10.,contrast*.5))>.63:continue
        # An inscribed square excludes the car roof at the four corners.
        q=cv2.boxPoints(((px,py),(float(ra*1.40/scale),float(rb*1.40/scale)),float(angle)))
        q=np.rint(q).astype(int)
        if any(cv2.pointPolygonTest(poly,(float(i),float(j)),False)<0 for i,j in q) or overlaps_static(q,ignored):continue
        score=votes[color]+np.mean(np.minimum(change,60))/75.+min(contrast,70)/160.
        if min(ra,rb)<bay_width*.075:continue
        choices.append((score,{'center':[px,py],'radius':min(ra,rb)/scale,'color':color,'polygon':q.tolist(),
                               'disc':(cx/scale+x,cy/scale+y,ra/scale,rb/scale,angle)}))
    best=[]
    counts={}
    for _,candidate in sorted(choices,key=lambda item:item[0],reverse=True):
        if counts.get(candidate['color'],0)>=4:continue
        if any(candidate['color']==c['color'] and np.linalg.norm(np.subtract(candidate['center'],c['center']))<.65*max(candidate['radius'],c['radius']) for c in best):continue
        best.append(candidate)
        counts[candidate['color']]=counts.get(candidate['color'],0)+1
    return best


def recover_exposure(raw, alternate, slot, cfg, ignored):
    """Recover a candidate only when two exposure representations locate the same print."""
    first, raw_read = identify(raw, slot, None, cfg, ignored)
    estimate = raw_read.get('estimate')
    if not first or not estimate:
        return None
    x,y,w,h=cv2.boundingRect(np.int32(slot['polygon']))
    if np.array_equal(raw[y:y+h,x:x+w],alternate[y:y+h,x:x+w]):
        return None
    second, second_read=identify(alternate,slot,None,cfg,ignored)
    other=second_read.get('estimate')
    if (not second or not other or estimate['number']!=other['number'] or first['color']!=second['color']
            or np.linalg.norm(np.subtract(first['center'],second['center']))>.5*min(first['radius'],second['radius'])):
        return None
    raw_read['estimate']={**estimate,'exposure_agreement':'raw_and_partial_correction'}
    return first,raw_read


def identify_guide(image,slot,reader,cfg,ignored=None):
    """Resolve unstable disc sizes without treating two different prints as one."""
    x,y,w,h=cv2.boundingRect(np.int32(slot['polygon']))
    low,high,_,_=cv2.minMaxLoc(cv2.cvtColor(image[y:y+h,x:x+w],cv2.COLOR_BGR2GRAY))
    if high-low<7:return None,{'status':'round_sticker_unread','candidates':[]}
    initial=identify(image,slot,reader,cfg,ignored)
    disc,read=initial
    def value(result):
        return result.get('number_candidate') or (result.get('estimate') or {}).get('number')
    rotation_sensitive=value(read) in HALF_TURN_OTHER
    if not rotation_sensitive and not (read['status']=='round_sticker_unread' and read.get('guide_ink')):
        return initial
    points=np.asarray(slot['polygon'],float);center=points.mean(axis=0)
    verified=[]
    evidence=list(read.get('candidates',[]))
    for scale in (.85,.70):
        inner={**slot,'polygon':np.rint(center+(points-center)*scale).astype(int).tolist()}
        d,r=identify(image,inner,reader,cfg,ignored)
        if r.get('status') in ('multiple_stickers','multiple_sticker_candidates'):
            combined=evidence+[e for e in r.get('candidates',[]) if e not in evidence]
            return None,{**r,'selected':None,'candidates':combined}
        if not d:break
        prior=disc
        if prior is None and verified:prior=verified[0][0]
        if prior and (prior['color']!=d['color'] or np.linalg.norm(np.subtract(prior['center'],d['center']))>.5*min(prior['radius'],d['radius'])):break
        evidence.extend(e for e in r.get('candidates',[]) if e not in evidence)
        if not value(r):
            if r.get('status')=='sticker_number_unread' and r.get('reason'):
                return d,{**r,'selected':None,'candidates':evidence}
            break
        if rotation_sensitive and (r.get('number_candidate')!=value(read)):break
        if verified and value(verified[0][1])!=value(r):
            return d,{'status':'sticker_number_unread','selected':None,'candidates':evidence,
                      'reason':'가상선 내부의 같은 스티커에서 서로 다른 번호가 읽혔습니다. 원본을 확인하세요.'}
        verified.append((d,r))
    if len(verified)==2:
        if rotation_sensitive:return initial
        if value(verified[0][1]) in HALF_TURN_OTHER and not all(r.get('number_candidate') for d,r in verified):
            return verified[0][0],{'status':'sticker_number_unread','selected':None,'candidates':evidence,
                                    'reason':'가상선 내부 재판독에서 숫자 방향을 확인하지 못했습니다.'}
        return next((item for item in verified if item[1].get('estimate')),verified[0])
    if rotation_sensitive:return disc,{'status':'sticker_number_unread','selected':None,'candidates':evidence,
                                       'reason':'가상선 내부 재판독에서 숫자 방향을 확인하지 못했습니다.'}
    return initial


def competing_number_evidence(read, selected):
    """Collect corroborated counterevidence before accepting the detector's number."""
    value=selected['number_candidate']
    detector_views={'sticker_color','sticker_value_normalized','sticker_value_binary'}
    if read['number']!=value and selected.get('method') in detector_views:
        opposite=(selected['rotation_degrees']+180)%360
        # Weak observations can veto a confirmation only when all three image
        # representations agree with the shape at the opposite angle.
        matching={e['method'] for e in read['ocr_evidence'] if e.get('method') in detector_views
                  and e.get('status') in ('candidate','low_score') and e.get('number_candidate')==read['number']
                  and e.get('rotation_degrees')==opposite}
        if matching==detector_views:return True
    ink=[e for e in read['ocr_evidence'] if e.get('method') in ('ink_pair_contrast','ink_pair_binary')]
    # For a non-reversible label, detector, shape and ink can all support the
    # same answer even when the deliberately inverted view reads something else.
    if value==read['number'] and any(e['number_candidate']==value for e in ink):return False
    methods_by_reading={}
    for entry in ink:
        method=entry['method']
        other=entry.get('number_candidate')
        if not other or other==value:continue
        if other==read['number']:return True
        methods=methods_by_reading.setdefault((other,entry['rotation_degrees']),set())
        methods.add(method)
        if len(methods)==2:return True
    return False


def identify(image,slot,reader,cfg,ignored=None):
    from rectangular_sticker import identify as identify_rectangle
    rectangular=identify_rectangle(image,slot,reader,cfg,ignored)
    if rectangular is not None:return rectangular
    from print_estimator import examine
    from sticker_reference import compare
    from sticker_ocr import overlap_fraction
    from analysis import number
    candidates=[]
    for disc in proposals(image,slot['polygon'],ignored):
        if disc['radius']<5.8:continue
        read=examine(image,disc)
        if read and read['score']>=.70 and overlap_fraction(read['box'],disc['polygon'])>=.60:
            candidates.append((disc,read))
    if slot.get('boundary_kind')=='virtual_open_u':
        # A glossy windshield can form a second, larger colored ring around
        # the same digits. Attribute the print to the inner paper disc only.
        candidates=[(outer,read) for outer,read in candidates if not any(
            inner['radius']<.70*outer['radius'] and inner['color']!=outer['color']
            and ink['score']>=.85 and ink['number']==read['number']
            and np.linalg.norm(np.subtract(outer['center'],inner['center']))+inner['radius']<=1.08*outer['radius']
            and overlap_fraction(read['box'],inner['polygon'])>=.90
            and overlap_fraction(ink['box'],inner['polygon'])>=.90
            for inner,ink in candidates)]
    candidates.sort(key=lambda item:item[1]['score'],reverse=True)
    direct=[]
    for disc,read in candidates[:3]:
        if read['score']<.755 or not callable(getattr(reader,'recognize',None)):continue
        evidence=[]
        for method,pixels in zip(('ink_pair_contrast','ink_pair_binary'),read['views']):
            for turn in (0,180):
                frame=pixels if not turn else cv2.rotate(pixels,cv2.ROTATE_180)
                h,w=frame.shape
                hits=reader.recognize(frame,horizontal_list=[[0,w,0,h]],free_list=[],allowlist='0123456789',detail=1,paragraph=False)
                if not isinstance(hits,(list,tuple)) or len(hits)!=1:continue
                _,text,score=hits[0]
                value=number(text,cfg)
                if value and np.isfinite(score) and max(.78,cfg['ocr_min_score'])<=score<=1:
                    evidence.append({'method':method,'text':text,'score':float(score),'text_bbox':read['box'],
                                     'number_candidate':value,'rotation_degrees':(read['angle']+turn)%360,
                                     'status':'candidate','selected_in_view':False})
        for angle in {e['rotation_degrees'] for e in evidence}:
            hits=[e for e in evidence if e['rotation_degrees']==angle]
            if len(hits)!=2 or len({e['number_candidate'] for e in hits})!=1:continue
            val=hits[0]['number_candidate']
            # A confident current-image decision needs agreement on shape as well as OCR.
            if read['number']!=val or any(e['number_candidate']!=val for e in evidence):continue
            if val in HALF_TURN_OTHER and HALF_TURN_OTHER[val]==read.get('alternate') and read.get('margin',0)<.022:continue
            for e in hits:e['selected_in_view']=True
            direct.append((disc,{'status':'candidate','number_candidate':val,'selected':max(hits,key=lambda e:e['score']),
                               'candidates':hits,'method':'round_sticker_ink_ocr'}))
            break
        read['ocr_evidence']=evidence
    def opposite_numbers(read):
        values={e.get('number_candidate') for e in read.get('ocr_evidence',[]) if e.get('status')=='candidate'}
        return any(value in values and other in values for value,other in HALF_TURN_OTHER.items())

    def multiple_stickers():
        evidence=[e for _,read in candidates[:3] for e in read.get('ocr_evidence',[])]
        return None,{'status':'multiple_stickers','selected':None,'candidates':evidence,
                     'reason':'같은 칸에서 여러 스티커가 검출됐습니다. 원본의 위치와 번호를 확인하세요.'}

    orientation_reason='180도 회전 시 다른 번호와 방향 근거 충돌 · 방향 표시를 확인하고 재촬영하세요.'
    ambiguous=[(disc,read,orientation_reason) for disc,read in candidates[:3] if opposite_numbers(read)]
    if len(direct)+len(ambiguous)>1:return multiple_stickers()
    # At full camera resolution the original sticker often has enough pixels
    # for EasyOCR's own detector to read the number independently of font style.
    from sticker_ocr import read_sticker
    for disc,read in candidates[:3]:
        if any(found is disc for found,_ in direct) or opposite_numbers(read):continue
        if disc['radius']<13 or read['score']<.74 or not callable(getattr(reader,'readtext',None)):continue
        primary=read_sticker(image,disc['polygon'],reader,cfg)
        read.setdefault('ocr_evidence',[]).extend(primary.get('candidates',[]))
        if opposite_numbers(read):
            ambiguous.append((disc,read,orientation_reason))
            continue
        if primary.get('status')=='conflicting_views':
            ambiguous.append((disc,read,'같은 스티커의 번호 판독이 서로 다릅니다. 원본 숫자와 방향을 확인하고 재촬영하세요.'))
            continue
        if primary.get('selected'):
            value=primary['selected']['number_candidate']
            if HALF_TURN_OTHER.get(value)==read['number'] and read['alternate']==value:
                # Both are legal stickers and the opposite direction has direct
                # image support. Preserve the disc and its color until reviewed.
                ambiguous.append((disc,read,orientation_reason))
                continue
            # The same upside-down reading can win every detector preprocessing.
            # An independent reading of the ink that agrees with the shape is counterevidence.
            if competing_number_evidence(read,primary['selected']):
                ambiguous.append((disc,read,'번호 판독 근거 충돌 · 원본 숫자의 방향을 확인하고 재촬영하세요.'))
                continue
            direct.append((disc,primary))
        else:
            read['ocr_support']=any(e.get('selected_in_view') and e['number_candidate']==read['number']
                                    and e['score']>=.85 for e in primary.get('candidates',[]))
    if len(direct)+len(ambiguous)>1:return multiple_stickers()
    if direct:return direct[0]
    if ambiguous:
        disc,read,reason=ambiguous[0]
        return disc,{'status':'sticker_number_unread','selected':None,'candidates':read['ocr_evidence'],
                     'reason':reason}
    # Only unresolved live-image reads reach the six-photo fallback. Other digits
    # retain the open-set (10..99) estimate instead of being forced to one of six.
    estimates=[]
    for disc,read in candidates:
        match=compare(image,disc)
        # One supporting OCR view can corroborate a strong open-set shape for
        # review. Conflicting views still cannot produce a confirmed number.
        corroborated=read.get('ocr_support') and read['score']>=.90 and read['margin']>=.01
        if corroborated and (not match or read['score']>match['score']+.05):
            source=read;method='open_print'
        elif match and match['score']>=.75 and match['margin']>=.022 and match['score']>=read['score']-.105:
            source=match;method='reference_print'
        elif read['score']>=.85 and read['margin']>=.022:
            source=read;method='open_print'
        else:continue
        estimates.append((source['score'],disc,{'number':source['number'],'method':method,
                           'score':source['score'],'margin':source['margin'],'alternate':source['alternate'],
                           'text_bbox':read['box']}))
    estimates.sort(key=lambda item:item[0],reverse=True)
    if not estimates:
        # A clear physical disc can retain its independently sampled paper color
        # even when its digits have two equally plausible orientations.
        clear=[disc for disc,read in candidates if disc['radius']>=13 and read['score']>=.87]
        if len(clear)==1:return clear[0],{'status':'sticker_number_unread','candidates':[]}
        return None,{'status':'round_sticker_unread','candidates':[],'guide_ink':bool(candidates)}
    if len(estimates)>1 and estimates[0][0]-estimates[1][0]<.055:
        return None,{'status':'multiple_sticker_candidates','candidates':[]}
    _,disc,estimate=estimates[0]
    shape=next(read for d,read in candidates if d is disc)
    evidence=shape.get('ocr_evidence',[])
    # Physical and virtual captures must use the same unresolved-direction rule.
    if HALF_TURN_OTHER.get(estimate['number'])==estimate['alternate']:
        return disc,{'status':'sticker_number_unread','selected':None,'candidates':evidence,
                     'reason':orientation_reason}
    return disc,{'status':'estimated','selected':None,'candidates':evidence,'estimate':estimate,'method':estimate['method']}
