"""Drawing identity is (lot_id, space_id), independent of camera identity."""
import copy
import hashlib
import json
import re
from pathlib import Path
import cv2
import numpy as np
from parking_ai.storage import atomic_json

LOT_NAMES={'A':'가','B':'나','C':'다','D':'라'}
TYPE_NAMES={'general':'일반','electric':'전기차','accessible':'장애인'}
COLOR_TYPES={'white':'general','green':'electric','blue':'accessible'}
DEFINITIONS={
 'A':(6,{'A-05','A-06'},{'B-06'},'physical'),
 'B':(8,{'A-07','A-08'},{'B-07','B-08'},'physical'),
 'C':(6,{'A-05','A-06'},{'B-06'},'virtual'),
 'D':(16,{f'D-{i:02d}' for i in range(1,9)},{f'C-{i:02d}' for i in range(13,17)},'virtual')}


def layout(lot):
    count,electric,accessible,kind=DEFINITIONS[lot]
    spaces=[]
    for zone in (('A','B','C','D') if lot=='D' else ('A','B')):
        for n in range(1,count+1):
            sid=f'{zone}-{n:02d}'
            spaces.append({'space_id':sid,'space_type':'electric' if sid in electric else 'accessible' if sid in accessible else 'general','polygon':None})
    return {'schema_version':2,'lot_id':lot,'name':LOT_NAMES[lot],'kind':kind,'space_count':len(spaces),'spaces':spaces,'calibrations':{}}


def initialize(project):
    for lot in LOT_NAMES:
        path=Path(project)/'configs'/f'parking_{lot.lower()}.json'
        if not path.exists(): atomic_json(path,layout(lot))
        data=json.loads(path.read_text(encoding='utf-8-sig'))
        expected=layout(lot)
        if data['lot_id']!=lot or data['space_count']!=expected['space_count'] or [(s['space_id'],s['space_type']) for s in data['spaces']]!=[(s['space_id'],s['space_type']) for s in expected['spaces']]:
            raise ValueError(f'{path.name}: 팀 도면 계약과 다릅니다.')


def validate_spaces(lot,spaces,size):
    w,h=size
    catalog={s['space_id']:s['space_type'] for s in layout(lot)['spaces']}
    seen=set();polygons=[]
    for s in spaces:
        if s.get('boundary_kind')=='partial_frame':
            raise ValueError('일부 촬영 위치는 전체 주차칸 ROI로 저장할 수 없습니다.')
        sid=s['space_id']
        if sid not in catalog or sid in seen or s['space_type']!=catalog[sid]: raise ValueError('칸 ID/종류 불일치 또는 중복')
        seen.add(sid)
        points=np.asarray(s['polygon'])
        if (points.shape!=(4,2) or not np.issubdtype(points.dtype,np.integer) or
            (points<0).any() or (points[:,0]>=w).any() or (points[:,1]>=h).any()): raise ValueError('ROI는 원본 안의 정수 좌표 4점이어야 합니다.')
        points=points.astype(np.float32)
        if not cv2.isContourConvex(points) or cv2.contourArea(points)<=10: raise ValueError('네 모서리를 테두리 순서대로 지정하세요.')
        for other in polygons:
            area,_=cv2.intersectConvexConvex(points,other)
            if area>1.: raise ValueError('주차칸 ROI 내부가 겹칩니다.')
        polygons.append(points)
    return spaces


def calibration_key(lot,camera,serial,size,pose):
    return hashlib.sha256(json.dumps([lot,camera,serial,list(size),pose],ensure_ascii=False).encode()).hexdigest()


def load_calibration(project,lot,camera,serial,size,pose):
    path=Path(project)/'configs'/f'parking_{lot.lower()}.json'
    data=json.loads(path.read_text(encoding='utf-8-sig'))
    saved=data.get('calibrations',{}).get(camera)
    key=calibration_key(lot,camera,serial,size,pose)
    if not saved or saved.get('calibration_key')!=key: return None
    validate_snapshot(saved)
    if saved['lot_id']!=lot or saved['camera_id']!=camera or saved['serial']!=serial or saved['image_size']!=list(size):
        raise ValueError('저장 ROI 식별 정보 불일치')
    return copy.deepcopy(saved)


