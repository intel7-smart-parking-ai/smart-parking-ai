"""Shared validation at capture, analysis, review and export boundaries."""
import re
import copy
import math
import hashlib
import cv2
import numpy as np
from datetime import datetime
from layouts import validate_snapshot,LOT_NAMES
from capture_views import view_code
from parking_ai.storage import safe_path,valid_bbox


def capture_source(packet,config):
    """The input owns its kind; contextual details belong only to that kind."""
    kind=packet.source_kind
    # Compatibility with the original photo importer (default FramePacket kind).
    if packet.camera_id=='IMPORT' and kind=='camera':kind='import'
    if not isinstance(kind,str) or not kind.strip():raise ValueError('프레임 입력 출처 오류')
    details=config.get('capture_source')
    if isinstance(details,dict) and details.get('kind')==kind:return copy.deepcopy(details)
    return {'kind':kind}


def validate_sensor_frame(value,source_kind):
    if value is None:return
    import math
    if (source_kind!='camera' or not isinstance(value,dict) or set(value)-{'timestamp_ms','timestamp_domain','frame_number','exposure_us'} or
            value.get('timestamp_domain') not in ('hardware_clock','system_time','global_time') or
            type(value.get('timestamp_ms')) not in (int,float) or (isinstance(value['timestamp_ms'],float) and not math.isfinite(value['timestamp_ms'])) or value['timestamp_ms']<0 or
            type(value.get('frame_number')) is not int or value['frame_number']<0 or
            ('exposure_us' in value and (type(value['exposure_us']) is not int or value['exposure_us']<=0))):
        raise ValueError('RGB 프레임 시각·노출 근거 오류')


def validate_capture(packet,roi):
    validate_sensor_frame(getattr(packet,'sensor_frame',None),packet.source_kind)
    validate_snapshot(roi)
    image=packet.image
    if (not isinstance(image,np.ndarray) or image.ndim!=3 or image.shape[2]!=3
            or image.dtype!=np.uint8 or min(image.shape[:2])<1):raise ValueError('원본은 비어 있지 않은 BGR uint8 영상이어야 합니다.')
    if (roi['camera_id']!=packet.camera_id or roi['serial']!=packet.serial
            or roi['image_size']!=list(image.shape[1::-1])):
        raise ValueError('카메라/해상도가 ROI와 다릅니다. ROI를 다시 확인하세요.')
    from marker_setup import validate_frame
    validate_frame(roi,packet.sequence,packet.timestamp_utc,packet.stream_epoch,image)


def validate_record(record,root=None,expected_id=None):
    if not isinstance(record,dict):raise ValueError('촬영 기록은 객체여야 합니다.')
    try:
        identifier=record['image_id']
        if not isinstance(identifier,str) or not re.fullmatch(r'[A-Za-z0-9_-]+',identifier):raise ValueError('사진 ID 오류')
        if expected_id is not None and identifier!=expected_id:raise ValueError('파일명/사진 ID 불일치')
        view_code(record)
        if 'capture_view' in record and view_code(record)!=view_code(record['analysis_config']):raise ValueError('촬영 시점과 설정 기록 불일치')
        validate_snapshot(record['roi'])
        for field in ('lot_id','lot_name','camera_id','serial','image_size'):
            if record[field]!=record['roi'][field]:raise ValueError('촬영 기록/ROI '+field+' 불일치')
        for field in ('raw_path','captured_at_utc','batch_id','session_id'):
            if not isinstance(record[field],str) or not record[field]:raise ValueError('촬영 기록 '+field+' 오류')
        stamp=datetime.fromisoformat(record['captured_at_utc'])
        if stamp.tzinfo is None or stamp.utcoffset() is None:raise ValueError('촬영 시각에 시간대가 필요합니다.')
        if type(record['frame_id']) is not int or record['frame_id']<0:raise ValueError('촬영 프레임 번호 오류')
        if type(record.get('stream_epoch',0)) is not int or record.get('stream_epoch',0)<0:raise ValueError('촬영 연결 구간 오류')
        from marker_setup import validate_frame
        validate_frame(record['roi'],record['frame_id'],record['captured_at_utc'],record.get('stream_epoch',0))
        for status in ('asset_status','collection_status','prediction_status'):
            if status in record and record[status] not in ('pending','partial','complete'):raise ValueError('촬영 기록 완료 상태 오류: '+status)
        if not isinstance(record['analysis_config'],dict) or not isinstance(record['source'],dict) or not isinstance(record['source']['kind'],str):raise ValueError('촬영 설정/출처 오류')
        validate_sensor_frame(record.get('sensor_frame'),record['source']['kind'])
        if 'raw_sha256' in record and (not isinstance(record['raw_sha256'],str) or not re.fullmatch(r'[0-9a-f]{64}',record['raw_sha256'])):
            raise ValueError('원본 SHA-256 형식 오류')
        if root is not None:safe_path(root,record['raw_path'])
    except (KeyError,TypeError,AttributeError) as exc:raise ValueError('필수 촬영 기록이 없거나 형식이 잘못됐습니다.') from exc
    return record


