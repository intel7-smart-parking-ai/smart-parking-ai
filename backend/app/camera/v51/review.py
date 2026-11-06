"""Per-image human sticker masks; approvals obey current local dataset rules."""
import copy
import json
from datetime import datetime,timezone
from pathlib import Path
from dataset_lock import dataset_writer
import cv2
import numpy as np
from parking_ai.storage import atomic_json,valid_bbox,safe_path
from layouts import COLOR_TYPES,zone_rule
from analysis import number,color_candidate,validate_sticker_polygon
from contracts import CONTRACT_REVISION,FIELD_SET,number_config_for_lot
from capture_views import VIEW_NAMES,view_code,view_folder
from records import validate_record,load_original,current_capture_record
from sticker_ocr import MIN_NUMBER_STICKER_OVERLAP, overlap_fraction
import hashlib

_NO_EXPECTATION=object()


def review_revision(data):
    return None if data is None else hashlib.sha256(json.dumps(data,sort_keys=True,ensure_ascii=False).encode()).hexdigest()


def region_matches_slot(points,slot,all_slots):
    region=np.asarray(points,np.float32);area=cv2.contourArea(region)
    scores={}
    for candidate in all_slots:
        overlap,_=cv2.intersectConvexConvex(region,np.asarray(candidate["polygon"],np.float32))
        scores[candidate["space_id"]]=float(overlap/max(area,1.))
    return scores[slot]>=.75 and all(v<.2 for sid,v in scores.items() if sid!=slot)


def original_image(root,record):
    return load_original(root,record)


def number_region_issue(record, row):
    box = row.get('plate_bbox')
    polygon = row.get('sticker_polygon')
    if record['lot_id'] not in ('A', 'B') or box is None:
        return ''
    if polygon is None:
        if record['analysis_config'].get('physical_sticker_set') == FIELD_SET and row.get('plate_text'):
            return '숫자 박스를 승인하려면 원형 스티커 영역을 지정하세요.'
        return ''
    # Imported reviews are untrusted. Check geometry before invoking OpenCV.
    if not valid_bbox(box, record['image_size']):
        raise ValueError('번호판 박스가 원본 밖입니다.')
    points = validate_sticker_polygon(polygon, record['image_size'])
    if overlap_fraction(box, points) < MIN_NUMBER_STICKER_OVERLAP:
        return '번호 영역이 스티커 밖입니다. 스티커 안의 숫자를 다시 지정하세요.'
    return ''


