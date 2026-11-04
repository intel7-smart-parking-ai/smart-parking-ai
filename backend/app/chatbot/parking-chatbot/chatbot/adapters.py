"""데이터 연결층.

챗봇은 이 파일의 함수만 부른다. DATA_SOURCE=mock 이면 가짜 데이터,
DATA_SOURCE=api 이면 팀 백엔드(API v1)를 호출한다.
API v1이 확정되면 ApiAdapter 안의 경로·필드 이름만 고치면 된다.
"""
from __future__ import annotations

import json
import logging
import math
from collections import Counter
import threading
import time
import uuid
import urllib.request
from urllib.parse import quote
from copy import deepcopy
from contextvars import ContextVar
from concurrent.futures import Future
from datetime import datetime, timedelta

from .diagnostics import logger, warn_periodically
from .snapshots import freeze
from .breaker import CircuitBreaker
from . import config, contracts as C
from .config import KST
from .contracts import validate, timestamp, require
from .transport import load_json

request_gets = ContextVar("request_gets", default=None)
request_lot = ContextVar("request_lot", default=None)

request_deadline = ContextVar("backend_request_deadline", default=None)


def request_timeout(limit):
    """한 화면 요청의 여러 외부 조회가 제한 시간을 각각 모두 소비하지 않도록 한다."""
    deadline = request_deadline.get()
    if deadline is None:
        return limit
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("요청 조회 시간 한도를 넘었습니다.")
    return min(limit, remaining)


def now() -> datetime:
    return datetime.now(KST)


def iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def normalize_car_id(value):
    """실물 차량 번호(D-06): '10'~'99' 두 자리 문자열만. 한 자리를 0으로 채우지 않는다."""
    from .parser import normalize_car_id as rule, _FULLWIDTH
    result = rule(value.strip().translate(_FULLWIDTH)) if isinstance(value, str) else None
    if result is None:
        raise ValueError("차량 번호는 10부터 99까지 두 자리 숫자 문자열이어야 합니다.")
    return result


VEHICLE_TYPES = {"general", "ev", "accessible"}


def default_policy():
    return {"base_minutes": config.BASE_MINUTES, "base_fee": config.BASE_FEE, "unit_minutes": config.UNIT_MINUTES,
            "extra_fee": config.UNIT_FEE, "daily_cap": config.DAILY_CAP}


def fee_details(minutes: int, policy: dict | None = None) -> tuple[int, bool]:
    """목업의 일 단위 상한·부분일 요금(billing calculate_parking_fee_pure와 같은 규칙). 서버 요금 계산에는 사용하지 않는다."""
    p = policy or default_policy()
    minutes = max(1, math.ceil(minutes))
    days, remaining = divmod(minutes, 1440)
    remainder_fee = 0
    if remaining or not days:
        units = max(0, math.ceil((remaining - p["base_minutes"]) / p["unit_minutes"]))
        remainder_fee = min(p["base_fee"] + units * p["extra_fee"], p["daily_cap"])
    return days * p["daily_cap"] + remainder_fee, remainder_fee >= p["daily_cap"] or days > 0


def build_payment_body(session_id, quote_id, idempotency_key):
    """팀 결제 요청 계약(billing PaymentCreateRequest, extra=forbid). 결제 body를 만드는 유일한 지점."""
    return {"session_id": session_id, "quote_id": quote_id, "idempotency_key": idempotency_key, "payment_method": "MOCK_CARD"}


def calc_fee(minutes: int, policy: dict | None = None) -> int:
    return fee_details(minutes, policy)[0]


def _valid_car(value):
    from .parser import normalize_car_id as rule
    if rule(value) is None:
        return False
    assigned = config.catalog.settings.get("assigned_car_ids")
    return assigned is None or value in assigned