def snapshot(lot,camera,serial,size,pose,spaces):
    validate_spaces(lot,spaces,size)
    if not spaces: raise ValueError('최소 한 칸을 등록하세요.')
    all_ids={s['space_id'] for s in layout(lot)['spaces']}
    return {'lot_id':lot,'lot_name':LOT_NAMES[lot],'camera_id':camera,'serial':serial,'image_size':list(size),
        'pose':pose,'calibration_key':calibration_key(lot,camera,serial,size,pose),'spaces':copy.deepcopy(spaces),
        'unregistered_spaces':sorted(all_ids-{s['space_id'] for s in spaces}), 'operator_confirmed':True}


def save_calibration(project,roi):
    validate_snapshot(roi)
    path=Path(project)/'configs'/f'parking_{roi["lot_id"].lower()}.json'
    data=json.loads(path.read_text(encoding='utf-8-sig'))
    import uuid
    if path.exists(): path.with_suffix('.'+uuid.uuid4().hex+'.bak').write_bytes(path.read_bytes())
    data.setdefault('calibrations',{})[roi['camera_id']]=copy.deepcopy(roi)
    atomic_json(path,data)


def zone_rule(space_type,vehicle_type):
    if vehicle_type not in TYPE_NAMES: return 'unknown'
    if space_type=='general' or space_type==vehicle_type: return 'allowed'
    return 'not_allowed'


