"""가상 공항 확장 도면을 포함한 고정 합성 상태. DB 시드·실제 빈자리 정보가 아니다."""

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
    space_type: Literal['general', 'ev', 'accessible'] = 'general'
    vehicle_type: Literal['general', 'ev', 'accessible'] | None = None


LOT_TYPES = {'A': 'real', 'B': 'real', 'C': 'virtual', 'D': 'virtual'}
LOT_NAMES = {'A': '개발원주차장', 'B': '부산역주차장', 'C': '김해공항 주차장', 'D': '신세계백화점주차장'}
# 주차장 ID와 구역 접두어는 독립적이다. fixture와의 일치는 API 시험에서 확인한다.
LAYOUT = {
    'A': ('AB', 6, {'A-05', 'A-06'}, {'B-06'}),
    'B': ('AB', 8, {'A-07', 'A-08'}, {'B-07', 'B-08'}),
    'C': ('ABC', (24, 36, 4), {'A-05', 'A-06', 'A-23', 'A-24', 'B-35', 'B-36'}, {'A-01', 'B-06'}),
    'D': ('ABCD', 16, {f'D-{i:02}' for i in range(1, 9)}, {f'C-{i:02}' for i in range(13, 17)}),
}


def _spaces():
    for lot_id, (zones, count, ev, accessible) in LAYOUT.items():
        counts = (count,) * len(zones) if isinstance(count, int) else count
        for index, space_id in enumerate(f'{z}-{n:02}' for z, size in zip(zones, counts) for n in range(1, size + 1)):
            status = {0: 'occupied', 2: 'uncertain', 3: 'unavailable'}.get(index, 'empty')
            car = {'A': '10', 'B': '20'}.get(lot_id) if status == 'occupied' else None
            yield MockSpace(lot_id, space_id, status, car,
                None if status == 'unavailable' else .45 if status == 'uncertain' else .95,
                'ev' if space_id in ev else 'accessible' if space_id in accessible else 'general',
                'general' if car is not None else None)


SPACES = tuple(_spaces())
