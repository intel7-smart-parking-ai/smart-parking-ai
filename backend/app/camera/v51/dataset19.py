"""Durable scene assets and disk-backed counters; no UI-thread filesystem scans."""
import copy
import hashlib
import json
from pathlib import Path
from dataset_lock import dataset_writer
import uuid
from datetime import datetime,timezone
import cv2
import numpy as np
from pipeline import save_capture,read_json
from parking_ai.storage import atomic_json,safe_path
from records import capture_source,validate_record,validate_capture,load_original,load_prediction
from review import validate_review,rows_for_edit
from layouts import TYPE_NAMES
from contracts import CONTRACT_REVISION
from vision19 import crop_space,context_key
from prediction_contract import validate_prediction
from capture_views import VIEW_NAMES,view_code,view_folder,view_counts
from quality_selection import load_selection,validate_label_selection,current_selection,require_current_selection
from collection_output import atomic_bytes,input_fingerprints,render_collection,read_collection,validate_collection_label,expected_scene_labels,KINDS,COLLECTION_SCHEMA,verify_display_png


def write_png(path,image):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    ok,data=cv2.imencode('.png',image)
    if not ok:raise OSError('PNG encoding failed')
    return atomic_bytes(path,data.tobytes())


def export_readiness(row, slot):
    approved=row.get('review_status')=='approved'
    box=row.get('plate_bbox')
    usable_box=bool(box and int(box[2])>int(box[0]) and int(box[3])>int(box[1]))
    return {'occupancy':approved and slot.get('boundary_kind')!='virtual_open_u' and row.get('occupancy') in ('occupied','vacant'),
            'number':approved and bool(row.get('display_number')) and usable_box,
            'color':approved and row.get('color') in ('white','green','blue') and bool(row.get('sticker_polygon'))}


def observation_key(packet,roi):
    return (packet.camera_id,packet.serial,packet.sequence,packet.timestamp_utc,
            packet.stream_epoch,packet.source_kind,context_key(roi))


def capture_id(packet,roi,batch,session,cfg):
    """Keep the V32 UUID algorithm byte-for-byte for retries and interrupted writes."""
    capture_key=json.dumps([*observation_key(packet,roi),batch,session],ensure_ascii=False,separators=(',',':'))
    selected_view=view_code(cfg)
    if selected_view!='unspecified':capture_key+='|capture_view='+selected_view
    return uuid.uuid5(uuid.NAMESPACE_URL,'parking-capture:'+capture_key).hex