# ---------------------------------------------------------------- 가짜 데이터
class MockAdapter:
    """lots.json의 주차장·칸·구역·차량 배치로 생성하는 시연 데이터."""


    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.reset()

    def reset(self) -> None:
        t = now()
        config.catalog.reset()
        definitions = config.catalog.lots()
        self.capacity = {k:v["capacity"] for k,v in definitions.items()}
        self.restrictions = {}  # PROVISIONAL(API v1 확정 필요)
        self.unrecognized = []
        self.uncertain = {}
        self.demo_occupied = {}  # 시연용 점유 칸 {lot: set(spot)}. 차량 번호가 없는 가상 점유
        self.cars = {}
        for lot, definition in definitions.items():
            for no, spot, minutes in definition.get("mock_cars",[]):
                if not _valid_car(no) or not 1 <= spot <= self.capacity[lot]:continue
                if no in self.cars:raise ValueError("mock 차량 번호가 중복됩니다.")
                self.cars[no] = {"lot_id":lot,"spot_no":spot,"entry_at":t-timedelta(minutes=minutes),"paid_at":None}
        for lot, definition in definitions.items():
            if definition.get("mock_full"):
                used = {c["spot_no"] for c in self.cars.values() if c["lot_id"] == lot}
                for spot in range(1,self.capacity[lot]+1):
                    if spot in used:continue
                    # 만차 시연용 차량 번호는 90부터 내려가며 배정한다(시연·검사에서 쓰는 낮은 번호와 겹치지 않게).
                    no = next((str(n) for n in range(min(90, config.VEHICLE_MAX), config.VEHICLE_MIN-1, -1) if _valid_car(str(n)) and str(n) not in self.cars),None)
                    if no is None:raise ValueError("만차 시연과 미입차 차량을 함께 만들 차량 번호가 부족합니다.")
                    self.cars[no] = {"lot_id":lot,"spot_no":spot,"entry_at":t-timedelta(minutes=45),"paid_at":None}
        self.alert = {"level": 0, "version": 0}
        self.payments = {}
        self.quotes = {}

    def get_empty_spots(self) -> C.OccupancySnapshot:
        with self.lock:
            lots, states = {}, {}
            for lot, total in self.capacity.items():
                occupied = self._occupied_spots(lot)
                uncertain = self.uncertain.get(lot, set())
                states[lot] = {config.catalog.space_label(lot, n): ("uncertain" if n in uncertain else "occupied" if n in occupied else "empty")
                               for n in range(1, total + 1)}
                used = len(occupied)
                info = config.catalog.lot(lot)
                zones = {key:{"total":len(spots),"empty":sum(spot not in occupied for spot in spots),"uncertain":sum(spot in self.uncertain.get(lot,set()) for spot in spots)}
                         for key,spots in config.catalog.zone_spots(total, lot).items()}
                lots[lot] = {"total":total,"empty":total-used,"uncertain":len(self.uncertain.get(lot,set())),"name":info["name"],"kind":info["kind"],
                             "zones":zones,**self.restrictions.get(lot,{})}  # PROVISIONAL(API v1 확정 필요)
        return {"lots": lots, "space_states": states, "updated_at": iso(now())}

    def find_car(self, car_id: str) -> C.VehicleLocation | None:
        with self.lock:
            car = self.cars.get(car_id)
            car = dict(car) if car else None
        if not car:
            return None
        return {"car_id": car_id, "lot_id": car["lot_id"], "spot_no": car["spot_no"],
                "space_id": config.catalog.space_label(car["lot_id"], car["spot_no"]),
                "capacity": self.capacity[car["lot_id"]],
                "entry_at": iso(car["entry_at"]), "updated_at": iso(now())}

    def get_fee(self, car_id: str) -> dict | None:
        with self.lock:
            car = self.cars.get(car_id)
            car = dict(car) if car else None
        if not car:
            return None
        policy = config.catalog.rate_policy(car["lot_id"]) or default_policy()
        minutes = max(1, math.ceil((now() - car["entry_at"]).total_seconds() / 60))
        amount, capped = fee_details(minutes, policy)
        quote_id = str(uuid.uuid4())
        expires = now() + timedelta(seconds=config.MOCK_QUOTE_TTL_SEC)
        with self.lock:
            self.quotes[quote_id] = {"car_id": car_id, "amount": amount, "entry_at": iso(car["entry_at"]), "expires": expires}
            while len(self.quotes) > 1000:
                self.quotes.pop(next(iter(self.quotes)))
        return {
            "car_id": car_id,
            "lot_id": car["lot_id"],
            "entry_at": iso(car["entry_at"]),
            "minutes": minutes,
            "fee": amount,
            "is_daily_capped": capped,
            "fee_after_30": calc_fee(minutes + 30, policy),
            "free_minutes": 0,
            "paid": car["paid_at"] is not None,
            "rate_policy": policy,
            "quote_id": quote_id,
            "quote_expires_at": iso(expires),
            "updated_at": iso(now()),
        }

    def pay(self, car_id: str, *, idempotency_key=None, quote_amount=None, entry_at=None, quote_id=None) -> dict:
        with self.lock:
            if idempotency_key and idempotency_key in self.payments:
                return deepcopy(self.payments[idempotency_key])
            car = self.cars.get(car_id)
            if not car:
                return {"status": "not_found"}
            if car["paid_at"]:
                return {"status": "already_paid", "paid_at": iso(car["paid_at"])}
            if quote_id is not None:
                # billing과 같은 견적 검사: 없음·다른 차량(세션)·만료
                quote = self.quotes.get(quote_id)
                if quote is None:
                    return {"status": "quote_not_found"}
                if quote["car_id"] != car_id or quote["entry_at"] != iso(car["entry_at"]):
                    return {"status": "quote_mismatch"}
                if now() >= quote["expires"]:
                    return {"status": "quote_expired"}
            minutes = max(1, math.ceil((now() - car["entry_at"]).total_seconds() / 60))
            amount = calc_fee(minutes, config.catalog.rate_policy(car["lot_id"]))
            if ((quote_amount is not None and amount != quote_amount) or
                    (entry_at is not None and iso(car["entry_at"]) != entry_at)):
                return {"status": "quote_changed"}
            if not amount:
                return {"status": "free", "amount": 0, "paid_at": iso(now())}
            car["paid_at"] = now()
            result = {"status": "paid", "amount": amount, "paid_at": iso(car["paid_at"])}
            if idempotency_key:
                self.payments[idempotency_key] = deepcopy(result)
            return result

    def get_alert(self) -> dict:
        with self.lock:
            return dict(self.alert)

    def list_cars(self, lot_id: str | None = None) -> list[dict]:
        """주차 중인 차량 목록 (상황 보고서의 영향 범위 계산용)."""
        with self.lock:
            rows = [{"car_id":no,"lot_id":c["lot_id"],"spot_no":c["spot_no"],
                     "space_id":config.catalog.space_label(c["lot_id"], c["spot_no"]),"entry_at":iso(c["entry_at"])}
                    for no,c in self.cars.items()] + deepcopy(self.unrecognized)
        return sorted((c for c in rows if lot_id is None or c["lot_id"] == lot_id), key=lambda c:(c["lot_id"],c["spot_no"]))

    def _occupied_spots(self, lot):
        return {c["spot_no"] for c in self.cars.values() if c["lot_id"] == lot} | self.demo_occupied.get(lot, set()) | self.uncertain.get(lot, set()) | {c["spot_no"] for c in self.unrecognized if c["lot_id"] == lot}

    def _check_lot(self, lot_id):
        if lot_id not in self.capacity:
            raise ValueError("등록되지 않은 주차장입니다.")

    # 시연용: 입차 1대. car_id가 있으면 그 차량을 입차, 없으면 차량 번호 없는 시연용 점유
    def enter_car(self, lot_id, car_id=None) -> dict:
        self._check_lot(lot_id)
        if car_id is not None:
            car_id = normalize_car_id(car_id)
        with self.lock:
            free = sorted(set(range(1, self.capacity[lot_id] + 1)) - self._occupied_spots(lot_id))
            if not free:
                return {"ok": False, "reason": "만차", "lot_id": lot_id}
            spot = free[0]
            if car_id is not None:
                if car_id in self.cars:
                    return {"ok": False, "reason": "이미 주차 중", "car_id": car_id, "lot_id": self.cars[car_id]["lot_id"]}
                self.cars[car_id] = {"lot_id": lot_id, "spot_no": spot, "entry_at": now(), "paid_at": None}
                return {"ok": True, "car_id": car_id, "lot_id": lot_id, "spot_no": spot}
            self.demo_occupied.setdefault(lot_id, set()).add(spot)
            return {"ok": True, "demo": True, "lot_id": lot_id, "spot_no": spot}

    # 시연용: 남은 칸을 모두 시연용 점유로 채워 만차로 만든다
    def fill_lot(self, lot_id) -> dict:
        self._check_lot(lot_id)
        with self.lock:
            free = set(range(1, self.capacity[lot_id] + 1)) - self._occupied_spots(lot_id)
            self.demo_occupied.setdefault(lot_id, set()).update(free)
            return {"ok": True, "demo": True, "lot_id": lot_id, "filled": len(free)}

    # 시연용: 시연용 점유만 비운다 (실제 차량 기록은 그대로)
    def clear_demo(self, lot_id=None) -> dict:
        with self.lock:
            if lot_id:
                self._check_lot(lot_id)
                removed = len(self.demo_occupied.pop(lot_id, set()))
            else:
                removed = sum(len(v) for v in self.demo_occupied.values())
                self.demo_occupied.clear()
            return {"ok": True, "lot_id": lot_id, "cleared": removed}

    # 시연용: 운영 제한 켜기/끄기 (PROVISIONAL 필드 entry_allowed·restriction_reason)
    def set_restriction(self, lot_id, restricted, reason="") -> dict:
        self._check_lot(lot_id)
        if type(restricted) is not bool:
            raise ValueError("restricted는 JSON boolean이어야 합니다.")
        reason = str(reason or "").strip()[:30]
        with self.lock:
            if restricted:
                self.restrictions[lot_id] = {"entry_allowed": False, **({"restriction_reason": reason} if reason else {})}
            else:
                self.restrictions.pop(lot_id, None)
            return {"ok": True, "lot_id": lot_id, "restricted": bool(restricted), "reason": reason}

    def enter_unrecognized(self, lot_id):
        self._check_lot(lot_id)
        with self.lock:
            free = sorted(set(range(1,self.capacity[lot_id]+1))-self._occupied_spots(lot_id))
            if not free: return {"ok":False,"reason":"만차"}
            car = {"car_id":None,"lot_id":lot_id,"spot_no":free[0],"space_id":config.catalog.space_label(lot_id, free[0]),"entry_at":iso(now())}
            self.unrecognized.append(car)
            return {"ok":True,**car}

    def set_uncertain(self, lot_id, spot_no, uncertain):
        self._check_lot(lot_id)
        if type(spot_no) is not int or not 1 <= spot_no <= self.capacity[lot_id] or type(uncertain) is not bool:
            raise ValueError("칸 번호와 uncertain boolean이 필요합니다.")
        with self.lock:
            spots = self.uncertain.setdefault(lot_id,set())
            if uncertain:
                if spot_no in self._occupied_spots(lot_id) - spots:
                    raise ValueError("이미 점유된 칸은 비운 뒤 변경해 주세요.")
                spots.add(spot_no)
            else: spots.discard(spot_no)
        return {"ok":True,"lot_id":lot_id,"spot_no":spot_no,"uncertain":uncertain}

    def demo_state(self) -> dict:
        with self.lock:
            return {"uncertain":{k:sorted(v) for k,v in self.uncertain.items() if v}, "demo_occupied": {k: sorted(v) for k, v in self.demo_occupied.items() if v},
                    "restrictions": {k: dict(v) for k, v in self.restrictions.items()}}

    # 시연용: 차 한 대를 출차시켜 빈자리를 만든다 (빈자리 대기 알림 시연)
    def exit_car(self, car_id: str) -> dict:
        car_id = normalize_car_id(car_id)
        with self.lock:
            car = self.cars.pop(car_id, None)
        return {"ok": car is not None, "car_id": car_id, "lot_id": car["lot_id"] if car else None}

    def exit_for(self, lot_id: str) -> str:
        return (config.catalog.lot(lot_id) or {}).get("exit", "가장 가까운 출구")

    # 시연용: 경보를 수동으로 띄우거나 해제 (LED 쪽 수동 이벤트 시연 대안과 같은 역할)
    def set_alert(self, level: int, lot_id: str | None = None, spot_no: int | None = None) -> dict:
        if type(level) is not int or level not in (0, 1, 2):
            raise ValueError("level은 0, 1, 2 중 하나여야 합니다.")
        lot_id = lot_id or next(iter(self.capacity))
        if lot_id not in self.capacity:raise ValueError("등록되지 않은 주차장입니다.")
        if spot_no is None:
            # 기본 시연 위치: 그 주차장의 첫 충전(전기차) 칸. 없으면 첫 칸.
            ev = config.catalog.zone_spots(None, lot_id).get("ev") or [1]
            spot_no = ev[0]
        if type(spot_no) is not int or not 1 <= spot_no <= self.capacity[lot_id]:
            raise ValueError("주차칸 범위를 벗어났습니다.")
        with self.lock:
            version = self.alert["version"] + 1
            if level <= 0:
                self.alert = {"level": 0, "version": version}
            else:
                self.alert = {
                    "level": level, "version": version,
                    "event_type": "mock_fire_led_observed",
                    "lot_id": lot_id, "spot_no": spot_no, "space_id": config.catalog.space_label(lot_id, spot_no),
                    "confidence": 0.62 if level == 1 else 0.91,
                    "started_at": iso(now()),
                }
            return dict(self.alert)


