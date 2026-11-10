"""Bounded asynchronous OCR; module name must not shadow stdlib numbers."""
from pathlib import Path
import base64
import json
import logging
import math
import os
import queue
import subprocess
import threading
import time
import cv2
import numpy as np


def signature(crop):
    return cv2.resize(cv2.GaussianBlur(crop,(3,3),0),(64,64)).astype(np.float32)


def same_sticker(a,b):
    difference=np.abs(a-b)
    return float(difference.mean())<5 and float(np.mean(difference.max(axis=2)>20))<.01


class NumberReader:
    def __init__(self, python='', models=''):
        self.python=python;self.models=models;self.status='OCR 미설정 · 번호 후보만 표시'
        # One pending job per bay, bounded to the 28 bays plus photo previews.
        # A queue of one let earlier bays starve the second camera indefinitely.
        self.jobs=queue.Queue(32);self.cache={};self.pending={};self.lock=threading.Lock()
        self.read_history={};self.ready_event=threading.Event()
        self.stop_event=threading.Event();self.process=None;self.generation=0;self.camera_generations={}
        self.key_generations={}
        self.worker=None;self.restarts=0;self.last_error=None;self.process_lock=threading.Lock()
        if python and Path(python).is_file():
            self.worker=threading.Thread(target=self._run,daemon=True,name='v53-ocr');self.worker.start()

    def _generation(self,key):
        return self.generation,self.camera_generations.get(key[0],0),self.key_generations.get(key,0)

    @property
    def ready(self):
        return bool(self.ready_event.is_set() and self.worker and self.worker.is_alive()
                    and not self.stop_event.is_set())

    def _record_reading(self,key,sig,result,frame_id):
        """Called under lock, only once for an actual completed OCR request."""
        result=dict(result);now=time.monotonic();old=self.read_history.get(key)
        number=result.get('number');score=result.get('confidence');count=0
        if (number and frame_id is not None and type(score) in (int,float)
                and math.isfinite(score) and .8<=score<=1):
            count=1
            if (old and old['number']==number and now-old['time']<=6
                    and same_sticker(sig,old['signature'])):
                count=old['count'] if old['frame']==frame_id else min(3,old['count']+1)
            self.read_history[key]={'number':number,'frame':frame_id,'count':count,'time':now,'signature':sig}
        else:
            self.read_history.pop(key,None)
        result['independent_reads']=count
        result['ocr_frame_id']=frame_id
        return result

    def invalidate(self,camera=None):
        with self.lock:
            if camera is None:
                self.generation+=1;self.cache.clear();self.pending.clear();self.read_history.clear();self.camera_generations.clear();self.key_generations.clear()
            else:
                self.camera_generations[camera]=self.camera_generations.get(camera,0)+1
                for mapping in (self.cache,self.pending,self.read_history,self.key_generations):
                    for key in list(mapping):
                        if key[0]==camera:mapping.pop(key,None)
            self._drop_queued(lambda key: camera is None or key[0] == camera)

    def _drop_queued(self, discard):
        retained=[]
        while True:
            try:job=self.jobs.get_nowait()
            except queue.Empty:break
            if not discard(job[0]):retained.append(job)
        for job in retained:self.jobs.put_nowait(job)

    def retain_keys(self, camera, active):
        """A bay must earn new reads after its single visible OCR sticker disappears."""
        with self.lock:
            lost = {key for key in self.cache.keys() | self.pending.keys() | self.read_history.keys()
                    if key[0] == camera and key not in active}
            if not lost:return
            for key in lost:
                self.cache.pop(key, None)
                self.read_history.pop(key, None)
                if self.pending.pop(key, None) is not None:
                    self.key_generations[key] = self.key_generations.get(key, 0) + 1
            self._drop_queued(lost.__contains__)

    def _complete(self, key, sig, generation, result, frame_id):
        with self.lock:
            if self.pending.get(key) != generation:
                return False
            self.pending.pop(key)
            reading=self._record_reading(key,sig,result,frame_id)
            self.cache[key]=(time.monotonic(),sig,reading)
            if len(self.cache)>64:
                expired=next(iter(self.cache));self.cache.pop(expired);self.read_history.pop(expired,None)
            return True

    def query(self, key, crop, *, frame_id=None):
        if self.worker is None or not self.worker.is_alive() or self.stop_event.is_set():
            return None
        now=time.monotonic();sig=signature(crop)
        with self.lock:
            entry=self.cache.get(key)
            current=entry and now-entry[0]<4 and same_sticker(sig,entry[1])
            if current and now-entry[0]<2:return entry[2]
            # Refresh before expiry, but only return an unexpired result whose
            # pixels still match the currently observed sticker.
            value=entry[2] if current else None
            if not current:
                self.cache.pop(key,None)
                if entry and not same_sticker(sig,entry[1]):self.read_history.pop(key,None)
            if key in self.pending:
                return value
            try:
                generation=self._generation(key)
                self.jobs.put_nowait((key,crop.copy(),sig,generation,frame_id));self.pending[key]=generation
            except queue.Full:pass
        return value

    def has_pending(self,camera):
        with self.lock:return any(key[0]==camera for key in self.pending)

    def _run(self):
        while not self.stop_event.is_set():
            try:
                self._run_once()
                return
            except Exception as error:
                if self.stop_event.is_set():return
                self.last_error=type(error).__name__
                logging.getLogger(__name__).warning('OCR worker failed: %s',self.last_error)
                self.invalidate()
                if self.restarts>=2:
                    self.status='OCR 복구 실패 · 번호 null · 프로그램 재시작 필요'
                    return
                self.restarts+=1
                self.status=f'OCR 재시작 {self.restarts}/2 · 번호 null 유지'
                if self.stop_event.wait(.5*self.restarts):return

    def _run_once(self):
        env=os.environ.copy();env['PYTHONUTF8']='1';env['PYTHONUNBUFFERED']='1'
        if self.models:env['V53_OCR_MODELS']=self.models
        try:
            self.status='OCR 준비 중'
            with self.process_lock:
                if self.stop_event.is_set():return
                self.process=subprocess.Popen([self.python,str(Path(__file__).with_name('ocr_worker.py'))],
                    stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True,encoding='utf-8',env=env,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
                process=self.process
            lines=queue.Queue(2)
            def read_lines():
                try:
                    for line in process.stdout:
                        try:lines.put(line,timeout=1)
                        except queue.Full:break
                except (ValueError,OSError):pass
                try:lines.put('',timeout=1)
                except queue.Full:pass
            threading.Thread(target=read_lines,daemon=True,name='v53-ocr-output').start()
            if not json.loads(lines.get(timeout=45)).get('ready'):
                raise RuntimeError('OCR init')
            self.status='OCR 준비 완료'
            self.ready_event.set()
            while not self.stop_event.is_set():
                if process.poll() is not None:raise RuntimeError('OCR process exited')
                try:key,crop,sig,generation,frame_id=self.jobs.get(timeout=.2)
                except queue.Empty:continue
                with self.lock:
                    if generation!=self._generation(key):continue
                _,encoded=cv2.imencode('.png',crop)
                self.process.stdin.write(json.dumps({'id':str(key),'image':base64.b64encode(encoded).decode()})+'\n')
                self.process.stdin.flush()
                response=json.loads(lines.get(timeout=10))
                if response.get('id')!=str(key) or 'result' not in response:
                    raise RuntimeError('OCR response protocol')
                self._complete(key,sig,generation,response['result'],frame_id)
        finally:
            self.ready_event.clear()
            self._terminate()

    def _terminate(self):
        with self.process_lock:
            process=self.process
            if process is None:return
            if process.poll() is None:
                process.terminate()
                try:process.wait(3)
                except subprocess.TimeoutExpired:process.kill();process.wait(3)
            for stream in (process.stdin,process.stdout):
                if stream is not None:
                    try:stream.close()
                    except (OSError,ValueError):pass

    def close(self):
        self.stop_event.set();self._terminate()
        if self.worker:self.worker.join(4)
