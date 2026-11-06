"""Planar board registration. Markers locate bays; they never label vehicles."""
import copy
import hashlib
import json
import time
from pathlib import Path
import cv2
import numpy as np
from dataset_lock import dataset_writer
from parking_ai.storage import atomic_json, safe_path

METHOD='aruco_board_v1'
REVISION='roi-registration-aruco-v1'
DICTIONARY='DICT_4X4_50'
IDS={'A':list(range(6)), 'B':list(range(10,16))}
MIN_SIDE=20.0
MAX_ERROR=3.0
MAX_CROSS_ERROR=5.0


def digest(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def pixel_digest(image):return hashlib.sha256(image.tobytes()).hexdigest()


def template_path(root,lot):
    if lot not in IDS:raise ValueError('마커 등록은 실제 A/B 주차장만 지원합니다.')
    return safe_path(root,'marker_boards/'+lot+'.json')


def points(value,size):
    p=np.asarray(value,dtype=np.float64)
    if p.shape!=(4,2) or not np.isfinite(p).all():raise ValueError('마커 모서리 형식 오류')
    if (p<0).any() or (p[:,0]>=size[0]).any() or (p[:,1]>=size[1]).any():raise ValueError('마커가 원본 밖에 있습니다.')
    if not cv2.isContourConvex(p.astype(np.float32)) or cv2.contourArea(p.astype(np.float32),oriented=True)<=0:raise ValueError('마커 방향/모서리 순서 오류')
    if np.linalg.norm(p-np.roll(p,1,axis=0),axis=1).min()<MIN_SIDE:raise ValueError('마커 한 변이 20px 미만입니다. 더 크게 인쇄하거나 가까이 촬영하세요.')
    return p


def observations(rows,lot,size):
    if not isinstance(rows,list):raise ValueError('마커 검출 목록 오류')
    seen=set()
    for r in rows:
        if type(r['id']) is not int or r['id'] not in IDS[lot]:raise ValueError('다른 주차장/미지정 ID가 보입니다. 선택 주차장과 마커를 확인하세요.')
        if r['id'] in seen:raise ValueError('같은 ID 마커가 중복 배치되었습니다.')
        seen.add(r['id']);points(r['corners'],size)
    return sorted(rows,key=lambda r:r['id'])


def detect(image,lot):
    if not hasattr(cv2,'aruco') or not hasattr(cv2.aruco,'ArucoDetector'):
        raise ValueError('OpenCV ArUco API가 없습니다. requirements.txt로 설치 환경을 갱신하세요.')
    params=cv2.aruco.DetectorParameters();params.cornerRefinementMethod=cv2.aruco.CORNER_REFINE_SUBPIX
    detector=cv2.aruco.ArucoDetector(cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50),params)
    corners,ids,_=detector.detectMarkers(image)
    rows=[] if ids is None else [{'id':int(i),'corners':c.reshape(4,2).astype(float).tolist()} for i,c in zip(ids.flatten(),corners)]
    return observations(rows,lot,list(image.shape[1::-1]))


def hull_area(p):return float(cv2.contourArea(cv2.convexHull(np.float32(p).reshape(-1,2))))


def outside_bays(rows,spaces):
    """Printed calibration tags must not become vehicle/sticker training pixels."""
    for marker in rows:
        for slot in spaces:
            overlap,_=cv2.intersectConvexConvex(np.float32(marker['corners']),np.float32(slot['polygon']))
            if overlap>1.:
                raise ValueError(f"마커 ID {marker['id']}가 주차칸 {slot['space_id']} 내부와 겹칩니다. 칸 밖의 같은 평면에 고정하세요.")


def transform(p,h):
    p=np.asarray(p,dtype=float).reshape(-1,2)
    q=np.c_[p,np.ones(len(p))] @ np.asarray(h,dtype=float).T
    if not np.isfinite(q).all() or (q[:,2]<=1e-6).any():raise ValueError('불안정한 평면 변환 또는 뒤집힌 구도입니다.')
    return q[:,:2]/q[:,2:3]


def validate_template(t):
    from layouts import validate_snapshot,layout
    try:
        if t['schema']!='aruco-board-v1' or t['dictionary']!=DICTIONARY:raise ValueError('마커 기준 규격 오류')
        if digest({k:v for k,v in t.items() if k!='sha256'})!=t['sha256']:raise ValueError('마커 기준 해시 불일치')
        roi=t['roi']
        if roi.get('registration_method')==METHOD:raise ValueError('마커 기준은 원래 주차선/사람 등록 ROI여야 합니다.')
        validate_snapshot(roi)
        if len(roi['spaces'])!=layout(roi['lot_id'])['space_count']:raise ValueError('마커 기준에는 모든 주차칸이 필요합니다.')
        rows=observations(t['markers'],roi['lot_id'],roi['image_size'])
        if len(rows)<4:raise ValueError('처음 등록할 때 보드 외곽의 마커가 최소 4개 필요합니다.')
        allcorners=np.array([r['corners'] for r in rows]).reshape(-1,2)
        baycorners=np.array([s['polygon'] for s in roi['spaces']]).reshape(-1,2)
        hull=cv2.convexHull(np.float32(allcorners))
        if hull_area(allcorners)<hull_area(baycorners)*.8 or any(cv2.pointPolygonTest(hull,tuple(map(float,p)),False)<0 for p in baycorners):
            raise ValueError('마커를 주차장 외곽 네 방향에 분산 배치하여 모든 칸을 둘러싸세요.')
        outside_bays(rows,roi['spaces'])
        if not isinstance(t['reference_pixels_sha256'],str) or len(t['reference_pixels_sha256'])!=64:raise ValueError('기준 영상 해시 오류')
    except (KeyError,TypeError,AttributeError,OverflowError) as exc:raise ValueError('마커 기준 기록 형식 오류') from exc
    return t


