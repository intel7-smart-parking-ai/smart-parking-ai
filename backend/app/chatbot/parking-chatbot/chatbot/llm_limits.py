"""Process-local provider quota and bounded cache for chatbot sessions."""
from collections import OrderedDict, deque
from contextvars import ContextVar
from copy import deepcopy
from datetime import datetime
import hashlib
import threading
import time
import unicodedata

from . import config

session_id = ContextVar("llm_session_id", default=None)


class RouteLimits:
    def __init__(self):
        self.lock = threading.Lock()
        self.minute = deque()
        self.turns = deque()
        self.day = None
        self.daily_calls = 0
        self.cache = OrderedDict()

    def admit(self, session):
        if session is None:
            return True
        with self.lock:
            current = time.monotonic()
            while self.minute and current - self.minute[0][0] >= 60:
                self.minute.popleft()
            today = datetime.now(config.KST).date()
            if self.day is None or today > self.day:
                self.day, self.daily_calls = today, 0
            if (len(self.minute) >= config.LLM_MAX_PER_MIN
                    or sum(sid == session for _, sid in self.minute) >= config.LLM_MAX_PER_SESSION_MIN
                    or (config.LLM_MAX_PER_DAY and self.daily_calls >= config.LLM_MAX_PER_DAY)):
                return False
            self.minute.append((current, session))
            self.daily_calls += 1
            return True

    def admit_turn(self, session):
        """대화 에이전트용: 질문(턴) 1개를 한 번만 센다. 한 턴 안의 도구 선택·답 작성 호출은 따로 세지 않는다."""
        if session is None:
            return True
        with self.lock:
            current = time.monotonic()
            while self.turns and current - self.turns[0][0] >= 60:
                self.turns.popleft()
            today = datetime.now(config.KST).date()
            if self.day is None or today > self.day:
                self.day, self.daily_calls = today, 0
            if (len(self.turns) >= config.LLM_MAX_PER_MIN
                    or sum(sid == session for _, sid in self.turns) >= config.LLM_MAX_PER_SESSION_MIN
                    or (config.LLM_MAX_PER_DAY and self.daily_calls >= config.LLM_MAX_PER_DAY)):
                return False
            self.turns.append((current, session))
            self.daily_calls += 1
            return True

    def key(self, text, identity):
        if session_id.get() is None or not config.LLM_ROUTE_CACHE_SEC:
            return None
        normalized = " ".join(unicodedata.normalize("NFKC", text).casefold().split())
        return (identity, config.catalog.version, config.DATA_SOURCE, config.VEHICLE_MIN, config.VEHICLE_MAX,
                hashlib.sha256(normalized.encode("utf-8")).digest())

    def lookup(self, key):
        if key is None:
            return None
        with self.lock:
            row = self.cache.get(key)
            if row is None:
                return None
            created, value = row
            if time.monotonic() - created >= config.LLM_ROUTE_CACHE_SEC:
                del self.cache[key]
                return None
            self.cache.move_to_end(key)
            return deepcopy(value)

    def save(self, key, value):
        # A model-suggested vehicle number must never be reused across requests.
        if (key is None or value is None or value["intent"] == "unknown"
                or "car_id" in value["params"] or key[1] != config.catalog.version):
            return
        with self.lock:
            self.cache[key] = (time.monotonic(), deepcopy(value))
            self.cache.move_to_end(key)
            while len(self.cache) > 500:
                self.cache.popitem(last=False)


limits = RouteLimits()
