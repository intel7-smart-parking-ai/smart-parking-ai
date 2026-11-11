"""Bounded HTTP delivery, same-ID retry, cancellation, and no token in UI/logs."""
from concurrent.futures import ThreadPoolExecutor
from collections import Counter, deque
from datetime import datetime,timezone
from http.client import IncompleteRead
from urllib.parse import urlsplit
import urllib.request
import urllib.error
import json
import os
import queue
import threading
import time
from . import clock
from .policy import CATALOG, valid_score, FIRE_EVENTS, is_vehicle_fire, wire_event


# Safe server error codes kept in delivery statistics; anything else is UNSPECIFIED.
KNOWN_REJECTION_CODES = frozenset({
    'SESSION_IDENTITY_CONFLICT', 'ACTIVE_SESSION_EXISTS', 'SESSION_EXIT_REVIEW', 'SESSION_ID_CONFLICT',
    'SESSION_ALREADY_CLOSED', 'EVENT_ID_CONFLICT', 'VALIDATION_ERROR', 'EXPERIMENT_STORE_FULL',
    'SESSION_WRITE_BUSY',
})


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):
        return None


def destination():
    base=os.environ.get('API_BASE_URL','').rstrip('/')
    token=os.environ.get('VISION_TOKEN','')
    p=urlsplit(base)
    try:port=p.port
    except ValueError:raise ValueError('API_BASE_URL의 포트 번호를 확인하세요.') from None
    if (p.scheme not in ('http','https') or not p.hostname or p.username or p.password or p.query or p.fragment or
            p.path not in ('','/') or port==0 or not token or any(ord(c)<32 for c in base+token)):
        raise ValueError('API_BASE_URL과 VISION_TOKEN 환경변수를 설정한 뒤 프로그램을 다시 시작하세요.')
    return base,token


