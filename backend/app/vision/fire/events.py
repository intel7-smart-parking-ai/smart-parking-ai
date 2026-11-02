"""
표준 분석 이벤트 생성 어댑터 (Standard Vision Event Adapter)
공동 개발: 윤명수 · 서정범 (3팀)
"""
from __future__ import annotations
import datetime
from typing import Any

KST = datetime.timezone(datetime.timedelta(hours=9))

def now_kst_iso() -> str:
  return datetime.datetime.now(KST).strftime("%Y-%m-%dT%H:%M:%S+09:00")

def create_mock_fire_event(
    lot_id: str,
    space_id: str,
    confidence: float = 0.96,
    bbox_xyxy: list[int] | None = None,
    evidence_frame_id: str | None = None,
) -> dict[str, Any]:
  now_iso = now_kst_iso()
  frame_id = evidence_frame_id or f"frm_{lot_id.lower()}_{datetime.datetime.now().strftime('%H%M%S%f')[:8]}"
  bbox = bbox_xyxy or [140, 120, 175, 155]

  return {
      "event_id": f"evt_fire_{datetime.datetime.now().strftime('%H%M%S%f')[:8]}",
      "lot_id": lot_id,
      "camera_id": f"camera_{lot_id.lower()}" if lot_id in ("A", "B") else None,
      "event_type": "mock_fire_led_observed",
      "space_id": space_id,
      "bbox_xyxy": bbox,
      "confidence": round(confidence, 2),
      "evidence_frame_id": frame_id,
      "observed_at": now_iso,
      "display_label": "LED 모의 화재 (3팀 AI 감지)",
      "is_real_fire": False
  }
