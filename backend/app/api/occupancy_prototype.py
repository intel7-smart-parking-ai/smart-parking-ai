"""D5 메모리 점유 실험. 세션·차량 위치·DB를 확정하는 운영 엔진이 아니다."""
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from math import isfinite
from threading import RLock
from typing import Literal

from starlette.exceptions import HTTPException

from backend.app.api.vision_event_store import InMemoryEventStore

# C·D 시뮬레이터 관측 간격 허용(초). 조회 stale 10초보다 작아야 한다.
SIMULATION_MAX_GAP_SECONDS = 8


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
    def __init__(self, *, layout: dict, policy: ExperimentPolicy, max_events: int = 1000, on_confirmed=None,
                 identity_clock=None):
        super().__init__(max_events=max_events)
        self.policy = policy
        self._on_confirmed = on_confirmed
        self._identity_clock = identity_clock
        self._collect_identity_evidence = True
        self._identity_frames = set()
        self._state_lock = RLock()
        self._spaces = {}
        for lot in layout['lots']:
            for space in lot['spaces']:
                key = (lot['lot_id'], space['space_id'])
                if key in self._spaces:
                    raise ValueError('중복 주차칸 키입니다.')
                self._spaces[key] = dict(lot_id=key[0], space_id=key[1], space_type=space['space_type'],
                    status=policy.initial_status, car_id=None, vehicle_type=None, source=None, is_mock=None,
                    observed_at=None, version=0, candidate=None)

    def receive(self, event):
        key = (event.lot_id, event.space_id)
        with self._state_lock:
            if key not in self._spaces:
                raise HTTPException(422, '도면에 없는 주차칸입니다.')
            def apply():
                previous = self._spaces[key]
                row = previous.copy()
                frame = self._identity_frame(event)
                if frame in self._identity_frames:
                    return
                self._apply_observation(row, event)
                identity = row.get('identity_match')
                session_ready = row['status'] in ('occupied', 'empty') or identity is not None
                if (self._on_confirmed is not None and row['version'] != previous['version']
                        and session_ready):
                    self._on_confirmed(row, event.observed_at)
                self._spaces[key] = row
                if frame is not None:
                    self._identity_frames.add(frame)

            return super().receive(event, before_record=apply)

    def _identity_frame(self, event):
        if (self._identity_clock is not None and event.source == 'vision'
                and event.lot_id in ('A', 'B')
                and event.camera_id == f'camera_{event.lot_id.lower()}'
                and event.frame_id is not None
                and event.event_type in ('vehicle_observed', 'space_empty_observed')):
            return (event.lot_id, event.space_id, event.camera_id, event.frame_id)
        return None

    def _update_identity_evidence(self, row, event):
        row.pop('identity_match', None)
        frame = self._identity_frame(event)
        row['identity_observation'] = (
            event.source == 'vision' and event.lot_id in ('A', 'B')
            and event.event_type == 'vehicle_observed'
        )
        if frame is not None and (self._identity_clock() - event.observed_at).total_seconds() > 10:
            return
        if event.event_type == 'space_empty_observed' or frame is None or event.confidence < 0.8:
            row.pop('identity_evidence', None)
            return
        if not self._collect_identity_evidence:
            return
        at = event.observed_at
        car, first, last, count = row.get('identity_evidence', (None, at, at, 0))
        if ((at - last).total_seconds() > 2 or (at - first).total_seconds() > 10
                or (event.car_id is not None and event.car_id != car)):
            car, first, count = event.car_id, at, 0
        if event.car_id is not None:
            count = min(count + 1, 3)
        row['identity_evidence'] = (car, first, at, count)
        if event.car_id is not None and count == 3:
            row['identity_match'] = (car, first)

    def _is_camera_observation(self, event):
        return (
            self._identity_clock is not None and event.source == 'vision'
            and event.lot_id in ('A', 'B')
            and event.event_type in ('vehicle_observed', 'space_empty_observed')
        )

    def _apply_observation(self, row, event):
        # Previously journaled observations may still be ahead of the clock at startup.
        if self._is_camera_observation(event) and event.observed_at > self._identity_clock():
            return
        last = row['observed_at']
        # 지연/동시각 이벤트는 접수만 기록하고 최신 상태를 덮어쓰지 않는다.
        if last is not None and event.observed_at <= last:
            return
        # 번호/LED 관측은 점유 관측이 아니므로 안정화·최신 점유 시각을 갱신하지 않는다.
        if event.event_type not in ('vehicle_observed', 'space_empty_observed'):
            return
        if self._identity_clock is not None:
            self._update_identity_evidence(row, event)
        candidate = row['candidate']
        gap = (event.observed_at - last).total_seconds() if last else None
        row.update(observed_at=event.observed_at, source=event.source,
                   is_mock=event.source in ('simulation', 'manual'), version=row['version'] + 1)
        if event.confidence < self.policy.min_confidence or (
            event.event_type == 'space_empty_observed' and event.car_id is not None
        ):
            row.update(status='uncertain', car_id=None, vehicle_type=None, candidate=None)
            return
        target = 'occupied' if event.event_type == 'vehicle_observed' else 'empty'
        car = event.car_id if target == 'occupied' else None
        observed_type = event.vehicle_type if target == 'occupied' else None
        signature = (target, car, event.source, observed_type)
        max_gap = self.policy.max_gap_seconds
        if event.source == 'simulation' and event.lot_id in ('C', 'D'):
            # The virtual loop refreshes both lots sequentially under database load.
            # Stay below the 10s query staleness so a slow round never resets confirmed bays.
            max_gap = max(max_gap, SIMULATION_MAX_GAP_SECONDS)
        if candidate is None or candidate[0] != signature or gap is None or gap > max_gap:
            candidate = (signature, event.observed_at)
        elapsed = (event.observed_at - candidate[1]).total_seconds()
        required = self.policy.occupy_seconds if target == 'occupied' else self.policy.empty_seconds
        if elapsed >= required:
            row.update(status=target, car_id=car, vehicle_type=observed_type)
        else:
            row.update(status='uncertain', car_id=None, vehicle_type=None)
        row['candidate'] = candidate

    def spaces(self, lot_id: str, *, as_of: datetime):
        if as_of.tzinfo is None or as_of.utcoffset() is None:
            raise ValueError('조회 기준 시각에 시간대가 필요합니다.')
        with self._state_lock:
            rows = [deepcopy(v) for (lot, _), v in self._spaces.items() if lot == lot_id]
        if not rows:
            raise HTTPException(404, '주차장이 없습니다.')
        for row in rows:
            row.pop('candidate')
            for key in ('identity_evidence', 'identity_match', 'identity_observation', 'move_evidence'):
                row.pop(key, None)
            timestamp = row['observed_at']
            if timestamp is not None and (as_of - timestamp).total_seconds() > self.policy.stale_seconds:
                row.update(status='uncertain', car_id=None, vehicle_type=None)
                row.pop('session_id', None)
            row.pop('session_car_id', None)
        return rows
