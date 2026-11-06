"""Frame-bound inference. Predictions are never training ground truth."""
import copy
import hashlib
import json
import time
import cv2
import numpy as np
from analysis import analyze, color_candidate
from layouts import layout, COLOR_TYPES, zone_rule
from contracts import FIELD_SET,number_config_for_lot,sticker_colors


def context_key(roi):
    fields = {k:roi[k] for k in ('lot_id','camera_id','serial','image_size','pose','spaces')}
    return hashlib.sha256(json.dumps(fields,sort_keys=True).encode()).hexdigest()


def crop_space(image, polygon, masked=True):
    points=np.asarray(polygon,np.int32)
    x,y,w,h=cv2.boundingRect(points)
    crop=image[y:y+h,x:x+w].copy()
    mask=np.zeros((h,w),np.uint8)
    cv2.fillPoly(mask,[points-[x,y]],255)
    if masked: crop[mask==0]=255
    return crop, mask, (x,y)


def make_reference(packet, roi):
    return {'image':packet.image.copy(),'key':context_key(roi),'epoch':packet.stream_epoch,
            'timestamp':packet.timestamp_utc,'frame':packet.sequence}


def reference_difference(image, roi, reference, epoch, threshold=35, aligned_image=None):
    if reference is None: return None,'빈 기준 사진 미등록'
    if reference['key']!=context_key(roi) or reference['epoch']!=epoch:
        return None,'구도/ROI/연결 변경: 빈 기준 재등록 필요'
    base=reference['image']
    if base.shape!=image.shape: return None,'기준 해상도 불일치'
    if float(image.mean())<8: return None,'검은 영상: 판단 보류'
    if aligned_image is not None:
        image=aligned_image
    elif reference.get('empty_board') and roi.get('registration_method')=='fixed_printed_board_v1':
        from board_profile import match_aisle_light
        image,_=match_aisle_light(image,roi,base)
    current=cv2.GaussianBlur(image,(5,5),0)
    original=cv2.GaussianBlur(base,(5,5),0)
    if reference.get('printed_board_warp'):
        # Resampling a printed edge may shift it by a few pixels in either direction.
        radius=max(1,min(3,round(max(image.shape[:2])*.0015)))
        kernel=np.ones((2*radius+1,2*radius+1),np.uint8)
        diff=np.maximum(cv2.subtract(current,cv2.dilate(original,kernel)),
                        cv2.subtract(cv2.erode(original,kernel),current))
    else:diff=cv2.absdiff(current,original)
    mask=(np.max(diff,axis=2)>threshold).astype(np.uint8)*255
    mask=cv2.morphologyEx(mask,cv2.MORPH_OPEN,np.ones((3,3),np.uint8))
    if reference.get('printed_board_warp'):
        # Outside the registered board camera parallax can look like a vehicle.
        hull=cv2.convexHull(np.int32([pt for s in roi['spaces'] for pt in s['polygon']]))
        area=np.zeros(mask.shape,np.uint8)
        cv2.fillConvexPoly(area,hull,255)
        cv2.polylines(area,[hull],True,255,max(7,round(max(mask.shape)*.012)))
        mask=cv2.bitwise_and(mask,area)
        changed=cv2.countNonZero(mask)/cv2.countNonZero(area)
    else:changed=np.mean(mask>0)
    if changed>.65:return None,'화면 대부분 변화: 조명/가림/구도 확인'
    return mask,None


