#!/usr/bin/env python3
"""AI smart parking: collect unlabelled RGB frames from one/two cameras.

This is a *data capture* tool, not an automatic annotation or AI training tool.
Windows examples:
  python capture_dataset.py --source A:webcam:0 --mode vehicle
  python capture_dataset.py --source A:realsense --source B:webcam:1 --mode vehicle
  python capture_dataset.py --source A:realsense --mode ocr --min-change 0
  python capture_dataset.py --source A:realsense --mode led_on --interval 2 --min-change 0

Press 's' to save the current images manually, 'p' to pause auto saving,
'q' or Esc to quit. Images are saved WITHOUT preview text/overlays.
"""

import argparse
import csv
from datetime import datetime
from pathlib import Path
import re
import sys
import time

import cv2
import numpy as np


MODES = ("vehicle", "ocr", "led_on", "led_off", "background")


class Webcam:
    def __init__(self, index: int, width: int, height: int):
        backend = cv2.CAP_DSHOW if sys.platform.startswith("win") else cv2.CAP_ANY
        self.cap = cv2.VideoCapture(index, backend)
        if not self.cap.isOpened():
            raise RuntimeError(f"webcam:{index} 열기 실패. 인덱스/다른 앱 사용 여부를 확인하세요.")
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)

    def read(self):
        ok, frame = self.cap.read()
        return frame if ok else None

    def close(self):
        self.cap.release()


class RealSense:
    def __init__(self, width: int, height: int, serial: str | None = None):
        try:
            import pyrealsense2 as rs
        except ImportError as exc:
            raise RuntimeError("RealSense 사용 시 먼저 'python -m pip install pyrealsense2'가 필요합니다.") from exc
        self.rs = rs
        self.pipeline = rs.pipeline()
        config = rs.config()
        if serial:
            config.enable_device(serial)
        config.enable_stream(rs.stream.color, width, height, rs.format.bgr8, 30)
        try:
            self.pipeline.start(config)
        except Exception as exc:
            raise RuntimeError(f"RealSense RGB 시작 실패. RGB 지원 해상도/USB 연결 확인: {exc}") from exc

    def read(self):
        frames = self.pipeline.wait_for_frames(2500)
        color = frames.get_color_frame()
        return np.asanyarray(color.get_data()) if color else None

    def close(self):
        self.pipeline.stop()


def parse_source(text: str):
    """A:webcam:0 / B:realsense / A:realsense:SERIAL"""
    pieces = text.split(":")
    if len(pieces) < 2 or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", pieces[0]):
        raise ValueError(f"잘못된 --source '{text}': A:webcam:0 또는 A:realsense 형식")
    lot, kind = pieces[:2]
    if kind == "webcam" and len(pieces) == 3 and pieces[2].isdigit():
        return lot.upper(), kind, pieces[2]
    if kind == "realsense" and len(pieces) in (2, 3):
        return lot.upper(), kind, pieces[2] if len(pieces) == 3 else None
    raise ValueError(f"잘못된 --source '{text}'")


def change_score(previous_gray, frame) -> tuple[float, np.ndarray]:
    thumb = cv2.resize(frame, (320, 180), interpolation=cv2.INTER_AREA)
    gray = cv2.cvtColor(thumb, cv2.COLOR_BGR2GRAY)
    if previous_gray is None:
        return float("inf"), gray
    mean_difference = float(np.mean(cv2.absdiff(gray, previous_gray)))
    return mean_difference, gray


def sharpness(frame) -> float:
    thumb = cv2.resize(frame, (320, 180), interpolation=cv2.INTER_AREA)
    return float(cv2.Laplacian(cv2.cvtColor(thumb, cv2.COLOR_BGR2GRAY), cv2.CV_64F).var())


def write_image(path: Path, frame, jpg_quality: int):
    # imencode + write_bytes is robust to non-ASCII Windows file paths.
    ok, encoded = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, jpg_quality])
    if not ok:
        raise RuntimeError(f"이미지 인코딩 실패: {path}")
    path.write_bytes(encoded.tobytes())


