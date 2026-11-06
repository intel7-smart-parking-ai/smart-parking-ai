"""Durable originals and pending OCR. Analysis never throttles capture."""
import copy
import hashlib
import csv
import json
import logging
import os
import queue
import re
import threading
import time
import uuid
from pathlib import Path
from dataset_lock import dataset_writer
import cv2
import numpy as np
from parking_ai.storage import atomic_json, safe_path
from analysis import analyze, make_reader
from contracts import CONTRACT_REVISION, number_config_for_lot
from records import capture_source,validate_capture,validate_record,validate_analysis,load_original
from multi_vehicle import summarize,FRAME_REVISION
from quality import image_quality
from capture_views import view_code,view_folder


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


@dataset_writer
def save_capture(root,packet,roi,batch,session,config,scene_assets=False,identifier=None,prediction_expected=False,prediction_snapshot=None):
    root=Path(root)
    validate_capture(packet,roi)
    if identifier is None:identifier=uuid.uuid4().hex
    elif not isinstance(identifier,str) or not re.fullmatch(r'[a-f0-9]{32}',identifier):raise ValueError('예약된 사진 ID 형식 오류')
    raw=root/'raw'/packet.camera_id/view_folder(config)/(identifier+'.png')
    raw.parent.mkdir(parents=True,exist_ok=True)
    ok,encoded=cv2.imencode('.png',packet.image)
    if not ok: raise OSError('PNG encoding failed')
    encoded_bytes=encoded.tobytes()
    source=capture_source(packet,config)
    if 'capture_source' in config:config={**config,'capture_source':source}
    record={'image_id':identifier,'camera_id':packet.camera_id,'serial':packet.serial,'lot_id':roi['lot_id'],'lot_name':roi['lot_name'],
        'source':source,'capture_view':view_code(config),
        'frame_id':packet.sequence,'captured_at_utc':packet.timestamp_utc,'batch_id':batch,'session_id':session,
        'raw_path':raw.relative_to(root).as_posix(),'raw_sha256':hashlib.sha256(encoded_bytes).hexdigest(),'image_size':list(packet.image.shape[1::-1]),
        'contract_revision':CONTRACT_REVISION,'roi':copy.deepcopy(roi),'analysis_config':copy.deepcopy(number_config_for_lot(roi['lot_id'],config)),'label_status':'unreviewed',
        'quality_advisory':image_quality(packet.image),'camera_options':copy.deepcopy(packet.camera_options),
        'stream_epoch':packet.stream_epoch,'analysis_status':'pending'}
    if getattr(packet,'sensor_frame',None) is not None:record['sensor_frame']=copy.deepcopy(packet.sensor_frame)
    if scene_assets:record.update(asset_schema='scene-assets-v1',asset_status='pending',collection_schema='korean-collection-v2',collection_status='pending')
    if prediction_expected:record['prediction_status']='pending'
    validate_record(record,root,identifier)
    path=root/'manifests'/(identifier+'.json')
    from capture_recovery import prepare_journal,finish_journal
    if path.exists():raise ValueError('이미 등록된 사진 ID입니다. 기존 장면 저장 경로로 다시 시도하세요.')
    prepare_journal(root,record,prediction_snapshot)
    try:
        if raw.exists():
            # An interrupted initial save may have committed the original only.
            # Never replace it; resume only with exactly the same encoded pixels.
            if raw.read_bytes()!=encoded_bytes:raise ValueError('중단된 촬영의 원본과 재시도 픽셀이 다릅니다. 원본을 보존합니다.')
        else:
            with raw.open('xb') as f:
                f.write(encoded_bytes);f.flush();os.fsync(f.fileno())
        atomic_json(path,record)
    except Exception:
        # Preserve both complete and partial originals for verified recovery.
        raise
    if not scene_assets:finish_journal(root,identifier)
    return path


class Writer(threading.Thread):
    def __init__(self,root):
        super().__init__(daemon=False)
        self.root=root;self.jobs=queue.Queue(2);self.results=queue.Queue()
    def run(self):
        while True:
            job=self.jobs.get()
            if job is None: return
            try:
                path=save_capture(self.root,*job)
                self.results.put((job[0].camera_id,str(path),None))
            except Exception as exc:
                logging.exception('Save failed')
                self.results.put((job[0].camera_id,None,str(exc)))