def geometry_candidates(mask, spaces):
    """Foreground changes are candidates, not vehicle segmentation or violations."""
    if mask is None: return [],{}
    contours,_=cv2.findContours(mask,cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)
    minimum_area=max(80,mask.size*.0003)
    contours=[c for c in contours if cv2.contourArea(c)>=minimum_area]
    if not contours:return [],{}
    slots = []
    union = np.zeros_like(mask)
    for slot in spaces:
        polygon = np.asarray(slot['polygon'], np.int32)
        x, y, width, height = cv2.boundingRect(polygon)
        right, bottom = min(mask.shape[1], x+width), min(mask.shape[0], y+height)
        x, y = max(0,x), max(0,y)
        if x >= right or y >= bottom:
            continue
        local = np.zeros((bottom-y, right-x), np.uint8)
        cv2.fillPoly(local, [polygon], 255, offset=(-x, -y))
        union[y:bottom, x:right] |= local
        slots.append((slot, x, y, right, bottom, local))
    open_board=bool(slots) and all(s.get('painted_edges') and s.get('entrance_edge') for s,*_ in slots)
    warnings=[];by_slot={}
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        change = np.zeros((h, w), np.uint8)
        cv2.drawContours(change, [contour], -1, 255, -1, offset=(-x, -y))
        pixels = cv2.countNonZero(change)
        touched = []
        for slot, sx, sy, right, bottom, local in slots:
            x0, y0 = max(x, sx), max(y, sy)
            x1, y1 = min(x+w, right), min(y+h, bottom)
            if x0 >= x1 or y0 >= y1:
                continue
            overlap = local[y0-sy:y1-sy, x0-sx:x1-sx] & change[y0-y:y1-y, x0-x:x1-x]
            # A small crossing into a neighbouring physical bay still counts
            # when the change actually reaches one of its three painted edges.
            area=cv2.countNonZero(overlap)
            if area<=max(30,pixels*(.06 if open_board else .12)):
                continue
            if area<=pixels*.12 and not touches_lines(change,x,y,slot['painted_edges']):
                continue
            touched.append(slot['space_id'])
        inside = cv2.countNonZero(change & union[y:y+h, x:x+w])
        outside = (pixels - inside) / max(pixels, 1)
        reason='여러 칸 걸침 변화 후보' if len(touched)>1 else '칸 경계/외부 변화 후보' if outside>.2 else None
        if open_board and len(touched)<=1 and reason:
            if not touched:continue
            slot=next(s for s,*_ in slots if s['space_id']==touched[0])
            if touches_lines(change,x,y,slot['painted_edges']):
                reason='주차선 침범 변화 후보'
            elif touches_lines(change,x,y,[slot['entrance_edge']]):
                reason='주차칸 입구 돌출 변화 후보'
        if reason:
            warnings.append({'reason':reason,'spaces':touched,'bbox':[x,y,x+w,y+h],
                             'source':'background_change_not_vehicle_mask'})
            for sid in touched:by_slot.setdefault(sid,[]).append(reason)
    return warnings,by_slot


def touches_lines(change,x,y,edges):
    lines=np.zeros_like(change)
    for a,b in edges:
        cv2.line(lines,(a[0]-x,a[1]-y),(b[0]-x,b[1]-y),255,1)
    return cv2.countNonZero(lines & change)>=3


def sticker_region(image, polygon, text_box, ignored_regions=None):
    """Conservative color-region proposal around OCR; unknown on ambiguity."""
    crop,slotmask,(ox,oy)=crop_space(image,polygon,False)
    hsv=cv2.cvtColor(crop,cv2.COLOR_BGR2HSV);h,s,v=cv2.split(hsv)
    masks=[((s<65)&(v>155)),((h>=40)&(h<90)&(s>65)&(v>55)),((h>=90)&(h<=135)&(s>65)&(v>55))]
    tx1,ty1,tx2,ty2=text_box
    center=((tx1+tx2)/2-ox,(ty1+ty2)/2-oy)
    candidates=[];text_area=max(1,(tx2-tx1)*(ty2-ty1))
    for raw in masks:
        m=(raw&(slotmask>0)).astype(np.uint8)*255
        m=cv2.morphologyEx(m,cv2.MORPH_CLOSE,np.ones((5,5),np.uint8))
        contours,_=cv2.findContours(m,cv2.RETR_LIST,cv2.CHAIN_APPROX_SIMPLE)
        for c in contours:
            area=cv2.contourArea(c)
            if not text_area*1.05<area<min(text_area*12,slotmask.size*.55):continue
            rect=cv2.minAreaRect(c);quad=cv2.boxPoints(rect)
            if cv2.pointPolygonTest(quad,center,False)<0:continue
            bx,by,bw,bh=cv2.boundingRect(quad)
            if not (bx<=tx1-ox and by<=ty1-oy and bx+bw>=tx2-ox and by+bh>=ty2-oy):continue
            quad=np.rint(quad+[ox,oy]).astype(int)
            if (quad<0).any() or (quad[:,0]>=image.shape[1]).any() or (quad[:,1]>=image.shape[0]).any():continue
            from static_markings import overlaps_static
            if overlaps_static(quad,ignored_regions):continue
            try: color=color_candidate(image,quad.tolist())
            except ValueError:continue
            if color['name']!='unknown':candidates.append((area,quad.tolist(),color))
    if not candidates:return None,'unknown'
    candidates.sort(key=lambda c:c[0])
    if len({c[2]['name'] for c in candidates})>1:return None,'unknown'
    return candidates[0][1],candidates[0][2]['name']