@dataset_writer
def persist_scene(root,packet,roi,batch,session,cfg,prediction=None,existing=None):
    root=Path(root)
    validate_capture(packet,roi)
    reserved=capture_id(packet,roi,batch,session,cfg)
    if not existing and (root/'manifests'/(reserved+'.json')).exists():existing=reserved
    if prediction is not None:
        validate_prediction(prediction,{'roi':roi,'frame_id':packet.sequence,'captured_at_utc':packet.timestamp_utc,
                                       'lot_id':roi['lot_id'],'stream_epoch':packet.stream_epoch,'image_size':list(packet.image.shape[1::-1])})
    if existing:
        if not isinstance(existing,str) or Path(existing).name!=existing:raise ValueError('사진 ID 오류')
        record=validate_record(read_json(safe_path(root,'manifests/'+existing+'.json')),root,existing)
        if cfg.get('dual_measurement')!=record['analysis_config'].get('dual_measurement'):raise ValueError('기존 장면과 동시측정 연결 정보가 다릅니다.')
        if (record['batch_id'],record['session_id'])!=(batch,session):raise ValueError('기존 장면과 촬영 배치/세션이 다릅니다. 원래 장면을 보존합니다.')
        if view_code(record)!=view_code(cfg):raise ValueError('기존 장면의 촬영 시점이 다릅니다.')
        if record['source']['kind']!=capture_source(packet,cfg)['kind']:raise ValueError('기존 장면과 입력 출처가 다릅니다.')
        if record['frame_id']!=packet.sequence or record['captured_at_utc']!=packet.timestamp_utc or record.get('stream_epoch',0)!=packet.stream_epoch or context_key(record['roi'])!=context_key(roi):raise ValueError('다른 장면/연결 구간을 덮어쓸 수 없습니다.')
        if record.get('sensor_frame')!=packet.sensor_frame:raise ValueError('기존 촬영의 RGB 프레임 근거 불일치')
        original=load_original(root,record)
        if not np.array_equal(original,packet.image):raise ValueError('같은 장면 ID의 원본 픽셀이 변경되었습니다.')
        journal=root/'capture_journal'/(existing+'.json')
        if journal.is_file():
            from capture_recovery import validate_journal,require_matching_prediction
            from records import current_capture_record
            prepared,snapshot=validate_journal(read_json(journal),root,existing)
            current_capture_record(root,prepared)
            if (root/'predictions'/(existing+'.json')).is_file():
                require_matching_prediction(snapshot,load_prediction(root,record))
            if prediction is not None:require_matching_prediction(snapshot,prediction)
        if prediction is None:prediction=load_prediction(root,record)
    else:
        path=save_capture(root,packet,roi,batch,session,cfg,scene_assets=True,identifier=reserved,prediction_expected=prediction is not None,prediction_snapshot=prediction);record=read_json(path)
    # Publish the durable incomplete state BEFORE changing any existing asset.
    # A crash must never leave an old complete bundle exportable as current.
    record.update(asset_schema='scene-assets-v1',asset_status='pending',collection_schema=COLLECTION_SCHEMA,collection_status='pending')
    if prediction is not None:record['prediction_status']='pending'
    atomic_json(root/'manifests'/(record['image_id']+'.json'),record)
    atomic_json(root/'bundles'/(record['image_id']+'.json'),{'schema':COLLECTION_SCHEMA,'image_id':record['image_id'],
        'status':'pending','items':[],'errors':[]})
    identifier=record['image_id'];items=[];errors=[]
    def asset(relative,image,kind,sid=None):
        try:
            digest=write_png(root/relative,image)
            items.append({'path':relative,'kind':kind,'space_id':sid,'sha256':digest,'status':'complete'})
        except Exception as exc:
            errors.append({'path':relative,'kind':kind,'error':str(exc)})
    for slot in roi['spaces']:
        crop,mask,origin=crop_space(packet.image,slot['polygon'])
        relative=f"spaces/{roi['lot_id']}/{view_folder(record)}{slot['space_id']}/{identifier}.png"
        asset(relative,crop,'space',slot['space_id'])
        if items and items[-1]['path']==relative:
            items[-1].update(origin=list(origin),polygon=slot['polygon'],mask_outside='white')
    if prediction is not None:
        try:
            validate_prediction(prediction,record)
            prediction_path=root/'predictions'/(identifier+'.json')
            if prediction_path.exists():
                previous=read_json(prediction_path)
                if previous!=prediction:atomic_json(root/'prediction_history'/identifier/(uuid.uuid4().hex+'.json'),previous)
            atomic_json(prediction_path,prediction)
            for row in prediction['spaces']:
                if row.get('sticker_polygon'):
                    crop,_,_=crop_space(packet.image,row['sticker_polygon'])
                    asset(f"stickers/{view_folder(record)}{identifier}_{row['space_id']}.png",crop,'sticker',row['space_id'])
        except Exception as exc:errors.append({'kind':'prediction','error':str(exc)})
    previous_review=None;reviewpath=root/'reviews'/(identifier+'.json')
    if reviewpath.exists():
        try:
            previous_review=read_json(reviewpath)
            clean=validate_review(record,rows_for_edit(record,previous_review),previous_review['reviewer'],packet.image)
            previous_review={**previous_review,'spaces':clean}
        except Exception as exc:previous_review=None;errors.append({'kind':'review','error':str(exc)})
    bundle=render_collection(root,record,packet.image,prediction if not any(e['kind']=='prediction' for e in errors) else None,previous_review)
    errors.extend({'kind':'collection','error':str(e)} for e in bundle['errors'])
    receipt={'image_id':identifier,'items':items,'errors':errors,'status':'partial' if errors else 'complete',
        'collection_counts':{kind:sum(i['kind']==kind for i in bundle['items']) for kind in KINDS}}
    atomic_json(root/'assets'/(identifier+'.json'),receipt)
    record.update(asset_schema='scene-assets-v1',asset_status=receipt['status'])
    record.update(collection_schema=COLLECTION_SCHEMA,collection_status=bundle['status'])
    if prediction is not None:record['prediction_status']='complete' if not any(e['kind']=='prediction' for e in errors) else 'pending'
    atomic_json(root/'manifests'/(identifier+'.json'),record)
    if receipt['status']=='complete':
        from capture_recovery import finish_journal
        finish_journal(root,identifier)
    return record,receipt


