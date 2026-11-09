"""대화 상태·일회성 확인·요청 재전송 기록. 저장소 교체와 기능 확장의 경계."""
from __future__ import annotations

import threading
import time
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from .session_flow import Flow as Flow, Pending as Pending


class Conflict(ValueError):
    """이전 화면/동일 요청 키의 다른 내용 등 재조회가 필요한 충돌."""


@dataclass
class Session:
    lock: object = field(default_factory=threading.RLock, repr=False)
    car_id: str | None = None
    mentioned_vehicles: deque = field(default_factory=lambda: deque(maxlen=10))
    payment_vehicle_choices: tuple[str, ...] = ()
    last_space: tuple[str, str] | None = None
    pending: Pending | None = None
    last_intent: str | None = None
    fail_count: int = 0
    history: deque = field(default_factory=lambda: deque(maxlen=100))
    requests: OrderedDict = field(default_factory=OrderedDict)
    destination: dict | None = None
    destination_expires: float = 0
    destination_problem: tuple | None = None
    query_context: dict = field(default_factory=dict)
    query_context_at: float = 0
    # 대화 맥락(dialog_context.py): 마지막으로 다룬 주차장, 방금 고르라고 보여 준 주차장 목록
    focus_lot: str | None = None
    focus_at: float = 0
    offered_lots: tuple = ()
    offered_at: float = 0
    facility_question: dict = field(default_factory=dict)
    facility_question_at: float = 0
    agent_followup: dict = field(default_factory=dict)
    agent_vehicle: dict = field(default_factory=dict)
    conversation_question: str | None = None
    conversation_reply: str | None = None
    conversation_at: float = 0
    conversation_failures: int = 0
    forecast_question: dict = field(default_factory=dict)
    forecast_at: float = 0
    alert_unavailable_seen: bool = False
    alerts_source: str = "ok"
    observed_alert: int = 0
    candidate_options: dict = field(default_factory=dict)
    candidate_expires: float = 0
    selected_candidate: dict | None = None
    last_seen: float = field(default_factory=time.monotonic)

    @property
    def flow(self):
        return self.pending.flow if self.pending else Flow.IDLE

    def begin(self, flow, intent=None, params=None, car_id=None):
        self.pending = Pending(flow, intent, dict(params or {}), car_id)
        return self.pending

    def clear(self):
        self.pending = None

    def expire(self):
        if self.pending and time.monotonic() >= self.pending.expires_at:
            self.clear()
            return True
        return False
