"""Single owner of RealSense pipelines; one latest frame per camera, no disk writes."""
from datetime import datetime, timezone
import logging
import math
import os
import threading
import time
import numpy as np

MAX_FRAME_AGE = 1.0
LOG = logging.getLogger(__name__)
# 11/11 A-04·05: fixed RGB exposure/white balance/flicker filter, only when set.
# V53_CAMERA_{id}_EXPOSURE overrides V53_EXPOSURE for that camera.
COLOR_ENV = ('EXPOSURE', 'GAIN', 'WHITE_BALANCE', 'POWER_LINE')


def color_options(camera_id, env=None):
    """Return [(option, value)] in the order to apply; empty when nothing is set."""
    env = os.environ if env is None else env
    values = {}
    for name in COLOR_ENV:
        raw = env.get(f'V53_CAMERA_{camera_id}_{name}', env.get(f'V53_{name}', '')).strip()
        if not raw:continue
        try:value = float(raw)
        except ValueError:raise ValueError(f'V53_{name} 값은 숫자여야 합니다.') from None
        if not math.isfinite(value) or value < 0:raise ValueError(f'V53_{name} 값이 올바르지 않습니다.')
        values[name] = value
    result = []
    if 'POWER_LINE' in values:
        hz = {50: 1, 60: 2}.get(int(values['POWER_LINE']))
        if hz is None:raise ValueError('V53_POWER_LINE 은 50 또는 60 입니다.')
        result.append(('power_line_frequency', hz))
    if 'EXPOSURE' in values or 'GAIN' in values:
        result.append(('enable_auto_exposure', 0))
        if 'EXPOSURE' in values:result.append(('exposure', values['EXPOSURE']))
        if 'GAIN' in values:result.append(('gain', values['GAIN']))
    if 'WHITE_BALANCE' in values:
        result += [('enable_auto_white_balance', 0), ('white_balance', values['WHITE_BALANCE'])]
    return result


def apply_color_options(sensor, options, rs, camera_id=''):
    """Apply each option on its own; a refused value is logged and the SDK default kept."""
    applied = []
    for name, value in options:
        try:
            option = getattr(rs.option, name)
            if sensor.supports(option):
                sensor.set_option(option, float(value))
                applied.append(f'{name}={value:g}')
            else:
                LOG.warning('카메라 %s RGB 설정 미지원: %s', camera_id, name)
        except Exception as error:
            LOG.warning('카메라 %s RGB 설정 실패: %s=%s (%s)', camera_id, name, value, type(error).__name__)
    if applied:LOG.info('카메라 %s RGB 설정: %s', camera_id, ', '.join(applied))
    return applied


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
                profile=pipeline.start(cfg)
                try:options=color_options(camera_id)
                except ValueError as error:
                    LOG.warning('카메라 %s RGB 설정 무시: %s',camera_id,error);options=[]
                if options:
                    apply_color_options(profile.get_device().first_color_sensor(),options,rs,camera_id)
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
