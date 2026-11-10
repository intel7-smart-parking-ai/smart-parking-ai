"""Single owner of RealSense pipelines; one latest frame per camera, no disk writes."""
from datetime import datetime, timezone
import threading
import time
import numpy as np

MAX_FRAME_AGE = 1.0


def discover():
    import pyrealsense2 as rs
    result=[]
    for device in rs.context().query_devices():
        name=device.get_info(rs.camera_info.name)
        if 'platform camera' in name.lower():continue
        result.append({'serial':device.get_info(rs.camera_info.serial_number),'name':name})
    return sorted(result,key=lambda item:item['serial'])


class CameraHub:
    def __init__(self, devices, width=1920, height=1080, fps=30):
        self.devices=devices;self.width=width;self.height=height;self.fps=fps
        self.lock=threading.Lock();self.stop_event=threading.Event();self.threads=[]
        self.frames={};self.states={str(i+1):'연결 중' for i in range(len(devices))}
        self.epochs={str(i+1):0 for i in range(len(devices))}

    def start(self):
        for i,device in enumerate(self.devices):
            thread=threading.Thread(target=self._capture,args=(str(i+1),device['serial']),daemon=True)
            thread.start();self.threads.append(thread)

    def _capture(self, camera_id, serial):
        import pyrealsense2 as rs
        while not self.stop_event.is_set():
            pipeline=None
            with self.lock:
                self.epochs[camera_id]+=1;epoch=self.epochs[camera_id]
                self.frames.pop(camera_id,None);self.states[camera_id]='연결 중'
            try:
                pipeline=rs.pipeline();cfg=rs.config();cfg.enable_device(serial)
                cfg.enable_stream(rs.stream.color,self.width,self.height,rs.format.bgr8,self.fps)
                pipeline.start(cfg)
                self._receive(camera_id,epoch,pipeline)
            except Exception:
                # Avoid exposing device serials through raw SDK errors in UI/logs.
                with self.lock:
                    self.frames.pop(camera_id,None)
                    self.states[camera_id]='연결 대기 · 다른 카메라 프로그램을 종료하고 USB를 확인하세요'
            finally:
                if pipeline is not None:
                    try:pipeline.stop()
                    except RuntimeError:pass
            self.stop_event.wait(1)
        with self.lock:self.frames.pop(camera_id,None);self.states[camera_id]='정지'

    def _receive(self,camera_id,epoch,pipeline):
        last_new=time.monotonic()
        previous=None
        while not self.stop_event.is_set():
            ok,frames=pipeline.try_wait_for_frames(350)
            color=frames.get_color_frame() if ok else None
            number=color.get_frame_number() if color else None
            now=time.monotonic()
            # A buffered frame returned after a stalled SDK call or host pause
            # cannot establish continuous capture, even with a larger counter.
            if now-last_new>=2.0:raise RuntimeError('RGB frame timeout')
            if number is None or number==previous:
                if now-last_new>=1.0:
                    with self.lock:self.states[camera_id]='새 RGB 프레임 대기'
                continue
            if previous is not None and number<previous:raise RuntimeError('RGB frame counter reset')
            # Consumers may have been analysing the other board throughout the gap.
            # Mark the break in the capture thread before publishing another RGB image.
            if previous is not None and now-last_new > MAX_FRAME_AGE:
                epoch += 1
            previous=number
            last_new=now
            packet={'image':np.asanyarray(color.get_data()).copy(),'sequence':number,'epoch':epoch,
                    'received':now,'observed_at':datetime.now(timezone.utc).isoformat()}
            with self.lock:
                self.epochs[camera_id]=epoch
                self.frames[camera_id]=packet;self.states[camera_id]='실시간 수신 중'

    def latest(self,camera_id,max_age=MAX_FRAME_AGE):
        with self.lock:
            packet=self.frames.get(camera_id)
            if packet is None or time.monotonic()-packet['received']>max_age:return None
            # The capture thread replaces the entire packet, never mutating it.
            return packet

    def snapshot(self):
        with self.lock:return dict(self.states)

    def stop(self):
        self.stop_event.set()
        deadline=time.monotonic()+5
        for thread in self.threads:thread.join(max(0,deadline-time.monotonic()))
        return all(not thread.is_alive() for thread in self.threads)

    def stopped(self):
        return all(not thread.is_alive() for thread in self.threads)
