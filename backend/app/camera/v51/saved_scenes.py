"""Read existing datasets without rewriting originals, labels or config."""
from datetime import datetime
from pathlib import Path
from layouts import layout
from parking_ai.storage import safe_path
from pipeline import read_json
from records import validate_record,load_original,load_prediction
from prediction_contract import validate_prediction
from review import validate_review,rows_for_edit


def load_saved_scene(root,identifier):
    root=Path(root)
    if not isinstance(identifier,str) or not identifier or Path(identifier).name!=identifier:
        raise ValueError('사진 ID 오류')
    record=validate_record(read_json(safe_path(root,'manifests/'+identifier+'.json')),root,identifier)
    image=load_original(root,record)
    pred=load_prediction(root,record)
    rp=root/'reviews'/(identifier+'.json');previous=read_json(rp) if rp.exists() else None
    if rp.exists():validate_review(record,rows_for_edit(record,previous),previous['reviewer'],image)
    return record,image,pred,previous


def load_latest_lot(root,lot):
    """Latest readable scene in the requested lot; report every skipped error."""
    root=Path(root);layout(lot);candidates=[];warnings=[]
    for path in (root/'manifests').glob('*.json'):
        try:
            data=read_json(path)
            if isinstance(data,dict) and data.get('lot_id')!=lot:continue
            record=validate_record(data,root,path.stem)
            candidates.append((datetime.fromisoformat(record['captured_at_utc']).timestamp(),path.stem))
        except Exception as exc:warnings.append(path.name+': '+str(exc))
    for _,identifier in sorted(candidates,reverse=True):
        try:return {'lot':lot,'scene':load_saved_scene(root,identifier),'warnings':warnings}
        except Exception as exc:warnings.append(identifier+': '+str(exc))
    return {'lot':lot,'scene':None,'warnings':warnings}
