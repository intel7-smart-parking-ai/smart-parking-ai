"""Camera/inference/HTTP are separate bounded workers; no recorded-image sending."""
import threading
import logging
import os
import time
import sys
from datetime import datetime,timezone
import cv2
from collections import deque
from contextlib import contextmanager
from functools import partial
from urllib.parse import urlsplit
from .camera_hub import CameraHub,discover
from .pipeline import public_result,same_pose,empty_similarity
from .policy import (duplicate_identities,to_events,without_duplicate_identities,FIRE_EVENTS,on_vehicle_roof,
                     is_vehicle_fire,apply_bay_pins)
from .transport import EventTransport,destination


def unattended_send_allowed():
    """Opt-in for a demo PC whose camera screen may be closed (default: stop after 30s)."""
    return os.environ.get('V53_UNATTENDED_SEND','').strip()=='1'


@contextmanager
def prevent_idle_sleep():
    """Keep a running Windows capture session awake; release on the owning thread."""
    if sys.platform!='win32':
        yield None
        return
    import ctypes
    set_state=ctypes.windll.kernel32.SetThreadExecutionState
    set_state.argtypes=[ctypes.c_uint32]
    set_state.restype=ctypes.c_uint32
    continuous=0x80000000
    system_required=0x00000001
    active=bool(set_state(continuous | system_required))
    try:yield active
    finally:
        if active:set_state(continuous)