def validate_snapshot(roi):
    if not isinstance(roi,dict):raise ValueError('ROI 기록 형식 오류')
    try:
        lot=roi['lot_id'];size=roi['image_size']
        if lot not in LOT_NAMES or roi['lot_name']!=LOT_NAMES[lot]:raise ValueError('ROI 주차장 이름 불일치')
        if roi['camera_id'] not in ('A','B','IMPORT'):raise ValueError('ROI 카메라 ID 오류')
        if not isinstance(size,list) or len(size)!=2 or not all(type(n) is int and n>0 for n in size):raise ValueError('ROI 해상도 오류')
        if not isinstance(roi['serial'],str) or not isinstance(roi['pose'],str):raise ValueError('ROI 설치 정보 오류')
        expected=calibration_key(lot,roi['camera_id'],roi['serial'],size,roi['pose'])
        if roi['calibration_key']!=expected:raise ValueError('ROI 설치 키 불일치')
        from sticker_guide import is_guide
        if is_guide(roi):
            from sticker_guide import validate
            validate(roi)
            return roi
        if 'guide_sha256' in roi or any(s.get('boundary_kind')=='virtual_open_u' for s in roi.get('spaces',[])):
            raise ValueError('가상 안내선은 실물 주차칸으로 등록할 수 없습니다.')
        if roi.get('registration_method')=='fixed_printed_board_v1':
            from board_profile import validate
            validate(roi)
            return roi
        if roi.get('registration_method')=='aruco_board_v1':
            from marker_setup import validate_proof
            validate_proof(roi)
            if roi.get('unregistered_spaces')!=[]:raise ValueError('마커 등록 칸 누락')
            return roi
        if roi.get('operator_confirmed') is not True:
            evidence=roi.get('auto_validation',{})
            registration=(roi.get('registration_revision'),roi.get('registration_method'))
            if (registration not in (('roi-registration-v2','auto_line_and_text_v1'),('roi-registration-v3','auto_open_u_and_text_v1'),('roi-registration-v4','auto_occluded_name_v1'))
                    or evidence.get('ready') is not True or evidence.get('expected_count')!=layout(lot)['space_count']
                    or evidence.get('detected_count')!=layout(lot)['space_count'] or evidence.get('missing') or evidence.get('errors') or evidence.get('duplicate_names')):
                raise ValueError('ROI 사람 확인 또는 전칸 자동등록 근거가 필요합니다.')
        if not isinstance(roi['spaces'],list) or not roi['spaces']:raise ValueError('등록된 ROI가 없습니다.')
        validate_spaces(lot,roi['spaces'],size)
        supported=[s for s in roi['spaces'] if s.get('boundary_kind')=='open_u']
        if roi.get('registration_method')=='auto_open_u_and_text_v1' and not supported:raise ValueError('ㄷ자 주차선 근거 누락')
        if supported:
            for slot in supported:
                p=slot['polygon'];scores=slot.get('line_support',[])
                if slot.get('painted_edges')!=[[p[3],p[0]],[p[0],p[1]],[p[1],p[2]]] or slot.get('entrance_edge')!=[p[2],p[3]]:
                    raise ValueError('실제 세 면/가상 입구 경계와 ROI 불일치')
                if len(scores)!=3 or any(type(v) not in (int,float) or not np.isfinite(v) or not .72<=v<=1 for v in scores):
                    raise ValueError('ㄷ자 주차선 지지 근거 오류')
        if roi.get('operator_confirmed') is not True and len(roi['spaces'])!=layout(lot)['space_count']:raise ValueError('자동등록 칸 누락')
        if roi.get('operator_confirmed') is not True:
            rows=evidence.get('text_evidence',[]);threshold=evidence.get('min_score',.7)
            if type(threshold) not in (int,float) or not np.isfinite(threshold) or not 0<=threshold<=1:raise ValueError('자동등록 판독 기준 오류')
            slots={s['space_id']:s for s in roi['spaces']}
            read_ids={r['space_id'] for r in rows}
            inferred={sid for sid,s in slots.items() if 'name_from_adjacent' in s}
            if roi.get('registration_revision')=='roi-registration-v4':
                from occluded_bays import adjacent_names,gap_between,overlap,fits_visible_neighbours
                if not inferred or lot not in ('A','B'):raise ValueError('가려진 칸 근거 누락')
                for sid in inferred:
                    neighbours=adjacent_names(sid);slot=slots[sid]
                    if (slot['name_from_adjacent']!=neighbours or not set(neighbours).issubset(read_ids & slots.keys())
                            or slot.get('boundary_kind')!='open_u'):
                        raise ValueError('가려진 칸의 양옆 이름 또는 주차선 근거 오류')
                    nearby=[slots[n] for n in neighbours]
                    gap=gap_between(*nearby)
                    if gap is None or overlap(gap,slot['polygon'])<.82 or not fits_visible_neighbours(slot,gap,nearby):raise ValueError('가려진 칸 주차선과 양옆 경계 불일치')
            elif inferred:raise ValueError('가려진 칸의 등록 개정 오류')
            if len(rows)!=len(read_ids) or read_ids!=slots.keys()-inferred:raise ValueError('자동등록 칸별 문자 근거 누락/중복')
            for row in rows:
                a,b,c,d=row['bbox'];score=row['score']
                if any(type(v) is not int for v in (a,b,c,d)):raise ValueError('자동등록 문자 좌표는 원본 정수 픽셀이어야 합니다.')
                if roi.get('registration_revision') in ('roi-registration-v3','roi-registration-v4'):
                    from static_markings import decode
                    decode(row.get('appearance'))
                name=re.fullmatch(r'([AB])\s*[-_. ]?\s*([0-9]{1,2})',row['text'].strip().upper())
                if not name or f'{name[1]}-{int(name[2]):02d}'!=row['space_id']:raise ValueError('자동등록 문자 원문/칸 이름 불일치')
                if not 0<=a<c<=size[0] or not 0<=b<d<=size[1] or not np.isfinite(score) or not threshold<=score<=1:raise ValueError('자동등록 문자 좌표/점수 오류')
                if 'local_reads' in row or 'localized_retry' in row:
                    from bay_name_retry import validate_local_reads
                    validate_local_reads(row, size, threshold)
                box=np.float32([[a,b],[c,b],[c,d],[a,d]])
                slot=next(s for s in roi['spaces'] if s['space_id']==row['space_id'])
                area,_=cv2.intersectConvexConvex(box,np.float32(slot['polygon']))
                if area/((c-a)*(d-b))<.98:raise ValueError('자동등록 칸 이름과 경계 귀속 불일치')
    except (KeyError,TypeError,AttributeError) as exc:raise ValueError('ROI 기록 형식 오류') from exc
    return roi
