"""D5 메모리 점유 실험. 세션·차량 위치·DB를 확정하는 운영 엔진이 아니다."""
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from math import isfinite
from threading import RLock
from typing import Literal

from starlette.exceptions import HTTPException

from backend.app.api.vision_event_store import InMemoryEventStore, Receipt


@dataclass(frozen=True)
class ExperimentPolicy:
    occupy_seconds: float
    empty_seconds: float
    max_gap_seconds: float
    stale_seconds: float
    min_confidence: float
    initial_status: Literal['empty', 'uncertain']

    def __post_init__(self):
        values = (self.occupy_seconds, self.empty_seconds, self.max_gap_seconds, self.stale_seconds)
        if any(type(v) not in (int, float) or not isfinite(v) or v <= 0 for v in values):
            raise ValueError('실험 시간 설정은 유한한 양수여야 합니다.')
        if (type(self.min_confidence) not in (int, float) or not isfinite(self.min_confidence)
                or not 0 <= self.min_confidence <= 1 or self.initial_status not in ('empty', 'uncertain')):
            raise ValueError('잘못된 실험 신뢰도/초기 상태입니다.')


class OccupancyExperiment(InMemoryEventStore):
    def __init__(self, *, layout: dict, policy: ExperimentPolicy, max_events: int = 1000):
        super().__init__(max_events=max_events)
        self.policy = policy
        self._state_lock = RLock()
        self._spaces = {}
        for lot in layout['lots']:
            for space in lot['spaces']:
                key = (lot['lot_id'], space['space_id'])
                if key in self._spaces:
                    raise ValueError('중복 주차칸 키입니다.')
                self._spaces[key] = dict(lot_id=key[0], space_id=key[1], space_type=space['space_type'],
                    status=policy.initial_status, car_id=None, source=None, is_mock=None,
                    observed_at=None, version=0, candidate=None)

    def receive(self, event):
        key = (event.lot_id, event.space_id)
        with self._state_lock:
            if key not in self._spaces:
                raise HTTPException(422, '도면에 없는 주차칸입니다.')
            outcome = super().receive(event)
            if outcome is not Receipt.ACCEPTED:
                return outcome
            row = self._spaces[key]
            last = row['observed_at']
            # 지연/동시각 이벤트는 접수만 기록하고 최신 상태를 덮어쓰지 않는다.
            if last is not None and event.observed_at <= last:
                return outcome
            # 번호/LED 관측은 점유 관측이 아니므로 안정화·최신 점유 시각을 갱신하지 않는다.
            if event.event_type not in ('vehicle_observed', 'space_empty_observed'):
                return outcome
            candidate = row['candidate']
            gap = (event.observed_at - last).total_seconds() if last else None
            row.update(observed_at=event.observed_at, source=event.source,
                       is_mock=event.source in ('simulation', 'manual'), version=row['version'] + 1)
            if event.confidence < self.policy.min_confidence or (
                event.event_type == 'space_empty_observed' and event.car_id is not None
            ):
                row.update(status='uncertain', car_id=None, candidate=None)
                return outcome
            target = 'occupied' if event.event_type == 'vehicle_observed' else 'empty'
            car = event.car_id if target == 'occupied' else None
            signature = (target, car, event.source)
            if (candidate is None or candidate[0] != signature or gap is None
                    or gap > self.policy.max_gap_seconds):
                candidate = (signature, event.observed_at)
            elapsed = (event.observed_at - candidate[1]).total_seconds()
            required = self.policy.occupy_seconds if target == 'occupied' else self.policy.empty_seconds
            if elapsed >= required:
                row.update(status=target, car_id=car)
            else:
                row.update(status='uncertain', car_id=None)
            row['candidate'] = candidate
            return outcome

    def spaces(self, lot_id: str, *, as_of: datetime):
        if as_of.tzinfo is None or as_of.utcoffset() is None:
            raise ValueError('조회 기준 시각에 시간대가 필요합니다.')
        with self._state_lock:
            rows = [deepcopy(v) for (lot, _), v in self._spaces.items() if lot == lot_id]
        if not rows:
            raise HTTPException(404, '주차장이 없습니다.')
        for row in rows:
            row.pop('candidate')
            timestamp = row['observed_at']
            if timestamp is not None and (as_of - timestamp).total_seconds() > self.policy.stale_seconds:
                row.update(status='uncertain', car_id=None)
        return rows
