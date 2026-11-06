"""Shared, single-owner frame input for two RealSense D435 cameras.

Every downstream consumer should call CameraHub.get_latest_frame() rather than
opening the same USB device a second time.
"""

from __future__ import annotations

import json
import copy
import queue
import logging
import threading
from collections import deque
import time
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Protocol

import numpy as np


# 카메라 화질 시험용 설정: A/B 각각 따로 조절할 수 있습니다.
# None은 해당 항목을 변경하지 않는다는 뜻입니다.
# white_balance만 예외: None = 자동, 4500/5000/5500 등 숫자 = 수동(K).
# power_line_frequency: 0=끄기, 1=50Hz, 2=60Hz (국내 형광등 시험값).
# 노출/게인은 조명과 설치 높이를 확정하기 전에는 None으로 유지하세요.
RGB_QUALITY = {
    "A": {
        "sharpness": 70,
        "contrast": None,
        "saturation": None,
        "brightness": None,
        "white_balance": None,
        "power_line_frequency": 2,
        "exposure": None,
        "gain": None,
    },
    "B": {
        "sharpness": 70,
        "contrast": None,
        "saturation": None,
        "brightness": None,
        "white_balance": None,
        "power_line_frequency": 2,
        "exposure": None,
        "gain": None,
    },
}


@dataclass(frozen=True)
class CameraSpec:
    camera_id: str
    source: str = "realsense"
    serial: str = ""
    file_path: str = ""
    width: int = 640
    height: int = 480
    fps: int = 30


@dataclass(frozen=True)
class FramePacket:
    camera_id: str
    serial: str
    sequence: int
    timestamp_utc: str
    received_monotonic: float
    image: np.ndarray  # BGR, no overlays
    camera_options: dict | None = None  # 시작 시 옵션 적용 결과. 촬영 순간 노출값은 아닙니다.
    stream_epoch: int = 0  # Changes on reconnect and every attempted settings mutation.
    source_kind: str = 'camera'  # camera / video / import / synthetic; never infer from camera_id.
    sensor_frame: dict | None = None  # Optional observations from this exact RGB frame.


@dataclass
class _CameraState:
    lock: threading.Lock = field(default_factory=threading.Lock)
    status: str = "STOPPED"
    last_error: str = ""
    latest: FramePacket | None = None
    recent: deque = field(default_factory=lambda: deque(maxlen=4))
    sequence: int = 0
    stream_epoch: int = 0


class FrameSource(Protocol):
    def open(self) -> None: ...

    def read(self) -> np.ndarray: ...

    def close(self) -> None: ...


def rgb_frame_metadata(frame, rs):
    import math
    try:
        stamp=float(frame.get_timestamp())
        domain=str(frame.get_frame_timestamp_domain()).rsplit('.',1)[-1]
        seq=int(frame.get_frame_number())
        if not math.isfinite(stamp) or stamp<0 or seq<0 or domain not in ('hardware_clock','system_time','global_time'):
            return None
    except (AttributeError, RuntimeError, TypeError, ValueError, OverflowError):
        return None
    data={'frame_number':seq,'timestamp_ms':stamp,'timestamp_domain':domain}
    try:
        option=rs.frame_metadata_value.actual_exposure
        if frame.supports_frame_metadata(option):
            exposure=int(frame.get_frame_metadata(option))
            if exposure>0:data['exposure_us']=exposure
    except (AttributeError, RuntimeError, TypeError, ValueError, OverflowError):
        pass
    return data


