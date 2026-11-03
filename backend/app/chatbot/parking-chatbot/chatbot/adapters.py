"""데이터 연결층.

챗봇은 이 파일의 함수만 부른다. DATA_SOURCE=mock 이면 가짜 데이터,
DATA_SOURCE=api 이면 팀 백엔드(API v1)를 호출한다.
API v1이 확정되면 ApiAdapter 안의 경로·필드 이름만 고치면 된다.
"""
from __future__ import annotations

import json
import math
import threading
import time
import urllib.request
from urllib.parse import quote, urlencode
from copy import deepcopy
from contextvars import ContextVar
from concurrent.futures import Future
from datetime import datetime, timedelta

from . import config
from .config import KST
from .contracts import validate, timestamp, require
from .transport import load_json

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


def normalize_vehicle_no(value):
    if not isinstance(value, str) or not value.strip().isdecimal():
        raise ValueError("차량 번호를 숫자로 입력해 주세요.")
    number = int(value.strip())
    if not config.VEHICLE_MIN <= number <= config.VEHICLE_MAX:
        raise ValueError("차량 번호 범위를 벗어났습니다.")
    return f"{number:02d}"


def calc_fee(minutes: int) -> int:
    """임시 요금 규칙: 무료 시간 이후 단위 시간마다 일정 금액 (EPIC 05 규칙으로 교체 예정)."""
    if minutes <= config.FREE_MINUTES:
        return 0
    units = math.ceil((minutes - config.FREE_MINUTES) / config.FEE_UNIT_MINUTES)
    return units * config.FEE_PER_UNIT


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
                if not config.VEHICLE_MIN <= int(no) <= config.VEHICLE_MAX or not 1 <= spot <= self.capacity[lot]:continue
                if no in self.cars:raise ValueError("mock 차량 번호가 중복됩니다.")
                self.cars[no] = {"lot_id":lot,"spot_no":spot,"entered_at":t-timedelta(minutes=minutes),"paid_at":None}
        for lot, definition in definitions.items():
            if definition.get("mock_full"):
                used = {c["spot_no"] for c in self.cars.values() if c["lot_id"] == lot}
                for spot in range(1,self.capacity[lot]+1):
                    if spot in used:continue
                    no = next((f"{n:02d}" for n in range(config.VEHICLE_MIN,config.VEHICLE_MAX) if f"{n:02d}" not in self.cars),None)
                    if no is None:raise ValueError("만차 시연과 미입차 차량을 함께 만들 차량 번호가 부족합니다.")
                    self.cars[no] = {"lot_id":lot,"spot_no":spot,"entered_at":t-timedelta(minutes=45),"paid_at":None}
        self.alert = {"level": 0, "version": 0}
        self.payments = {}

    def get_empty_spots(self) -> dict:
        with self.lock:
            lots = {}
            for lot, total in self.capacity.items():
                occupied = self._occupied_spots(lot)
                used = len(occupied)
                info = config.catalog.lot(lot)
                zones = {key:{"total":len(spots),"empty":sum(spot not in occupied for spot in spots),"uncertain":sum(spot in self.uncertain.get(lot,set()) for spot in spots)}
                         for key,spots in config.catalog.zone_spots(total, lot).items()}
                lots[lot] = {"total":total,"empty":total-used,"uncertain":len(self.uncertain.get(lot,set())),"name":info["name"],"kind":info["kind"],
                             "zones":zones,**self.restrictions.get(lot,{})}  # PROVISIONAL(API v1 확정 필요)
        return {"lots": lots, "updated_at": iso(now())}

    def find_car(self, vehicle_no: str) -> dict | None:
        with self.lock:
            car = self.cars.get(vehicle_no)
            car = dict(car) if car else None
        if not car:
            return None
        return {"vehicle_no": vehicle_no, "lot_id": car["lot_id"], "spot_no": car["spot_no"],
                "capacity": self.capacity[car["lot_id"]],
                "entered_at": iso(car["entered_at"]), "updated_at": iso(now())}

    def get_fee(self, vehicle_no: str) -> dict | None:
        with self.lock:
            car = self.cars.get(vehicle_no)
            car = dict(car) if car else None
        if not car:
            return None
        minutes = int((now() - car["entered_at"]).total_seconds() // 60)
        return {
            "vehicle_no": vehicle_no,
            "entered_at": iso(car["entered_at"]),
            "minutes": minutes,
            "fee": calc_fee(minutes),
            "fee_after_30": calc_fee(minutes + 30),
            "free_minutes": config.FREE_MINUTES,
            "paid": car["paid_at"] is not None,
            "updated_at": iso(now()),
        }

    def pay(self, vehicle_no: str, *, idempotency_key=None, expected_amount=None, entered_at=None) -> dict:
        with self.lock:
            if idempotency_key and idempotency_key in self.payments:
                return deepcopy(self.payments[idempotency_key])
            car = self.cars.get(vehicle_no)
            if not car:
                return {"status": "not_found"}
            if car["paid_at"]:
                return {"status": "already_paid", "paid_at": iso(car["paid_at"])}
            minutes = int((now() - car["entered_at"]).total_seconds() // 60)
            amount = calc_fee(minutes)
            if ((expected_amount is not None and amount != expected_amount) or
                    (entered_at is not None and iso(car["entered_at"]) != entered_at)):
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
            rows = [{"vehicle_no":no,"lot_id":c["lot_id"],"spot_no":c["spot_no"],"entered_at":iso(c["entered_at"])}
                    for no,c in self.cars.items()] + deepcopy(self.unrecognized)
        return sorted((c for c in rows if lot_id is None or c["lot_id"] == lot_id), key=lambda c:(c["lot_id"],c["spot_no"]))

    def _occupied_spots(self, lot):
        return {c["spot_no"] for c in self.cars.values() if c["lot_id"] == lot} | self.demo_occupied.get(lot, set()) | self.uncertain.get(lot, set()) | {c["spot_no"] for c in self.unrecognized if c["lot_id"] == lot}

    def _check_lot(self, lot_id):
        if lot_id not in self.capacity:
            raise ValueError("등록되지 않은 주차장입니다.")

    # 시연용: 입차 1대. vehicle_no가 있으면 그 차량을 입차, 없으면 차량 번호 없는 시연용 점유
    def enter_car(self, lot_id, vehicle_no=None) -> dict:
        self._check_lot(lot_id)
        if vehicle_no is not None:
            vehicle_no = normalize_vehicle_no(vehicle_no)
        with self.lock:
            free = sorted(set(range(1, self.capacity[lot_id] + 1)) - self._occupied_spots(lot_id))
            if not free:
                return {"ok": False, "reason": "만차", "lot_id": lot_id}
            spot = free[0]
            if vehicle_no is not None:
                if vehicle_no in self.cars:
                    return {"ok": False, "reason": "이미 주차 중", "vehicle_no": vehicle_no, "lot_id": self.cars[vehicle_no]["lot_id"]}
                self.cars[vehicle_no] = {"lot_id": lot_id, "spot_no": spot, "entered_at": now(), "paid_at": None}
                return {"ok": True, "vehicle_no": vehicle_no, "lot_id": lot_id, "spot_no": spot}
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
            car = {"vehicle_no":None,"lot_id":lot_id,"spot_no":free[0],"entered_at":iso(now())}
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
    def exit_car(self, vehicle_no: str) -> dict:
        vehicle_no = normalize_vehicle_no(vehicle_no)
        with self.lock:
            car = self.cars.pop(vehicle_no, None)
        return {"ok": car is not None, "vehicle_no": vehicle_no, "lot_id": car["lot_id"] if car else None}

    def exit_for(self, lot_id: str) -> str:
        return (config.catalog.lot(lot_id) or {}).get("exit", "가장 가까운 출구")

    # 시연용: 경보를 수동으로 띄우거나 해제 (LED 쪽 수동 이벤트 시연 대안과 같은 역할)
    def set_alert(self, level: int, lot_id: str | None = None, spot_no: int | None = None) -> dict:
        if type(level) is not int or level not in (0, 1, 2):
            raise ValueError("level은 0, 1, 2 중 하나여야 합니다.")
        lot_id = lot_id or next(iter(self.capacity))
        if lot_id not in self.capacity:raise ValueError("등록되지 않은 주차장입니다.")
        spot_no = spot_no if spot_no is not None else min(7,self.capacity[lot_id])
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
                    "lot_id": lot_id, "spot_no": spot_no,
                    "confidence": 0.62 if level == 1 else 0.91,
                    "started_at": iso(now()),
                }
            return dict(self.alert)


# ---------------------------------------------------------------- 팀 백엔드 호출
class ApiAdapter(MockAdapter):
    """API v1 확정 전 임시 경로. TODO(11/02): 실제 경로·필드명으로 수정."""

    def __init__(self, base_url: str) -> None:
        super().__init__()
        self.base = base_url.rstrip("/")

    def _get(self, path: str):
        req = urllib.request.Request(self.base + path, headers=self._headers())
        return load_json(req, request_timeout(config.BACKEND_TIMEOUT_SEC))

    def _headers(self):
        return {"Authorization": "Bearer " + config.BACKEND_TOKEN} if config.BACKEND_TOKEN else {}

    def _post(self, path: str, body: dict, idempotency_key=None):
        req = urllib.request.Request(self.base + path, data=json.dumps(body).encode("utf-8"),
                                     headers={"Content-Type": "application/json", **self._headers(),
                                              **({"Idempotency-Key": idempotency_key} if idempotency_key else {})}, method="POST")
        return load_json(req, request_timeout(config.BACKEND_TIMEOUT_SEC))

    def get_empty_spots(self) -> dict:
        return self._get(config.API_PATHS["occupancy"])

    def find_car(self, vehicle_no: str) -> dict | None:
        return self._optional_get(config.API_PATHS["location"].format(vehicle_no=quote(vehicle_no, safe="")))

    def get_fee(self, vehicle_no: str) -> dict | None:
        return self._optional_get(config.API_PATHS["fee"].format(vehicle_no=quote(vehicle_no, safe="")))

    def _optional_get(self, path):
        from urllib.error import HTTPError
        try:
            return self._get(path)
        except HTTPError as exc:
            if exc.code == 404:
                return None
            raise

    def pay(self, vehicle_no: str, *, idempotency_key=None, expected_amount=None, entered_at=None) -> dict:
        return self._post(config.API_PATHS["payment"], {"vehicle_no": vehicle_no,
            "expected_amount": expected_amount, "entered_at": entered_at}, idempotency_key)

    def get_alert(self) -> dict:
        data = self._get(config.API_PATHS["alerts"])
        # TODO: 응답 형식 확정 후 level/lot_id/spot_no/confidence로 변환
        return data

    def list_cars(self, lot_id=None):
        q = "?" + urlencode({"lot_id": lot_id}) if lot_id else ""
        return self._get(config.API_PATHS["parked"] + q)

    def set_alert(self, *args, **kwargs):
        raise ValueError("시연 기능은 DATA_SOURCE=mock에서만 사용할 수 있습니다.")

    def exit_car(self, *args, **kwargs):
        raise ValueError("시연 기능은 DATA_SOURCE=mock에서만 사용할 수 있습니다.")

    enter_car = enter_unrecognized = set_uncertain = fill_lot = clear_demo = set_restriction = demo_state = exit_car


# ---------------------------------------------------------------- 실패 대비 캐시
class DataUnavailable(RuntimeError):
    """캐시도 없는 백엔드 조회 실패. 사용자에게 재시도를 안내한다."""


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
            shared = key == "empty" or key.startswith("car:")
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
            return deepcopy(value)
        try:
            result = self._fetch(key, fn, args, generation)
            flight.set_result(deepcopy(result))
            return result
        except BaseException as exc:
            flight.set_exception(exc)
            raise
        finally:
            with self.lock:
                self.inflight.pop(flight_key, None)

    def _fetch(self, key, fn, args, generation):
        try:
            result = fn(*args)
            validate(key.split(":", 1)[0], result)
            if key.startswith(("car:", "fee:")) and result:
                require(result["vehicle_no"] == args[0], "요청 차량과 응답 차량이 다릅니다.")
            if isinstance(result, dict) and result.get("updated_at"):
                age = (now() - timestamp(result["updated_at"])).total_seconds()
                if age > config.DATA_MAX_AGE_SEC or age < -5:
                    raise DataUnavailable("수신한 정보의 갱신 시각이 너무 오래되었습니다.")
            with self.lock:
                if generation != self._generation_for(key):
                    raise DataUnavailable("조회 중 데이터가 초기화되거나 변경됐습니다. 다시 조회해 주세요.")
                if key == "empty" and isinstance(self.inner,ApiAdapter):
                    config.catalog.adopt(result["lots"])
                    self.catalog_ready = True
                if isinstance(result, dict):
                    self.cache[key] = deepcopy(result)
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
                    return {**deepcopy(self.cache[key]), "stale": True, "cache_age_sec": round(age, 1)}
            raise DataUnavailable("백엔드 정보를 가져올 수 없습니다.") from exc

    def get_empty_spots(self):
        return self._call("empty", self.inner.get_empty_spots)

    def find_car(self, v):
        self._ensure_catalog()
        return self._call(f"car:{v}", self.inner.find_car, v)

    def get_fee(self, v):
        return self._call(f"fee:{v}", self.inner.get_fee, v)

    def pay(self, v, **kwargs):
        try:
            result = validate("payment", self.inner.pay(v, **kwargs))
            if result["status"] in ("paid", "free") and kwargs.get("expected_amount") is not None:
                require(result["amount"] == kwargs["expected_amount"], "확인 금액과 결제 결과가 다릅니다.")
            if "vehicle_no" in result:
                require(result["vehicle_no"] == v, "결제 대상 차량 불일치")
            if "entered_at" in result and kwargs.get("entered_at"):
                require(result["entered_at"] == kwargs["entered_at"], "결제 입차 기록 불일치")
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

    def exit_car(self, vehicle_no):
        vehicle_no = normalize_vehicle_no(vehicle_no)
        result = self.inner.exit_car(vehicle_no)
        if not result.get("ok"):
            return result
        with self.lock:
            for key in (f"car:{vehicle_no}", f"fee:{vehicle_no}", "empty"):
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

    def enter_car(self, lot_id, vehicle_no=None):
        result = self.inner.enter_car(lot_id, vehicle_no)
        if result.get("ok"):
            self._invalidate(["empty"] + ([f"car:{result['vehicle_no']}", f"fee:{result['vehicle_no']}"] if result.get("vehicle_no") else []))
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

    def list_cars(self, lot_id=None):
        self._ensure_catalog()
        try:
            result = validate("cars", self.inner.list_cars(lot_id))
            identified = [c["vehicle_no"] for c in result if c["vehicle_no"] is not None]
            require(len(set(identified)) == len(identified), "중복 차량 데이터")
            require(len({(c["lot_id"],c["spot_no"]) for c in result}) == len(result), "중복 주차칸 데이터")
            require(not lot_id or all(c["lot_id"] == lot_id for c in result), "요청 주차장과 응답 불일치")
            return result
        except (OSError, ValueError) as exc:
            raise DataUnavailable("주차 차량 목록을 확인할 수 없습니다.") from exc

    def __getattr__(self, name):
        return getattr(self.inner, name)


def build_adapter():
    inner = ApiAdapter(config.BACKEND_BASE_URL) if config.DATA_SOURCE == "api" else MockAdapter()
    return CachedAdapter(inner)