def independent_sticker(image,polygon,ignored_regions=None):
    """Conservative compact color+print proposal (round or rectangular stickers).

    This is a heuristic proposal, not a trained sticker detector or approved label.
    Multiple plausible regions remain unknown.
    """
    crop,slotmask,(ox,oy)=crop_space(image,polygon,False)
    hsv=cv2.cvtColor(crop,cv2.COLOR_BGR2HSV);h,s,v=cv2.split(hsv)
    masks=[(s<60)&(v>155),(h>=40)&(h<90)&(s>35)&(v>55),(h>=90)&(h<=135)&(s>65)&(v>55)]
    candidates=[];slot_area=int((slotmask>0).sum())
    for index,raw in enumerate(masks):
        mask=(raw&(slotmask>0)).astype(np.uint8)*255
        if index==2:
            mask=cv2.morphologyEx(mask,cv2.MORPH_OPEN,cv2.getStructuringElement(cv2.MORPH_ELLIPSE,(3,3)))
        mask=cv2.morphologyEx(mask,cv2.MORPH_CLOSE,np.ones((5,5),np.uint8))
        contours,_=cv2.findContours(mask,cv2.RETR_LIST,cv2.CHAIN_APPROX_SIMPLE)
        for contour in contours:
            area=cv2.contourArea(contour);rect=cv2.minAreaRect(contour);rw,rh=rect[1]
            if min(rw,rh)<10 or not max(150,slot_area*.002)<area<slot_area*.25:continue
            if max(rw,rh)/min(rw,rh)>3 or area/max(rw*rh,1)<.65:continue
            quad=np.rint(cv2.boxPoints(rect)).astype(np.int32)
            if any(cv2.pointPolygonTest(np.asarray(polygon,np.float32),(float(x+ox),float(y+oy)),False)<0 for x,y in quad):continue
            local=np.zeros_like(slotmask);cv2.fillPoly(local,[quad],255)
            local=cv2.erode(local,np.ones((3,3),np.uint8));inside=local>0
            if not inside.any():continue
            background=float(np.percentile(v[inside],75))
            dark=float(np.mean(v[inside]<min(150,background*.72)))
            if not .015<dark<.45:continue  # require printed dark marks, not a uniform car/floor patch
            absolute=(quad+[ox,oy]).tolist()
            from static_markings import overlaps_static
            if overlaps_static(absolute,ignored_regions):continue
            try:measured=color_candidate(image,absolute)
            except ValueError:continue
            if measured['name']!='unknown':candidates.append((absolute,measured['name']))
    # The same sticker can appear both as a colored foreground contour and
    # as a hole in a bright background mask. Deduplicate geometry, not colors alone.
    unique=[]
    for polygon,color in candidates:
        points=np.asarray(polygon,np.float32);area=cv2.contourArea(points);duplicate=False
        for other,other_color in unique:
            if color!=other_color:continue
            other_points=np.asarray(other,np.float32);intersection,_=cv2.intersectConvexConvex(points,other_points)
            union=area+cv2.contourArea(other_points)-intersection
            if intersection/max(union,1)>.75:duplicate=True;break
        if not duplicate:unique.append((polygon,color))
    if len(unique)!=1:return None,'unknown'
    return unique[0]