# ---------------------------------------------------------------- 팀 백엔드 호출
def _alert_time(item):
    """경보 발생 시각. 팀 /alerts 필드명이 미결(detected_at/occurred_at)이라 둘 다 받는다. occurred_at 우선."""
    for key in ("occurred_at", "detected_at"):
        if isinstance(item.get(key), str) and item[key]:
            return item[key]
    return None


class ApiAdapter:
    """Team API boundary. Never falls back to locally generated parking data."""

    def __init__(self, base_url: str) -> None:
        self.base = base_url.rstrip("/")
        self.lock = threading.RLock()
        self._last_alert = None
        self._alert_fingerprint = None
        self._alert_version = 0
        self._breakers = {name: CircuitBreaker() for name in ("lookup", "billing", "alerts")}

    def breaker_stats(self):
        return {name: breaker.stats() for name, breaker in self._breakers.items()}

    @staticmethod
    def _request_group(path):
        if path == config.API_PATHS["alerts"]:
            return "alerts"
        fee_prefix, _, fee_suffix = config.API_PATHS["fee"].partition("{session_id}")
        if (path in (config.API_PATHS["payment"], config.API_PATHS.get("rate_policies"))
                or (path.startswith(fee_prefix) and path.endswith(fee_suffix))):
            return "billing"
        return "lookup"

    def reset(self):
        with self.lock:
            self._last_alert = None
            self._alert_fingerprint = None
            self._alert_version = 0
            config.catalog.reset()

    def _headers(self):
        return {"Authorization": "Bearer " + config.BACKEND_TOKEN} if config.BACKEND_TOKEN else {}

    def _get(self, path):
        req = urllib.request.Request(self.base + path, headers=self._headers())
        return load_json(req, request_timeout(config.BACKEND_TIMEOUT_SEC))

    def _post(self, path, body, idempotency_key=None):
        req = urllib.request.Request(self.base + path, data=json.dumps(body).encode("utf-8"),
                                     headers={"Content-Type": "application/json", **self._headers()}, method="POST")
        return load_json(req, request_timeout(config.BACKEND_TIMEOUT_SEC))

    def _request(self, method, path, body=None):
        cache = request_gets.get()
        key = (id(self), method, path)
        if method == "GET" and cache is not None and key in cache:
            return deepcopy(cache[key])
        result = self._request_uncached(method, path, body)
        if method == "GET" and cache is not None:
            cache[key] = deepcopy(result)
        return result

    def _request_uncached(self, method, path, body=None):
        group = self._request_group(path)
        breaker = self._breakers[group]
        try:
            timeout = request_timeout(config.BACKEND_TIMEOUT_SEC)
        except TimeoutError:
            if method == "POST":
                raise PaymentNotSent("결제 요청 시간 한도 초과")
            return 0, {}, {}
        ticket = breaker.admit(may_probe=method == "GET")
        if ticket is None:
            if method == "POST":
                raise PaymentNotSent("결제 경로 일시 차단")
            return -1, {}, {}
        failed = False
        try:
            req = urllib.request.Request(self.base + path,
                data=json.dumps(body).encode("utf-8") if body is not None else None,
                headers={**self._headers(), **({"Content-Type": "application/json"} if body is not None else {})},
                method=method)
            status, value, headers = load_json(req, timeout, with_metadata=True)
            failed = 500 <= status <= 599
            if status in (401, 403):
                warn_periodically(logger(__name__), ("auth", group, status),
                                  "API authentication rejected: group=%s status=%s", group, status)
            require(isinstance(value, dict), "API 응답은 객체여야 합니다.")
            return status, value, headers
        except (OSError, ValueError) as exc:
            failed = failed or isinstance(exc, OSError)
            warn_periodically(logger(__name__), ("transport", group, type(exc)),
                              "API transport error: %s (%s)", group, type(exc).__name__)
            return 0, {}, {}
        finally:
            breaker.complete(ticket, failed)

    @staticmethod
    def _meta(headers):
        return {"api_result": True, **({"data_source": "mock"} if any(
            k.lower() == "x-parkflow-data-source" and v == "mock" for k, v in headers.items()) else {})}

    def _failure(self, status, value, headers):
        error = value.get("error", {}) if isinstance(value, dict) else {}
        code = error.get("code") if isinstance(error, dict) else None
        if status not in (-1, 401, 403):
            warn_periodically(logger(__name__), ("status", status), "API response: status=%s", status)
        result = "conflict" if code == "IDEMPOTENCY_CONFLICT" else "error" if status in (400, 422) else "unavailable"
        return {"status": result, **({"unavailable": True} if result == "unavailable" else {}),
                "error_code": code, "http_status": max(status, 0),
                **({"breaker_blocked": True} if status == -1 else {}), **self._meta(headers)}

    @staticmethod
    def _spot(space_id, lot_id):
        """서버 칸 이름(도면 번호) → 실행 중 명부의 순번. 명부에 없으면 서버 기준으로 추가한다."""
        if lot_id is None:
            return None
        label = None
        if isinstance(space_id, str) and len(space_id) <= 16:
            from .catalog import normalize_space
            label = normalize_space(space_id)
        if label is None:
            logger(__name__).warning("Invalid space_id")
            return None
        return config.catalog.space_index(lot_id, label) or config.catalog.register_space(lot_id, label)

    _warned_cars = set()
    _warned_lock = threading.Lock()

    @classmethod
    def _server_car(cls, value, where):
        """규칙(10~99 두 자리)에 맞지 않는 서버 car_id는 그 항목만 미인식(null)으로 본다. 같은 값은 한 번만 기록."""
        from .parser import normalize_car_id as rule
        if value is None or rule(value):
            return value
        key = (repr(value), where)
        with cls._warned_lock:
            first = key not in cls._warned_cars and len(cls._warned_cars) < 500
            if first:
                cls._warned_cars.add(key)
        if first:
            logger(__name__).warning("Rule-violating car_id from server treated as unrecognized: %s", where)
        return None

    @staticmethod
    def _mock_row(row):
        return row.get("is_mock") is True or row.get("source") in ("simulation", "manual")

    @staticmethod
    def _entry_at(value):
        try:
            return value if timestamp(value) <= now() + timedelta(seconds=5) else None
        except (ValueError, OverflowError):
            return None

    def _space_snapshot(self, lot, total):
        """Normalize one /spaces response once per request, retaining completeness."""
        from .catalog import normalize_space

        cache = request_gets.get()
        key = (id(self), "space_snapshot", lot, total)
        if cache is not None and key in cache:
            return cache[key]
        path = config.API_PATHS["spaces"].format(lot_id=quote(lot, safe=""))
        status, body, headers = self._request("GET", path)
        rows = {}
        complete = status == 200 and isinstance(body.get("items"), list) and not body.get("next_cursor")
        if complete:
            for item in body["items"]:
                if not isinstance(item, dict):
                    complete = False
                    continue
                label = normalize_space(item.get("space_id"))
                state = item.get("status")
                if (not label or label in rows or item.get("lot_id", lot) != lot
                        or state not in ("empty", "occupied", "uncertain", "unavailable")):
                    complete = False
                    continue
                car_id = self._server_car(item.get("car_id"), f"{lot} {label}")
                rows[label] = {
                    "status": state, "car_id": car_id, "vehicle_type": item.get("vehicle_type"),
                    "space_type": item.get("space_type"), "observed_at": item.get("observed_at"),
                    "source": item.get("source"), "is_mock": self._mock_row(item),
                    "entry_at": self._entry_at(item.get("entry_at")),
                }
        complete = complete and len(rows) == total
        if complete:
            config.catalog.adopt_spaces(lot, list(rows))
        result = rows, complete, self._meta(headers).get("data_source") == "mock"
        if cache is not None:
            cache[key] = result
        return result

    def get_empty_spots(self) -> C.OccupancySnapshot:
        status, value, headers = self._request("GET", config.API_PATHS["lots"])
        if status != 200:
            return self._failure(status, value, headers)
        require(isinstance(value, dict) and isinstance(value.get("items"), list), "주차장 목록 형식 오류")
        lots, states = {}, {}
        unrecognized = []
        mocked = False
        zone_keys = {z["key"] for z in config.catalog.zones()}
        for item in value["items"]:
            require(isinstance(item, dict) and isinstance(item.get("lot_id"), str)
                    and all(type(item.get(k)) is int and item[k] >= 0 for k in ("total_spaces", "empty_spaces")), "주차장 필드 오류")
            lot = item["lot_id"]
            require(lot not in lots, "중복 주차장 ID")
            info = {"total": item["total_spaces"], "empty": item["empty_spaces"],
                    "uncertain": item.get("uncertain_spaces", 0), "unavailable": item.get("unavailable_spaces", 0),
                    "name": item.get("name") or item.get("lot_name") or config.catalog.name(lot)}
            if request_lot.get() in (None, lot):
                rows, complete, mock_header = self._space_snapshot(lot, info["total"])
                mocked = mocked or mock_header or any(row["is_mock"] for row in rows.values())
                counts = Counter()
                zone_rows = {key: {"total": 0, "empty": 0, "uncertain": 0} for key in zone_keys}
                for label, space in rows.items():
                    state = space["status"]
                    counts[state] += 1
                    states.setdefault(lot, {})[label] = state
                    server_type = space.get("space_type")
                    configured = config.catalog.space_zone(lot, label)
                    zone = server_type if isinstance(server_type, str) and server_type in zone_keys else configured
                    if isinstance(server_type, str) and server_type in zone_keys and configured not in (None, server_type):
                        logger(__name__).warning("space_type differs from settings, using server")
                    if zone is None:
                        info["zone_partial"] = True
                    else:
                        row = zone_rows[zone]
                        row["total"] += 1
                        if state in ("empty", "uncertain"):
                            row[state] += 1
                    car = space["car_id"]
                    if state == "occupied" and car is None:
                        spot = self._spot(label, lot)
                        unrecognized.append({"car_id": None, "lot_id": lot, "spot_no": spot, "space_id": label, "session_id": None})
                complete = complete and all(
                    counts[state] == info[field] for state, field in (("empty", "empty"), ("uncertain", "uncertain"), ("unavailable", "unavailable")))
                if complete:
                    info["zones"] = zone_rows
            lots[lot] = info
        return {"lots": lots, "space_states": states, "unrecognized": unrecognized, "as_of": value.get("as_of"),
                "updated_at": value.get("as_of"), **self._meta(headers),
                **({"data_source": "mock"} if mocked else {})}

    def find_car(self, car_id) -> C.VehicleLocation | dict | None:
        path = config.API_PATHS["location"].format(car_id=quote(car_id, safe=""))
        status, value, headers = self._request("GET", path)
        if status == 404 and isinstance(value.get("error"), dict) and value["error"].get("code") == "VEHICLE_NOT_FOUND":
            return None
        if status != 200:
            return self._failure(status, value, headers)
        provenance = self._meta(headers)
        if self._mock_row(value):
            provenance["data_source"] = "mock"
        state = value.get("status")
        if value.get("car_id") == car_id and ((isinstance(state, str) and state in C.PENDING_LOCATION_STATUSES)
                                              or ("space_id" in value and value.get("space_id") is None)):
            # 번호는 인식됐지만 칸 배정 전: 위치를 지어내지 않고 "입차 확인 중"으로 답한다.
            lot = value.get("lot_id") if isinstance(value.get("lot_id"), str) and config.catalog.lot(value["lot_id"]) is not None else None
            return {"car_id": car_id, "status": C.LOCATION_PENDING, "pending": True, "lot_id": lot,
                    "session_id": value.get("session_id") if isinstance(value.get("session_id"), str) else None,
                    "updated_at": value.get("observed_at"), **provenance}
        require(value.get("status") in ("occupied", "uncertain"), "위치 상태 오류")
        require(value.get("session_id") is None or (isinstance(value.get("session_id"), str) and bool(value["session_id"])), "위치 세션 형식 오류")
        timestamp(value.get("observed_at"))
        require(value.get("car_id") == car_id, "요청 차량과 응답 차량 불일치")
        require(isinstance(value.get("lot_id"), str), "위치 주차장 오류")
        lot = value["lot_id"]
        require(config.catalog.lot(lot) is not None, "위치가 서버 주차장 범위를 벗어남")
        spot = self._spot(value.get("space_id"), lot)
        require(spot is not None, "위치의 칸 ID 오류")
        vehicle_type = value.get("vehicle_type")
        if vehicle_type is not None and vehicle_type not in VEHICLE_TYPES:
            logger(__name__).warning("Unknown vehicle_type")
            vehicle_type = None
        return {"car_id": car_id, "lot_id": lot, "spot_no": spot, "space_id": config.catalog.space_label(lot, spot),
                "session_id": value.get("session_id"), "vehicle_type": vehicle_type,
                "status": value.get("status"), "confidence": value.get("confidence"),
                "capacity": config.catalog.space_count(lot), "entry_at": value.get("entry_at"),
                "updated_at": value.get("observed_at"), **provenance}

    def get_fee(self, car_id):
        car = self.find_car(car_id)
        if car is None or car.get("status") == "unavailable":
            return car
        if car.get("status") == C.LOCATION_PENDING:
            return {"status": "unavailable", "unavailable": True, "reason": "pending", **self._meta({}),
                    **({"data_source": "mock"} if car.get("data_source") == "mock" else {})}
        session_id = car.get("session_id")
        if not isinstance(session_id, str) or not session_id:
            return {"status": "unavailable", "unavailable": True, "reason": "session", **self._meta({}),
                    **({"data_source": "mock"} if car.get("data_source") == "mock" else {})}
        path = config.API_PATHS["fee"].format(session_id=quote(session_id, safe=""))
        status, value, headers = self._request("GET", path)
        if status != 200:
            return self._failure(status, value, headers)
        self._require_visit(value, car)
        if not isinstance(value.get("payment_status"), str) or value.get("payment_status") not in C.PAYMENT_STATES:
            logger(__name__).warning("Unknown payment status")
            return {"status": "unavailable", "unavailable": True, **self._meta(headers)}
        require(all(k in value for k in ("amount_krw", "duration_minutes", "entry_at")), "요금 필드 오류")
        # 실제 billing 응답 키(target_at, rate_policy, quote_id …). calculated_at·applied_policy_id·session_status는 요구하지 않는다.
        basis = next((value[k] for k in ("target_at", "calculated_at", "as_of") if isinstance(value.get(k), str)), None)
        if basis is not None:
            timestamp(basis)
        quote_id = value.get("quote_id")
        quote_expires = value.get("quote_expires_at")
        if quote_id is not None:
            require(isinstance(quote_id, str) and bool(quote_id), "견적 ID 형식 오류")
            timestamp(quote_expires)
        session_status = value.get("session_status")
        session_status = C.SESSION_ALIASES.get(session_status, session_status) if isinstance(session_status, str) else None
        if session_status is not None and session_status not in (C.SESSION_ACTIVE, C.SESSION_CLOSED):
            session_status = None
        if session_status is None:
            session_status = C.SESSION_CLOSED if value.get("exit_at") else C.SESSION_ACTIVE
        policy = value.get("rate_policy") if isinstance(value.get("rate_policy"), dict) else None
        return {"car_id": car_id, "session_id": session_id, "lot_id": value.get("lot_id") or car.get("lot_id"),
                "fee": value["amount_krw"], "minutes": value["duration_minutes"], "entry_at": value["entry_at"],
                "exit_at": value.get("exit_at"), "session_status": session_status,
                "paid": value["payment_status"] == C.PAYMENT_COMPLETED,
                "payment_status": value["payment_status"], "is_daily_capped": value.get("is_daily_capped", False),
                "rate_policy": policy, "quote_id": quote_id, "quote_expires_at": quote_expires,
                "updated_at": basis or iso(now()), **self._meta(headers)}

    def get_rate_policies(self):
        path = config.API_PATHS.get("rate_policies")
        if not path:
            return {"status": "unavailable", "unavailable": True}
        status, value, headers = self._request("GET", path)
        if status != 200 or not isinstance(value.get("items"), list):
            return self._failure(status, value, headers) if status != 200 else {"status": "unavailable", "unavailable": True}
        from .catalog import RATE_KEYS
        policies = {}
        for item in value["items"]:
            if isinstance(item, dict) and isinstance(item.get("lot_id"), str) and all(type(item.get(k)) is int and item[k] > 0 for k in RATE_KEYS):
                policies[item["lot_id"]] = {k: item[k] for k in RATE_KEYS}
        return {"policies": policies, **self._meta(headers)}

    @staticmethod
    def _require_visit(response, car):
        """Reject a fee or receipt explicitly referring to a different visit."""
        for field in ("session_id", "car_id", "lot_id"):
            if response.get(field) is not None and response[field] != car.get(field):
                # An identity conflict is not a transient read failure. A
                # cached quote may belong to the very visit we must reject.
                raise DataUnavailable("요금·결제 응답의 주차 세션 또는 차량 불일치")

    def pay(self, car_id, *, idempotency_key=None, quote_amount=None, entry_at=None, session_id=None, quote_id=None):
        car = self.find_car(car_id)
        if car is None:
            return {"status": "not_found", "api_result": True}
        if car.get("breaker_blocked"):
            raise PaymentNotSent("위치 조회 경로 일시 차단")
        if car.get("status") == "unavailable" or not car.get("session_id"):
            raise PaymentNotSent("위치 응답에 확인 가능한 세션이 없습니다.")
        if session_id is not None and session_id != car["session_id"]:
            raise PaymentNotSent("결제 확인 이후 주차 세션이 변경됐습니다.")
        if not isinstance(quote_id, str) or not quote_id:
            # 서버 결제는 quote_id가 필수다. 필수 필드 없이 보내 400/422를 받지 않는다.
            return {"status": "quote_missing", "api_result": True}
        require(isinstance(idempotency_key, str) and bool(idempotency_key), "결제 확인 키가 필요합니다.")
        body = build_payment_body(car["session_id"], quote_id, idempotency_key)
        status, value, headers = self._request("POST", config.API_PATHS["payment"], body)
        code = value.get("error", {}).get("code") if isinstance(value.get("error"), dict) else None
        if code in C.PAYMENT_ERROR_STATUS:
            if code == "ALREADY_PAID":
                return {"status": "already_paid", "paid_at": value.get("paid_at"), **self._meta(headers)}
            return {"status": C.PAYMENT_ERROR_STATUS[code], "error_code": code, "http_status": status, **self._meta(headers)}
        if status not in (200, 201):
            return self._failure(status, value, headers)
        self._require_visit(value, car)
        require(type(value.get("amount_krw")) is int and value["amount_krw"] >= 0, "결제 응답 금액 오류")
        # Team billing receipts include status and idempotency_key. Older fixture
        # receipts may omit both fields, so verify the team contract when supplied.
        if "status" in value:
            require(value["status"] == C.PAYMENT_COMPLETED, "결제 응답 상태 오류")
            if quote_amount is not None:
                require(value["amount_krw"] == quote_amount, "결제 응답과 견적 금액 불일치")
        if "idempotency_key" in value:
            require(value["idempotency_key"] == idempotency_key, "결제 응답 멱등키 불일치")
        return {"status": "paid", "amount": value["amount_krw"], "payment_id": value.get("payment_id"),
                "idempotent_replayed": value.get("idempotent_replayed", False),
                "paid_at": value.get("paid_at") or value.get("created_at"), **self._meta(headers)}

    def get_alert(self):
        status, value, headers = self._request("GET", config.API_PATHS["alerts"])
        if status != 200:
            previous = dict(self._last_alert or {"level": 0, "version": 0})
            # Preserve safety state, but provenance belongs to this failed response.
            previous.pop("data_source", None)
            return {**previous, **self._failure(status, value, headers), "status": "unavailable", "unavailable": True}
        require(isinstance(value.get("items"), list), "경보 목록 형식 오류")
        items, violations = [], []
        for candidate in value["items"]:
            require(isinstance(candidate, dict), "경보 항목은 객체여야 합니다.")
            kind = candidate.get("type")
            if kind in C.VIOLATION_ALERT_TYPES:
                # 전용 칸 위반은 화재 경보 레벨과 섞지 않는다.
                if C.alert_level(candidate):
                    lot = candidate.get("lot_id") if isinstance(candidate.get("lot_id"), str) else None
                    spot = self._spot(candidate.get("space_id"), lot) if candidate.get("space_id") is not None else None
                    violations.append({"lot_id": lot, "spot_no": spot, "space_id": config.catalog.space_label(lot, spot) if lot else None,
                                       "space_type": candidate.get("space_type"), "car_id": self._server_car(candidate.get("car_id"), "violation"),
                                       "occurred_at": _alert_time(candidate)})
                continue
            if not isinstance(kind, str) or kind not in C.FIRE_ALERT_TYPES:
                # The standalone alarm probe has historically listened to this logger.
                alert_log = logger(__name__) if config.EMBEDDED else logging.getLogger(__name__)
                alert_log.warning("Ignored alert type")
                continue
            items.append(candidate)
        active = [(C.alert_level(x), x) for x in items if C.alert_level(x)]
        level, item = max(active, key=lambda x: x[0]) if active else (0, {})
        alert = {"level": level, "updated_at": value.get("as_of"), "violations": violations, **self._meta(headers)}
        if level:
            lot = item.get("lot_id")
            require(lot is None or isinstance(lot, str), "경보 주차장 형식 오류")
            space = item.get("space_id")
            spot = self._spot(space, lot) if space is not None and lot is not None else None
            require(space is None or spot is not None, "경보 위치 오류")
            alert.update(lot_id=lot, spot_no=spot, space_id=config.catalog.space_label(lot, spot) if lot else None,
                         started_at=_alert_time(item),
                         confidence=item.get("confidence", 0), manager_checking=item.get("status") == C.ALERT_ACKNOWLEDGED,
                         event_type=item.get("type"), source=item.get("source") if isinstance(item.get("source"), str) else None)
        age = (now() - timestamp(alert["updated_at"])).total_seconds()
        trusted = age >= -5 and (level > 0 or age <= config.DATA_MAX_AGE_SEC)
        with self.lock:
            fingerprint = (level, item.get("alert_id"), item.get("lot_id"), item.get("space_id"), item.get("status"))
            if trusted and fingerprint != self._alert_fingerprint:
                self._alert_version += 1
                self._alert_fingerprint = fingerprint
            alert["version"] = self._alert_version
            alert["resolved_transition"] = bool(not level and (self._last_alert or {}).get("level"))
            # Rejected all-clear/future observations cannot erase the last safe state.
            if trusted:
                self._last_alert = deepcopy(alert)
        return alert

    def list_cars(self, lot_id=None, *, candidates=False):
        status, body, _ = self._request("GET", config.API_PATHS["lots"])
        require(status == 200 and isinstance(body.get("items"), list), "주차장 목록 확인 불가")
        lots = {}
        for row in body["items"]:
            require(isinstance(row, dict) and isinstance(row.get("lot_id"), str)
                    and C.SAFE_ID.fullmatch(row["lot_id"]) and C.integer(row.get("total_spaces"), 1)
                    and row["lot_id"] not in lots, "주차장 목록 형식 오류")
            lots[row["lot_id"]] = row["total_spaces"]
        require(bool(lots) and (lot_id is None or lot_id in lots), "주차장 목록 확인 불가")
        cars = []
        for lot in ([lot_id] if lot_id else lots):
            rows, complete, mocked = self._space_snapshot(lot, lots[lot])
            require(complete, "주차 차량 목록 확인 불가")
            for label, row in rows.items():
                if row["status"] not in ("occupied", "uncertain"):
                    continue
                if candidates and row["car_id"] is not None:
                    continue
                if candidates:
                    require(row["entry_at"] is not None, "번호 미인식 차량 입차 시각 없음")
                spot = config.catalog.space_index(lot, label)
                require(spot is not None, "차량 위치 확인 불가")
                kind = row["vehicle_type"]
                cars.append({"car_id": row["car_id"], "lot_id": lot, "spot_no": spot,
                             "space_id": label, "vehicle_type": kind if isinstance(kind, str) and kind in VEHICLE_TYPES else None,
                             "source": row["source"], "is_mock": mocked or row["is_mock"],
                             **({"entry_at": row["entry_at"]} if candidates else {})})
        return cars

    def exit_for(self, lot_id):
        return (config.catalog.lot(lot_id) or {}).get("exit", "지정 출구")

    def set_alert(self, *args, **kwargs):
        raise ValueError("시연 기능은 DATA_SOURCE=mock에서만 사용할 수 있습니다.")

    enter_car = enter_unrecognized = set_uncertain = fill_lot = clear_demo = set_restriction = demo_state = exit_car = set_alert