class EventTransport:
    # Wait for an in-flight acknowledgement without re-stamping its evidence.
    # New dispatch/retry still has the separate two-second freshness limit.
    response_timeout=4.0
    max_stable_in_flight=3
    def __init__(self):
        self.jobs=queue.Queue(1);self.stop_event=threading.Event();self.lock=threading.RLock()
        self.generation=0;self.counts={};self.last_status='전송 꺼짐';self.batch_offset=0
        self.recent=deque(maxlen=2048)
        self.minute=deque()
        self.reasons=Counter()
        self.http=threading.local()
        # One bounded slot per physical bay; do not serialize the second half
        # of a 28-bay frame behind the first wave of HTTP responses.
        self.max_workers=28*self.max_stable_in_flight
        self.worker=threading.Thread(target=self._run,daemon=True,name='v53-http');self.worker.start()

    def _record(self,outcome,event,info=None):
        info=info or {}
        with self.lock:
            self.counts[outcome]=self.counts.get(outcome,0)+1
            self.last_status=outcome
            second=int(time.monotonic())
            if not self.minute or self.minute[-1][0]!=second:
                self.minute.append((second,Counter(),Counter()))
            while self.minute[0][0]<=second-60:self.minute.popleft()
            self.minute[-1][1][outcome]+=1
            if info.get('reason'):
                key=outcome+'/'+info['reason']
                self.minute[-1][2][key]+=1
                self.reasons[key]+=1
            self.recent.append(dict(at=datetime.now(timezone.utc).isoformat(),outcome=outcome,**info,
                **{key:event.get(key) for key in ('event_id','event_type','lot_id','space_id','frame_id','observed_at')}))

    def recent_outcomes(self):
        with self.lock:return list(self.recent)

    def cancel(self):
        with self.lock:
            self.generation+=1
            try:
                old=self.jobs.get_nowait()
                for args in old.values():self._record('cancelled_pending',args[0])
            except queue.Empty:pass

    def offer(self,events,validator):
        base,token=destination()
        with self.lock:
            if self.stop_event.is_set():
                raise ValueError('종료된 송신기에는 새 관측을 추가할 수 없습니다.')
            # An observation and its capture validator travel together, including
            # when a second camera adds different bays to the waiting batch.
            job={}
            for e in events:
                key=(e['lot_id'],e['space_id'])
                if e.get('event_type') in FIRE_EVENTS:
                    key+=('led',)
                job[key]=(e,validator,base,token,self.generation)
            try:
                previous=self.jobs.get_nowait()
                # Per-camera publishing must not replace the other camera's
                # waiting observations. Coalesce only the same lot/bay.
                context=(base,token,self.generation)
                retained={key:args for key,args in previous.items() if key not in job and args[2:]==context}
                for key,args in previous.items():
                    if key not in retained:self._record('queue_replaced',args[0])
                job={**retained,**job}
            except queue.Empty:pass
            self.jobs.put_nowait(job)

    def send_one(self,event,validator,base,token,generation):
        if not valid_score(event.get('confidence')):
            return 'invalid_confidence'
        try:
            # A worker handles one request at a time. Rebuilding default TLS
            # and proxy handlers for every bay delays a whole frame on Windows.
            local=urlsplit(base).hostname in ('127.0.0.1','localhost','::1')
            if getattr(self.http,'local',None)!=local:
                handlers=[NoRedirect()]
                if local:handlers.append(urllib.request.ProxyHandler({}))
                self.http.opener=urllib.request.build_opener(*handlers)
                self.http.local=local
            opener=self.http.opener
            payload=json.dumps(wire_event(event),allow_nan=False).encode()
        except (TypeError,ValueError):return 'invalid_event'
        unconfirmed=False
        for attempt in range(2):
            try:
                stamp=datetime.fromisoformat(event['observed_at'])
                if stamp.tzinfo is None:return 'invalid_time'
                # The camera validator can wait for the live lock. Check age
                # and cancellation after that wait, immediately before HTTP.
                evidence_valid=validator(event)
                with self.lock:
                    valid=generation==self.generation and not self.stop_event.is_set()
                dispatched_at=datetime.now(timezone.utc)+clock.delta()
                age=(dispatched_at-stamp).total_seconds()
                if not valid or not -.5<=age<=2 or not evidence_valid:
                    return 'delivery_unconfirmed' if unconfirmed else 'withheld_or_expired'
                req=urllib.request.Request(base+'/api/v1/internal/vision-events',data=payload,
                    headers={'Authorization':'Bearer '+token,'Content-Type':'application/json',
                             'X-Vision-Review-Receipt':'1',
                             'X-Vision-Dispatched-At':dispatched_at.isoformat()},method='POST')
                self.http.trace={'dispatched_at':dispatched_at.isoformat(),'attempt':attempt+1}
                with opener.open(req,timeout=self.response_timeout) as response:
                    if response.status in (200,202):
                        expected='accepted' if response.status==202 else 'duplicate'
                        if response.length is not None and response.length>4096:
                            return 'delivery_unconfirmed'
                        body=response.read(4097)
                        # read(n) can return a short, valid-looking JSON prefix
                        # on early EOF without raising IncompleteRead.
                        if len(body)>4096 or response.length not in (None,0):
                            return 'delivery_unconfirmed'
                        try:receipt=json.loads(body)
                        except (ValueError,UnicodeDecodeError,RecursionError):return 'delivery_unconfirmed'
                        if (isinstance(receipt,dict) and receipt.get('event_id')==event['event_id']
                                and receipt.get('disposition')==expected):
                            review=receipt.get('session_review')
                            if review is not None:
                                if expected!='accepted' or review not in ('SESSION_IDENTITY_CONFLICT','ACTIVE_SESSION_EXISTS','SESSION_EXIT_REVIEW'):
                                    return 'delivery_unconfirmed'
                                self.http.trace['reason']=review
                                return 'session_review'
                            return expected
                        return 'delivery_unconfirmed'
                    return 'delivery_unconfirmed' if unconfirmed else 'HTTP_'+str(response.status)
            except urllib.error.HTTPError as e:
                # A failed retry cannot disprove storage of an earlier request
                # whose acknowledgement disappeared.
                with e:
                    if e.code in (409,422,503):
                        # Keep only known diagnostic codes. A remote message may echo private input.
                        try:
                            detail=json.loads(e.read(4097)).get('error',{})
                            code=detail.get('code')
                            if code not in KNOWN_REJECTION_CODES:
                                code='UNSPECIFIED'
                        except (AttributeError,TypeError,ValueError,OSError,IncompleteRead):
                            code='UNSPECIFIED'
                        self.http.trace['reason']=code
                if e.code<500 or attempt:
                    return 'delivery_unconfirmed' if unconfirmed else 'HTTP_'+str(e.code)
            except (urllib.error.URLError,TimeoutError,OSError,IncompleteRead):
                # A timeout/disconnect does not prove that the receiver failed
                # to store the request. Preserve that uncertainty in counters.
                unconfirmed=True
                if attempt:
                    self.http.trace['reason']='NETWORK'
                    return 'delivery_unconfirmed'
            except (ValueError,KeyError,TypeError):return 'invalid_event'
        return 'network_error'

    def _deliver(self,event,validator,base,token,generation):
        try:return self.send_one(event,validator,base,token,generation)
        except Exception:return 'delivery_error'

    def _deliver_traced(self,*args):
        self.http.trace={}
        return self._deliver(*args),self.http.trace

    @staticmethod
    def _allow_overlap(previous, current, dispatched_at):
        """Only identical, successive periodic observations may overlap an outstanding ACK."""
        old, _, base, token, generation=previous
        event=current[0]
        if current[2:]!=(base,token,generation) or time.monotonic()-dispatched_at<.5:
            return False
        if event.get('event_type') not in ('vehicle_observed','space_empty_observed'):
            return False
        car=event.get('car_id')
        if event['event_type']=='vehicle_observed':
            if not isinstance(car,str) or car not in CATALOG or CATALOG[car]!=event.get('vehicle_type'):
                return False
        elif car is not None or event.get('vehicle_type') is not None:
            return False
        for field in ('source','camera_id','event_type','car_id','vehicle_type','bbox_xyxy'):
            if event.get(field)!=old.get(field):return False
        if not all(valid_score(e.get('confidence')) and e['confidence']>=.8 for e in (event,old)):
            return False
        try:
            # The final component is an ISO timestamp containing colons.
            def frame(e):
                camera,epoch,seq,_=e['frame_id'].split(':',3)
                return camera,epoch,int(seq),datetime.fromisoformat(e['observed_at'])
            cam,epoch,seq,stamp=frame(event)
            old_cam,old_epoch,old_seq,old_stamp=frame(old)
            return (cam==old_cam==event['camera_id'] and epoch==old_epoch and seq>old_seq
                    and event['event_id']!=old['event_id'] and (stamp-old_stamp).total_seconds()>=.5)
        except (AttributeError,KeyError,TypeError,ValueError):
            return False

    def _run(self):
        pending={};running={};active={}
        with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
            while not self.stop_event.is_set() or running or pending:
                for future,key in list(running.items()):
                    if future.done():
                        args,_=active[key].pop(future)
                        outcome,info=future.result()
                        self._record(outcome,args[0],info);running.pop(future)
                        if not active[key]:del active[key]
                try:batch=self.jobs.get(timeout=.02)
                except queue.Empty:batch={}
                for key,args in batch.items():
                    if key in pending:self._record('queue_replaced',pending[key][0])
                    pending[key]=args
                # No batch-wide barrier: a slow bay must not hold every other
                # bay. Transitions, reconnects and repeated IDs wait for all
                # outstanding acknowledgements; job memory remains bounded.
                # Send a vehicle-mounted alarm before routine heartbeat observations.
                ordered=sorted(pending.items(),key=lambda item:not is_vehicle_fire(item[1][0]))
                for key,args in ordered:
                    with self.lock:valid=args[4]==self.generation and not self.stop_event.is_set()
                    if not valid:
                        pending.pop(key);self._record('cancelled_pending',args[0]);continue
                    if len(running)>=self.max_workers:continue
                    # Occupancy and its fire observation are separate POSTs.
                    # Allow a currently dispatching same-frame alarm to reach
                    # the monitor first; no other bay waits for this receipt.
                    if args[0].get('event_type')=='vehicle_observed':
                        alarms=active.get((*key,'led'),{})
                        if any(self._wait_for_frame_alarm(args,alarm,started)
                               for alarm,started in alarms.values()):
                            continue
                    peers=active.get(key,{})
                    if peers:
                        if len(peers)>=self.max_stable_in_flight:continue
                        prior,started=next(reversed(peers.values()))
                        if not self._allow_overlap(prior,args,started):continue
                    pending.pop(key)
                    future=pool.submit(self._deliver_traced,*args)
                    running[future]=key
                    active.setdefault(key,{})[future]=(args,time.monotonic())

    @staticmethod
    def _wait_for_frame_alarm(occupancy, alarm, started):
        event, fire=occupancy[0],alarm[0]
        return (is_vehicle_fire(fire)
                and event.get('frame_id') is not None and fire.get('frame_id')==event['frame_id']
                and occupancy[2:]==alarm[2:] and time.monotonic()-started<.10)

    def snapshot(self):
        with self.lock:
            cutoff=int(time.monotonic())-60
            counts=Counter()
            reasons=Counter()
            for second,values,details in self.minute:
                if second>cutoff:
                    counts.update(values)
                    reasons.update(details)
            return {'counts':dict(self.counts),'recent_counts':dict(counts),'reasons':dict(self.reasons),
                    'recent_reasons':dict(reasons),'last_status':self.last_status,'worker_alive':self.worker.is_alive()}

    def close(self):
        with self.lock:
            self.stop_event.set()
            self.cancel()
        self.worker.join(5)