class RealSenseColorSource:
    def __init__(self, spec: CameraSpec) -> None:
        self.spec = spec
        self.pipeline = None

    def open(self) -> None:
        import pyrealsense2 as rs

        pipeline = rs.pipeline()
        config = rs.config()
        config.enable_device(self.spec.serial)
        config.enable_stream(
            rs.stream.color,
            self.spec.width,
            self.spec.height,
            rs.format.bgr8,
            self.spec.fps,
        )
        # 반드시 한 번만 시작합니다. 여기서 RGB 센서 설정을 적용합니다.
        profile = pipeline.start(config)
        self.pipeline = pipeline
        self._rs=rs
        self._configure_rgb_sensor(profile, rs)

    def _configure_rgb_sensor(self, profile: object, rs: object) -> None:
        quality = dict(RGB_QUALITY.get(self.spec.camera_id, {}))
        config_path = Path(__file__).resolve().parent / 'camera_quality.json'
        if config_path.exists():
            from quality import read_quality_config
            override = read_quality_config(config_path)
            quality.update(override.get('defaults', {}))
            quality.update(override.get(self.spec.camera_id, {}))
        self.option_results = {}


        def apply(sensor: object, name: str, option: object, value: object) -> None:
            if value is None:
                return
            entry = {'requested':value,'status':'pending'}
            self.option_results[name] = entry
            try:
                if not sensor.supports(option):
                    entry['status']='unsupported'
                    print(f"[{self.spec.camera_id}] {name}: 미지원, 건너뜀")
                    return
                if sensor.is_option_read_only(option):
                    entry['status']='read_only'
                    print(f"[{self.spec.camera_id}] {name}: 읽기 전용, 건너뜀")
                    return
                allowed = sensor.get_option_range(option)
                if not allowed.min <= float(value) <= allowed.max:
                    entry['status']='out_of_range'
                    print(f"[{self.spec.camera_id}] {name}: 허용 범위 밖, 건너뜀")
                    return
                sensor.set_option(option, float(value))
                entry.update(status='applied',actual_at_start=float(sensor.get_option(option)))
                print(f"[{self.spec.camera_id}] {name} = {value}")
            except Exception as exc:
                entry.update(status='failed',error=str(exc))
                # 화질 옵션 실패가 카메라 A/B 자체를 멈추게 하지는 않습니다.
                print(f"[{self.spec.camera_id}] {name}: 설정 실패 ({exc})")

        for sensor in profile.get_device().query_sensors():
            if not sensor.supports(rs.option.sharpness):
                continue  # 깊이 센서가 아닌 RGB 센서만 선택

            self._rgb_sensor=sensor
            exposure = quality.get("exposure")
            gain = quality.get("gain")
            manual_exposure = exposure is not None or gain is not None
            apply(sensor, "auto_exposure", rs.option.enable_auto_exposure,
                  0 if manual_exposure else 1)
            if manual_exposure:
                apply(sensor, "exposure", rs.option.exposure, exposure)
                apply(sensor, "gain", rs.option.gain, gain)

            apply(sensor, 'auto_exposure_priority', rs.option.auto_exposure_priority, quality.get('auto_exposure_priority'))
            white_balance = quality.get("white_balance")
            apply(sensor, "auto_white_balance", rs.option.enable_auto_white_balance,
                  1 if white_balance is None else 0)
            if white_balance is not None:
                apply(sensor, "white_balance", rs.option.white_balance, white_balance)

            for name, option in (
                ("power_line_frequency", rs.option.power_line_frequency),
                ("sharpness", rs.option.sharpness),
                ("contrast", rs.option.contrast),
                ("saturation", rs.option.saturation),
                ("brightness", rs.option.brightness),
            ):
                apply(sensor, name, option, quality.get(name))
            # 콘솔에만 보이던 적용 결과를 파일에도 남겨 현장 문제를 구분합니다.
            try:
                from parking_ai.storage import atomic_json
                atomic_json(Path(__file__).resolve().parent/'logs'/f'camera_{self.spec.camera_id}_options.json',
                            {'camera_id':self.spec.camera_id,'options_at_start':self.option_results})
            except OSError:
                logging.exception('카메라 옵션 기록 실패')
            return

        print(f"[{self.spec.camera_id}] RGB 화질 설정 센서를 찾지 못했습니다")

    def apply_quality(self,changes):
        from quality import apply_sensor,inspect_sensor
        if not hasattr(self,'_rgb_sensor'):raise RuntimeError('RGB 설정 센서 없음')
        try:
            actual=apply_sensor(self._rgb_sensor,self._rs,changes)
        finally:
            # Even a partial hardware failure leaves an actual-value audit snapshot.
            self.option_results={'runtime_actual':inspect_sensor(self._rgb_sensor,self._rs)}
        return actual

    def read(self) -> np.ndarray:
        if self.pipeline is None:
            raise RuntimeError("RealSense pipeline is not open")
        frames = self.pipeline.wait_for_frames(timeout_ms=2000)
        color = frames.get_color_frame()
        if not color:
            raise RuntimeError("No color frame returned")
        self.sensor_frame=rgb_frame_metadata(color,self._rs)
        # Copy: SDK frame buffer is recycled after the next read.
        return np.asanyarray(color.get_data()).copy()

    def close(self) -> None:
        pipeline, self.pipeline = self.pipeline, None
        if pipeline is not None:
            pipeline.stop()