def main():
    parser = argparse.ArgumentParser(description="스마트 주차장 AI 학습용 RGB 이미지 자동 수집")
    parser.add_argument("--source", action="append", required=True,
                        help="예: A:webcam:0, A:realsense, B:webcam:1, A:realsense:SERIAL")
    parser.add_argument("--mode", choices=MODES, default="vehicle")
    parser.add_argument("--output", default="datasets/raw")
    parser.add_argument("--interval", type=float, default=2.0, help="자동 저장 최소 간격 (초), 기본 2")
    parser.add_argument("--min-change", type=float, default=0.6,
                        help="직전 저장 장면과 평균 명암 차이. 0이면 중복 검사 끔 (기본 0.6)")
    parser.add_argument("--min-sharpness", type=float, default=0.0,
                        help="초점/흐림 필터 기준, 0이면 끔 (기본 0)")
    parser.add_argument("--width", type=int, default=640)
    parser.add_argument("--height", type=int, default=480)
    parser.add_argument("--quality", type=int, default=95)
    parser.add_argument("--max-images", type=int, default=0,
                        help="전체 카메라를 합친 자동+수동 저장 최대 장수. 0이면 무제한")
    parser.add_argument("--no-preview", action="store_true", help="GUI 화면 없이 저장")
    args = parser.parse_args()
    if args.interval <= 0 or args.min_change < 0 or args.min_sharpness < 0 or args.width <= 0 or args.height <= 0:
        parser.error("interval/width/height은 양수, min-change/min-sharpness는 0 이상이어야 합니다.")
    if not (1 <= args.quality <= 100) or args.max_images < 0:
        parser.error("quality는 1~100, max-images는 0 이상이어야 합니다.")

    sources = [parse_source(s) for s in args.source]
    lots = [s[0] for s in sources]
    if len(lots) != len(set(lots)):
        parser.error("한 실행 내에서 lot 이름은 중복될 수 없습니다 (예: A, B).")
    if sum(kind == "realsense" and ident is None for _, kind, ident in sources) > 1:
        parser.error("여러 RealSense 카메라 사용 시 --source A:realsense:SERIAL 형태로 기기 번호를 지정하세요.")

    session = datetime.now().strftime("%Y%m%d_%H%M%S")
    base = Path(args.output).resolve() / args.mode
    devices = []
    metadata_path = base / f"capture_{session}.csv"
    total = 0
    paused = False
    try:
        for lot, kind, ident in sources:
            device = Webcam(int(ident), args.width, args.height) if kind == "webcam" else RealSense(args.width, args.height, ident)
            out = base / lot / session
            out.mkdir(parents=True, exist_ok=True)
            devices.append({"lot": lot, "device": device, "out": out,
                            "last_saved_time": float("-inf"), "previous_gray": None,
                            "count": 0, "name": f"{lot}_{kind}", "latest": None})
        metadata_path.parent.mkdir(parents=True, exist_ok=True)
        with metadata_path.open("w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f)
            writer.writerow(["timestamp", "mode", "lot", "camera", "file", "width", "height",
                             "sharpness", "difference", "save_method", "session"])
            print(f"저장 위치: {base}")
            print("조작: [s] 수동 저장 / [p] 자동 저장 일시정지 / [q] 종료")
            if args.no_preview:
                print("화면 표시 없음: 종료하려면 Ctrl+C")
            while True:
                now = time.monotonic()
                for source in devices:
                    try:
                        frame = source["device"].read()
                    except Exception as exc:
                        print(f"[{source['name']}] 프레임 읽기 실패: {exc}")
                        continue
                    if frame is None:
                        continue
                    source["latest"] = frame
                    diff, gray = change_score(source["previous_gray"], frame)
                    clarity = sharpness(frame)
                    source["diff"] = diff
                    source["clarity"] = clarity
                    if (not paused and now - source["last_saved_time"] >= args.interval
                            and diff >= args.min_change and clarity >= args.min_sharpness):
                        saved = save(source, frame, now, gray, clarity, diff, "auto", session,
                                     args, writer, f)
                        total += int(saved)
                    if not args.no_preview:
                        preview = frame.copy()
                        status = (f"{source['name']} mode={args.mode} saved={source['count']} "
                                  f"diff={0.0 if diff == float('inf') else diff:.2f} "
                                  f"{'PAUSED' if paused else 'AUTO'}")
                        cv2.putText(preview, status, (10, 28), cv2.FONT_HERSHEY_SIMPLEX,
                                    0.5, (0, 255, 255), 1, cv2.LINE_AA)
                        cv2.imshow(source["name"], preview)
                if args.max_images and total >= args.max_images:
                    print(f"최대 이미지 수 {args.max_images}장 도달. 저장 종료.")
                    break
                if not args.no_preview:
                    key = cv2.waitKey(1) & 0xFF
                    if key in (ord("q"), 27):
                        break
                    if key == ord("p"):
                        paused = not paused
                        print(f"자동 저장 {'일시정지' if paused else '재개'}")
                    if key == ord("s"):
                        for source in devices:
                            frame = source["latest"]
                            if frame is not None:
                                _, gray = change_score(source["previous_gray"], frame)
                                if save(source, frame, time.monotonic(), gray, source["clarity"],
                                        source["diff"], "manual", session, args, writer, f):
                                    total += 1
                            if args.max_images and total >= args.max_images:
                                break
                else:
                    time.sleep(0.01)
    except KeyboardInterrupt:
        print("사용자가 수집을 종료했습니다.")
    finally:
        for source in devices:
            source["device"].close()
        if not args.no_preview:
            cv2.destroyAllWindows()
    print(f"완료: 총 {total}장, 기록 파일: {metadata_path}")


def save(source, frame, now, gray, clarity, diff, method, session, args, writer, f):
    timestamp = datetime.now()
    name = f"{source['lot']}_{args.mode}_{timestamp.strftime('%Y%m%d_%H%M%S_%f')}.jpg"
    path = source["out"] / name
    write_image(path, frame, args.quality)
    source["last_saved_time"] = now
    source["previous_gray"] = gray
    source["count"] += 1
    writer.writerow([timestamp.isoformat(timespec="milliseconds"), args.mode, source["lot"],
                     source["name"], str(path.relative_to(Path(args.output).resolve())),
                     frame.shape[1], frame.shape[0], f"{clarity:.2f}",
                     "first" if diff == float("inf") else f"{diff:.3f}", method, session])
    f.flush()
    print(f"[{method}] {path} ({source['count']}장)")
    return True


if __name__ == "__main__":
    main()
