"""Conservative, reproducible screening; a pass is never a truth approval."""
import hashlib,json,math
from pathlib import Path
import cv2
import numpy as np
from layouts import layout
from contracts import sticker_colors

SCHEMA='주차품질선별-v1'
CHECK_REVISION='number-pixels-v1'
DEFAULT_POLICY={'min_bay_short_side':32,'min_sticker_short_side':16,
                'min_sharpness':60.,'max_dark_ratio':.98,'max_bright_ratio':.98}
LABEL_FIELDS=('주차칸명','주차칸꼭짓점_px','점유상태','차량번호표기','스티커색',
              '번호영역_xyxy_px','스티커꼭짓점_px','라벨출처')


def digest(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode('utf-8')).hexdigest()


def policy_for(config):
    value=config.get('quality_selection',{})
    # Keep legacy five-key policy snapshots byte-for-byte comparable when read.
    if not isinstance(value,dict) or set(value)-(set(DEFAULT_POLICY)|{'min_number_short_side'}):raise ValueError('품질 선별 설정 항목 오류')
    result={**DEFAULT_POLICY,**value}
    for key,n in result.items():
        if type(n) not in (int,float) or not math.isfinite(n):raise ValueError('품질 선별 기준은 유한한 숫자여야 합니다: '+key)
        if key.startswith('max_'):
            if not 0<n<=1:raise ValueError('품질 노출 비율 범위 오류')
        elif key=='min_sharpness':
            if not 0<n<=100000:raise ValueError('품질 선명도 기준 범위 오류')
        elif type(n) is not int or not 4<=n<=4096:raise ValueError('품질 최소 크기는 4~4096 정수입니다.')
    return result


def context_digest(record):
    fields={k:record.get(k) for k in ('image_id','lot_id','roi','raw_sha256','image_size',
                  'captured_at_utc','frame_id','stream_epoch','batch_id','session_id','source','analysis_config')}
    if 'capture_view' in record:fields['capture_view']=record['capture_view']
    return digest(fields)


def label_digest(label):
    return digest({k:label.get(k) for k in LABEL_FIELDS})


def measure(image,polygon):
    """Exclude mask padding and polygon edges from exposure/sharpness."""
    pts=np.asarray(polygon,np.int32);x,y,w,h=cv2.boundingRect(pts)
    if x<0 or y<0 or x+w>image.shape[1] or y+h>image.shape[0]:raise ValueError('품질 측정 영역이 원본을 벗어났습니다.')
    gray=cv2.cvtColor(image[y:y+h,x:x+w],cv2.COLOR_BGR2GRAY)
    mask=np.zeros((h,w),np.uint8);cv2.fillPoly(mask,[pts-[x,y]],255)
    valid=cv2.erode(mask,np.ones((3,3),np.uint8),iterations=2,borderType=cv2.BORDER_CONSTANT,borderValue=0)>0
    if not np.any(valid):return {'짧은변_px':0.,'유효픽셀수':0,'선명도':0.,'어두운픽셀비율':1.,'밝은픽셀비율':1.}
    pixels=gray[valid];lap=cv2.Laplacian(gray,cv2.CV_64F)[valid]
    return {'짧은변_px':round(float(min(cv2.minAreaRect(pts.astype(np.float32))[1])),3),
            '유효픽셀수':int(pixels.size),'선명도':round(float(lap.var()),3),
            '어두운픽셀비율':round(float(np.mean(pixels<40)),6),'밝은픽셀비율':round(float(np.mean(pixels>245)),6)}


def metric_reasons(metrics,policy,region):
    reasons=[];minimum=policy['min_bay_short_side' if region=='주차칸' else 'min_sticker_short_side']
    if metrics['짧은변_px']<minimum or not metrics['유효픽셀수']:reasons.append(region+' 해상도 부족')
    if metrics['선명도']<policy['min_sharpness']:reasons.append(region+' 선명도 부족')
    if metrics['어두운픽셀비율']>policy['max_dark_ratio']:reasons.append(region+' 과도하게 어두움')
    if metrics['밝은픽셀비율']>policy['max_bright_ratio']:reasons.append(region+' 과도하게 밝음')
    return reasons


