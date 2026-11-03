"""프로세스 하나에서만 유효한 Step 2 실험용 메모리 접수함. DB 대체물이 아니다."""

import json
from datetime import datetime, timezone
from enum import Enum
from threading import Lock

from backend.app.api.vision_event_schema import VisionEvent


class Receipt(Enum):
    ACCEPTED = "accepted"
    DUPLICATE = "duplicate"
    CONFLICT = "conflict"
    FULL = "full"


def canonical_payload(event: VisionEvent) -> bytes:
    """검증된 전체 필드를 비교한다. 키 순서·동일 순간의 시간대 표기 차이는 무시한다."""
    content = event.model_dump(mode="json")
    # UTC datetime으로 변환하면 연도 1/9999 경계에서 overflow가 날 수 있다.
    delta = event.observed_at - datetime(1970, 1, 1, tzinfo=timezone.utc)
    content["observed_at"] = ((delta.days * 86400 + delta.seconds) * 1000000
                              + delta.microseconds)
    return json.dumps(content, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


class InMemoryEventStore:
    """확인과 등록을 같은 잠금 안에서 수행한다. 외부 상태 변경은 하지 않는다."""

    def __init__(self, *, max_events: int = 1000):
        if type(max_events) is not int or max_events < 1:
            raise ValueError("max_events는 양의 정수여야 합니다.")
        self._max_events = max_events
        self._records: dict[str, bytes] = {}
        self._lock = Lock()

    def receive(self, event: VisionEvent) -> Receipt:
        payload = canonical_payload(event)
        with self._lock:
            existing = self._records.get(event.event_id)
            if existing is not None:
                return Receipt.DUPLICATE if existing == payload else Receipt.CONFLICT
            if len(self._records) >= self._max_events:
                return Receipt.FULL
            self._records[event.event_id] = payload
            return Receipt.ACCEPTED

    def __len__(self) -> int:
        with self._lock:
            return len(self._records)