def validate_review(record,rows,reviewer,image):
    if not isinstance(reviewer,str) or not reviewer.strip():raise ValueError('검수자 이름을 입력하세요.')
    validate_record(record)
    if not isinstance(rows,list) or any(not isinstance(row,dict) for row in rows):raise ValueError('검수 목록 형식 오류')
    required={'space_id','occupancy','plate_text','color','plate_bbox','review_status'}
    if any(not required.issubset(row) or not isinstance(row['space_id'],str) for row in rows):raise ValueError('검수 필수 항목/칸 ID 오류')
    catalog={s['space_id']:s for s in record['roi']['spaces']}
    if len(rows)!=len(catalog) or {r.get('space_id') for r in rows}!=set(catalog):raise ValueError('등록된 칸과 검수 목록이 다릅니다.')
    cfg=number_config_for_lot(record['lot_id'],record['analysis_config'])
    # Accept editable label fields only. Derived metadata must never override
    # original-image identity when a hand-edited/imported review is exported.
    clean=[{key:copy.deepcopy(row[key]) for key in required} | {'sticker_polygon':copy.deepcopy(row.get('sticker_polygon'))} for row in rows]
    for row in clean:
        if row['occupancy'] not in ('unknown','occupied','vacant'):raise ValueError('점유 상태 오류')
        if row['review_status'] not in ('pending','approved'):raise ValueError('검수 상태 오류')
        if row['review_status']=='approved' and row['occupancy']=='unknown':
            raise ValueError('점유 상태를 occupied 또는 vacant로 확인한 뒤 승인하세요. 미확인은 초안으로 저장할 수 있습니다.')
        if row['color'] not in ('unknown','white','green','blue'):raise ValueError('스티커 배경색 오류')
        if not isinstance(row.get('plate_text'),str):raise ValueError('번호는 문자열이어야 합니다.')
        raw=row['plate_text'].strip();row['plate_text']=raw;display=number(raw,cfg) if raw else None
        if raw and display is None and row['review_status']=='approved':
            if cfg.get('number_mode')=='physical_sticker' and not cfg.get('allowed_numbers'):
                raise ValueError('실물 스티커에 인쇄된 10~99의 두 자리 숫자를 입력하세요.')
            raise ValueError('이 촬영의 등록 스티커 번호를 입력하세요. 가상은 1724 또는 17가1724 형식입니다.')
        polygon=row.get('sticker_polygon')
        if row['occupancy']=='vacant' and (raw or row['plate_bbox'] is not None or row['color']!='unknown' or polygon is not None):raise ValueError('빈칸에는 번호·색상·스티커 영역을 입력할 수 없습니다.')
        if row['plate_bbox'] is not None and not valid_bbox(row['plate_bbox'],record['image_size']):raise ValueError('번호판 박스가 원본 밖입니다.')
        if row['color']!='unknown' and polygon is None:raise ValueError('색상을 지정하려면 해당 사진의 스티커 네 모서리를 먼저 등록하세요.')
        if polygon is not None:validate_sticker_polygon(polygon,record['image_size'])
        if row['review_status']=='approved':
            issue = number_region_issue(record, row)
            if issue:
                raise ValueError(f"{row['space_id']} {issue}")
            regions=[]
            if polygon is not None:regions.append(polygon)
            if row['plate_bbox'] is not None:
                a,b,c,d=row['plate_bbox'];regions.append([[a,b],[c,b],[c,d],[a,d]])
            if any(not region_matches_slot(region,row['space_id'],record['roi']['spaces']) for region in regions):
                raise ValueError('스티커/번호판 영역이 선택한 주차칸과 일치하지 않습니다. 경계 차량은 승인 보류하세요.')
        measured=color_candidate(image,polygon)
        row.update(lot_id=record['lot_id'],space_type=catalog[row['space_id']]['space_type'],
            number_status='valid' if display else 'invalid_number' if raw else 'unread',
            display_number=display,car_id=display if cfg['number_mode']=='physical_sticker' else None,
            vehicle_type=COLOR_TYPES.get(row['color']),sticker_polygon=polygon,color_measurement=measured,
            color_decision_source='human_review' if row['color']!='unknown' else 'unknown')
        row['zone_rule']=zone_rule(row['space_type'],row['vehicle_type']) if row['occupancy']=='occupied' else 'unknown'
        if record['roi'].get('registration_method')=='virtual_sticker_guide_v1':row['zone_rule']='unknown'
    confirmed=[r['car_id'] for r in clean if r['review_status']=='approved' and r['car_id'] is not None]
    if len(set(confirmed))!=len(confirmed):raise ValueError('같은 실물 차량 번호를 여러 칸에 중복 승인할 수 없습니다.')
    return clean


def save_review(root,record,rows,reviewer,expected_revision=_NO_EXPECTATION):
    data, _ = save_review_with_receipt(root,record,rows,reviewer,expected_revision)
    return data


