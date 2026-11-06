"""Automatic bay registration from visible names and painted closed/open-U boundaries."""
import copy,hashlib,json,re,uuid
from pathlib import Path
from dataset_lock import dataset_writer
import cv2,numpy as np
from layouts import layout,snapshot,validate_snapshot,validate_spaces
from parking_ai.storage import atomic_json,safe_path
from collection_output import atomic_bytes
from vision19 import context_key
from partial_bays import partial_label


def normalize_bay(text,lot):
    if not isinstance(text,str):return None
    match=re.fullmatch(r'([AB])\s*[-_. ]?\s*([0-9]{1,2})',text.strip().upper())
    if not match:return None
    name=f'{match[1]}-{int(match[2]):02d}'
    return name if name in {s['space_id'] for s in layout(lot)['spaces']} else None


def extract_quads(image):
    gray=cv2.cvtColor(image,cv2.COLOR_BGR2GRAY)
    adaptive=cv2.adaptiveThreshold(gray,255,cv2.ADAPTIVE_THRESH_GAUSSIAN_C,cv2.THRESH_BINARY,51,-7)
    edges=cv2.Canny(gray,40,130)
    masks=[cv2.morphologyEx(adaptive,cv2.MORPH_CLOSE,np.ones((3,3),np.uint8)),
           cv2.morphologyEx(edges,cv2.MORPH_CLOSE,np.ones((5,5),np.uint8))]
    quads=[];area_total=gray.size
    for mask in masks:
        contours,_=cv2.findContours(mask,cv2.RETR_LIST,cv2.CHAIN_APPROX_SIMPLE)
        for contour in contours:
            polygon=cv2.approxPolyDP(contour,.025*cv2.arcLength(contour,True),True).reshape(-1,2)
            if len(polygon)!=4 or not cv2.isContourConvex(polygon):continue
            area=cv2.contourArea(polygon)
            if not area_total*.002<area<area_total*.30:continue
            edges=[np.linalg.norm(polygon[(i+1)%4]-polygon[i]) for i in range(4)]
            if min(edges)<25 or max(edges)/min(edges)>8:continue
            if (polygon<0).any() or (polygon[:,0]>=image.shape[1]).any() or (polygon[:,1]>=image.shape[0]).any():continue
            quads.append(polygon.astype(int).tolist())
    return quads


def read_bay_names(image,lot,reader,min_score=.7):
    """Evaluate image orientation separately; map OCR evidence back to original pixels."""
    best=({},set());height,width=image.shape[:2]
    def overlap(a,b):
        intersection=max(0,min(a[2],b[2])-max(a[0],b[0]))*max(0,min(a[3],b[3])-max(a[1],b[1]))
        return intersection/max(1,min((a[2]-a[0])*(a[3]-a[1]),(b[2]-b[0])*(b[3]-b[1])))
    first_scale=min(1.,2400/max(image.shape[:2]))
    for turns in (0,3,1,2):
        rotated=np.ascontiguousarray(np.rot90(image,-turns));names={};conflicts=set()
        for scale in dict.fromkeys((first_scale,min(first_scale*1.5,3200/max(image.shape[:2])))):
            pixels=rotated if scale==1 else cv2.resize(rotated,None,fx=scale,fy=scale,interpolation=cv2.INTER_CUBIC)
            for points,text,score in reader.readtext(pixels,detail=1,paragraph=False,rotation_info=None,
                    allowlist='AB-0123456789',canvas_size=4096,mag_ratio=1.):
                sid=normalize_bay(text,lot);points=np.asarray(points,np.float32)/scale
                if not sid or points.shape!=(4,2) or not np.isfinite(points).all() or not np.isfinite(score) or not min_score<=score<=1:continue
                if turns==1:points=np.column_stack((points[:,1],height-1-points[:,0]))
                elif turns==2:points=np.column_stack((width-1-points[:,0],height-1-points[:,1]))
                elif turns==3:points=np.column_stack((width-1-points[:,1],points[:,0]))
                a,b=np.floor(points.min(axis=0)).astype(int);c,d=np.ceil(points.max(axis=0)).astype(int)
                if not 0<=a<c<=width or not 0<=b<d<=height:continue
                entry={'space_id':sid,'text':text,'score':float(score),'bbox':[int(a),int(b),int(c),int(d)],'view_scale':scale,'view_rotation_clockwise':turns*90}
                for other,prior in names.items():
                    same_place=overlap(entry['bbox'],prior['bbox'])>=.6
                    if (other==sid and not same_place) or (other!=sid and same_place):conflicts.update((sid,other))
                if sid not in names or score>names[sid]['score']:names[sid]=entry
            if len(names)==layout(lot)['space_count'] and not conflicts:break
        if len(names)-len(conflicts)>len(best[0])-len(best[1]):best=(names,conflicts)
        if len(names)==layout(lot)['space_count'] and not conflicts:return names,conflicts
    from bay_name_retry import retry_names
    retry_names(image,lot,reader,*best,min_score)
    return best


