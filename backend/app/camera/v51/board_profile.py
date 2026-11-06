"""Register the two physical printed boards against stationary marks from their supplied photographs."""
import hashlib
import json
from functools import lru_cache
from pathlib import Path
import cv2
import numpy as np

METHOD='fixed_printed_board_v1'
DIRECTORY=Path(__file__).resolve().parent/'configs'/'board_print'
# Neutral asphalt between the open bay entrances, sampled on the two enrolled
# diagrams at the same percentiles as aisle_levels. No vehicle pixels are used.
AISLE_LEVELS={'A':(34,39,46,51,59,74,95),'B':(28,34,40,44,56,85,103)}

def aisle_levels(image,spaces,registered_empty=False):
    if not spaces:
        return None
    painted=all(s.get('boundary_kind') in ('board_open_u','open_u') for s in spaces)
    if not painted and not registered_empty:return None
    key='entrance_edge' if painted else 'polygon'
    hull=cv2.convexHull(np.float32([q for s in spaces for q in s[key]]))
    center=hull.mean(axis=0)
    polygon=np.rint(center+(hull-center)*.88).astype(np.int32)
    x,y,w,h=cv2.boundingRect(polygon)
    if min(w,h)<12 or x<0 or y<0 or y+h>image.shape[0] or x+w>image.shape[1]:return None
    mask=np.zeros((h,w),np.uint8)
    cv2.fillConvexPoly(mask,polygon-[x,y],255)
    if not painted:
        for slot in spaces:cv2.fillConvexPoly(mask,np.int32(slot['polygon'])-[x,y],0)
        mask=cv2.erode(mask,np.ones((9,9),np.uint8))
    gray=cv2.cvtColor(image[y:y+h,x:x+w],cv2.COLOR_BGR2GRAY)
    values=gray[::2,::2][mask[::2,::2]!=0]
    if values.size<100:return None
    return np.percentile(values,[5,15,35,50,70,85,95])

def match_aisle_light(image,roi,baseline=None):
    if baseline is None and roi.get('registration_method')!=METHOD:return image,None
    levels=aisle_levels(image,roi['spaces'],baseline is not None)
    if levels is None:return image,None
    target=aisle_levels(baseline,roi['spaces'],True) if baseline is not None else AISLE_LEVELS.get(roi['lot_id'])
    if target is None or np.ptp(levels)<18:return image,None
    target=np.asarray(target)
    gain,bias=np.polyfit(levels,target,1)
    if (not .63<=gain<=1.60 or abs(bias)>20 or np.max(np.abs(levels*gain+bias-target))>2.7
        or np.max(np.abs(target-levels))<6):return image,None
    correction={'gain':float(gain),'offset':float(bias),'source':'empty_aisle' if baseline is not None else 'printed_aisle'}
    return apply_lighting(image,correction),correction


def apply_lighting(image,correction):
    if correction is None:return image
    lut=np.clip(np.rint(np.arange(256)*correction['gain']+correction['offset']),0,255).astype(np.uint8)
    return cv2.LUT(image,lut)

@lru_cache(maxsize=2)
def load(lot):
    path=DIRECTORY/(lot+'.npz')
    with np.load(path,allow_pickle=False) as obj:
        return (obj['xy'],obj['descriptor'],json.loads(obj['slots'].item()),hashlib.sha256(path.read_bytes()).hexdigest())

def features(image,mask=None,max_side=1100):
    scale=min(1.,max_side/max(image.shape[:2]))
    gray=cv2.cvtColor(image,cv2.COLOR_BGR2GRAY)
    if scale<1:
        gray=cv2.resize(gray,None,fx=scale,fy=scale,interpolation=cv2.INTER_AREA)
        if mask is not None:mask=cv2.resize(mask,gray.shape[::-1],interpolation=cv2.INTER_NEAREST)
    points,descriptor=cv2.SIFT_create(nfeatures=2200,contrastThreshold=.022).detectAndCompute(gray,mask)
    return np.float32([p.pt for p in points]).reshape(-1,2)/scale,descriptor

