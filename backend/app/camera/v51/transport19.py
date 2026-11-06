"""Optional bounded HTTP transport. Off until the team contract is configured."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timezone
import json
import os
import queue
import threading
import time
import urllib.request
import urllib.error
from urllib.parse import urlparse
import uuid


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        return None


def build_events(packet,roi,prediction,config):
    from vision19 import context_key
    from prediction_contract import validate_prediction
    if not config.get('api_contract_confirmed'):raise ValueError('팀 API의 confidence/bbox null 허용·camera_id 매핑을 확인한 뒤 설정하세요.')
    if packet.camera_id=='IMPORT' or packet.source_kind!='camera' or config.get('capture_source',{}).get('kind','camera')!='camera':raise ValueError('사진·녹화·합성 입력은 실제 카메라 관측으로 송신하지 않습니다.')
    from records import validate_capture
    validate_capture(packet,roi)
    validate_prediction(prediction,{'roi':roi,'frame_id':packet.sequence,'captured_at_utc':packet.timestamp_utc,
                                   'lot_id':roi['lot_id'],'stream_epoch':packet.stream_epoch,'image_size':list(packet.image.shape[1::-1])})
    if roi.get('registration_method')=='virtual_sticker_guide_v1':return []
    if prediction['frame_id']!=packet.sequence or prediction['captured_at_utc']!=packet.timestamp_utc:raise ValueError('전송 장면 불일치')
    if prediction.get('context_key')!=context_key(roi) or prediction.get('stream_epoch')!=packet.stream_epoch or prediction.get('lot_id')!=roi['lot_id']:raise ValueError('전송 위치/연결 구간 불일치')
    source_time=datetime.fromisoformat(packet.timestamp_utc)
    if source_time.tzinfo is None:raise ValueError('관측 시각에 시간대가 필요합니다.')
    age=(datetime.now(timezone.utc)-source_time).total_seconds()
    if age<-.5 or age>config.get('event_max_age_seconds',2.):raise ValueError('오래되거나 미래 시각인 관측은 전송하지 않습니다.')
    camera=config.get('api_camera_ids',{}).get(packet.camera_id)
    if not camera:raise ValueError('API camera_id 매핑이 필요합니다.')
    if not config.get('api_nullable_bbox') or not config.get('api_nullable_confidence'):raise ValueError('차량 검출 박스가 없는 관측의 null 계약 확인이 필요합니다.')
    events=[]
    for row in prediction['spaces']:
        if row['occupancy'] not in ('occupied','vacant'):continue
        occupied=row['occupancy']=='occupied'
        events.append({'event_id':str(uuid.uuid4()),'event_type':'vehicle_observed' if occupied else 'space_empty_observed',
                       'source':'vision','lot_id':roi['lot_id'],'camera_id':camera,'space_id':row['space_id'],
                       'car_id':row['number'] if occupied else None,
                       'vehicle_type':{'electric':'ev','general':'general','accessible':'accessible'}.get(row['vehicle_type']) if occupied else None,
                       'confidence':None,'bbox_xyxy':None,'observed_at':packet.timestamp_utc,
                       'frame_id':f"{packet.camera_id}:{packet.stream_epoch}:{packet.timestamp_utc}:{packet.sequence}"})
    return events


class EventSender(threading.Thread):
    def __init__(self):
        super().__init__(daemon=False);self.jobs=queue.Queue(1);self.results=queue.Queue();self.stop_event=threading.Event()
        self.lock=threading.RLock();self.generation=0
    def cancel(self):
        """Invalidate queued/retry tasks. An HTTP request already in flight cannot be recalled."""
        with self.lock:
            self.generation+=1
            while True:
                try:self.jobs.get_nowait()
                except queue.Empty:break
            return self.generation
    def current(self,generation):
        with self.lock:return not self.stop_event.is_set() and generation==self.generation
    def offer(self,events,max_age):
        with self.lock:
            if self.stop_event.is_set():return
            job=(events,max_age,self.generation)
            try:self.jobs.put_nowait(job)
            except queue.Full:
                try:self.jobs.get_nowait()
                except queue.Empty:pass
                self.jobs.put_nowait(job)
    def send_one(self,event,max_age,base,token,generation=None):
        if generation is None:
            with self.lock:generation=self.generation
        body=json.dumps(event,allow_nan=False).encode()
        opener=urllib.request.build_opener(NoRedirect())
        for attempt in range(2):
            age=(datetime.now(timezone.utc)-datetime.fromisoformat(event['observed_at'])).total_seconds()
            if not self.current(generation) or age>max_age:return 'expired'
            request=urllib.request.Request(base+'/api/v1/internal/vision-events',data=body,
                headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'},method='POST')
            try:
                with opener.open(request,timeout=1.5) as response:
                    return 'accepted' if response.status==202 else 'HTTP '+str(response.status)
            except urllib.error.HTTPError as exc:
                if exc.code<500 or attempt: return 'HTTP '+str(exc.code)
            except (urllib.error.URLError,TimeoutError,OSError):
                if attempt:return 'network_error'
        return 'network_error'
    def run(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            while not self.stop_event.is_set():
                try:events,max_age,generation=self.jobs.get(timeout=.2)
                except queue.Empty:continue
                if not self.current(generation):continue
                base=os.environ.get('API_BASE_URL','').rstrip('/');token=os.environ.get('VISION_TOKEN','')
                parsed=urlparse(base)
                if parsed.scheme not in ('http','https') or not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment or not token:
                    self.results.put({'error':'API_BASE_URL / VISION_TOKEN 설정 확인'});continue
                counts={}
                for value in pool.map(lambda e:self.send_one(e,max_age,base,token,generation),events):counts[value]=counts.get(value,0)+1
                if self.current(generation):self.results.put(counts)