def number_crop_metrics(box,image=None):
    # Training export uses integer, half-open image slices, not float spans.
    x1,y1,x2,y2=map(int,box)
    width=max(0,x2-x1);height=max(0,y2-y1)
    metrics={'너비_px':width,'높이_px':height,'짧은변_px':min(width,height)}
    if image is None:return metrics
    crop=image[y1:y2,x1:x2]
    if not crop.size:
        metrics.update(유효픽셀수=0,선명도=0.,어두운픽셀비율=1.,밝은픽셀비율=1.)
        return metrics
    gray=cv2.cvtColor(crop,cv2.COLOR_BGR2GRAY)
    lap=cv2.Laplacian(gray,cv2.CV_64F)
    # Exclude crop-edge derivatives; sharp surrounding pavement must not help.
    pixels=gray[1:-1,1:-1] if min(gray.shape)>2 else gray
    interior=lap[1:-1,1:-1] if min(gray.shape)>2 else lap
    metrics.update(유효픽셀수=int(pixels.size),선명도=round(float(interior.var()),3),
        어두운픽셀비율=round(float(np.mean(pixels<40)),6),밝은픽셀비율=round(float(np.mean(pixels>245)),6))
    return metrics


def number_crop_reasons(label,policy,metrics=None):
    reasons=[]
    box=label.get('번호영역_xyxy_px')
    if label.get('점유상태')=='차량 있음' and box:
        if number_crop_metrics(box)['짧은변_px']<policy.get('min_number_short_side',16):
            reasons.append('번호 영역 해상도 부족')
        if metrics is not None:
            if metrics['선명도']<policy['min_sharpness']:reasons.append('번호 영역 선명도 부족')
            if metrics['어두운픽셀비율']>policy['max_dark_ratio']:reasons.append('번호 영역 과도하게 어두움')
            if metrics['밝은픽셀비율']>policy['max_bright_ratio']:reasons.append('번호 영역 과도하게 밝음')
            polygon=label.get('스티커꼭짓점_px')
            if polygon:
                from sticker_ocr import overlap_fraction, MIN_NUMBER_STICKER_OVERLAP
                if overlap_fraction(box,polygon)<MIN_NUMBER_STICKER_OVERLAP:reasons.append('번호와 스티커 영역 불일치')
    return reasons


def current_selection(selection):
    return bool(selection and selection.get('검사개정')==CHECK_REVISION)


def require_current_selection(selection):
    if not current_selection(selection):raise ValueError('품질 재검사 필요: 선택 장면 저장 복구로 번호 화질·스티커 연결을 검사하세요.')


def evaluate(record,image,labels,inputs):
    policy=policy_for(record['analysis_config']);scene_reasons=[];rows=[]
    assignments=sticker_colors(record['analysis_config'])
    expected={s['space_id'] for s in layout(record['lot_id'])['spaces']}
    if record['lot_id'] not in ('A','B'):scene_reasons.append('실물 A/B 주차장만 선별 대상')
    if {r['주차칸명'] for r in labels}!=expected:scene_reasons.append('전체 주차칸 등록 누락')
    if record['source']['kind'] not in ('camera','import'):scene_reasons.append('카메라·가져온 사진 이외의 입력 출처')
    numbers=[r['차량번호표기'] for r in labels if r['점유상태']=='차량 있음' and r['차량번호표기']]
    for label in labels:
        reasons=[];metrics={'주차칸':measure(image,label['주차칸꼭짓점_px'])}
        reasons.extend(metric_reasons(metrics['주차칸'],policy,'주차칸'))
        state=label['점유상태'];number=label['차량번호표기'];color=label['스티커색']
        if state not in ('차량 있음','빈칸'):reasons.append('점유상태 미확인')
        if state=='차량 있음':
            if assignments and number not in assignments:reasons.append('D-39 번호 미확인')
            if not assignments and (not isinstance(number,str) or len(number)!=2 or not number.isascii() or not number.isdecimal() or not 10<=int(number)<=99):
                reasons.append('두 자리 실물 번호 미확인')
            if color not in ('흰색','초록','파랑'):reasons.append('스티커색 미확인')
            if number in assignments:
                expected_color={'green':'초록','blue':'파랑','white':'흰색'}[assignments[number]]
                if color!=expected_color:reasons.append('번호와 스티커색 불일치')
            if number and numbers.count(number)>1:reasons.append('장면 내 번호 중복')
            if not label['번호영역_xyxy_px']:reasons.append('번호 영역 미확인')
            else:
                metrics['번호']=number_crop_metrics(label['번호영역_xyxy_px'],image)
                if label['스티커꼭짓점_px']:
                    from sticker_ocr import overlap_fraction
                    metrics['번호']['스티커겹침비율']=round(overlap_fraction(label['번호영역_xyxy_px'],label['스티커꼭짓점_px']),6)
                reasons.extend(number_crop_reasons(label,policy,metrics['번호']))
            if not label['스티커꼭짓점_px']:reasons.append('스티커 영역 미확인')
            else:
                metrics['스티커']=measure(image,label['스티커꼭짓점_px'])
                reasons.extend(metric_reasons(metrics['스티커'],policy,'스티커'))
        elif state=='빈칸' and (number or color not in ('미확인',None) or label['번호영역_xyxy_px'] or label['스티커꼭짓점_px']):
            reasons.append('빈칸과 차량 정보 충돌')
        rows.append({'주차칸명':label['주차칸명'],'상태':'보류' if reasons else '통과','사유':reasons,
                     '측정값':metrics,'라벨출처':label['라벨출처'],'라벨근거_SHA256':label_digest(label)})
    held=sum(r['상태']=='보류' for r in rows)
    if held:scene_reasons.append('보류 주차칸 포함')
    return {'자료규격':SCHEMA,'검사개정':CHECK_REVISION,'사진ID':record['image_id'],'주차장':record['lot_id'],
            '촬영시각_UTC':record['captured_at_utc'],'원본_SHA256':record['raw_sha256'],
            '촬영문맥_SHA256':context_digest(record),'입력지문':inputs,'정책':policy,
            '상태':'보류' if scene_reasons else '통과','통과칸수':len(rows)-held,'보류칸수':held,
            '보류사유':scene_reasons,'칸별결과':rows,
            '설명':'초기 수치 기준을 충족했다는 뜻입니다. 실측 정확도나 사람 승인이 아닙니다.'}


