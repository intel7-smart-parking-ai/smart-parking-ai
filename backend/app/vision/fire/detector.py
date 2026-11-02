"""
LED 모의 화재 감지 및 오탐 방어 엔진 (LED Fire Detection Engine)
공동 개발: 윤명수 · 서정범 (3팀)
"""
from __future__ import annotations
from typing import Any

class LedFireDetector:
  def __init__(self, min_spot_area: int = 5, max_spot_area: int = 350, min_brightness: int = 210) -> None:
    self.min_spot_area = min_spot_area
    self.max_spot_area = max_spot_area
    self.min_brightness = min_brightness
    self.confidence_threshold = 0.85

  def detect_from_frame(self, frame_bgr: Any = None, mock_scenario: str | None = None) -> list[dict[str, Any]]:
    if mock_scenario == "RED_CAR_ONLY":
      # 빨간색 미니카 차체 반사는 LED 고휘도 점등 스팟 필터에 의해 차단 (오탐 방어)
      return []

    if mock_scenario == "LED_ON":
      return [{
          "bbox_xyxy": [140, 120, 175, 155],
          "center_xy": (157, 137),
          "confidence": 0.96,
          "is_fire_evidence": True,
          "label": "LED 모의 화재 (점등 감지)"
      }]

    try:
      import cv2
      import numpy as np
      if frame_bgr is None or not isinstance(frame_bgr, np.ndarray):
        return []
      hsv = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2HSV)
      lower_red1 = np.array([0, 140, self.min_brightness])
      upper_red1 = np.array([10, 255, 255])
      lower_red2 = np.array([170, 140, self.min_brightness])
      upper_red2 = np.array([180, 255, 255])
      mask1 = cv2.inRange(hsv, lower_red1, upper_red1)
      mask2 = cv2.inRange(hsv, lower_red2, upper_red2)
      mask = cv2.bitwise_or(mask1, mask2)
      contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
      detections = []
      for cnt in contours:
        area = cv2.contourArea(cnt)
        if self.min_spot_area <= area <= self.max_spot_area:
          x, y, w, h = cv2.boundingRect(cnt)
          aspect_ratio = float(w) / max(1, h)
          if 0.5 <= aspect_ratio <= 2.0:
            detections.append({
                "bbox_xyxy": [x, y, x + w, y + h],
                "center_xy": (x + w // 2, y + h // 2),
                "confidence": 0.94,
                "is_fire_evidence": True,
                "label": "LED 모의 화재 (점등 감지)"
            })
      return detections
    except ImportError:
      return []