def fit(lot,indices,observed,size):
    base,_,slots,_=load(lot)
    ids=np.asarray(indices);dst=np.float32(observed)
    if (ids.ndim!=1 or not np.issubdtype(ids.dtype,np.integer) or not 16<=len(ids)<=160 or len(set(indices))!=len(ids)
            or ids.min()<0 or ids.max()>=len(base) or dst.shape!=(len(ids),2) or not np.isfinite(dst).all()
            or (dst<0).any() or (dst>=np.array(size)).any()):raise ValueError('도면 특징점 근거 부족')
    src=base[ids]
    corners=np.float32([q for s in slots for q in s['polygon']])
    if cv2.contourArea(cv2.convexHull(src))<.31*cv2.contourArea(cv2.convexHull(corners)):raise ValueError('도면 특징 분포 부족')
    matrix,_=cv2.findHomography(src,dst,0)
    if matrix is None or not np.isfinite(matrix).all():raise ValueError('도면 변환 오류')
    projected=cv2.perspectiveTransform(src[None],matrix)[0]
    tolerance=max(1.7,max(size)*.0025)
    if np.max(np.linalg.norm(projected-dst,axis=1))>tolerance:raise ValueError('도면 특징점 재투영 오류')
    result=[]
    for s in slots:
        raw=np.float32(s['polygon'])
        p=cv2.perspectiveTransform(raw[None],matrix)[0]
        if ((p<1).any() or (p>=np.array(size)-1).any() or
                cv2.contourArea(p,oriented=True)*cv2.contourArea(raw,oriented=True)<=0):
            raise ValueError('도면 경계가 화면 밖이거나 뒤집혔습니다.')
        q=np.rint(p).astype(int).tolist()
        result.append({**s,'polygon':q,'painted_edges':[[q[3],q[0]],[q[0],q[1]],[q[1],q[2]]],'entrance_edge':[q[2],q[3]]})
    from layouts import validate_spaces
    validate_spaces(lot,result,size)
    return result

def locate(packet,lot,pose):
    from layouts import snapshot
    if lot not in ('A','B'):return None
    base,desc,_,digest=load(lot)
    size=list(packet.image.shape[1::-1])
    for max_side in dict.fromkeys(min(max(size),n) for n in (1100,820)):
        xy,current=features(packet.image,max_side=max_side)
        matched=_match_print(lot,base,desc,xy,current,size)
        if matched:break
    else:return None
    spaces,indices,observed=matched
    roi=snapshot(lot,packet.camera_id,packet.serial,size,pose,spaces)
    roi.update(operator_confirmed=False,registration_method=METHOD,registration_revision='printed-board-1',
               board_alignment={'sha256':digest,'indices':indices,'observed':observed})
    return {'ready':True,'roi':roi,'method':METHOD}

def _match_print(lot,base,desc,xy,current,size):
    if current is None or len(current)<16:return None
    pairs=cv2.BFMatcher().knnMatch(desc,current,k=2)
    matches=sorted((a for a,b in pairs if a.distance<.73*b.distance),key=lambda m:m.distance)
    unique={}
    for m in matches:unique.setdefault(m.trainIdx,m)
    good=list(unique.values())
    if len(good)<16:return None
    idx=np.int32([m.queryIdx for m in good]);dst=xy[[m.trainIdx for m in good]]
    _,mask=cv2.findHomography(base[idx],dst,cv2.RANSAC,max(1.2,max(size)*.0015))
    if mask is None or mask.mean()<.35:return None
    use=np.flatnonzero(mask.ravel())[:160]
    indices=idx[use].tolist();observed=dst[use].tolist()
    try:spaces=fit(lot,indices,observed,size)
    except ValueError:return None
    return spaces,indices,observed

def validate(roi):
    e=roi['board_alignment'];lot=roi['lot_id']
    if (lot not in ('A','B') or roi.get('registration_revision')!='printed-board-1' or roi.get('operator_confirmed') is not False
        or roi.get('unregistered_spaces')!=[] or e['sha256']!=load(lot)[3]
        or fit(lot,e['indices'],e['observed'],roi['image_size'])!=roi['spaces']):
        raise ValueError('사진 도면과 주차칸 좌표가 다릅니다.')


