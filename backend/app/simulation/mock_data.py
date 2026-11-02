"""DB 시드가 아닌 고정 합성 예시. 실제 A/B/C 면수·차량 정보가 아니다."""

from dataclasses import dataclass
from typing import Literal

OBSERVED_AT = "2026-11-02T10:00:00+09:00"
STATUS = Literal["occupied", "empty", "uncertain", "unavailable"]


@dataclass(frozen=True)
class MockSpace:
    lot_id: str
    space_id: str
    status: STATUS
    car_id: str | None = None
    confidence: float | None = None


LOT_TYPES = {"A": "real", "B": "real", "C": "virtual"}
# 각 구역 4칸은 시험용 예시이며 실제 주차칸 수가 아니다.
SPACES = tuple(
    space
    for number, lot_id in enumerate(LOT_TYPES, start=1)
    for space in (
        MockSpace(lot_id, f"{lot_id}-01", "occupied", f"{number:02d}", 0.95),
        MockSpace(lot_id, f"{lot_id}-02", "empty", confidence=0.98),
        MockSpace(lot_id, f"{lot_id}-03", "uncertain", confidence=0.45),
        MockSpace(lot_id, f"{lot_id}-04", "unavailable"),
    )
)