@dataset_writer
def save_review_with_receipt(root,record,rows,reviewer,expected_revision=_NO_EXPECTATION):
    """Commit the authoritative labels, then return any derivative refresh warning."""
    record=current_capture_record(root,record)
    from capture_recovery import require_finished_capture
    require_finished_capture(root, record)
    clean=validate_review(record,rows,reviewer,original_image(root,record))
    data={'contract_revision':CONTRACT_REVISION,'image_id':record['image_id'],'lot_id':record['lot_id'],
          'reviewer':reviewer.strip(),'updated_at_utc':datetime.now(timezone.utc).isoformat(),'spaces':clean}
    path=Path(root)/'reviews'/(record['image_id']+'.json')
    old_bytes=path.read_bytes() if path.exists() else None
    old=json.loads(old_bytes) if old_bytes is not None else None
    if expected_revision is not _NO_EXPECTATION and review_revision(old)!=expected_revision:
        raise ValueError('다른 창/작업에서 검수 결과가 변경되었습니다. 창을 다시 열어 최신 내용을 확인하세요.')
    if old is not None:
        import uuid
        from collection_output import atomic_bytes
        atomic_bytes(Path(root)/'reviews'/'history'/(record['image_id']+'-'+uuid.uuid4().hex+'.json'),old_bytes)
    atomic_json(path,data)
    if not record.get('collection_schema'):
        return data, ''
    # Even a crash here invalidates old derivatives through the review fingerprint.
    from collection_output import COLLECTION_SCHEMA, render_collection
    from records import load_prediction
    manifest = Path(root)/'manifests'/path.name
    current_record = dict(record, collection_status='pending', collection_schema=COLLECTION_SCHEMA)
    try:
        atomic_json(manifest, current_record)
        try:
            prediction = load_prediction(root,current_record)
            bundle = render_collection(root,record,original_image(root,record),prediction,data)
        except Exception as exc:
            bundle = {'schema':COLLECTION_SCHEMA,'image_id':record['image_id'],'status':'partial',
                      'items':[],'errors':[{'error':str(exc)}]}
            atomic_json(Path(root)/'bundles'/path.name, bundle)
        current_record['collection_status'] = bundle['status']
        atomic_json(manifest, current_record)
        if bundle['status'] == 'complete':
            return data, ''
        reason = ', '.join(str(item.get('error', item)) for item in bundle.get('errors', [])) or bundle['status']
    except Exception as exc:
        reason = str(exc)
    return data, '표시사진·JSON 갱신 보류 · [선택 장면 저장 복구]를 실행하세요. ' + reason


def default_review(record):
    return [{'space_id':s['space_id'],'occupancy':'unknown','plate_text':'','color':'unknown','plate_bbox':None,
             'sticker_polygon':None,'review_status':'pending'} for s in record['roi']['spaces']]


def rows_for_edit(record,previous=None):
    if previous is not None and (not isinstance(previous,dict) or previous.get('image_id')!=record['image_id'] or previous.get('lot_id')!=record['lot_id']):
        raise ValueError('검수 파일과 원본의 사진/주차장 ID가 다릅니다.')
    rows=copy.deepcopy(previous['spaces'] if previous else default_review(record))
    if not isinstance(rows,list) or any(not isinstance(row,dict) for row in rows):
        raise ValueError('검수 목록 형식 오류')
    current=bool(previous and previous.get('contract_revision')==CONTRACT_REVISION)
    for row in rows:
        row.setdefault('sticker_polygon',None)
        if row.get('review_status')=='approved' and row.get('occupancy')=='unknown':
            row['review_status']='pending'
        if current and row.get('review_status')=='approved' and number_region_issue(record, row):
            row['review_status']='pending'
        if previous and not current:
            row['review_status']='pending'
            if row['sticker_polygon'] is None:
                row.update(color='unknown',vehicle_type=None)
    return rows