class VideoFileSource:
    """Replay an MP4/AVI for development when physical cameras are unavailable."""

    def __init__(self, spec: CameraSpec) -> None:
        self.spec = spec
        self.cap = None

    def open(self) -> None:
        import cv2

        cap = cv2.VideoCapture(self.spec.file_path)
        if not cap.isOpened():
            cap.release()
            raise RuntimeError(f"Cannot open replay file: {self.spec.file_path}")
        self.cap = cap

    def read(self) -> np.ndarray:
        import cv2

        if self.cap is None:
            raise RuntimeError("File source is not open")
        ok, frame = self.cap.read()
        if not ok:
            self.cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
            ok, frame = self.cap.read()
        if not ok:
            raise RuntimeError("File source has no decodable video frames")
        time.sleep(1.0 / max(self.spec.fps, 1))
        return frame

    def close(self) -> None:
        cap, self.cap = self.cap, None
        if cap is not None:
            cap.release()


def load_camera_specs(config_path: Path) -> list[CameraSpec]:
    config_path = Path(config_path).resolve()
    data = json.loads(config_path.read_text(encoding="utf-8-sig"))
    if not isinstance(data,dict): raise ValueError("Camera config must be an object")
    raw = data.get("cameras")
    if not isinstance(raw, dict) or not raw or not set(raw).issubset({"A", "B"}):
        raise ValueError("Camera config needs A or B (single camera supported)")

    width = data.get("width", 640)
    height = data.get("height", 480)
    fps = data.get("fps", 30)
    if any(type(n) is not int or n<=0 for n in (width,height,fps)):
        raise ValueError("width/height/fps must be positive")

    specs = []
    realsense_serials = []
    for camera_id in sorted(raw):
        item = raw[camera_id]
        if not isinstance(item,dict): raise ValueError(f"{camera_id}: camera entry must be an object")
        source = item.get("source", "realsense")
        if source not in ("realsense", "file"):
            raise ValueError(f"{camera_id}: unsupported source: {source}")
        serial = item.get("serial", "")
        file_path = item.get("file_path", "")
        if not isinstance(serial,str) or not isinstance(file_path,str): raise ValueError("serial/file_path must be strings")
        serial, file_path = serial.strip(), file_path.strip()
        if source == "realsense":
            if not serial:
                raise ValueError(f"{camera_id}: serial is required")
            realsense_serials.append(serial)
        if source == "file" and not file_path:
            raise ValueError(f"{camera_id}: file_path is required")
        if source == "file":
            replay=Path(file_path).expanduser()
            file_path=str(replay.resolve() if replay.is_absolute() else (config_path.parent/replay).resolve())
        specs.append(CameraSpec(camera_id, source, serial, file_path, width, height, fps))

    if len(realsense_serials) != len(set(realsense_serials)):
        raise ValueError("A and B must not use the same RealSense serial")
    return specs


def find_realsense_devices() -> list[dict[str, str]]:
    import pyrealsense2 as rs

    context = rs.context()
    results: list[dict[str, str]] = []
    for device in context.query_devices():
        info = rs.camera_info

        def read(key: object) -> str:
            return device.get_info(key) if device.supports(key) else "unknown"

        results.append(
            {
                "name": read(info.name),
                "serial": read(info.serial_number),
                "usb": read(info.usb_type_descriptor),
            }
        )
    return results


