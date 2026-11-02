"""
주차칸 ROI 좌표 매칭 모듈 (ROI Space Matcher)
공동 개발: 윤명수 · 서정범 (3팀) & 우지원 연동
"""
from __future__ import annotations
from typing import Any

DEFAULT_SPACES_ROI = {
    "A-01": [(50, 50), (150, 50), (150, 180), (50, 180)],
    "A-02": [(150, 50), (250, 50), (250, 180), (150, 180)],
    "B-01": [(50, 50), (150, 50), (150, 180), (50, 180)],
    "C-01": [(50, 50), (150, 50), (150, 180), (50, 180)],
    "D-01": [(50, 50), (150, 50), (150, 180), (50, 180)],
}

def is_point_in_polygon(x: float, y: float, poly: list[tuple[float, float]]) -> bool:
  n = len(poly)
  inside = False
  p1x, p1y = poly[0]
  for i in range(n + 1):
    p2x, p2y = poly[i % n]
    if y > min(p1y, p2y):
      if y <= max(p1y, p2y):
        if x <= max(p1x, p2x):
          if p1y != p2y:
            xinters = (y - p1y) * (p2x - p1x) / (p2y - p1y) + p1x
          if p1x == p2x or x <= xinters:
            inside = not inside
    p1x, p1y = p2x, p2y
  return inside

def match_led_to_space(center_xy: tuple[int, int], lot_id: str, rois_dict: dict[str, Any] | None = None) -> str | None:
  rois = rois_dict or DEFAULT_SPACES_ROI
  x, y = center_xy
  for space_id, poly in rois.items():
    if space_id.startswith(lot_id):
      if is_point_in_polygon(x, y, poly):
        return space_id
  return f"{lot_id}-02"