def detect_spaces(image,lot,reader,min_score=.7):
    from sticker_ocr import overlap_fraction
    from open_boundaries import extract_open_quads,matches_name_spacing,inset_candidate,painted_closed_quads
    names,conflicts=read_bay_names(image,lot,reader,min_score)
    evidence=list(names.values())
    from static_markings import appearance
    for entry in evidence:entry['appearance']=appearance(image,entry['bbox'])
    open_candidates=extract_open_quads(image)
    quads=painted_closed_quads(image,extract_quads(image))+open_candidates
    catalog={s['space_id']:s for s in layout(lot)['spaces']};spaces=[]
    reliable_names={key:v for key,v in names.items() if key not in conflicts}
    for sid,entry in names.items():
        if sid in conflicts:continue
        a,b,c,d=entry['bbox'];matches=[]
        for candidate in quads:
            if entry.get('localized_retry') and '_painted_closed' in candidate and not candidate['_painted_closed']:
                candidate = candidate.get('_three_painted')
                if candidate is None:
                    continue
            poly=candidate['polygon']
            if lot in ('A','B') and any(x<=0 or x>=image.shape[1]-1 or y<=0 or y>=image.shape[0]-1 for x,y in poly):
                continue
            if overlap_fraction(entry['bbox'],poly)<.98:continue
            if sum(overlap_fraction(other['bbox'],poly)>.9 for other in names.values())!=1:continue
            if not matches_name_spacing(candidate,sid,reliable_names):continue
            x,y,w,h=cv2.boundingRect(np.asarray(poly,np.int32))
            if w<2.2*(c-a) or h<2.2*(d-b):continue
            matches.append(candidate)
        if matches:
            # Prefer actual three-side line evidence over closed contours caused
            # by colored floor patches. Minimum enclosing area rejects multi-bay merges.
            closed=[m for m in matches if m.get('_painted_closed')]
            supported=[m for m in matches if m.get('boundary_kind')=='open_u']
            selected=dict(min(closed or supported or matches,key=lambda c:cv2.contourArea(np.asarray(c['polygon'],np.float32))))
            selected.pop('_painted_closed',None)
            selected.pop('_three_painted',None)
            if selected.get('boundary_kind')=='open_u':selected=inset_candidate(selected)
            spaces.append({**catalog[sid],**selected})
    from occluded_bays import recover_hidden
    recovered=recover_hidden(lot,catalog,spaces,names,open_candidates)
    spaces.extend(recovered)
    expected=set(catalog);found={s['space_id'] for s in spaces};errors=[]
    try:
        validate_spaces(lot,spaces,list(image.shape[1::-1]))
        areas=[cv2.contourArea(np.asarray(s['polygon'],np.float32)) for s in spaces]
        if areas and max(areas)>min(areas)*3:errors.append('칸 면적 차이가 큽니다. 경계 오인식 또는 구도를 확인하세요.')
    except ValueError as exc:errors.append(str(exc))
    missing=sorted(expected-found)
    from partial_bays import partial_end_bays
    partial=partial_end_bays(image,lot,catalog,spaces,names,conflicts) if missing and not errors else []
    return {'ready':not missing and not errors and not conflicts,'spaces':spaces,'expected_count':len(expected),
        'partial_spaces':partial,'registered_count':len(spaces)+len(partial),
        'detected_count':len(spaces),'missing':missing,'duplicate_names':sorted(conflicts),'errors':errors,'text_evidence':evidence,
        'method':'auto_occluded_name_v1' if recovered else 'auto_open_u_and_text_v1' if any(s.get('boundary_kind')=='open_u' for s in spaces) else 'auto_line_and_text_v1','min_score':float(min_score)}