def _default_source(spec: CameraSpec) -> FrameSource:
    return RealSenseColorSource(spec) if spec.source == "realsense" else VideoFileSource(spec)


class CameraHub:
    def __init__(
        self,
        specs: list[CameraSpec],
        source_factory: Callable[[CameraSpec], FrameSource] = _default_source,
        retry_seconds: float = 2.0,
    ) -> None:
        ids = [spec.camera_id for spec in specs]
        if not specs or len(set(ids)) != len(ids):
            raise ValueError("Camera IDs must be unique")
        self.specs = {spec.camera_id: spec for spec in specs}
        self._states = {camera_id: _CameraState() for camera_id in ids}
        self._source_factory = source_factory
        self._retry_seconds = retry_seconds
        self._shutdown = threading.Event()
        self._threads: list[threading.Thread] = []
        self._started = False
        self._quality_commands={camera:queue.Queue(2) for camera in ids}

    def start(self) -> None:
        if self._started:
            raise RuntimeError("CameraHub.start() must only be called once")
        self._started = True
        self._shutdown.clear()
        for spec in self.specs.values():
            thread = threading.Thread(
                target=self._worker,
                args=(spec,),
                daemon=True,
                name=f"D435-{spec.camera_id}",
            )
            self._threads.append(thread)
            thread.start()

    def _worker(self, spec: CameraSpec) -> None:
        state = self._states[spec.camera_id]
        while not self._shutdown.is_set():
            source: FrameSource | None = None
            with state.lock:
                state.status = "CONNECTING"
                state.stream_epoch += 1
                # 재연결 중에는 이전 연결의 프레임을 다시 내보내지 않습니다.
                state.latest = None
                state.recent.clear()
            try:
                source = self._source_factory(spec)
                source.open()
                with state.lock:
                    state.status = "CONNECTING"  # 첫 새 프레임 수신 후 RUNNING으로 바뀝니다.
                    state.last_error = ""

                while not self._shutdown.is_set():
                    try:changes,receipt=self._quality_commands[spec.camera_id].get_nowait()
                    except queue.Empty:pass
                    else:
                        try:
                            with state.lock:
                                if self._shutdown.is_set() or getattr(receipt,'cancelled',False):raise RuntimeError('종료되거나 닫힌 설정 창의 요청 취소')
                                if receipt.stream_epoch != state.stream_epoch:raise RuntimeError('연결/설정이 변경되어 이전 요청 취소 · 다시 조회하세요.')
                                if changes:
                                    state.stream_epoch += 1
                                    state.status = 'CONFIGURING'
                                    state.latest = None
                                    state.recent.clear()
                            receipt.put({'ok':True,'actual':source.apply_quality(changes)})
                        except Exception as exc:receipt.put({'ok':False,'error':str(exc),'actual':getattr(source,'option_results',{})})
                    image = source.read()
                    if image is None or image.ndim != 3 or image.shape[2] != 3:
                        raise RuntimeError("Color frame must have shape H x W x 3")
                    now = time.monotonic()
                    with state.lock:
                        state.sequence += 1
                        state.latest = FramePacket(
                            camera_id=spec.camera_id,
                            serial=spec.serial if spec.source == "realsense" else spec.file_path,
                            sequence=state.sequence,
                            timestamp_utc=datetime.now(timezone.utc).isoformat(
                                timespec="milliseconds"
                            ),
                            received_monotonic=now,
                            image=image.copy(),
                            camera_options=copy.deepcopy(getattr(source,"option_results",None)),
                            sensor_frame=copy.deepcopy(getattr(source,"sensor_frame",None)),
                            stream_epoch=state.stream_epoch,
                            source_kind='camera' if spec.source=='realsense' else 'video',
                        )
                        if len(self.specs)>1:state.recent.append(state.latest)
                        state.status = "RUNNING"
                        state.last_error = ""
            except Exception as exc:
                with state.lock:
                    state.status = "ERROR"
                    state.latest = None
                    state.last_error = f"{type(exc).__name__}: {exc}"
            finally:
                # Admission and draining share the same lock: no request can slip
                # into the next connection after the old connection was drained.
                with state.lock:
                    state.status = 'STOPPED' if self._shutdown.is_set() else 'ERROR'
                    state.latest = None
                    while True:
                        try:_,receipt=self._quality_commands[spec.camera_id].get_nowait()
                        except queue.Empty:break
                        receipt.put_nowait({'ok':False,'error':'연결 종료/재연결로 설정 요청 취소','actual':{}})
                if source is not None:
                    try:
                        source.close()
                    except Exception as exc:
                        with state.lock:
                            state.last_error = f"Close failed: {exc}"

            if not self._shutdown.is_set():
                self._shutdown.wait(self._retry_seconds)

        with state.lock:
            state.status = "STOPPED"

    def request_quality(self,camera,changes):
        if not isinstance(changes,dict):raise ValueError('설정은 객체여야 합니다.')
        state=self._states[camera]
        with state.lock:
            if self._shutdown.is_set():raise RuntimeError('카메라 종료 중')
            if state.status!='RUNNING' or state.latest is None or time.monotonic()-state.latest.received_monotonic>2:
                raise RuntimeError('설정 처리 후 새 영상이 있는 카메라를 먼저 연결하세요.')
            receipt=queue.Queue(1)
            receipt.stream_epoch=state.stream_epoch
            self._quality_commands[camera].put_nowait((copy.deepcopy(changes),receipt))
        return receipt

    def get_latest_frame(self, camera_id: str, max_age_seconds: float = 2.0) -> FramePacket | None:
        """Return a private copy. Downstream model code may safely draw on it."""
        state = self._states[camera_id]
        with state.lock:
            pkt = state.latest
            if self._shutdown.is_set() or state.status != "RUNNING" or pkt is None or time.monotonic() - pkt.received_monotonic > max_age_seconds:
                return None
            return replace(pkt, image=pkt.image.copy(), camera_options=copy.deepcopy(pkt.camera_options),sensor_frame=copy.deepcopy(pkt.sensor_frame))

    def get_frame_near(self, camera_id, epoch, target_time, after_sequence, not_before):
        """Copy just the closest eligible frame retained on this connection."""
        state=self._states[camera_id]
        with state.lock:
            if self._shutdown.is_set() or state.status!='RUNNING' or state.stream_epoch!=epoch:
                return None
            candidates=(p for p in state.recent if p.stream_epoch==epoch and p.sequence>after_sequence
                        and (not_before is None or p.received_monotonic>not_before))
            frame=min(candidates,key=lambda p:(abs(p.received_monotonic-target_time),-p.sequence),default=None)
            if frame is None:return None
            return replace(frame,image=frame.image.copy(),camera_options=copy.deepcopy(frame.camera_options),sensor_frame=copy.deepcopy(frame.sensor_frame))

    def recent_times(self, camera_id, epoch):
        """Read timing only, so comparing pairs never copies the full history of images."""
        state=self._states[camera_id]
        with state.lock:
            if self._shutdown.is_set() or state.status!='RUNNING' or state.stream_epoch!=epoch:return ()
            return tuple((p.sequence,p.received_monotonic) for p in state.recent if p.stream_epoch==epoch)

    def statuses(self) -> dict[str, dict[str, object]]:
        results = {}
        for camera_id, state in self._states.items():
            with state.lock:
                age = (
                    round(time.monotonic() - state.latest.received_monotonic, 2)
                    if state.latest is not None
                    else None
                )
                status = state.status
                if status == "RUNNING" and age is not None and age > 3.0:
                    status = "STALE"
                results[camera_id] = {
                    "status": status,
                    "age_seconds": age,
                    "sequence": state.sequence,
                    "last_error": state.last_error,
                }
        return results

    def request_stop(self) -> None:
        self._shutdown.set()

    def is_stopped(self) -> bool:
        return not any(thread.is_alive() for thread in self._threads)

    def stop(self) -> None:
        self.request_stop()
        for thread in self._threads:
            thread.join(timeout=5.0)

    def __enter__(self) -> "CameraHub":
        self.start()
        return self

    def __exit__(self, *_: object) -> None:
        self.stop()