@dataset_writer
def export_reviewed(root,lot=None,session=None):
    """Export only current, revalidated approvals; report skipped legacy data."""
    from pipeline import read_json
    import uuid
    from export_integrity import ExportTransaction
    target=Path(root)/'exports'/('reviewed-'+uuid.uuid4().hex)/'reviewed.jsonl';target.parent.mkdir(parents=True,exist_ok=True)
    temporary=target.with_suffix('.jsonl.tmp');report=target.with_suffix('.report.json');frames=target.with_suffix('.frames.jsonl')
    from export_images import write_images
    import shutil
    image_folder=target.with_suffix('.images')
    with ExportTransaction(target.parent) as transfer:
        count=0;skipped=[];exported_images=[];expected_jsonl=hashlib.sha256();pair_cache={}
        try:
            with temporary.open('x',encoding='utf-8',newline='\n') as f:
                for path in sorted((Path(root)/'reviews').glob('*.json')):
                    try:
                        review=read_json(path)
                        if not isinstance(review,dict):raise ValueError('검수 파일 형식 오류')
                        if review.get('contract_revision')!=CONTRACT_REVISION:
                            skipped.append({'file':path.name,'reason':'기존 규격 검수 — 스티커 영역과 번호 재검수 필요'});continue
                        record=validate_record(read_json(Path(root)/'manifests'/path.name),root,path.stem)
                        if lot and record['lot_id']!=lot:continue
                        if session and record['session_id']!=session:continue
                        from dual_capture import require_complete_pair
                        require_complete_pair(root,record,pair_cache)
                        if review['image_id']!=record['image_id'] or review['lot_id']!=record['lot_id']:raise ValueError('검수와 원본 ID 불일치')
                        edited=rows_for_edit(record,review)
                        image=original_image(root,record)
                        rows=validate_review(record,edited,review['reviewer'],image)
                        for before,after in zip(review['spaces'],rows):
                            if before['review_status']=='approved' and after['review_status']=='pending':
                                reason = '점유 미확인 승인 제외 — 점유 상태 재검수 필요' if before.get('occupancy')=='unknown' else '번호와 스티커 영역 불일치 — 재검수 필요'
                                skipped.append({'file':path.name,'space_id':after['space_id'],'reason':reason})
                        polygons={s['space_id']:s['polygon'] for s in record['roi']['spaces']}
                        lines=[]
                        for row in rows:
                            if row['review_status']!='approved':continue
                            out={**row,'contract_revision':CONTRACT_REVISION,'image_id':record['image_id'],
                                 'raw_path':image_folder.name+'/originals/'+view_folder(record)+record['image_id']+'.png',
                                 'source_raw_path':record['raw_path'],'image_size':record['image_size'],'source':record['source'],
                                 'batch_id':record['batch_id'],'reviewer':review['reviewer'],'capture_view':view_code(record),'촬영시점':VIEW_NAMES[view_code(record)],
                                 'parking_polygon':polygons[row['space_id']],
                                 'plate_box_scope':'human_registered' if row['plate_bbox'] else 'not_registered',
                                 'all_objects_in_image_annotated':False}
                            if 'raw_sha256' in record:out['raw_sha256']=record['raw_sha256']
                            from dual_capture import label_fields
                            out.update(label_fields(record))
                            if out.get('실물칸확인') is False:
                                out.update(guide_polygon=out['parking_polygon'],parking_polygon=None)
                            lines.append(json.dumps(out,ensure_ascii=False,allow_nan=False)+'\n')
                    except (ValueError,TypeError,KeyError,OSError,cv2.error) as exc:
                        skipped.append({'file':path.name,'reason':str(exc)});continue
                    # Output failures must abort the export, not masquerade as a bad
                    # input review. Prepare a whole image before writing any of it.
                    if lines:
                        entry=write_images(root,image_folder,record,rows,image)
                        exported_images.append(entry)
                        transfer.verify(image_folder.name+'/'+entry['original'],entry['raw_sha256'])
                        transfer.verify(image_folder.name+'/'+entry['preview'],entry['preview_sha256'])
                    f.writelines(lines);expected_jsonl.update(''.join(lines).encode('utf-8'));count+=len(lines)
                f.flush()
                import os
                os.fsync(f.fileno())
            from multi_vehicle import group_reviewed
            group_reviewed(temporary,frames)
            report_data={'exported_spaces':count,'skipped':skipped,'scope':{'lot':lot,'session':session},'contract_revision':CONTRACT_REVISION,
                               'image_folder':image_folder.name,'exported_images':exported_images,
                               'preview_scope':'human_reviewed_labels_not_automatic_ocr'}
            atomic_json(report,report_data)
            temporary.replace(target)  # Publish the main dataset last.
            transfer.verify(target.name,expected_jsonl.hexdigest());transfer.report(report.name,report_data)
            grouped=[json.loads(line) for line in frames.read_text(encoding='utf-8').splitlines()]
            if sum(len(frame['annotations']) for frame in grouped)!=count:raise ValueError('장면별 내보내기 라벨 수 불일치')
            for frame in grouped:
                expected=next((e for e in exported_images if e['image_id']==frame['image_id']),None)
                if expected is None or frame['raw_path']!=image_folder.name+'/'+expected['original']:
                    raise ValueError('장면별 내보내기 원본 경로 불일치')
                if frame.get('raw_sha256',expected['raw_sha256'])!=expected['raw_sha256']:
                    raise ValueError('장면별 내보내기 원본 해시 불일치')
                transfer.verify(frame['raw_path'],expected['raw_sha256'])
            transfer.verify(frames.name,hashlib.sha256(frames.read_bytes()).hexdigest())
            transfer.finish()
        except Exception:
            if image_folder.exists():shutil.rmtree(image_folder)
            for incomplete in (temporary,target,report,frames):
                try:incomplete.unlink(missing_ok=True)
                except OSError:pass  # Keep the original exception; no success is reported.
            raise
        return target,count