@dataset_writer
def rebuild_scene_assets(root,identifier,cfg):
    """Rebuild derivatives from verified stored evidence, never recapture or guess."""
    from camera_hub import FramePacket
    import time
    root=Path(root)
    from parking_ai.storage import safe_path
    if not isinstance(identifier,str) or not identifier or Path(identifier).name!=identifier:raise ValueError('사진 ID 오류')
    record=validate_record(read_json(safe_path(root,'manifests/'+identifier+'.json')),root,identifier)
    image=load_original(root,record);prediction=load_prediction(root,record)
    rp=root/'reviews'/(identifier+'.json')
    if rp.exists():
        review=read_json(rp);validate_review(record,rows_for_edit(record,review),review['reviewer'],image)
    packet=FramePacket(record['camera_id'],record['serial'],record['frame_id'],record['captured_at_utc'],time.monotonic(),image,
        camera_options=record.get('camera_options'),stream_epoch=record.get('stream_epoch',0),source_kind=record['source']['kind'],sensor_frame=record.get('sensor_frame'))
    return persist_scene(root,packet,record['roi'],record['batch_id'],record['session_id'],record['analysis_config'],prediction,identifier)


class DatasetIndex:
    def __init__(self):self.cache={}
    def scan(self,root,lot=None,session=None,force=False):
        root=Path(root);entries=[];problems=[]
        totals={'capture_scenes':0,'originals':0,'spaces':0,'stickers':0,'label_files':0,'label_rows':0,
                'occupancy_approved':0,'number_approved':0,'color_approved':0,
                'occupancy_ready':0,'number_ready':0,'color_ready':0,'bytes':0,'partial':0,
                'selected_occupancy_ready':0,'selected_number_ready':0,'selected_color_ready':0,
                'annotated_spaces':0,'korean_labels':0,'annotated_scenes':0,'scene_labels':0,'collection_pairs':0,
                'information_complete':0,'needs_attention':0,'quality_warning_labels':0,'quality_passed':0,'quality_held':0,'quality_unassessed':0,'quality_recheck_required':0}
        active_keys=set()
        pair_cache={}
        unique_files={k:set() for k in ('originals','spaces','stickers',*KINDS.values())};image_bytes={}
        for path in (root/'manifests').glob('*.json'):
            try:
                record=validate_record(read_json(path),root,path.stem)
                if lot and record['lot_id']!=lot:continue
                if session and record['session_id']!=session:continue
                raw=safe_path(root,record['raw_path']);reviewpath=root/'reviews'/path.name;assetpath=root/'assets'/path.name
                predpath=root/'predictions'/path.name
                receipt_errors=[]
                required_assets='asset_status' in record or 'asset_schema' in record
                try:
                    receipt=read_json(assetpath) if assetpath.exists() else {'items':[],'errors':[],'status':'missing' if required_assets else 'legacy'}
                    if not isinstance(receipt,dict) or not isinstance(receipt.get('items'),list) or not isinstance(receipt.get('errors'),list):raise ValueError('자산 기록 형식 오류')
                    if assetpath.exists() and (receipt.get('image_id')!=path.stem or receipt.get('status') not in ('complete','partial')):raise ValueError('자산 ID/상태 오류')
                    paths=set();space_ids=set()
                    for a in receipt['items']:
                        safe_path(root,a['path'])
                        if a['path'] in paths or a['kind'] not in ('space','sticker') or a.get('status')!='complete':raise ValueError('자산 중복/종류/상태 오류')
                        if a['space_id'] not in {s['space_id'] for s in record['roi']['spaces']}:raise ValueError('자산 칸 오류')
                        if a['kind']=='space':
                            if a['space_id'] in space_ids:raise ValueError('칸 자산 중복')
                            space_ids.add(a['space_id'])
                        paths.add(a['path'])
                    if required_assets and len(space_ids)!=len(record['roi']['spaces']):receipt_errors.append('칸 크롭 자산 기록 누락')
                    if required_assets and not assetpath.exists():receipt_errors.append('필수 assets 기록 유실')
                except Exception as exc:
                    receipt={'items':[],'errors':[],'status':'invalid'};receipt_errors.append('자산 기록: '+str(exc))
                bundlepath=root/'bundles'/path.name;bundle=None
                try:bundle=read_collection(root,record,False)
                except Exception as exc:receipt_errors.append('표시사진/한글 JSON: '+str(exc))
                from dual_capture import require_complete_pair
                deliverable=True
                try:
                    verified_dual = require_complete_pair(root,record,pair_cache) is not None
                except Exception as exc:
                    verified_dual = False
                    deliverable=False
                    receipt_errors.append('촬영 완료 확인: '+str(exc))
                tracked=[path,raw,reviewpath,assetpath,predpath,bundlepath,root/'selection'/path.name]+[safe_path(root,a['path']) for a in receipt['items']]
                if bundle:tracked.extend(safe_path(root,a['path']) for a in bundle['items'])
                signature=tuple((str(p),p.stat().st_mtime_ns,p.stat().st_size) if p.exists() else (str(p),None,None) for p in tracked)
                signature+=tuple(receipt_errors)
                key=str(path.resolve());active_keys.add(key);cached=self.cache.get(key)
                if force or not cached or cached[0]!=signature:
                    counts={k:0 for k in totals};faults=list(receipt_errors)
                    original_image=load_original(root,record);counts['originals']=1;counts['bytes']=raw.stat().st_size
                    counts['capture_scenes']=1;verified_files=[('originals',raw,counts['bytes'])]
                    expected_labels=None
                    try:
                        prediction=load_prediction(root,record)
                        expected_labels=expected_scene_labels(root,record,prediction,original_image)
                    except Exception as exc:faults.append('예측·검수 근거 '+path.name+': '+str(exc))
                    for item in receipt['items']:
                        try:
                            p=safe_path(root,item['path']);payload=p.read_bytes()
                            if hashlib.sha256(payload).hexdigest()!=item['sha256']:raise ValueError('해시 불일치')
                            counts['spaces' if item['kind']=='space' else 'stickers']+=1;counts['bytes']+=len(payload)
                            verified_files.append(('spaces' if item['kind']=='space' else 'stickers',p,len(payload)))
                        except Exception as exc:faults.append(item['path']+': '+str(exc))
                    selection=None
                    if bundle:
                        selection=load_selection(root,record,bundle,input_fingerprints(root,record['image_id']))
                        valid_pairs={'annotated_space':set(),'korean_label':set()}
                        collection_labels={};scene_label=None
                        for item in bundle['items']:
                            try:
                                payload=safe_path(root,item['path']).read_bytes()
                                if hashlib.sha256(payload).hexdigest()!=item['sha256']:raise ValueError('해시 불일치')
                                if item['kind'] in ('korean_label','scene_label'):
                                    if expected_labels is None:raise ValueError('예측·검수 근거를 검증할 수 없습니다.')
                                    validate_collection_label(json.loads(payload),record,item,bundle['items'],expected_labels)
                                if item['kind']=='korean_label':
                                    label=json.loads(payload)
                                    validate_label_selection(label,selection)
                                    if label['사진ID']!=record['image_id'] or label['주차칸명']!=item['space_id'] or label['주차장']!=record['lot_id']:raise ValueError('한글 라벨 식별정보 불일치')
                                    complete=label['자동정보완성'];warning=bool(label['영상품질']['주의사항'])
                                    if type(complete) is not bool:raise ValueError('정보완성 형식 오류')
                                    counts['information_complete' if complete else 'needs_attention']+=1
                                    counts['quality_warning_labels']+=warning
                                    collection_labels[item['space_id']]=label
                                elif item['kind']=='scene_label':scene_label=json.loads(payload)
                                counts[KINDS[item['kind']]]+=1
                                verified_files.append((KINDS[item['kind']],safe_path(root,item['path']),len(payload) if item['kind'] in ('annotated_space','annotated_scene') else None))
                                if item['kind'] in valid_pairs:valid_pairs[item['kind']].add(item['space_id'])
                                if item['kind'] in ('annotated_space','annotated_scene'):counts['bytes']+=len(payload)
                            except Exception as exc:faults.append(item['path']+': '+str(exc))
                        if scene_label is not None and {r['주차칸명']:r for r in scene_label['주차칸별결과']}!=collection_labels:
                            faults.append('전체 장면 JSON과 칸별 JSON 결과가 다릅니다.')
                        counts['collection_pairs']=len(valid_pairs['annotated_space']&valid_pairs['korean_label'])
                        faults.extend('표시사진/한글 JSON: '+str(e) for e in bundle['errors'])
                        if bundle['status']!='complete':faults.append('표시사진/한글 JSON 묶음 일부 실패')
                    approved=0
                    if reviewpath.exists():
                        try:
                            review=read_json(reviewpath)
                            edited=rows_for_edit(record,review)
                            rows=validate_review(record,edited,review['reviewer'],original_image)
                            counts['label_files']=1;counts['label_rows']=len(rows)
                            slots={s['space_id']:s for s in record['roi']['spaces']}
                            for row in rows:
                                if row['review_status']!='approved':continue
                                approved+=1
                                readiness=export_readiness(row,slots[row['space_id']])
                                counts['occupancy_approved']+=int(readiness['occupancy'])
                                counts['number_approved']+=int(bool(row['display_number']))
                                counts['color_approved']+=int(row['color']!='unknown')
                                if deliverable:
                                    for task,ready in readiness.items():counts[task+'_ready']+=int(ready)
                        except Exception as exc:faults.append('검수 라벨 '+reviewpath.name+': '+str(exc))
                    counts['partial']=int(bool(faults or receipt['errors'] or record.get('asset_status') in ('partial','pending')))
                    quality_state=selection['상태'] if selection and not counts['partial'] and (selection['상태']=='보류' or current_selection(selection)) else '미판정'
                    counts[{'통과':'quality_passed','보류':'quality_held','미판정':'quality_unassessed'}[quality_state]]=1
                    counts['quality_recheck_required']=int(bool(selection and selection['상태']=='통과' and not current_selection(selection) and not counts['partial']))
                    if quality_state=='통과':
                        for task in ('occupancy','number','color'):counts['selected_'+task+'_ready']=counts[task+'_ready']
                    entry={'capture_view':view_code(record),'quality_selection':quality_state,'quality_recheck_required':bool(counts['quality_recheck_required']),'id':path.stem,'lot':record['lot_id'],'time':record['captured_at_utc'],'raw_path':str(raw),
                           'session':record['session_id'],'spaces':counts['spaces'],'stickers':counts['stickers'],
                           'approved':approved,'verified_dual':verified_dual,'state':'일부 실패/손상' if counts['partial'] else receipt['status'],
                           'errors':faults+receipt['errors']}
                    self.cache[key]=(signature,counts,entry,verified_files)
                _,counts,entry,verified_files=self.cache[key]
                for k in totals:totals[k]+=counts[k]
                for category,p,size in verified_files:
                    unique_files[category].add(p)
                    if size is not None:image_bytes[p]=size
                entries.append(entry)
                problems.extend(entry['errors'])
            except Exception as exc:problems.append(path.name+': '+str(exc))
        self.cache={key:value for key,value in self.cache.items() if key in active_keys}
        for category,paths in unique_files.items():totals[category]=len(paths)
        totals['bytes']=sum(image_bytes.values())
        def capture_time(entry):
            try:
                value=datetime.fromisoformat(entry['time'])
                if value.tzinfo is None:raise ValueError('시간대 없음')
                return value.timestamp(),entry['id']
            except (ValueError,TypeError):
                problems.append(entry['id']+': 촬영 시각 형식 오류');return float('-inf'),entry['id']
        entries.sort(key=capture_time,reverse=True)
        return {'views':view_counts(entries),'counts':totals,'entries':entries,'errors':problems,'root':str(root.resolve()),'lot':lot,'session':session}