class Analyzer(threading.Thread):
    def __init__(self,project,root,config):
        super().__init__(daemon=False)
        self.project=project;self.root=root;self.config=config;self.stop_event=threading.Event()
        self.retry_requested=threading.Event()
        self.status='Loading OCR';self.latest=queue.Queue(2);self.reader=None;self.engine=None
    from dataset_lock import dataset_method_writer
    @dataset_method_writer
    def process(self,path):
        record=validate_record(read_json(path),self.root,path.stem)
        image=load_original(self.root,record)
        effective=number_config_for_lot(record['lot_id'],record['analysis_config'])
        rows=analyze(image,record['roi']['spaces'],self.reader,effective)
        result={'contract_revision':CONTRACT_REVISION,'number_mode':effective['number_mode'],'image_id':record['image_id'],'camera_id':record['camera_id'],'captured_at_utc':record['captured_at_utc'],
            'lot_id':record['lot_id'],'lot_name':record['lot_name'],'engine':self.engine,'review_status':'pending','spaces':rows,'frame_revision':FRAME_REVISION,'frame_summary':summarize(rows)}
        validate_analysis(result,record)
        atomic_json(self.root/'analysis'/(path.stem+'.json'),result)
        record.pop('analysis_error',None)
        record['analysis_status']='complete'
        record['analysis_revision']=CONTRACT_REVISION
        atomic_json(path,record)
        try:self.latest.put_nowait((record,result))
        except queue.Full:
            # UI may drain this queue between the full check and removal.
            try:self.latest.get_nowait()
            except queue.Empty:pass
            self.latest.put_nowait((record,result))
    def request_retry_errors(self):
        self.retry_requested.set()

    def run(self):
        try: self.reader,self.engine=make_reader(self.project,self.config)
        except Exception as exc:
            logging.exception('OCR startup failed')
            self.status='OCR unavailable: '+str(exc);return
        while not self.stop_event.is_set():
            pending=[]
            retry=self.retry_requested.is_set();self.retry_requested.clear()
            for path in sorted((self.root/'manifests').glob('*.json')):
                try:
                    record=validate_record(read_json(path),self.root,path.stem)
                    if needs_analysis(record,retry,self.root/'analysis'/path.name): pending.append(path)
                except Exception:
                    logging.exception('Invalid manifest %s',path)
            self.status=f'OCR {self.engine["device"]} {self.engine.get("gpu") or ""}: pending={len(pending)} | {self.engine.get("fallback_reason") or ""}'
            for path in pending:
                if self.stop_event.is_set(): return
                try: self.process(path)
                except Exception as exc:
                    logging.exception('Analysis failed %s',path)
                    try:
                        from dataset_lock import dataset_write
                        with dataset_write(self.root):
                            record=read_json(path);record.update(analysis_status='error',analysis_error=str(exc));atomic_json(path,record)
                    except Exception:logging.exception('Could not record analysis failure %s',path)
                    self.status='Analysis error: '+str(exc)
            self.stop_event.wait(1.)


@dataset_writer
def export_csv(root):
    from dual_capture import require_complete_pair
    root=Path(root);rows=[];errors=[];pair_cache={}
    for path in sorted((root/'manifests').glob('*.json')):
        try:
            record=validate_record(read_json(path),root,path.stem)
            require_complete_pair(root,record,pair_cache)
        except (OSError,ValueError,TypeError) as exc:
            errors.append({'file':str(path),'reason':str(exc)});continue
        result_path=root/'analysis'/path.name;by_id={};state=record.get('analysis_status','pending')
        if result_path.exists():
            try:
                result=read_json(result_path)
                if result.get('contract_revision')==CONTRACT_REVISION:
                    validate_analysis(result,record);by_id={r['space_id']:r for r in result['spaces']}
                else:state='needs_current_analysis'
            except (OSError,ValueError,TypeError,AttributeError) as exc:
                errors.append({'file':str(result_path),'reason':str(exc)});state='invalid_analysis'
        elif state=='complete':state='missing_analysis'
        for registered in record['roi']['spaces']:
            slot=by_id.get(registered['space_id'],{'space_id':registered['space_id'],'number_candidate':None,'color_candidate':'unknown'})
            virtual=registered.get('boundary_kind')=='virtual_open_u'
            rows.append({'image_id':path.stem,'camera_id':record['camera_id'],'batch_id':record['batch_id'],
                'raw_path':record['raw_path'],'lot_id':record['lot_id'],'lot_name':record['lot_name'],
                'source_kind':record['source']['kind'],'space_type':registered['space_type'],'analysis_status':state,
                'space_id':slot['space_id'],'number_candidate':slot['number_candidate'],
                'color_status':slot.get('color_status','sticker_region_required'),'color_candidate':slot['color_candidate'],
                'vehicle_type_candidate':slot.get('vehicle_type_candidate'),
                'position_basis':'virtual_guide' if virtual else 'registered_bay',
                'space_label':('가상 배치 ' if virtual else record['lot_name']+' 주차장 ')+slot['space_id'],'occupancy':'unknown','review_status':'pending'})
    target=root/'candidates.csv';root.mkdir(parents=True,exist_ok=True)
    temp=root/('export-'+uuid.uuid4().hex+'.tmp')
    fields=['image_id','camera_id','batch_id','raw_path','lot_id','lot_name','source_kind','space_type','analysis_status','space_id','number_candidate','color_status','color_candidate','vehicle_type_candidate','space_label','occupancy','review_status','position_basis']
    try:
        with temp.open('x',encoding='utf-8-sig',newline='') as f:
            writer=csv.DictWriter(f,fieldnames=fields);writer.writeheader();writer.writerows(rows)
        temp.replace(target)
    finally:temp.unlink(missing_ok=True)
    atomic_json(root/'candidates.report.json',{'rows':len(rows),'errors':errors})
    return target


def needs_analysis(record,retry_errors=False,result_path=None):
    status=record.get('analysis_status')
    if status=='pending' or (retry_errors and status=='error'):return True
    if status!='complete':return False
    if record.get('analysis_revision')!=CONTRACT_REVISION:return True
    if result_path is not None:
        try:
            result=read_json(result_path)
            if result.get('contract_revision')!=CONTRACT_REVISION or result.get('frame_revision')!=FRAME_REVISION:return True
            validate_analysis(result,record)
        except (OSError,ValueError,TypeError,AttributeError):return True
    return False