def load_template(root,lot):
    path=template_path(root,lot)
    if not path.exists():return None
    t=validate_template(json.loads(path.read_text(encoding='utf-8-sig')))
    if t['roi']['lot_id']!=lot:raise ValueError('마커 기준 파일/주차장 불일치')
    return t


def fit(t,rows,size):
    ref={r['id']:r['corners'] for r in t['markers']}
    rows=observations(rows,t['roi']['lot_id'],size)
    if len(rows)<3:raise ValueError(f'마커 {len(rows)}개 검출: 분산된 기준 마커가 최소 3개 필요합니다.')
    if any(r['id'] not in ref for r in rows):raise ValueError('처음 기준에 없던 마커입니다. 전체 기준을 다시 등록하세요.')
    src=np.float64([ref[r['id']] for r in rows]).reshape(-1,2)
    dst=np.float64([r['corners'] for r in rows]).reshape(-1,2)
    if hull_area(src)<.35*hull_area(list(ref.values())) or hull_area(src.reshape(-1,4,2).mean(axis=1))<.12*hull_area(list(ref.values())):
        raise ValueError('마커가 한쪽에 몰려 있습니다. 보드 외곽의 다른 마커를 보이게 하세요.')
    # All observed markers must agree. Do not silently discard a moved marker as an outlier.
    h,_=cv2.findHomography(src,dst,0)
    if h is None or not np.isfinite(h).all() or abs(h[2,2])<1e-9:raise ValueError('마커 평면 변환 추정 실패')
    h=h/h[2,2]
    residual=float(np.linalg.norm(transform(src,h)-dst,axis=1).max())
    cross=0.
    for i in range(len(rows)):
        mask=np.ones(len(src),bool);mask[i*4:i*4+4]=False
        hh,_=cv2.findHomography(src[mask],dst[mask],0)
        if hh is None:raise ValueError('마커 교차 검증 실패')
        hh=hh/hh[2,2]
        cross=max(cross,float(np.linalg.norm(transform(src[~mask],hh)-dst[~mask],axis=1).max()))
    if residual>MAX_ERROR or cross>MAX_CROSS_ERROR:raise ValueError(f'마커 배치/평면 일치 불량: 오차 {residual:.2f}px / 교차오차 {cross:.2f}px')
    return h,{'maximum_error_px':residual,'cross_validation_error_px':cross,'marker_count':len(rows)}


def mapped_spaces(t,h,size):
    from layouts import validate_spaces
    spaces=copy.deepcopy(t['roi']['spaces'])
    for s in spaces:
        q=transform(s['polygon'],h)
        if (q<0).any() or (q[:,0]>=size[0]-1).any() or (q[:,1]>=size[1]-1).any():raise ValueError('전체 주차칸이 영상 안에 들어오도록 촬영하세요.')
        if cv2.contourArea(np.float32(q),oriented=True)*cv2.contourArea(np.float32(s['polygon']),oriented=True)<=0:raise ValueError('주차칸 방향이 뒤집혔습니다.')
        s['polygon']=np.rint(q).astype(int).tolist()
        if s.get('boundary_kind') in ('open_u','board_open_u'):
            p=s['polygon'];s['painted_edges']=[[p[3],p[0]],[p[0],p[1]],[p[1],p[2]]];s['entrance_edge']=[p[2],p[3]]
    validate_spaces(t['roi']['lot_id'],spaces,size)
    return spaces


def frame_evidence(packet):
    return {'frame_id':packet.sequence,'timestamp':packet.timestamp_utc,'stream_epoch':packet.stream_epoch,
            'pixels_sha256':pixel_digest(packet.image)}