def row_summary(selection,row):
    return {**row,'장면상태':selection['상태'],'장면보류사유':selection['보류사유'],
            '정책':selection['정책'],'설명':selection['설명'],
            **({'검사개정':selection['검사개정']} if '검사개정' in selection else {})}


def load_selection(root,record,receipt,inputs):
    """Old bundles remain readable but cannot silently acquire a pass."""
    expected=receipt.get('quality_selection_sha256')
    if expected is None:return None
    try:
        payload=(Path(root)/'selection'/(record['image_id']+'.json')).read_bytes()
        if hashlib.sha256(payload).hexdigest()!=expected:raise ValueError('품질 선별 기록 해시 불일치')
        value=json.loads(payload)
        bindings={'자료규격':SCHEMA,'사진ID':record['image_id'],'주차장':record['lot_id'],
                  '촬영시각_UTC':record['captured_at_utc'],'원본_SHA256':record['raw_sha256'],
                  '촬영문맥_SHA256':context_digest(record),'입력지문':inputs,'정책':policy_for(record['analysis_config'])}
        if any(value.get(k)!=v for k,v in bindings.items()):raise ValueError('품질 선별과 현재 원본·문맥·예측·검수 불일치')
        rows=value['칸별결과'];ids={s['space_id'] for s in record['roi']['spaces']}
        if not isinstance(rows,list) or len(rows)!=len(ids) or {r['주차칸명'] for r in rows}!=ids:raise ValueError('품질 선별 칸 목록 오류')
        for row in rows:
            if not isinstance(row['사유'],list) or row['상태']!=('보류' if row['사유'] else '통과'):raise ValueError('품질 선별 칸 상태 오류')
        held=sum(r['상태']=='보류' for r in rows)
        if value['보류칸수']!=held or value['통과칸수']!=len(rows)-held:raise ValueError('품질 선별 수량 오류')
        if not isinstance(value['보류사유'],list) or value['상태']!=('보류' if value['보류사유'] else '통과'):raise ValueError('품질 선별 장면 상태 오류')
        if value['상태']=='통과' and (held or ids!={s['space_id'] for s in layout(record['lot_id'])['spaces']} or record['source']['kind'] not in ('camera','import')):raise ValueError('품질 선별 통과 조건 오류')
        return value
    except (KeyError,TypeError,AttributeError) as exc:raise ValueError('품질 선별 기록 형식 오류') from exc


def validate_label_selection(label,selection):
    if selection is None:return
    row=next(r for r in selection['칸별결과'] if r['주차칸명']==label['주차칸명'])
    if label_digest(label)!=row['라벨근거_SHA256'] or label.get('품질선별')!=row_summary(selection,row):
        raise ValueError('한글 라벨과 품질 선별 근거 불일치')
    if selection['상태']=='통과':
        metrics=None
        if current_selection(selection) and label.get('점유상태')=='차량 있음':
            metrics=row.get('측정값',{}).get('번호')
            fields=('유효픽셀수','선명도','어두운픽셀비율','밝은픽셀비율')
            if not isinstance(metrics,dict) or any(type(metrics.get(k)) not in (int,float) or not math.isfinite(metrics[k]) or metrics[k]<0 for k in fields):
                raise ValueError('번호 화질 측정값 누락/오류: 선택 장면 저장 복구가 필요합니다.')
            if any(metrics.get(k)!=v for k,v in number_crop_metrics(label['번호영역_xyxy_px']).items()):raise ValueError('번호 크롭 측정 크기 불일치')
        reasons=number_crop_reasons(label,selection['정책'],metrics)
        if reasons:raise ValueError(', '.join(reasons)+': 선택 장면 복구로 품질 판정을 갱신하세요.')