def validate_model_settings(settings):
    """Reject invalid thresholds/preprocessing before model loading or UI commit."""
    try:
        if not isinstance(settings,dict) or not isinstance(settings['model'],str) or not settings['model'].strip():raise ValueError('model 경로가 필요합니다.')
        if not isinstance(settings['labels'],list) or len(settings['labels'])!=2 or set(settings['labels'])!={'occupied','vacant'}:raise ValueError('labels는 occupied/vacant 두 개입니다.')
        if settings['layout'] not in ('NCHW','NHWC') or settings['color'] not in ('RGB','BGR'):raise ValueError('입력 layout/color 확인 필요')
        size=settings['size']
        if not isinstance(size,(list,tuple)) or len(size)!=2 or any(type(v) is not int or v<=0 for v in size):raise ValueError('모델 size는 양의 정수 너비/높이입니다.')
        scale=settings['scale'];threshold=settings.get('threshold',.85)
        if type(scale) not in (int,float) or not np.isfinite(scale) or scale==0:raise ValueError('모델 scale은 0이 아닌 유한한 숫자입니다.')
        if type(threshold) not in (int,float) or not np.isfinite(threshold) or not 0<=threshold<=1:raise ValueError('모델 threshold는 0~1의 유한한 숫자입니다.')
        for key in ('mean','std'):
            values=settings[key] if isinstance(settings[key],(list,tuple)) else [settings[key]]
            if len(values) not in (1,3) or any(type(v) not in (int,float) or not np.isfinite(v) or (key=='std' and v<=0) for v in values):raise ValueError('모델 '+key+'는 유한한 숫자 1개 또는 3개이며 std는 양수입니다.')
        if settings['output'] not in ('logits','probabilities'):raise ValueError('모델 output은 logits/probabilities입니다.')
        if not isinstance(settings.get('device','CPU'),str) or not settings.get('device','CPU').strip():raise ValueError('모델 device가 필요합니다.')
    except (KeyError,TypeError) as exc:raise ValueError('점유 모델 설정 필수 항목/형식 오류: '+str(exc)) from exc
    return settings


class Classifier:
    """Optional OpenVINO two-class adapter, configured with explicit preprocessing."""
    def __init__(self):self.cache=None;self.key=None
    def infer(self,image,settings):
        validate_model_settings(settings)
        from pathlib import Path
        model_path=Path(settings['model'])
        companions=[model_path]+([model_path.with_suffix('.bin')] if model_path.suffix.lower()=='.xml' else [])
        fingerprint=[(str(p.resolve()),p.stat().st_mtime_ns,p.stat().st_size) for p in companions]
        key=json.dumps([settings,fingerprint],sort_keys=True)
        if key!=self.key:
            from openvino import Core
            model=Path(settings['model'])
            if not model.is_file():raise ValueError('점유 모델 파일 없음')
            if set(settings['labels'])!={'occupied','vacant'} or len(settings['labels'])!=2:raise ValueError('labels는 occupied/vacant 두 개입니다.')
            if settings['layout'] not in ('NCHW','NHWC') or settings['color'] not in ('RGB','BGR'):raise ValueError('입력 layout/color 확인 필요')
            self.cache=Core().compile_model(str(model),settings.get('device','CPU'));self.key=key
        w,h=settings['size'];data=cv2.resize(image,(w,h)).astype(np.float32)
        if settings['color']=='RGB':data=data[:,:,::-1]
        data=data*float(settings['scale']);data=(data-np.asarray(settings['mean'],np.float32))/np.asarray(settings['std'],np.float32)
        if not np.isfinite(data).all():raise ValueError('전처리 결과 오류')
        if settings['layout']=='NCHW':data=data.transpose(2,0,1)
        raw=np.asarray(self.cache([data[None]])[self.cache.output(0)]).reshape(-1)
        if len(raw)!=2 or not np.isfinite(raw).all():raise ValueError('2분류 출력 형식 오류')
        if settings['output']=='logits':
            raw=np.exp(raw-raw.max());raw=raw/raw.sum()
        elif settings['output']!='probabilities' or (raw<0).any() or (raw>1).any() or not np.isclose(raw.sum(),1,atol=.02):raise ValueError('출력 점수 의미 확인 필요')
        index=int(raw.argmax());score=float(raw[index])
        return (settings['labels'][index] if score>=settings.get('threshold',.85) else 'unknown'),score