def anchor_alignment(base,current,roi):
    if base.shape!=current.shape:return {'passed':False,'reason':'해상도 변경'}
    mask=np.zeros(base.shape[:2],np.uint8)
    for slot in roi['spaces']:cv2.polylines(mask,[np.asarray(slot['polygon'],np.int32)],True,255,12)
    for e in roi.get('auto_validation',{}).get('text_evidence',[]):
        a,b,c,d=e['bbox'];cv2.rectangle(mask,(max(0,a-5),max(0,b-5)),(min(mask.shape[1]-1,c+5),min(mask.shape[0]-1,d+5)),255,-1)
    orb=cv2.ORB_create(nfeatures=2500)
    ka,da=orb.detectAndCompute(cv2.cvtColor(base,cv2.COLOR_BGR2GRAY),mask)
    kb,db=orb.detectAndCompute(cv2.cvtColor(current,cv2.COLOR_BGR2GRAY),None)
    if da is None or db is None:return {'passed':False,'reason':'구도 대조 특징 부족'}
    pairs=cv2.BFMatcher(cv2.NORM_HAMMING).knnMatch(da,db,k=2)
    good=[pair[0] for pair in pairs if len(pair)==2 and pair[0].distance<.7*pair[1].distance]
    if len(good)<20:return {'passed':False,'reason':'구도 대조 일치점 부족','matches':len(good)}
    src=np.float32([ka[m.queryIdx].pt for m in good]);dst=np.float32([kb[m.trainIdx].pt for m in good])
    homography,inliers=cv2.findHomography(src,dst,cv2.RANSAC,3.)
    if homography is None:return {'passed':False,'reason':'구도 변환 추정 실패'}
    h,w=mask.shape;corners=np.float32([[[0,0],[w-1,0],[w-1,h-1],[0,h-1]]])
    projected=cv2.perspectiveTransform(corners,homography)
    drift=float(np.max(np.linalg.norm(projected-corners,axis=2)));ratio=float(inliers.mean());count=int(inliers.sum())
    shifts=(dst-src)[inliers.ravel().astype(bool)]
    translation=0.
    if count>=15 and ratio>=.65:
        median=np.median(shifts,axis=0)
        if np.mean(np.linalg.norm(shifts-median,axis=1)<=1.5)>=.7:
            translation=float(np.linalg.norm(median))
    passed=count>=15 and ratio>=.65 and drift<=max(3,max(w,h)*.003)
    return {'passed':bool(passed),'matches':len(good),'inliers':count,'inlier_ratio':ratio,'maximum_drift_px':drift,'consistent_translation_px':translation,
        'reason':'주차선/칸이름 구도 일치' if passed else '구도 이동 또는 불확실: 자동 재등록 필요'}


def anchor_path(root,lot,camera,pose=None):
    from capture_views import VIEW_NAMES
    code=str(pose).rsplit('__view-',1)[-1] if '__view-' in str(pose) else None
    suffix='__'+code if code in VIEW_NAMES and code!='unspecified' else ''
    return Path(root)/'calibration_anchors'/(lot+'_'+camera+suffix+'.json')


def missing_stages(setup):
    """Explain only what this registration attempt actually observed."""
    if 'text_evidence' not in setup or 'missing' not in setup:
        return None
    read_ids = {entry['space_id'] for entry in setup['text_evidence']}
    conflicts = set(setup.get('duplicate_names', ()))
    partial = {s['space_id'] for s in setup.get('partial_spaces',())}
    stages = {}
    for sid in setup['missing']:
        if sid in partial:
            stages[sid]='일부 촬영 · 위치 등록'
        elif sid in conflicts:
            stages[sid]='칸명 충돌'
        else:
            stages[sid]='경계 미확인' if sid in read_ids else '칸명 미판독'
    return stages


@dataset_writer
def save_anchor(root,packet,roi):
    relative='calibration_anchors/'+roi['lot_id']+'_'+packet.camera_id+'_'+uuid.uuid4().hex+'.png'
    ok,data=cv2.imencode('.png',packet.image)
    if not ok:raise OSError('구도 기준 저장 실패')
    digest=atomic_bytes(safe_path(root,relative),data.tobytes())
    atomic_json(anchor_path(root,roi['lot_id'],packet.camera_id,roi['pose']),{'roi':roi,'image':relative,'sha256':digest,'context_key':context_key(roi)})


def try_anchor(root,packet,lot,pose):
    path=anchor_path(root,lot,packet.camera_id,pose)
    if not path.exists():return None
    try:
        saved=json.loads(path.read_text(encoding='utf-8-sig'));roi=validate_snapshot(saved['roi'])
        if saved.get('context_key')!=context_key(roi):return None
        if (roi['lot_id']!=lot or roi['camera_id']!=packet.camera_id or roi['serial']!=packet.serial or roi['pose']!=pose
                or roi['image_size']!=list(packet.image.shape[1::-1]) or len(roi['spaces'])!=layout(lot)['space_count']):return None
        payload=safe_path(root,saved['image']).read_bytes()
        if hashlib.sha256(payload).hexdigest()!=saved['sha256']:return None
        image=cv2.imdecode(np.frombuffer(payload,np.uint8),cv2.IMREAD_COLOR)
        if image is None:return None
        report=anchor_alignment(image,packet.image,roi)
        if report['passed']:return {'ready':True,'roi':copy.deepcopy(roi),'method':'verified_anchor_reuse','alignment':report}
    except (ValueError,KeyError,OSError,TypeError,cv2.error):return None
    return None