def load_original(root,record):
    """Decode the same verified byte snapshot used for the digest comparison."""
    validate_record(record,root)
    payload=safe_path(root,record['raw_path']).read_bytes()
    if 'raw_sha256' in record and hashlib.sha256(payload).hexdigest()!=record['raw_sha256']:
        raise ValueError('원본 SHA-256 불일치: 사진이 교체되거나 손상되었습니다. 원래 파일을 복구하세요.')
    image=cv2.imdecode(np.frombuffer(payload,np.uint8),cv2.IMREAD_COLOR)
    if image is None or list(image.shape[1::-1])!=record['image_size']:
        raise ValueError('원본 이미지 손상 또는 크기 불일치')
    from marker_setup import validate_frame
    validate_frame(record['roi'],record['frame_id'],record['captured_at_utc'],record.get('stream_epoch',0),image)
    return image


def current_capture_record(root,record):
    """Reject edits from a stale capture while allowing completion-state updates."""
    import json
    from pathlib import Path
    validate_record(record,root)
    path=Path(root)/'manifests'/(record['image_id']+'.json')
    current=validate_record(json.loads(path.read_text(encoding='utf-8-sig')),root,record['image_id'])
    fields=('image_id','camera_id','serial','lot_id','lot_name','image_size','roi','raw_path','raw_sha256',
            'captured_at_utc','frame_id','stream_epoch','source','batch_id','session_id','contract_revision','analysis_config','capture_view','sensor_frame')
    if any(current.get(k)!=record.get(k) for k in fields):
        raise ValueError('열어 둔 장면과 현재 촬영 기록이 다릅니다. 최신 장면을 다시 열어 검수하세요.')
    return current


def load_prediction(root,record):
    """A promised prediction cannot silently become absent after interruption."""
    import json
    from pathlib import Path
    from prediction_contract import validate_prediction
    path=Path(root)/'predictions'/(record['image_id']+'.json')
    if path.exists():return validate_prediction(json.loads(path.read_text(encoding='utf-8-sig')),record)
    if record.get('prediction_status') in ('complete','pending'):
        raise ValueError('필수 예측 파일 유실: 원래 예측을 복구하거나 원본을 다시 분석하세요.')
    return None


def validate_analysis(result,record):
    if not isinstance(result,dict):raise ValueError('분석 파일 형식 오류')
    try:
        from analysis import number
        from contracts import number_config_for_lot
        from multi_vehicle import summarize
        cfg=number_config_for_lot(record['lot_id'],record['analysis_config'])
        if result['image_id']!=record['image_id'] or result['lot_id']!=record['lot_id'] or result['camera_id']!=record['camera_id']:raise ValueError('분석/촬영 ID 불일치')
        spaces=result['spaces'];expected={s['space_id']:s for s in record['roi']['spaces']}
        if not isinstance(spaces,list) or len(spaces)!=len(expected) or {s['space_id'] for s in spaces}!=set(expected):raise ValueError('분석 칸 목록 불일치')
        if result.get('number_mode')!=cfg['number_mode']:raise ValueError('분석 번호 규칙 불일치')
        selected=[]
        for row in spaces:
            if row['status'] not in ('candidate','ambiguous','no_text','duplicate_number'):raise ValueError('분석 칸 상태 오류')
            if row.get('occupancy')!='unknown' or row.get('review_status')!='pending':raise ValueError('자동 분석은 점유/정답을 확정할 수 없습니다.')
            if row.get('space_type')!=expected[row['space_id']]['space_type']:raise ValueError('분석 칸 종류 오류')
            value=row['number_candidate']
            if value is not None and (not isinstance(value,str) or number(value,cfg)!=value):raise ValueError('번호 후보 형식/범위 오류')
            if row['color_candidate'] not in ('unknown','white','green','blue'):raise ValueError('색상 후보 형식 오류')
            candidates=row['candidates']
            if not isinstance(candidates,list):raise ValueError('OCR 후보 목록 오류')
            for candidate in candidates:
                if (not isinstance(candidate,dict) or not valid_bbox(candidate.get('text_bbox'),record['image_size'])
                        or not isinstance(candidate.get('text'),str) or not isinstance(candidate.get('status'),str)):
                    raise ValueError('OCR 후보 박스/텍스트 오류')
                score=candidate.get('score')
                if type(score) not in (int,float) or not math.isfinite(score) or not 0<=score<=1:raise ValueError('OCR 신뢰도 오류')
                if candidate['status'] not in ('candidate','invalid_number','low_score','rotation_ambiguous','clipped_text'):raise ValueError('OCR 후보 상태 오류')
                normalized=number(candidate['text'],cfg)
                if candidate.get('number_candidate')!=normalized:raise ValueError('OCR 원문/번호 후보 불일치')
                if candidate['status']=='candidate' and (normalized is None or score<cfg['ocr_min_score']):raise ValueError('확정 불가능한 OCR 후보')
            if value is not None:
                if row['status']!='candidate' or len(candidates)!=1 or candidates[0]['status']!='candidate' or candidates[0]['number_candidate']!=value:
                    raise ValueError('칸 번호와 OCR 후보 불일치')
                selected.append(value)
            elif row['status']=='candidate':raise ValueError('번호가 없는 candidate 상태')
            if (row['status']=='no_text')!= (not candidates):raise ValueError('OCR 목록/칸 상태 불일치')
        if len(selected)!=len(set(selected)):raise ValueError('분석 칸 번호 중복')
        if 'frame_summary' in result and result['frame_summary']!=summarize(spaces):raise ValueError('전체 칸 요약과 개별 결과 불일치')
    except (KeyError,TypeError,AttributeError) as exc:raise ValueError('분석 결과 형식 오류') from exc
    return result