class VisionEngine:
    def __init__(self):self.reader=None;self.ocr_error=None;self.classifier=Classifier()
    def run(self,packet,roi,cfg,reference=None,mode='reference'):
        from records import validate_capture
        validate_capture(packet,roi)
        started=time.perf_counter();image=packet.image
        if roi.get('registration_method')=='virtual_sticker_guide_v1' and (reference is not None or mode not in ('sticker','manual')):
            raise ValueError('가상 안내선에서는 스티커 판독만 지원합니다.')
        from static_markings import unchanged_regions
        static_regions=unchanged_regions(image,roi)
        confirmed_pair = mode == 'reference' and reference is not None and 'empty_board' in reference
        field_board=cfg.get('physical_sticker_set')==FIELD_SET and roi['lot_id'] in ('A','B')
        correction=None;adjusted=None
        if field_board:
            from board_profile import match_aisle_light
            baseline=reference['image'] if confirmed_pair and reference['image'].shape==image.shape else None
            adjusted,correction=match_aisle_light(image,roi,baseline)
        diff,ref_error=reference_difference(image,roi,reference,packet.stream_epoch,cfg.get('difference_threshold',35),
                                            adjusted if confirmed_pair else None)
        if confirmed_pair and ref_error:
            raise ValueError(ref_error+' · 빈 기준과 현재 구도를 확인하세요.')
        new_edges=None
        geometry_diff=diff
        light_holds=set()
        if confirmed_pair and reference.get('printed_board_warp'):
            def edges(pixels):
                gray=cv2.cvtColor(cv2.GaussianBlur(pixels,(5,5),0),cv2.COLOR_BGR2GRAY)
                return cv2.Canny(gray,30,65)
            kernel=np.ones((7,7),np.uint8)
            missing_from_empty=cv2.bitwise_not(cv2.dilate(edges(reference['image']),kernel))
            current_edges=edges(adjusted if adjusted is not None else image)
            new_edges=cv2.bitwise_and(current_edges,missing_from_empty,mask=cv2.dilate(diff,kernel))
            geometry_diff=diff.copy()
        from quality import image_quality
        occupancy={};errors=[];model_error=None
        for slot in roi['spaces']:
            sid=slot['space_id'];crop,mask,origin=crop_space(image,slot['polygon'])
            state='unknown';score=None;reason=None
            if mode=='reference':
                reason=ref_error
                if diff is not None:
                    x,y=origin;d=diff[y:y+mask.shape[0],x:x+mask.shape[1]]
                    score=float(np.mean(d[mask>0]>0))
                    state='vacant' if score<cfg.get('empty_ratio',.025) else 'occupied' if score>cfg.get('occupied_ratio',.12) else 'unknown'
                    reason='빈 기준 변화 비율: 차량 여부 사람 확인' if state!='unknown' else '변화량 애매: 보류'
                    if confirmed_pair and state=='vacant' and score>0:
                        # A small, coherent change may be a distant vehicle. An
                        # average over the entire bay can conceal it.
                        _,_,stats,_=cv2.connectedComponentsWithStats(cv2.bitwise_and(d,mask))
                        if any(area>=25 for area in stats[1:,cv2.CC_STAT_AREA]):
                            state='unknown'
                            reason='빈 기준과 국소 변화 · 작은 물체 또는 가림 확인 필요'
                    if new_edges is not None and state!='vacant':
                        interior=cv2.erode(mask,kernel,borderType=cv2.BORDER_CONSTANT,borderValue=0)
                        count=cv2.countNonZero(cv2.bitwise_and(new_edges[y:y+mask.shape[0],x:x+mask.shape[1]],interior))
                        if count<max(12,np.sqrt(cv2.countNonZero(mask))*.12):
                            state='unknown';reason='밝기 변화와 물체 구분 보류 · 새 윤곽 부족'
                            light_holds.add(sid)
                            cv2.fillConvexPoly(geometry_diff,np.int32(slot['polygon']),0)
            elif mode=='classifier':
                settings=cfg.get('occupancy_model')
                if not settings:reason='B안 학습 모델 미등록'
                elif float(image.mean())<8:reason='검은 영상'
                elif model_error:reason=model_error
                else:
                    try:state,score=self.classifier.infer(crop,settings);reason='모델 분류 · 가림은 검수 필요'
                    except Exception as exc:reason='모델 오류: '+str(exc);model_error=reason
            elif mode=='sticker':reason='점유 모델/빈 기준 없음: 스티커 근거가 없으면 보류'
            else:reason='점유 자동 분석 끔'
            occupancy[sid]=(state,score,reason)
        warnings,by_slot=geometry_candidates(geometry_diff,roi['spaces'])
        rows=[]
        for slot in roi['spaces']:
            sid=slot['space_id'];state,score,reason=occupancy[sid]
            unmasked,_,_=crop_space(image,slot['polygon'],False)
            quality=image_quality(unmasked)
            text=None;box=None;poly=None;color='unknown'
            if state!='vacant' and cfg.get('physical_sticker_set')!=FIELD_SET:
                poly,color=independent_sticker(image,slot['polygon'],static_regions.get(sid))
            if mode=='sticker' and poly:
                state='occupied';reason='인쇄 흔적이 있는 스티커 검출 기반 점유 후보 · 차량 여부 확인 필요'
            rule=zone_rule(slot['space_type'],COLOR_TYPES.get(color)) if state=='occupied' and slot.get('boundary_kind')!='virtual_open_u' else 'unknown'
            issues=list(by_slot.get(sid,[]))
            if rule=='not_allowed':issues.append('전용칸 종류 불일치 의심')
            if sid in light_holds:issues.append(reason)
            elif state!='vacant':issues.append('번호 분석 대기' if cfg.get('enable_ocr') else 'OCR 꺼짐')
            rows.append({'space_id':sid,'occupancy':state,'occupancy_score':score,'occupancy_reason':reason,
                         'score_kind':'difference_ratio' if mode=='reference' else 'sticker_presence' if mode=='sticker' else 'model_score',
                         'number':text,'color':color,'sticker_polygon':poly,'plate_bbox':box,
                         'vehicle_type':COLOR_TYPES.get(color),'zone_rule':rule,'issues':issues,
                         'geometry_status':'변화영역 후보·차량 확정 아님' if diff is not None else '위치 자동검사 불가',
                         'color_method':'independent_color_shape_candidate' if poly else 'unavailable',
                         'ocr_status':'lighting_change_unconfirmed' if sid in light_holds else 'pending' if cfg.get('enable_ocr') else 'disabled','review_status':'pending','quality':quality})
        result={'schema':'prediction-v21-1','stage':'occupancy','context_key':context_key(roi),'frame_id':packet.sequence,
                'captured_at_utc':packet.timestamp_utc,'stream_epoch':packet.stream_epoch,'lot_id':roi['lot_id'],
                'mode':mode,'spaces':rows,'geometry':warnings,'errors':errors,
                'reference_error':ref_error,'reference':{k:v for k,v in reference.items() if k!='image'} if reference else None,
                'parameters':{k:cfg.get(k,d) for k,d in [('difference_threshold',35),('empty_ratio',.025),('occupied_ratio',.12),('ocr_min_score',.7)]},
                'elapsed_ms':round((time.perf_counter()-started)*1000,2)}
        if cfg.get('physical_sticker_set'):
            result['parameters']['physical_sticker_set']=cfg['physical_sticker_set']
        if field_board:
            result['parameters']['measurement_brightness']='aisle-v1'
            if correction:result['measurement_lighting']=correction
        return self.enrich(packet,roi,cfg,result) if cfg.get('enable_ocr') else result

    def enrich(self,packet,roi,cfg,prediction):
        """Read identity on a separate worker; never modify the published occupancy result."""
        from prediction_contract import validate_prediction
        from records import validate_capture
        validate_capture(packet,roi)
        from review import region_matches_slot
        from sticker_ocr import read_sticker,overlap_fraction
        validate_prediction(prediction,{'roi':roi,'frame_id':packet.sequence,'captured_at_utc':packet.timestamp_utc,
            'lot_id':roi['lot_id'],'stream_epoch':packet.stream_epoch,'image_size':list(packet.image.shape[1::-1])})
        started=time.perf_counter();result=copy.deepcopy(prediction);image=packet.image
        from static_markings import unchanged_regions
        static_regions=unchanged_regions(image,roi)
        rows={r['space_id']:r for r in result['spaces']};slots=[s for s in roi['spaces'] if rows[s['space_id']]['occupancy']!='vacant' and rows[s['space_id']]['ocr_status']!='lighting_change_unconfirmed']
        alternate=None
        if slots and cfg.get('physical_sticker_set')==FIELD_SET:
            from board_profile import apply_lighting, match_aisle_light
            if result['parameters'].get('measurement_brightness')=='aisle-v1':
                correction=result.get('measurement_lighting')
                image=apply_lighting(image,correction)
            else:
                image,correction=match_aisle_light(image,roi)
            if correction:
                result['sticker_lighting']=correction
                # Move part-way back toward the input exposure. Keep the excursion
                # bounded so saturated pixels never masquerade as new detail.
                gain=np.clip(1./correction['gain'],.9,1.1)
                if abs(gain-1.)<.02:
                    alternate=cv2.addWeighted(packet.image,.4,image,.6,0)
                else:
                    alternate=cv2.LUT(image,np.rint(np.arange(256)*gain).clip(0,255).astype(np.uint8))
        initialization_error=None;effective=number_config_for_lot(roi['lot_id'],cfg)
        try:
            if slots and self.reader is None:
                from analysis import make_reader
                from pathlib import Path
                self.reader,_=make_reader(Path(cfg['project']),cfg)
        except Exception as exc:initialization_error=str(exc);result['errors'].append('OCR 초기화: '+str(exc))
        for slot in slots:
            row=rows[slot['space_id']];row.update(number=None,plate_bbox=None,selected_ocr=None)
            r={'status':'error','candidates':[],'selected':None,'number_candidate':None}
            row['issues']=[s for s in row['issues'] if s not in ('번호 분석 대기','OCR 꺼짐','번호 미확인','전용칸 종류 불일치 의심') and not s.startswith('번호 미확인:')]
            try:
                polygon=row['sticker_polygon']
                if cfg.get('physical_sticker_set')==FIELD_SET and roi['lot_id'] in ('A','B'):
                    from round_sticker import identify, identify_guide, recover_exposure
                    locate_sticker=identify_guide if slot.get('boundary_kind')=='virtual_open_u' else identify
                    row.pop('number_estimate',None)
                    row.update(sticker_polygon=None,color='unknown',color_method='unavailable')
                    ignored=static_regions.get(slot['space_id'])
                    try:
                        disc,r=locate_sticker(image,slot,None if initialization_error else self.reader,effective,ignored)
                    except (RuntimeError,OSError,ImportError) as exc:
                        if initialization_error:raise
                        initialization_error=str(exc)
                        result['errors'].append('OCR 실행 보류 · 인쇄 모양으로 추정: '+str(exc))
                        disc,r=locate_sticker(image,slot,None,effective,ignored)
                    if not disc and r['status']=='round_sticker_unread' and alternate is not None:
                        recovered=recover_exposure(packet.image,alternate,slot,effective,ignored)
                        if recovered:disc,r=recovered
                    from sticker_exposure import diagnose
                    row['sticker_exposure']=diagnose(packet.image,image,slot,disc)
                    exposure_status=row['sticker_exposure']['status']
                    if exposure_status=='raw_saturated_without_detail':
                        row['issues'].append('원본 영역의 흰색 포화·명암 부족 · 숫자 부위 확인 후 노출을 낮춰 재촬영하세요.')
                    elif exposure_status=='correction_lost_detail':
                        row['issues'].append('밝기 보정 영역의 명암 손실 · 원본과 대조해 재촬영하세요.')
                    if r.get('reason'):row['issues'].append(r['reason'])
                    if disc:
                        color_method='rectangular_sticker_border' if disc.get('shape')=='rectangle' else 'round_sticker_inner_rim'
                        row.update(sticker_polygon=disc['polygon'],color=disc['color'],color_method=color_method)
                        if 'estimate' in r:row['number_estimate']=r['estimate']
                        if slot.get('boundary_kind')=='virtual_open_u' and r['status']=='sticker_number_unread':
                            row.update(occupancy='occupied',occupancy_score=None,score_kind='sticker_presence',
                                       occupancy_reason='가상선 안에 원형 스티커 확인 · 번호 보류')
                        if result['mode']=='sticker' and r.get('estimate'):
                            row.update(occupancy='occupied',occupancy_score=None,score_kind='sticker_presence',
                                       occupancy_reason='원형 스티커와 인쇄 숫자 후보 확인')
                elif initialization_error:raise RuntimeError(initialization_error)
                elif polygon:
                    r=read_sticker(image,polygon,self.reader,effective)
                else:
                    # Full-slot OCR only proposes a region; local sticker evidence is required for identity.
                    proposal=analyze(image,[slot],self.reader,effective)[0]
                    r.update(status='sticker_not_localized',candidates=proposal['candidates'])
                    if proposal.get('number_candidate'):
                        box=proposal['candidates'][0]['text_bbox'];poly,color=sticker_region(image,slot['polygon'],box,static_regions.get(slot['space_id']))
                        if poly and region_matches_slot(poly,slot['space_id'],roi['spaces']):
                            row.update(sticker_polygon=poly,color=color,color_method='ocr_local_color_candidate')
                            r=read_sticker(image,poly,self.reader,effective)
                chosen=r.get('selected')
                if chosen:
                    a,b,c,d=chosen['text_bbox'];region=[[a,b],[c,b],[c,d],[a,d]]
                    if region_matches_slot(region,slot['space_id'],roi['spaces']) and overlap_fraction(chosen['text_bbox'],row['sticker_polygon'])>=.6:
                        row.update(number=r['number_candidate'],plate_bbox=chosen['text_bbox'],selected_ocr=chosen)
                    else:r['status']='slot_assignment_uncertain'
            except Exception as exc:
                result['errors'].append(slot['space_id']+' OCR: '+str(exc));r['status']='error'
            text=row['number']
            recovered_identity = (result['mode']=='reference' and row['occupancy']=='unknown'
                                  and (result.get('reference') or {}).get('empty_board') and not result['reference_error'])
            if (recovered_identity and r.get('estimate') and row['score_kind']=='difference_ratio'
                    and row['occupancy_score'] and row.get('sticker_polygon') is not None):
                row.update(occupancy='occupied',occupancy_score=None,score_kind='sticker_presence',
                           occupancy_reason='빈 기준 대비 국소 변화와 원형 숫자 스티커 확인 · 번호는 추정')
            if (result['mode']=='sticker' or recovered_identity) and text and row['sticker_polygon'] is not None:
                row.update(occupancy='occupied',occupancy_score=None,score_kind='sticker_presence',
                    occupancy_reason='스티커 영역과 복수 영상 번호 판독으로 확인한 점유 후보 · 차량 여부 확인 필요')
            if text and row['plate_bbox'][3]-row['plate_bbox'][1]<24:row['quality']['warnings'].append('번호 높이 24 px 미만: 초점/거리 확인')
            if r['status']=='estimated':
                row['issues'].append('번호 추정 · 참고 사진 비교' if r['method']=='reference_print' else '번호 추정 · 인쇄 숫자 비교')
                x1,y1,x2,y2=r['estimate']['text_bbox']
                if y2-y1<24:
                    row['issues'].append(f'원본 추정 숫자 영역 {x2-x1}×{y2-y1}px · 초점과 촬영 거리를 확인하세요.')
            elif not text:row['issues'].append('번호 미확인: '+r['status'])
            row['vehicle_type']=COLOR_TYPES.get(row['color'])
            row['zone_rule']=zone_rule(slot['space_type'],row['vehicle_type']) if row['occupancy']=='occupied' and slot.get('boundary_kind')!='virtual_open_u' else 'unknown'
            if row['zone_rule']=='not_allowed':row['issues'].append('전용칸 종류 불일치 의심')
            expected_color=sticker_colors(cfg).get(text)
            if expected_color and row['color']!='unknown' and expected_color!=row['color']:row['issues'].append('D-39 배정표와 색 다름: 실물 확인')
            row['ocr_status']=r['status'];row['ocr_candidates']=r['candidates'];row['ocr_method']=r.get('method','slot_region_proposal')
        numbers=[r['number'] for r in rows.values() if r['number']]
        for row in rows.values():
            if row['number'] and numbers.count(row['number'])>1:
                row.update(number=None,plate_bbox=None,selected_ocr=None,ocr_status='duplicate_number')
                row['issues'].append('중복 번호: 귀속 확인')
        result['stage']='complete';result['identity_elapsed_ms']=round((time.perf_counter()-started)*1000,2)
        result['elapsed_ms']+=result['identity_elapsed_ms']
        return result
