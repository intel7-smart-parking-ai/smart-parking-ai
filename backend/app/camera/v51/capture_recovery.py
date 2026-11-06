"""Prepared capture evidence survives process death before manifest publication."""
import copy,hashlib,json
from pathlib import Path
from parking_ai.storage import atomic_json
from dataset_lock import dataset_writer

SCHEMA='capture-journal-v1'


def digest(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode('utf-8')).hexdigest()


def journal_payload(record,prediction=None):
    from prediction_contract import validate_prediction
    if prediction is not None:validate_prediction(prediction,record)
    evidence={'record':copy.deepcopy(record),'prediction':copy.deepcopy(prediction)}
    return {'schema':SCHEMA,**evidence,'evidence_sha256':digest(evidence)}


def validate_journal(value,root,identifier):
    from records import validate_record
    from prediction_contract import validate_prediction
    try:
        if value['schema']!=SCHEMA:raise ValueError('촬영 복구 기록 규격 오류')
        evidence={'record':value['record'],'prediction':value['prediction']}
        if value['evidence_sha256']!=digest(evidence):raise ValueError('촬영 복구 기록 해시 불일치')
        record=validate_record(value['record'],root,identifier)
        if value['prediction'] is not None:validate_prediction(value['prediction'],record)
        return record,value['prediction']
    except (KeyError,TypeError,AttributeError) as exc:raise ValueError('촬영 복구 기록 형식 오류') from exc


def prepare_journal(root,record,prediction=None):
    root=Path(root);path=root/'capture_journal'/(record['image_id']+'.json')
    value=journal_payload(record,prediction)
    if path.exists():
        existing=json.loads(path.read_text(encoding='utf-8-sig'))
        validate_journal(existing,root,record['image_id'])
        if existing!=value:raise ValueError('중단된 촬영 기록과 재시도 문맥/예측이 다릅니다. 기존 자료를 보존합니다.')
    else:atomic_json(path,value)


def finish_journal(root,identifier):
    from parking_ai.storage import safe_path
    safe_path(root,'capture_journal/'+identifier+'.json').unlink(missing_ok=True)


def require_finished_capture(root, record):
    if (Path(root)/'capture_journal'/(record['image_id']+'.json')).exists():
        raise ValueError('촬영 복구 기록 대기 · 미완료 측정 확인이 필요합니다.')


def require_matching_prediction(snapshot, prediction):
    if snapshot is not None and digest(snapshot)!=digest(prediction):
        raise ValueError('촬영 복구 기록과 현재 예측이 다릅니다. 원본과 두 판독 기록을 보존합니다.')


@dataset_writer
def recover_captures(root,cfg):
    """Restore only hashed, decodable originals with recorded context/evidence.

    Missing/truncated pixels, invalid journals or existing conflicting metadata
    are preserved and reported. Never reconstruct unknown metadata from pixels.
    """
    from pipeline import read_json
    from records import load_original,current_capture_record,load_prediction
    from dataset19 import rebuild_scene_assets
    root=Path(root);restored=[];held=[]
    for path in sorted((root/'capture_journal').glob('*.json')):
        try:
            original,prediction=validate_journal(read_json(path),root,path.stem)
            load_original(root,original)
            manifest=root/'manifests'/path.name
            if manifest.exists():record=current_capture_record(root,original)
            else:atomic_json(manifest,original);record=original
            prediction_path=root/'predictions'/path.name
            if prediction is not None and not prediction_path.exists():atomic_json(prediction_path,prediction)
            # An interrupted prediction promise remains an error unless its
            # validated snapshot or a valid stored prediction actually exists.
            require_matching_prediction(prediction,load_prediction(root,record))
            if record.get('asset_schema'):
                record,receipt=rebuild_scene_assets(root,record['image_id'],cfg)
                if receipt['status']!='complete':raise ValueError('파생 자료 일부 복구 실패: '+str(receipt['errors']))
            else:finish_journal(root,record['image_id'])
            restored.append(record['image_id'])
        except Exception as exc:held.append({'사진ID':path.stem,'사유':str(exc)})
    return {'복구완료수':len(restored),'복구보류수':len(held),'복구완료':restored,'복구보류':held,
            '설명':'검증된 원본·촬영문맥·저장된 예측만 복구합니다. 없는 정보는 추정하지 않습니다.'}