def matches_registered_bays(packet,roi):
    """Independently locate the whole printed board when parked cars cover anchors."""
    if roi.get('registration_method')!=METHOD:
        return False
    found=locate(packet,roi['lot_id'],roi['pose'])
    if not found:return False
    actual={s['space_id']:np.asarray(s['polygon'],float) for s in found['roi']['spaces']}
    limit=max(3,max(roi['image_size'])*.003)
    return len(actual)==len(roi['spaces']) and all(s['space_id'] in actual and
        np.max(np.linalg.norm(actual[s['space_id']]-s['polygon'],axis=1))<=limit for s in roi['spaces'])


def align_printed_reference(old,current,empty_image,frame):
    """Map the confirmed empty board to this frame, validating painted edges in both rows."""
    if (old.get('registration_method')!=METHOD or current.get('registration_method')!=METHOD
            or old['calibration_key']!=current['calibration_key'] or empty_image.shape!=frame.shape):
        raise ValueError('같은 카메라·구도 설정의 실물 도면 두 장이 필요합니다.')
    before={s['space_id']:s['polygon'] for s in old['spaces']}
    slots=current['spaces']
    if set(before)!={s['space_id'] for s in slots}:raise ValueError('빈 기준과 현재 도면의 칸 목록 불일치')
    src=np.float32([before[s['space_id']] for s in slots])
    dst=np.float32([s['polygon'] for s in slots])
    matrix,_=cv2.findHomography(src.reshape(-1,2),dst.reshape(-1,2),0)
    if matrix is None or not np.isfinite(matrix).all():raise ValueError('빈 기준 도면 변환 실패')
    moved=cv2.perspectiveTransform(src,matrix)
    drift=float(np.linalg.norm(dst-src,axis=2).max())
    h,w=frame.shape[:2]
    ratios=[cv2.contourArea(b)/cv2.contourArea(a) for a,b in zip(src,dst)]
    if (drift>.20*max(w,h) or min(ratios)<.5 or max(ratios)>2
            or np.linalg.norm(moved-dst,axis=2).max()>2):
        raise ValueError('빈 기준과 촬영 위치·크기가 크게 달라졌습니다. 두 빈 기준을 다시 등록하세요.')
    aligned=cv2.warpPerspective(empty_image,matrix,(w,h),borderMode=cv2.BORDER_REPLICATE)
    first=cv2.cvtColor(aligned,cv2.COLOR_BGR2GRAY)
    second=cv2.cvtColor(frame,cv2.COLOR_BGR2GRAY)
    counts={};passed={}
    for slot in slots:
        sid=slot['space_id'];row=sid.split('-')[0]
        counts[row]=counts.get(row,0)+1
        points=np.int32(slot['polygon'])[[3,0,1,2]]
        x,y,bw,bh=cv2.boundingRect(points)
        width=max(5,round(max(w,h)*.008))
        left=max(0,x-width);top=max(0,y-width)
        a=first[top:y+bh+width,left:x+bw+width]
        b=second[top:y+bh+width,left:x+bw+width]
        mask=np.zeros(a.shape,np.uint8)
        cv2.polylines(mask,[points-[left,top]],False,255,width)
        u=a[mask!=0];v=b[mask!=0]
        if min(u.std(),v.std())>=5 and np.corrcoef(u,v)[0,1]>=.65:
            passed.setdefault(row,[]).append(sid)
    if any(3*len(passed.get(row,[]))<2*count for row,count in counts.items()):
        raise ValueError('현재 사진의 주차선과 빈 기준이 충분히 일치하지 않습니다. 두 빈 기준을 다시 등록하세요.')
    proof={'빈기준에서현재로':matrix.tolist(),'최대칸이동_px':round(drift,2),'일치한주차선':passed}
    return aligned,proof
