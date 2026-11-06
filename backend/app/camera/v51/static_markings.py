"""Suppress unchanged printed bay names, never blanket-mask their future location."""
import base64,hashlib
import cv2,numpy as np


def signature(image,bbox):
    a,b,c,d=bbox
    gray=cv2.cvtColor(image[b:d,a:c],cv2.COLOR_BGR2GRAY)
    return cv2.resize(gray,(32,16),interpolation=cv2.INTER_AREA)


def appearance(image,bbox):
    pixels=signature(image,bbox).tobytes()
    return {'schema':'bay-text-gray-32x16-v1','pixels_base64':base64.b64encode(pixels).decode('ascii'),
            'sha256':hashlib.sha256(pixels).hexdigest()}


def decode(reference):
    if not isinstance(reference,dict) or reference.get('schema')!='bay-text-gray-32x16-v1':raise ValueError('칸 이름 외관 근거 오류')
    pixels=base64.b64decode(reference['pixels_base64'],validate=True)
    if len(pixels)!=512 or hashlib.sha256(pixels).hexdigest()!=reference.get('sha256'):raise ValueError('칸 이름 외관 해시/크기 오류')
    return np.frombuffer(pixels,np.uint8).reshape(16,32)


def compare_markings(image,roi):
    from marker_setup import transform
    marker=roi.get('marker_evidence') if roi.get('registration_method')=='aruco_board_v1' else None
    source=marker['template']['roi'] if marker else roi
    if marker:
        gray=cv2.cvtColor(image,cv2.COLOR_BGR2GRAY)
        destination=np.float32([[-.5,-.5],[31.5,-.5],[31.5,15.5],[-.5,15.5]])
    for row in source.get('auto_validation',{}).get('text_evidence',[]):
        if not row.get('appearance'):continue
        region=row['bbox']
        if marker:
            a,b,c,d=region
            poly=np.float32(transform([[a,b],[c,b],[c,d],[a,d]],marker['homography']))
            h=cv2.getPerspectiveTransform(poly,destination)
            current=cv2.warpPerspective(gray,h,(32,16)).astype(float)
            region=poly.tolist()
        else:current=signature(image,region).astype(float)
        baseline=decode(row['appearance']).astype(float)
        baseline-=baseline.mean();current-=current.mean()
        norm=np.linalg.norm(baseline)*np.linalg.norm(current)
        correlation=float(np.sum(baseline*current)/norm) if norm>1 else 0.
        yield row['space_id'],region,correlation,baseline.std()


def unchanged_regions(image,roi):
    result={}
    for sid,region,correlation,_ in compare_markings(image,roi):
        if correlation>=.92:result.setdefault(sid,[]).append(region)
    return result


def overlaps_static(polygon,regions):
    poly=np.float32(polygon);area=max(1.,cv2.contourArea(poly))
    for region in regions or []:
        if np.asarray(region).shape==(4,2):quad=np.float32(region)
        else:
            a,b,c,d=region;quad=np.float32([[a,b],[c,b],[c,d],[a,d]])
        overlap,_=cv2.intersectConvexConvex(poly,quad)
        if overlap/area>=.5:return True
    return False