class LiveSession:
    def __init__(self,pipeline,*,discover_fn=discover,hub_factory=CameraHub,transport_factory=EventTransport,collection=None):
        self.collection=collection
        self.pipeline=pipeline;self.discover_fn=discover_fn;self.hub_factory=hub_factory
        self.transport=transport_factory();self.lock=threading.RLock();self.lifecycle=threading.RLock()
        self.stop_event=threading.Event();self.hub=None;self.worker=None;self.sender=None
        self.publication_ready=threading.Event()
        self.send_revision=0
        self.loopback_delivery=False
        self.send_stopped=None
        self.running=False;self.results={};self.errors={};self.packets={};self.review_frames={};self.revision=0
        self.review_waiters=0;self.photo_active=False
        self.review_cutoff={}
        # (lot_id, space_id) -> car number held while that bay stays occupied.
        self.bay_pins={}
        self.idle_sleep_request=None
        self.last_pause_seconds=None
        self.watchdog_stopped=False
        self.lots={'1':'auto','2':'auto'};self.threshold=.85;self.sending=False;self.last_client=time.monotonic()

    def start(self):
        with self.lifecycle:
            if self.photo_active:raise ValueError('저장 사진 분석이 끝난 뒤 카메라를 시작하세요.')
            if self.running:return self.snapshot(include_images=False)
            if self.worker and self.worker.is_alive() or self.sender and self.sender.is_alive() or self.hub and not self.hub.stopped():
                raise ValueError('이전 카메라가 종료될 때까지 기다려 주세요.')
            devices=self.discover_fn()[:2]
            if not devices:raise ValueError('Intel 카메라가 없습니다. USB 연결을 확인하세요.')
            self.pipeline.invalidate();self.transport.cancel()
            self.hub=self.hub_factory(devices);self.stop_event.clear()
            with self.lock:
                if self.collection:self.collection.begin()
                self.running=True;self.results={};self.packets={};self.review_frames={};self.errors={};self.revision+=1
                self.review_cutoff.clear();self.bay_pins.clear()
                self.last_pause_seconds=None;self.watchdog_stopped=False
                self.sending=False;self.last_client=time.monotonic()
            try:
                self.hub.start()
                self.worker=threading.Thread(target=self._infer,daemon=True,name='v53-inference');self.worker.start()
                self.sender=threading.Thread(target=self._publish,daemon=True,name='v53-1hz');self.sender.start()
            except Exception:
                self._stop()
                raise
        return self.snapshot()

    @contextmanager
    def photo_turn(self):
        with self.lifecycle:
            if self.running:raise ValueError('저장 사진 시험은 Esc로 실시간 인식을 정지한 뒤 사용하세요.')
            if self.photo_active:raise ValueError('다른 사진을 분석 중입니다.')
            self.photo_active=True
        try:
            yield
        finally:
            with self.lifecycle:self.photo_active=False

    def _infer(self):
        with prevent_idle_sleep() as active:
            with self.lock:self.idle_sleep_request=active
            try:self._infer_frames()
            finally:
                with self.lock:self.idle_sleep_request=None

    def _infer_frames(self):
        last={};completed={}
        last_turn=time.monotonic()
        resume_deadline=None
        try:
            while not self.stop_event.is_set():
                now=time.monotonic()
                if now-last_turn>30:
                    # The browser and camera threads can wake in either order.
                    # Discard pre-pause geometry/observations before admitting
                    # a new inference, even if the SDK reuses its epoch.
                    with self.lock:
                        self.last_pause_seconds=round(now-last_turn,1)
                        self.revision+=1
                        self.results.clear();self.packets.clear();self.review_frames.clear()
                        self.transport.cancel()
                    self.pipeline.invalidate()
                    last.clear();completed.clear()
                    resume_deadline=now+10
                last_turn=now
                with self.lock:
                    revision=self.revision;lots=dict(self.lots);threshold=self.threshold
                    # A running local acquisition is owned by the server. A sleeping
                    # browser tab must not stop its two USB streams or disk writer.
                    idle=self.collection is None and now-self.last_client>30
                    if (self.collection and now-self.last_client>30 and self.sending and not self.loopback_delivery
                            and not unattended_send_allowed()):
                        self.sending=False
                        self.transport.cancel()
                        self.send_stopped={'reason':'operator_screen_idle_30s',
                                           'at':datetime.now(timezone.utc).isoformat()}
                        logging.getLogger(__name__).warning(
                            'External delivery stopped: camera screen has not polled for 30s. '
                            'Set V53_UNATTENDED_SEND=1 to keep an approved destination running.')
                    reviewing=self.review_waiters>0
                if resume_deadline is not None:
                    if idle and now<resume_deadline:
                        self.stop_event.wait(.05)
                        continue
                    resume_deadline=None
                if idle:
                    with self.lock:self.watchdog_stopped=True
                    self.stop_event.set()
                    break
                if reviewing:self.stop_event.wait(.01);continue
                changed=False
                for camera in self.hub.snapshot():
                    if self.stop_event.is_set():break
                    # Give a waiting frame review the next model-lock turn.
                    # A continuous A/B loop can otherwise reacquire RLock and
                    # age a user's selected frame past its safety deadline.
                    with self.lock:
                        if self.review_waiters:break
                    packet=self.hub.latest(camera)
                    if packet is None:
                        with self.lock:
                            existed=camera in self.results;self.results.pop(camera,None);self.packets.pop(camera,None)
                            self.review_frames.pop(camera,None)
                        # Per-event validation already rejects the missing camera.
                        # Do not cancel healthy-camera deliveries or OCR work.
                        had_frame=last.pop(camera,None) is not None
                        if existed or had_frame:self.pipeline.invalidate(camera,disconnected=True)
                        continue
                    key=(packet['epoch'],packet['sequence'],revision)
                    if last.get(camera)==key:continue
                    if camera in last and last[camera][0]!=packet['epoch']:
                        # A rapid unplug/replug can occur between inference turns
                        # without the worker ever seeing a missing packet.
                        self.pipeline.invalidate(camera,disconnected=True)
                        with self.lock:
                            self.results.pop(camera,None);self.packets.pop(camera,None)
                            self.review_frames.pop(camera,None)
                    last[camera]=key;changed=True
                    # A review must see this analysis (including a failed registration)
                    # before it can change the pipeline's ROI or empty reference.
                    with self.pipeline.lock:
                        if revision != self.revision or self.stop_event.is_set():break
                        try:
                            analysis_started=datetime.now(timezone.utc).isoformat()
                            result=self.pipeline.analyze(packet['image'],camera=camera,epoch=packet['epoch'],
                                sequence=packet['sequence'],observed_at=packet['observed_at'],lot_hint=lots[camera],threshold=threshold)
                            latest=self.hub.latest(camera);now=time.monotonic()
                            result['analysis_started_at']=analysis_started
                            result['analysis_ready_at']=datetime.now(timezone.utc).isoformat()
                            result['_ready_mono']=now
                            if (self.collection and latest and latest['epoch']==packet['epoch']
                                    and revision==self.revision and not self.stop_event.is_set()):
                                with self.lock:
                                    if revision==self.revision and not self.stop_event.is_set():
                                        peers=self._fresh_results(now)
                                        peers[camera]=result
                                        archived=public_result(without_duplicate_identities(peers)[camera])
                                        self.collection.offer(camera,archived,now-packet['received']<=2)
                            if latest is None or latest['epoch']!=packet['epoch'] or now-packet['received']>2:
                                with self.lock:
                                    self.errors[camera]='새 실시간 판독 대기 · 연결 또는 프레임 시각 확인'
                                    self.results.pop(camera,None);self.packets.pop(camera,None)
                                    self.review_frames.pop(camera,None)
                                continue
                            result['analysis_fps']=round(1/(now-completed[camera]),2) if camera in completed else None
                            completed[camera]=now
                            with self.lock:
                                if revision==self.revision and not self.stop_event.is_set():
                                    peers=self._fresh_results(now);peers[camera]=result
                                    apply_bay_pins(result,self.bay_pins,duplicate_identities(peers))
                                    self.results[camera]=result;self.packets[camera]=packet;self.errors.pop(camera,None)
                                    self._remember_review(camera, result, packet)
                                    self.publication_ready.set()
                        except Exception as error:
                            logging.getLogger(__name__).exception('Camera %s analysis failed',camera)
                            with self.lock:
                                self.results.pop(camera,None);self.packets.pop(camera,None)
                                self.review_frames.pop(camera,None)
                                self.errors[camera]='분석 오류: '+type(error).__name__+' · 오류 기록을 확인하세요'
                            self.pipeline.invalidate(camera)
                if not changed:self.stop_event.wait(.03)
        finally:
            self.transport.cancel();self.hub.stop()
            with self.lock:self.running=False;self.sending=False;self.results={};self.packets={};self.review_frames={}

    def _remember_review(self, camera, result, packet):
        # Keep only frames from one uninterrupted view of the same board.
        if not result.get('registration_ok'):
            self.review_frames.pop(camera, None)
            return
        shape = packet['image'].shape
        history = deque(maxlen=4)
        for previous, source in self.review_frames.get(camera, ()):
            if (source['epoch'] == packet['epoch'] and source['image'].shape == shape
                    and previous.get('lot_id') == result.get('lot_id')
                    and same_pose(previous.get('_outline'), result.get('_outline'), shape)):
                history.append((previous, source))
        history.append((result, packet))
        self.review_frames[camera] = history

    def _fresh_results(self,now):
        # Call under self.lock. The other channel may have reconnected while
        # the inference worker is still analysing a frame.
        fresh={}
        for camera,result in self.results.items():
            packet=self.packets.get(camera)
            if packet is None or now-packet['received']>2:continue
            latest=self.hub.latest(camera) if self.hub else None
            if latest and latest['epoch']==packet['epoch']==result['stream_epoch']:
                fresh[camera]=result
        return fresh

    def _valid_event(self,event,*,source_camera=None):
        with self.lock:
            if self.stop_event.is_set() or not self.running or not self.sending:return False
            fresh=self._fresh_results(time.monotonic())
            matches=[c for c,r in fresh.items() if r.get('lot_id')==event['lot_id']]
            if len(matches)!=1 or (source_camera is not None and matches[0]!=source_camera):return False
            camera=matches[0];r=fresh[camera];packet=self.packets[camera]
            if not r.get('roi_confirmed'):return False
            prefix={'A':'camera_a','B':'camera_b'}[event['lot_id']]+':'+str(packet['epoch'])+':'
            if not isinstance(event.get('frame_id'),str) or not event['frame_id'].startswith(prefix):return False
            cutoff=self.review_cutoff.get(camera)
            if cutoff and cutoff[0]==packet['epoch']:
                seq=event['frame_id'][len(prefix):].partition(':')[0]
                if not seq.isdecimal() or int(seq)<=cutoff[1]:return False
            row=next((s for s in r['spaces'] if s['space_id']==event['space_id']),None)
            if event['event_type'] in FIRE_EVENTS:
                if is_vehicle_fire(event):
                    if not row or not row.get('sendable') or not on_vehicle_roof(row,event['bbox_xyxy']):return False
                    identity = row.get('car_id')
                    if identity in duplicate_identities(fresh):identity=None
                    if identity != event.get('car_id') or row.get('vehicle_type') != event.get('vehicle_type'):return False
                for light in r.get('fire_lights',()):
                    if light.get('state')!='candidate' and light.get('location') in ('space','boundary','aisle') and light.get('space_id')==event['space_id']:
                        x,y,u,v=event['bbox_xyxy']
                        a,b,c,d=light['bbox_xyxy']
                        if max(x,a)<min(u,c) and max(y,b)<min(v,d):
                            return True
                fire=(row or {}).get('fire_led') or {}
                seq=event['frame_id'][len(prefix):].partition(':')[0]
                return bool(row and row['sendable'] and row['observation']=='occupied' and fire.get('state')=='confirmed'
                            and 'since_seq' in fire
                            and seq.isdecimal() and fire['since_seq']<=int(seq)<=r['frame_sequence'])
            if not row or not row['sendable'] or row['observation']!=('occupied' if event['event_type']=='vehicle_observed' else 'empty'):
                return False
            if event['event_type']=='space_empty_observed' and not row.get('empty_evidence_ok'):
                return False
            car=row.get('car_id')
            if car and car in duplicate_identities(fresh):car=None
            return car==event.get('car_id') and row.get('vehicle_type')==event.get('vehicle_type')

    def _publish(self):
        sent={}
        while not self.stop_event.is_set():
            self.publication_ready.clear()
            with self.lock:
                deadline=self._offer_current(sent) if self.sending else None
            # Wake immediately for either camera. The timer also admits the
            # latest unsent steady frame when its one-second heartbeat is due.
            timeout=.5 if deadline is None else max(0,min(.5,deadline-time.monotonic()))
            self.publication_ready.wait(timeout)

    def _offer_current(self,sent):
        now=time.monotonic()
        fresh=self._fresh_results(now)
        lots=[r.get('lot_id') for r in fresh.values()]
        deadline=None
        eligible=set()
        for camera,result in without_duplicate_identities(fresh).items():
            lot=result.get('lot_id')
            if lots.count(lot)!=1:continue
            packet=self.packets[camera]
            scope=(camera,lot,packet['epoch'],self.revision,self.send_revision)
            events=to_events(result,dict(packet,source='live_camera'),{'A':'camera_a','B':'camera_b'}.get(lot))
            burning={item['space_id'] for item in events if is_vehicle_fire(item)}
            changes=[]
            heartbeats=[]
            for event in events:
                key=(scope,event['space_id'],event['event_type'] in FIRE_EVENTS)
                eligible.add(key)
                signature=(event['event_type'],is_vehicle_fire(event),event['car_id'],event['vehicle_type'],event['space_id'] in burning)
                seq=packet['sequence']
                previous=sent.get(key)
                tick=now
                if previous:
                    old_seq,old_signature,last,last_capture=previous
                    if seq==old_seq:continue
                    if signature==old_signature:
                        due=last+1
                        ready=result.get('_ready_mono')
                        tick=max(due,now-.4)
                        if ready is not None:
                            # Choose a just-completed frame near the nominal heartbeat.
                            # A timer waking between frames would send an older image.
                            fps=result.get('analysis_fps')
                            lead=min(.4,.5/fps) if fps and fps>0 else 0
                            if now-ready>.15:continue
                            if now<due-lead:
                                # The receiver measures its 2s freshness limit from capture.
                                # Reserve 250ms for transport and the next browser update.
                                if not fps or fps<=0 or now-last_capture+1/fps+.25<2:continue
                                tick=now
                        elif now<due:
                            deadline=due if deadline is None else min(deadline,due)
                            continue
                if previous and signature==previous[1]:
                    heartbeats.append(event)
                else:
                    changes.append(event)
                sent[key]=(seq,signature,tick,packet['received'])
            # Occupancy and LED heartbeats run on separate 1s timers. Sending a
            # lit car's occupancy alone lets a newer frame hide its older LED on
            # the receiver, so the same-frame mock fire always travels with it.
            chosen={id(e) for e in changes+heartbeats}
            car_fires={e['space_id']:e for e in events if is_vehicle_fire(e)}
            for event in list(changes+heartbeats):
                fire=car_fires.get(event['space_id']) if event['event_type']=='vehicle_observed' else None
                if fire is None or id(fire) in chosen:continue
                heartbeats.append(fire);chosen.add(id(fire))
                fire_key=(scope,fire['space_id'],True)
                sent[fire_key]=(packet['sequence'],(fire['event_type'],True,fire['car_id'],fire['vehicle_type'],True),
                                now,packet['received'])
            batch=changes+heartbeats
            if batch:
                try:self.transport.offer(batch,partial(self._valid_event,source_camera=camera))
                except ValueError:
                    self.sending=False
                    sent.clear()
                    return None
        for key in sent.keys()-eligible:
            del sent[key]
        return deadline
    def configure(self,threshold,lots):
        if type(threshold) not in (int,float) or not .50<=threshold<=.99 or not isinstance(lots,dict) or set(lots)!={'1','2'} or any(v not in ('auto','A','B') for v in lots.values()):
            raise ValueError('표시 기준(0.50~0.99)과 주차장 설정을 확인하세요.')
        # A frame for the new configuration must acquire the model lock only
        # after the preceding ROI, empty reference and OCR history are gone.
        with self._review_turn():
            with self.lock:
                self.transport.cancel()
                self.revision+=1;self.results={};self.packets={};self.review_frames={};self.lots=dict(lots);self.threshold=float(threshold);self.sending=False
            self.pipeline.invalidate()

    @contextmanager
    def _review_turn(self):
        with self.lock:self.review_waiters+=1
        try:
            with self.pipeline.lock:yield
        finally:
            with self.lock:self.review_waiters-=1

    def confirm(self,camera,empty=False,reviewed=None):
        # Wait for the model lock first, then recheck freshness and generation.
        # Inference publishes its evidence before releasing the model lock.
        with self._review_turn():
            with self.lock:
                result=self.results.get(camera);packet=self.packets.get(camera)
                if not result or not packet or time.monotonic()-packet['received']>(2 if empty else 6):
                    raise ValueError('현재 카메라의 새 분석 결과가 필요합니다.')
                latest=self.hub.latest(camera)
                if latest is None or latest['epoch']!=packet['epoch']:
                    raise ValueError('카메라 연결이 변경되었습니다.')
                if (not isinstance(reviewed,dict) or set(reviewed)!={'epoch','sequence','observed_at','lot_id'} or
                        type(reviewed['epoch']) is not int or type(reviewed['sequence']) is not int):
                    raise ValueError('확인한 화면의 프레임 정보가 필요합니다.')
                selected=next(((r,p) for r,p in self.review_frames.get(camera,()) if
                    p['epoch']==reviewed['epoch'] and p['sequence']==reviewed['sequence'] and
                    p['observed_at']==reviewed['observed_at'] and r.get('lot_id')==reviewed['lot_id']),None)
                if selected is None:raise ValueError('화면이 변경되었습니다. 새 화면을 확인하세요.')
                checked,source=selected
                age=time.monotonic()-source['received']
                if (age>(2 if empty else 6) or source['epoch']!=packet['epoch'] or
                    checked.get('lot_id')!=result.get('lot_id') or not result.get('registration_ok') or
                    source['image'].shape!=packet['image'].shape or
                    not same_pose(checked.get('_outline'),result.get('_outline'),packet['image'].shape)):
                    raise ValueError('확인한 화면 이후 구도나 연결이 변경되었습니다. 다시 확인하세요.')
                if empty:
                    self.pipeline.validate_empty(result)
                    if (set(checked.get('_crops',{}))!=set(result.get('_crops',{})) or
                        any(not empty_similarity(result['_crops'][s],crop) for s,crop in checked['_crops'].items())):
                        raise ValueError('확인한 빈 화면 이후 영상이 달라졌습니다. 다시 확인하세요.')
                    self.pipeline.register_empty(camera,checked,source['image'])
                else:
                    if age>2:
                        # Both USB feeds share a model lock. Approval can wait
                        # through a peer inference. Re-observe board landmarks
                        # on a fresh raw frame before accepting geometry alone.
                        pose=checked.get('_pose')
                        if not pose or time.monotonic()-latest['received']>1:
                            raise ValueError('새 프레임의 도면 고정점을 확인할 수 없습니다.')
                        registry=self.pipeline.registry
                        reference=registry.fixed_view(source['image'],pose)
                        if registry.verify_fixed_view(latest['image'],reference) is None:
                            raise ValueError('실시간 도면 고정점이 달라졌습니다. 다시 확인하세요.')
                    self.pipeline.approve_roi(camera,checked,source['image'])
                # Earlier observations from this camera still need to fail the
                # dispatch/retry validator; the other camera keeps its queue.
                self.review_cutoff[camera]=(packet['epoch'],latest['sequence'])

    def enable_send(self,enabled,simulator_stopped=False):
        if type(enabled) is not bool:raise ValueError('전송 설정 형식 오류')
        if enabled:
            base,_=destination()
        # Toggling delivery changes no ROI/model configuration. Cancelling the
        # transport generation is sufficient; changing inference revision here
        # discards an in-flight A/B pass and can open a >2s evidence gap.
        with self.lock:
            if enabled and (not self.running or self.stop_event.is_set() or not any(
                    r.get('roi_confirmed') for r in self._fresh_results(time.monotonic()).values())):
                raise ValueError('카메라 시작 후 도면 위치를 확인하세요.')
            self.transport.cancel()
            self.sending=enabled
            self.loopback_delivery=enabled and urlsplit(base).hostname in ('127.0.0.1','::1','localhost')
            self.send_stopped=None if enabled else {'reason':'operator_turned_off',
                                                    'at':datetime.now(timezone.utc).isoformat()}
            self.send_revision+=1
            self.publication_ready.set()

    def preview(self,camera,epoch=-1,sequence=-1):
        """Pull one fresh USB image without taking the inference/model lock."""
        with self.lock:
            hub=self.hub
            if not self.running or self.stop_event.is_set() or hub is None:return 'offline',None,None
            packet=hub.latest(camera)
        if packet is None:return 'offline',None,None
        key=(packet['epoch'],packet['sequence'])
        if key==(epoch,sequence):return 'unchanged',key,None
        frame=packet['image']
        h,w=frame.shape[:2]
        if w>960:frame=cv2.resize(frame,(960,max(1,round(h*960/w))),interpolation=cv2.INTER_AREA)
        ok,jpeg=cv2.imencode('.jpg',frame,[cv2.IMWRITE_JPEG_QUALITY,76])
        if not ok:return 'offline',None,None
        with self.lock:
            current=hub.latest(camera)
            if (hub is not self.hub or not self.running or self.stop_event.is_set()
                    or current is None or current['epoch']!=key[0] or time.monotonic()-packet['received']>1):
                return 'offline',None,None
        return 'frame',key,jpeg.tobytes()

    def snapshot(self,include_images=True,*,collected=False,seen=None):
        now=time.monotonic()
        with self.lock:
            self.last_client=now
            fresh=self._fresh_results(now)
            visible={c:dict(public_result(r),frame_age_ms=round((now-self.packets[c]['received'])*1000))
                     for c,r in without_duplicate_identities(fresh).items()}
            if not include_images:
                for r in visible.values():r.pop('annotated_image',None)
            return {'running':self.running,'sending':self.sending,'send_stopped':getattr(self,'send_stopped',None),
                'unattended_send':unattended_send_allowed(),'results':visible,'errors':dict(self.errors),
                'collection':self.collection.snapshot(seen or {},collected) if self.collection else None,
                'last_pause_seconds':self.last_pause_seconds,'watchdog_stopped':self.watchdog_stopped,
                'idle_sleep_request':self.idle_sleep_request,
                'cameras':self.hub.snapshot() if self.hub else {},'threshold':self.threshold,'lots':dict(self.lots),
                'transport':self.transport.snapshot(),'ocr_status':self.pipeline.numbers.status}

    def stop(self):
        with self.lifecycle:
            return self._stop()

    def _stop(self):
        self.stop_event.set();self.transport.cancel()
        self.publication_ready.set()
        with self.lock:self.sending=False;self.revision+=1;self.results={};self.packets={};self.review_frames={}
        if self.hub:self.hub.stop()
        if self.worker and self.worker.is_alive():self.worker.join(5)
        if self.sender and self.sender.is_alive():self.sender.join(2)
        self.pipeline.invalidate()
        with self.lock:self.running=False
        return self.snapshot()

    def close(self):
        self.stop();self.transport.close();self.pipeline.close()
        if self.collection:self.collection.close()