def locate(t,packet,pose):
    from layouts import snapshot,validate_snapshot
    started=time.perf_counter();validate_template(t)
    # One camera, unchanged optics/resolution. Moving/rotating that camera is supported.
    r=t['roi']
    if (packet.camera_id,packet.serial,list(packet.image.shape[1::-1]))!=(r['camera_id'],r['serial'],r['image_size']):
        raise ValueError('카메라/해상도가 마커 기준과 다릅니다. 렌즈/설정을 확인하고 기준을 다시 등록하세요.')
    rows=detect(packet.image,r['lot_id']);size=list(packet.image.shape[1::-1]);h,stats=fit(t,rows,size)
    spaces=mapped_spaces(t,h,size);outside_bays(rows,spaces)
    roi=snapshot(r['lot_id'],packet.camera_id,packet.serial,size,pose,spaces)
    roi.update(operator_confirmed=False,registration_method=METHOD,registration_revision=REVISION,
        marker_evidence={'template':copy.deepcopy(t),'observed':rows,'homography':h.tolist(),'statistics':stats,'frame':frame_evidence(packet)})
    validate_snapshot(roi)
    return {'ready':True,'roi':roi,'method':METHOD,'marker_count':len(rows),'alignment':stats,'registration_ms':round((time.perf_counter()-started)*1000,2)}


def validate_proof(roi):
    try:
        if roi.get('registration_revision')!=REVISION or roi.get('operator_confirmed') is not False:raise ValueError('마커 등록 개정/확인 상태 오류')
        e=roi['marker_evidence'];t=validate_template(e['template']);r=t['roi']
        if any(roi[k]!=r[k] for k in ('lot_id','camera_id','serial','image_size')):raise ValueError('마커 기준/촬영 식별 불일치')
        h,stats=fit(t,e['observed'],roi['image_size'])
        if not np.allclose(np.asarray(e['homography']),h,rtol=1e-7,atol=1e-7):raise ValueError('마커 변환행렬 불일치')
        if any(not np.isclose(v,e['statistics'][k],rtol=1e-6,atol=1e-6) for k,v in stats.items()) or mapped_spaces(t,e['homography'],roi['image_size'])!=roi['spaces']:raise ValueError('마커 근거/주차칸 좌표 불일치')
        outside_bays(e['observed'],roi['spaces'])
        f=e['frame']
        if type(f['frame_id']) is not int or f['frame_id']<0 or type(f['stream_epoch']) is not int or f['stream_epoch']<0:raise ValueError('마커 프레임 식별 오류')
        from datetime import datetime
        if datetime.fromisoformat(f['timestamp']).tzinfo is None:raise ValueError('마커 촬영 시각 오류')
        import re
        if not re.fullmatch('[0-9a-f]{64}',f['pixels_sha256']):raise ValueError('마커 영상 해시 오류')
    except (KeyError,TypeError,AttributeError,OverflowError,cv2.error) as exc:raise ValueError('마커 등록 근거 형식 오류') from exc


def validate_frame(roi,frame_id,stamp,epoch,image=None):
    if roi.get('registration_method')!=METHOD:return
    f=roi['marker_evidence']['frame']
    if (f['frame_id'],f['timestamp'],f['stream_epoch'])!=(frame_id,stamp,epoch):raise ValueError('마커 좌표는 다른 촬영 프레임에 재사용할 수 없습니다.')
    if image is not None and f['pixels_sha256']!=pixel_digest(image):raise ValueError('마커 좌표 근거와 원본 영상이 다릅니다.')


@dataset_writer
def enroll(root,packet,roi):
    from records import validate_capture
    from collection_output import atomic_bytes
    if roi.get('registration_method')==METHOD:raise ValueError('주차선 자동등록 또는 ROI 네 모서리로 실제 위치를 확인한 뒤 마커 기준을 등록하세요.')
    validate_capture(packet,roi)
    rows=detect(packet.image,roi['lot_id'])
    t={'schema':'aruco-board-v1','dictionary':DICTIONARY,'roi':copy.deepcopy(roi),'markers':rows,
       'reference_pixels_sha256':pixel_digest(packet.image),'registered_at_utc':packet.timestamp_utc}
    t['sha256']=digest(t);validate_template(t)
    # Complete the same tracking checks required for collection BEFORE publishing
    # the active board. A failed registration must leave the old board unchanged.
    result=locate(t,packet,roi['pose'])
    # Immutable evidence image first, atomic active template last. Interrupted enrollment
    # leaves the old active board intact; an unreferenced image is safe to retain.
    ok,data=cv2.imencode('.png',packet.image)
    if not ok:raise OSError('마커 기준 영상 저장 실패')
    atomic_bytes(safe_path(root,'marker_boards/references/'+t['sha256']+'.png'),data.tobytes())
    atomic_json(template_path(root,roi['lot_id']),t)
    return result


def korean_evidence(roi):
    if roi.get('registration_method')!=METHOD:return None
    e=roi['marker_evidence'];s=e['statistics']
    return {'규격':REVISION,'사전':DICTIONARY,'기준_SHA256':e['template']['sha256'],
        '검출마커수':s['marker_count'],'검출ID':[r['id'] for r in e['observed']],
        '검출모서리_px':[{'ID':r['id'],'좌표':r['corners']} for r in e['observed']],
        '평면변환행렬':e['homography'],'최대재투영오차_px':s['maximum_error_px'],
        '마커제외교차오차_px':s['cross_validation_error_px'],'원본픽셀_SHA256':e['frame']['pixels_sha256'],
        '설명':'같은 평면에 고정한 마커로 주차칸 좌표를 옮겼습니다. 번호·색·차량 높이·정답률 근거가 아닙니다.'}