def training_pixels_hash(image):
    pixels_hash=hashlib.sha256(f'BGR8:{image.shape[1]}x{image.shape[0]}:'.encode('ascii'))
    pixels_hash.update(memoryview(image).cast('B'))
    return pixels_hash.hexdigest()


def training_split(group):
    bucket=int(hashlib.sha256(group.encode()).hexdigest()[:8],16)%100
    return 'train' if bucket<70 else 'validation' if bucket<85 else 'test'


@dataset_writer
def export_training(root,lot=None,session=None,selected_only=False,*,paired_only=False,correct_lighting=None,per_bay=False):
    """Task-specific reviewed crops; invalid input is isolated from output failure."""
    from export_integrity import ExportTransaction
    from dual_capture import info_for, require_complete_pair, label_fields
    if per_bay and not selected_only:
        raise ValueError('칸별 학습 전달에는 품질 선별이 필요합니다.')
    if correct_lighting is None:
        correct_lighting = selected_only and paired_only
    root=Path(root);target=root/'exports'/('training-'+uuid.uuid4().hex);target.mkdir(parents=True)
    with ExportTransaction(target) as transfer:
        samples=[];skipped=[];groups={};pixel_splits={};crop_splits={};pair_cache={}
        for path in sorted((root/'reviews').glob('*.json')):
            # Validate all inputs for a scene before writing any training sample.
            # A missing/corrupt source is skipped; a destination failure aborts.
            try:
                review_payload=path.read_bytes()
                review=json.loads(review_payload)
                review_sha=hashlib.sha256(review_payload).hexdigest()
                if not isinstance(review,dict):raise ValueError('검수 파일 형식 오류')
                if review.get('contract_revision')!=CONTRACT_REVISION:raise ValueError('이전 규격 재검수 필요')
                record=validate_record(read_json(root/'manifests'/path.name),root,path.stem)
                if lot and record['lot_id']!=lot:continue
                if session and record['session_id']!=session:continue
                if paired_only and info_for(record) is None:continue
                require_complete_pair(root,record,pair_cache);image=load_original(root,record)
                rows=validate_review(record,rows_for_edit(record,review),review['reviewer'],image)
                bay_holds={}
                if selected_only:
                    if record.get('asset_status')!='complete':raise ValueError('학습 선별 보류: 장면 자산 저장 미완료')
                    bundle=read_collection(root,record,True)
                    if not bundle or bundle['status']!='complete':raise ValueError('학습 선별 보류: 표시자료 미완료')
                    selection=load_selection(root,record,bundle,input_fingerprints(root,record['image_id']))
                    if not selection:raise ValueError('학습 품질 미판정: 선택 장면 저장 복구로 판정하세요.')
                    if selection['상태']!='통과':
                        if not per_bay or selection['보류사유']!=['보류 주차칸 포함']:
                            raise ValueError('학습 품질 보류: '+', '.join(selection['보류사유']))
                        from layouts import layout
                        if (record['lot_id'] not in ('A','B') or record['source']['kind'] not in ('camera','import')
                                or {r['주차칸명'] for r in selection['칸별결과']} != {s['space_id'] for s in layout(record['lot_id'])['spaces']}):
                            raise ValueError('칸별 학습 보류: 전체 칸 또는 촬영 출처 미확인')
                        bay_holds={r['주차칸명']:r['사유'] for r in selection['칸별결과'] if r['상태']=='보류'}
                    require_current_selection(selection)
                group=record['batch_id'];split=training_split(group)
                from board_profile import apply_lighting
                tone=(load_prediction(root,record) or {}).get('measurement_lighting') if correct_lighting else None
                training_image=apply_lighting(image,tone)
                slots={s['space_id']:s for s in record['roi']['spaces']};jobs=[];scene_skipped=[]
                for original,row in zip(review['spaces'],rows):
                    if row['space_id'] in bay_holds:
                        scene_skipped.append({'file':path.name,'space_id':row['space_id'],'reason':'칸 품질 보류 · '+', '.join(bay_holds[row['space_id']])})
                        continue
                    if row['review_status']!='approved':
                        if original.get('review_status')=='approved':
                            scene_skipped.append({'file':path.name,'space_id':row['space_id'],
                                'reason':'이전 승인 재검수 필요 · 점유 또는 번호와 스티커 영역 확인'})
                        continue
                    sid=row['space_id'];readiness=export_readiness(row,slots[sid])
                    if readiness['occupancy']:
                        polygon=slots[sid]['polygon']
                        crop,_,_=crop_space(training_image,polygon)
                        jobs.append(('occupancy',row['occupancy'],crop,sid,crop_space(image,polygon)[0] if tone else crop))
                    if readiness['number']:
                        x1,y1,x2,y2=map(int,row['plate_bbox'])
                        jobs.append(('number',row['display_number'],training_image[y1:y2,x1:x2],sid,image[y1:y2,x1:x2]))
                    elif row['plate_text']:scene_skipped.append({'file':path.name,'space_id':sid,'task':'number','reason':'번호 정답은 있으나 유효한 숫자 박스 없음'})
                    if readiness['color']:
                        polygon=row['sticker_polygon'];sc,_,_=crop_space(training_image,polygon)
                        jobs.append(('color',row['color'],sc,sid,crop_space(image,polygon)[0] if tone else sc))
            except (ValueError,KeyError,TypeError,OSError,cv2.error) as exc:
                skipped.append({'file':path.name,'reason':str(exc)});continue
            skipped.extend(scene_skipped)
            if not jobs:continue
            # Equal decoded images can have different PNG compression/metadata.
            # Include dimensions and pixel order; hash once per approved scene.
            pixel_id=training_pixels_hash(image)
            prior=pixel_splits.setdefault(pixel_id,(split,record['image_id'],group))
            if prior[0]!=split:
                raise ValueError(f"동일 원본 분할 누수: 같은 픽셀의 {prior[1]} ({prior[2]}/{prior[0]}) 및 {record['image_id']} ({group}/{split}). 배치를 확인하세요.")
            groups[group]=split
            for task,label,pixels,sid,raw_crop in jobs:
                relative=f"{task}/{split}/{label}/{path.stem}_{sid}.png"
                # Brightness correction can both hide and create exact crop duplicates.
                for candidate in ((pixels,raw_crop) if tone else (pixels,)):
                    crop_key=(task,candidate.shape,hashlib.sha256(candidate.tobytes()).digest())
                    previous=crop_splits.setdefault(crop_key,(split,relative,label))
                    if previous[0]!=split:
                        raise ValueError(f'동일 학습 크롭 분할 누수 · {previous[1]} / {relative}. 두 배치의 반복 촬영과 승인을 확인하세요.')
                    if previous[2]!=label:
                        raise ValueError(f'동일 학습 크롭 정답 충돌 · {previous[1]} / {relative}. 두 장면의 원본과 승인 라벨을 확인하세요.')
                digest=write_png(target/relative,pixels);transfer.verify(relative,digest)
                rgb=cv2.cvtColor(pixels,cv2.COLOR_BGR2RGB)
                verify_display_png((target/relative).read_bytes(),list(pixels.shape[1::-1]),hashlib.sha256(rgb.tobytes()).hexdigest())
                samples.append({**label_fields(record),'path':relative,'sha256':digest,'task':task,'label':label,'split':split,'group':group,
                    '주차칸종류':TYPE_NAMES[slots[sid]['space_type']],'사람검수완료':True,'검수파일_SHA256':review_sha,'적용한측정밝기보정':tone,
                    'capture_view':view_code(record),'촬영시점':VIEW_NAMES[view_code(record)],'image_id':path.stem,'lot_id':record['lot_id'],'space_id':sid,'raw_sha256':record.get('raw_sha256'),'raw_pixels_sha256':pixel_id})
        warnings=['검출용 전체 스티커 박스/차량 마스크 정답은 포함하지 않습니다.','Geti 설치판의 가져오기 호환은 별도 확인하세요.','장면이 실제로 달라졌을 때만 새 배치를 만드세요.']
        if len(set(groups.values()))<3:warnings.append('독립 학습/검증/시험 분할이 모두 확보되지 않았습니다. 독립 촬영 배치를 보강하세요.')
        report={'contract_revision':CONTRACT_REVISION,'selected_only':selected_only,'scope':{'lot':lot,'session':session},'samples':samples,'skipped':skipped,'warnings':warnings,'groups':groups}
        coverage={}
        for sample in samples:
            counts=coverage.setdefault(sample['task'],{}).setdefault(sample['label'],dict(train=0,validation=0,test=0))
            counts[sample['split']]+=1
        report['주차칸종류포함']=True
        report['과제별_라벨_분할수']=coverage
        if any(0 in counts.values() for task in coverage.values() for counts in task.values()):
            warnings.append('일부 과제·라벨에 빠진 분할이 있습니다. 과제별_라벨_분할수를 확인하세요.')
        if paired_only:report['scope']['paired_only']=True
        report['측정밝기보정사용']=bool(correct_lighting)
        if per_bay:report['선별단위']='주차칸'
        atomic_json(target/'manifest.json',report);transfer.report('manifest.json',report);transfer.finish()
        return str(target),len(samples)