@dataset_writer
def prepare_auto_scene(root,packet,lot,pose,reader_factory,min_score=.7):
    from marker_setup import load_template,locate,enroll,detect
    template=load_template(root,lot) if lot in ('A','B') else None
    if template is not None:return locate(template,packet,pose)
    previous=try_anchor(root,packet,lot,pose)
    if previous:
        if lot in ('A','B') and len(detect(packet.image,lot))>=4:return enroll(root,packet,previous['roi'])
        return previous
    if lot in ('A','B'):
        from board_profile import locate as locate_printed_board
        printed=locate_printed_board(packet,lot,pose)
        if printed:
            if len(detect(packet.image,lot))>=4:return enroll(root,packet,printed['roi'])
            return printed
    result=detect_spaces(packet.image,lot,reader_factory(),min_score)
    if result['ready']:
        roi=snapshot(lot,packet.camera_id,packet.serial,packet.image.shape[1::-1],pose,result['spaces'])
        revisions={'auto_occluded_name_v1':'roi-registration-v4','auto_open_u_and_text_v1':'roi-registration-v3','auto_line_and_text_v1':'roi-registration-v2'}
        roi.update(operator_confirmed=False,registration_method=result['method'],registration_revision=revisions[result['method']],
            auto_validation={k:v for k,v in result.items() if k!='spaces'})
        validate_snapshot(roi)
        if result['method']!='auto_occluded_name_v1':save_anchor(root,packet,roi)
        result['roi']=roi
    # Every registration attempt is reviewable without requiring a manual action.
    identifier=uuid.uuid4().hex if result['ready'] else 'latest_'+lot+'_'+packet.camera_id
    stem=Path(root)/'setup_attempts'/identifier
    partial=result.get('partial_spaces',[])
    view=draw_setup_candidates(packet.image,result['spaces']+partial)
    ok,data=cv2.imencode('.png',view)
    if not ok:raise OSError('등록 진단 사진 저장 실패')
    atomic_bytes(stem.with_suffix('.png'),data.tobytes())
    atomic_json(stem.with_suffix('.json'),{'주차장':lot,'카메라':packet.camera_id,'시리얼':packet.serial,
        '프레임번호':packet.sequence,'촬영시각_UTC':packet.timestamp_utc,'자동등록완료':result['ready'],'필요칸수':result['expected_count'],
        '찾은칸수':result['detected_count'],'못찾은칸':result['missing'],'중복이름':result['duplicate_names'],
        '미등록단계':missing_stages(result),
        '위치등록칸수':result['detected_count']+len(partial),'전체경계등록칸수':result['detected_count'],
        '부분등록칸':[partial_label(s) for s in partial],
        '오류':result['errors'],'검출근거':result['text_evidence'],'표시사진':str(stem.with_suffix('.png'))})
    result['diagnostic_path']=str(stem.with_suffix('.json'))
    if result['ready'] and result['method']!='auto_occluded_name_v1' and lot in ('A','B') and len(detect(packet.image,lot))>=4:
        return enroll(root,packet,result['roi'])
    return result


def draw_setup_candidates(image,spaces):
    view=image.copy()
    for slot in spaces:
        pts=np.asarray(slot['polygon'],np.int32)
        if slot.get('boundary_kind')=='partial_frame':
            cv2.polylines(view,[pts],True,(30,131,255),3)
        elif slot.get('boundary_kind') in ('open_u','board_open_u'):
            cv2.polylines(view,[pts[[3,0,1,2]]],False,(88,239,141),3)
            a,b=pts[2].astype(float),pts[3].astype(float);length=float(np.linalg.norm(b-a))
            for offset in range(0,max(1,int(length)),24):
                start=np.rint(a+(b-a)*offset/max(1,length)).astype(int)
                end=np.rint(a+(b-a)*min(offset+13,length)/max(1,length)).astype(int)
                cv2.line(view,tuple(start),tuple(end),(106,197,255),3)
        else:cv2.polylines(view,[pts],True,(0,230,255),3)
        # A corner can belong to both neighbours. Place the label inside its own bay.
        font = cv2.FONT_HERSHEY_SIMPLEX
        center = pts.mean(axis=0)
        top = pts[np.argsort(pts[:,1])[:2]].mean(axis=0)
        for scale in (.65,.48,.34):
            (w,h),base = cv2.getTextSize(slot['space_id'],font,scale,1)
            middle = .55*center + .45*top
            left, upper = np.rint(middle - [w/2,h/2]).astype(int)
            corners = [(left-2,upper-2),(left+w+2,upper-2),(left+w+2,upper+h+base+1),(left-2,upper+h+base+1)]
            if all(cv2.pointPolygonTest(pts,(float(x),float(y)),False)>=0 for x,y in corners):
                cv2.rectangle(view,corners[0],corners[2],(27,34,31),-1)
                cv2.putText(view,slot['space_id'],(left,upper+h),font,scale,(0,230,255),1,cv2.LINE_AA)
                break
    return view