# ---------------------------------------------------------------- 실패 대비 캐시
class DataUnavailable(RuntimeError):
    """캐시도 없는 백엔드 조회 실패. 사용자에게 재시도를 안내한다."""


class PaymentNotSent(DataUnavailable):
    """Payment POST was definitely not transmitted."""


class CachedAdapter:
    """백엔드가 응답하지 않으면 마지막으로 받은 값을 stale=True 로 돌려준다."""

    def __init__(self, inner: MockAdapter) -> None:
        self.inner = inner
        self.cache: dict[str, dict] = {}
        self.cached_at = {}
        self.lock = threading.RLock()
        self.inflight = {}
        self.generation = 0
        self.versions = {}
        self.catalog_ready = not isinstance(inner,ApiAdapter)

    def _generation_for(self, key):
        return self.generation, self.versions.get(key, 0)

    def _call(self, key: str, fn, *args):
        with self.lock:
            generation = self._generation_for(key)
            shared = key == "empty" or key.startswith(("empty:", "car:"))
            flight_key = (key, generation)
            flight = self.inflight.get(flight_key) if shared else None
            owner = flight is None
            if shared and owner:
                flight = self.inflight[flight_key] = Future()
        if not shared:
            return self._fetch(key, fn, args, generation)
        if not owner:
            try:
                value = flight.result(timeout=request_timeout(config.REQUEST_BUDGET_SEC))
            except TimeoutError as exc:
                raise DataUnavailable("공용 조회의 응답을 기다리는 시간이 지났습니다.") from exc
            with self.lock:
                if generation != self._generation_for(key):
                    raise DataUnavailable("조회 중 데이터가 초기화되거나 변경됐습니다. 다시 조회해 주세요.")
            return value
        try:
            result = self._fetch(key, fn, args, generation)
            flight.set_result(result)
            return result
        except BaseException as exc:
            flight.set_exception(exc)
            raise
        finally:
            with self.lock:
                self.inflight.pop(flight_key, None)

    def _fetch(self, key, fn, args, generation):
        kind = key.partition(":")[0]
        try:
            result = fn(*args)
            if isinstance(result, dict) and result.get("status") in {"unavailable", "error", "conflict"}:
                if kind in ("empty", "car"):
                    raise DataUnavailable("현재 정보를 확인할 수 없어요.")
                return freeze(result)
            validate(kind, result)
            if key.startswith(("car:", "fee:")) and result:
                require(result["car_id"] == args[0], "요청 차량과 응답 차량이 다릅니다.")
            if isinstance(result, dict) and result.get("updated_at"):
                age = (now() - timestamp(result["updated_at"])).total_seconds()
                api_result = result.get("api_result")
                if age < -5 and (api_result or result.get("data_source") != "mock"):
                    raise DataUnavailable("수신한 정보의 갱신 시각이 미래입니다.")
                # 오래된 '경보 없음'은 확정하지 않는다(안전). 활성 경보와 일반 조회는 서버 값을 그대로 쓴다.
                if key == "alert" and result.get("level", 0) == 0 and age > config.DATA_MAX_AGE_SEC and (
                        api_result or result.get("data_source") != "mock"):
                    raise DataUnavailable("경보 정보의 갱신 시각이 너무 오래되어 '경보 없음'을 확정할 수 없습니다.")
            result = freeze(result)
            with self.lock:
                if generation != self._generation_for(key):
                    raise DataUnavailable("조회 중 데이터가 초기화되거나 변경됐습니다. 다시 조회해 주세요.")
                if kind == "empty" and isinstance(self.inner, ApiAdapter):
                    config.catalog.adopt(result["lots"])
                    self.catalog_ready = True
                if isinstance(result, dict):
                    self.cache[key] = result
                    self.cached_at[key] = time.monotonic()
                    while len(self.cache) > 256:
                        oldest = min(self.cached_at, key=self.cached_at.get)
                        self.cache.pop(oldest, None); self.cached_at.pop(oldest, None)
                else:
                    self.cache.pop(key, None)  # 출차 후 None이면 이전 차량 정보 폐기
                    self.cached_at.pop(key, None)
            return result
        except (OSError, ValueError) as exc:
            with self.lock:
                if generation != self._generation_for(key):
                    raise DataUnavailable("조회 중 데이터가 변경됐습니다. 다시 조회해 주세요.") from exc
                age = time.monotonic() - self.cached_at.get(key, float("-inf"))
                if key in self.cache and age <= config.CACHE_MAX_STALE_SEC:
                    return freeze({**self.cache[key], "stale": True, "cache_age_sec": round(age, 1)})
            raise DataUnavailable("백엔드 정보를 가져올 수 없습니다.") from exc

    def _read(self, key, fn, *args):
        result = self._call(key, fn, *args)
        # A scoped engine request reads immutable data. Direct Python callers
        # retain their historical ownership of an editable, detached result.
        return result if request_gets.get() is not None else deepcopy(result)

    def get_empty_spots(self) -> C.OccupancySnapshot:
        lot = request_lot.get() if isinstance(self.inner, ApiAdapter) else None
        key = f"empty:{lot}" if lot is not None else "empty"
        return self._read(key, self.inner.get_empty_spots)

    def find_car(self, v):
        self._ensure_catalog()
        return self._read(f"car:{v}", self.inner.find_car, v)

    def get_fee(self, v):
        return self._read(f"fee:{v}", self.inner.get_fee, v)

    def get_rate_policies(self):
        if isinstance(self.inner, ApiAdapter):
            try:
                return self.inner.get_rate_policies()
            except (OSError, ValueError):
                return {"status": "unavailable", "unavailable": True}
        return {"policies": {k: config.catalog.rate_policy(k) for k in config.catalog.ids() if config.catalog.rate_policy(k)}}

    def pay(self, v, **kwargs):
        try:
            result = validate("payment", self.inner.pay(v, **kwargs))
            if not isinstance(self.inner, ApiAdapter) and result["status"] in ("paid", "free") and kwargs.get("quote_amount") is not None:
                require(result["amount"] == kwargs["quote_amount"], "확인 금액과 결제 결과가 다릅니다.")
            if result["status"] == "unavailable":
                raise DataUnavailable("결제 결과를 확인할 수 없어요.")
            if "car_id" in result:
                require(result["car_id"] == v, "결제 대상 차량 불일치")
            if "entry_at" in result and kwargs.get("entry_at"):
                require(result["entry_at"] == kwargs["entry_at"], "결제 입차 기록 불일치")
            return result
        except (OSError, ValueError) as exc:
            raise DataUnavailable("결제 처리 결과를 확인할 수 없습니다. 요금을 다시 조회해 주세요.") from exc
        finally:
            with self.lock:
                self.cache.pop(f"fee:{v}", None)
                self.cached_at.pop(f"fee:{v}", None)

    def reset(self):
        with self.lock, self.inner.lock:
            self.generation += 1
            self.versions.clear()
            self.inner.reset()
            self.catalog_ready = not isinstance(self.inner,ApiAdapter)
            self.cache.clear()
            self.cached_at.clear()

    def exit_car(self, car_id):
        car_id = normalize_car_id(car_id)
        result = self.inner.exit_car(car_id)
        if not result.get("ok"):
            return result
        with self.lock:
            for key in (f"car:{car_id}", f"fee:{car_id}", "empty"):
                self.versions[key] = self.versions.get(key, 0) + 1
                self.cache.pop(key, None)
                self.cached_at.pop(key, None)
        return result

    def _invalidate(self, keys):
        with self.lock:
            for key in keys:
                self.versions[key] = self.versions.get(key, 0) + 1
                self.cache.pop(key, None)
                self.cached_at.pop(key, None)

    def enter_car(self, lot_id, car_id=None):
        result = self.inner.enter_car(lot_id, car_id)
        if result.get("ok"):
            self._invalidate(["empty"] + ([f"car:{result['car_id']}", f"fee:{result['car_id']}"] if result.get("car_id") else []))
        return result

    def enter_unrecognized(self, lot_id):
        result = self.inner.enter_unrecognized(lot_id)
        self._invalidate(["empty"])
        return result

    def set_uncertain(self, lot_id, spot_no, uncertain):
        result = self.inner.set_uncertain(lot_id, spot_no, uncertain)
        self._invalidate(["empty"])
        return result

    def fill_lot(self, lot_id):
        result = self.inner.fill_lot(lot_id); self._invalidate(["empty"]); return result

    def clear_demo(self, lot_id=None):
        result = self.inner.clear_demo(lot_id); self._invalidate(["empty"]); return result

    def set_restriction(self, lot_id, restricted, reason=""):
        result = self.inner.set_restriction(lot_id, restricted, reason); self._invalidate(["empty"]); return result

    def _ensure_catalog(self):
        if not self.catalog_ready:
            self.get_empty_spots()

    def get_alert(self):
        self._ensure_catalog()
        return self._call("alert", self.inner.get_alert)

    def list_cars(self, lot_id=None, *, candidates=False):
        token = request_gets.set({}) if request_gets.get() is None and isinstance(self.inner, ApiAdapter) else None
        try:
            self._ensure_catalog()
            if isinstance(self.inner, ApiAdapter):
                result = self.inner.list_cars(lot_id, candidates=candidates)
                result = validate("cars" if candidates else "space_cars", result)
            else:
                result = validate("cars", self.inner.list_cars(lot_id))
            identified = [c["car_id"] for c in result if c["car_id"] is not None]
            require(len(set(identified)) == len(identified), "중복 차량 데이터")
            require(len({(c["lot_id"],c["spot_no"]) for c in result}) == len(result), "중복 주차칸 데이터")
            require(not lot_id or all(c["lot_id"] == lot_id for c in result), "요청 주차장과 응답 불일치")
            return result
        except (OSError, ValueError) as exc:
            raise DataUnavailable("주차 차량 목록을 확인할 수 없습니다.") from exc
        finally:
            if token is not None:
                request_gets.reset(token)


    def __getattr__(self, name):
        return getattr(self.inner, name)


def build_adapter():
    inner = ApiAdapter(config.BACKEND_BASE_URL) if config.DATA_SOURCE == "api" else MockAdapter()
    return CachedAdapter(inner)
