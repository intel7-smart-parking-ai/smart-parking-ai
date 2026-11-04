"""챗봇 엔진: 입력(칩/문장/이벤트) → 응답 JSON.

응답 형식 (화면은 이것만 그리면 됨):
{ "text", "card", "chips":[{id,label}], "banner", "updated_at", "alert_version", "meta" }

처리 순서: 칩 → 규칙 파서 → (선택) LLM 분류 → 처리 함수 → 템플릿 문장
"""
from __future__ import annotations

import json
import threading
import time
import uuid
from contextvars import ContextVar
from contextlib import contextmanager
from copy import deepcopy
from functools import wraps
from collections import Counter, deque
from pathlib import Path
from datetime import datetime, timedelta

from . import config, incidents, navigation as N, templates as T
from .adapters import ApiAdapter, request_gets, request_lot, DataUnavailable, build_adapter, iso, now, request_deadline, request_timeout
from .parser import parse
from .state import Session, Flow, Conflict
from .notifications import Notifications

CHIP_DATA = json.loads((Path(__file__).parent / "chips.json").read_text(encoding="utf-8"))
CHIPS, SETS, SUGGESTIONS = CHIP_DATA["chips"], CHIP_DATA["sets"], CHIP_DATA["suggestions"]

NEEDS_VEHICLE = {"find_my_car", "get_fee", "pay_fee", "alert_near_my_car"}
QUOTE_RETRY_STATUSES = {"quote_expired", "quote_mismatch", "quote_not_found"}

data = build_adapter()
# 선택 라우터 준비는 별도 스레드에서 시작. 서버 시작을 지연시키지 않는다.
if config.LLM_ENGINE == "langchain":
    from .llm_router import warmup
    warmup()
_lock = threading.RLock()
_alert_snapshot = ContextVar("alert_snapshot", default=None)
_vehicle_snapshot = ContextVar("vehicle_snapshot", default=None)
_occupancy_snapshot = ContextVar("occupancy_snapshot", default=None)
_answer_evidence = ContextVar("answer_evidence", default=None)
_request_session = ContextVar("request_session", default=None)
LOG = deque(maxlen=2000)
ADMIN_CALLS: list[dict] = []
notifications = Notifications()
RUNTIME_ID = uuid.uuid4().hex
_alert_lock = threading.RLock()
_alert_revision = 0
_alert_fingerprint = None
_alert_condition = threading.Condition(_alert_lock)
_alert_refreshing = False
_last_alert = None
_alert_fetched_at = 0
_alert_events = deque(maxlen=100)
_view_revision = 0
_payment_lock = threading.RLock()
_uncertain_payments = set()


SESSIONS: dict[str, Session] = {}
_session_users = Counter()


def get_session(sid: str) -> Session:
    active = _request_session.get()
    if active is not None and active[0] == sid:
        return active[1]
    with _lock:
        # 오래 사용하지 않은 세션만 정리. 처리 중인 요청의 잠금은 기다리지 않는다.
        cutoff = time.monotonic() - config.SESSION_IDLE_SEC
        for key, old in list(SESSIONS.items()):
            if key != sid and not _session_users[key] and old.last_seen < cutoff and old.lock.acquire(blocking=False):
                try:
                    if old.last_seen < cutoff:
                        SESSIONS.pop(key, None)
                        notifications.forget(key)
                finally:
                    old.lock.release()
        if sid not in SESSIONS:
            if len(SESSIONS) >= config.MAX_SESSIONS:
                raise Conflict("접속 세션이 많습니다. 잠시 후 다시 접속해 주세요.")
            SESSIONS[sid] = Session()
        return SESSIONS[sid]


@contextmanager
def _reserved_session(sid):
    # 세션 조회와 사용 예약을 원자적으로 처리한다. 잠금 대기 중에도 정리·초기화에서 보호.
    with _lock:
        session = get_session(sid)
        _session_users[sid] += 1
    try:
        yield session
    finally:
        with _lock:
            _session_users[sid] -= 1
            if not _session_users[sid]:
                del _session_users[sid]


def current_alert() -> dict:
    """외부 경보 버전과 앱 버전을 분리. 백엔드 재시작 후 낮아진 버전도 반영한다."""
    global _alert_revision, _alert_fingerprint, _alert_refreshing, _last_alert, _alert_fetched_at
    cached = _alert_snapshot.get()
    if cached is not None:
        return cached
    with _alert_condition:
        if _alert_refreshing:
            try:
                wait_limit = request_timeout(config.REQUEST_BUDGET_SEC)
            except TimeoutError:
                wait_limit = 0
            if not _alert_condition.wait_for(lambda: not _alert_refreshing, timeout=wait_limit):
                return {**deepcopy(_last_alert or {"level": 0}), "version": _alert_revision, "unavailable": True}
            return deepcopy(_last_alert)
        if (config.DATA_SOURCE == "api" and _last_alert is not None
                and time.monotonic() - _alert_fetched_at < config.ALERT_SHARE_SEC):
            return deepcopy(_last_alert)
        _alert_refreshing = True
    # 동시에 들어온 조회는 한 결과를 공유한다. 네트워크 I/O 중 상태 잠금은 해제.
    try:
        try:
            a = data.get_alert()
        except DataUnavailable:
            a = {**(_last_alert or {"level": 0}), "version": _alert_revision, "unavailable": True}
        if a.get("stale") or a.get("unavailable"):
            a = {**a, "source_version": a.get("version", 0), "version": _alert_revision}
        else:
            fingerprint = (a.get("source_epoch"), a["version"], a["level"], a.get("lot_id"), a.get("spot_no"))
            changed = fingerprint != _alert_fingerprint
            if changed:
                if _alert_fingerprint is not None or a["level"] or a["version"]:
                    _alert_revision += 1
                _alert_fingerprint = fingerprint
            a = {**a, "source_version": a["version"], "version": _alert_revision}
            a["incident_id"] = incidents.observe(a, data)
            if changed and _alert_revision:
                with _alert_lock:
                    _alert_events.append({**deepcopy(a), "observed_at": iso(now())})
        return deepcopy(a)
    finally:
        with _alert_condition:
            # 예기치 않은 예외에서도 대기자를 해제한다.
            _last_alert = deepcopy(locals().get("a", {"level": 0, "version": _alert_revision, "unavailable": True}))
            _alert_fetched_at = time.monotonic()
            _alert_refreshing = False
            _alert_condition.notify_all()


@contextmanager
def _locked_session(session):
    """잠금 대기도 요청 예산에 포함해 동일 세션의 요청 누적을 제한한다."""
    deadline = time.monotonic() + config.REQUEST_BUDGET_SEC
    if not session.lock.acquire(timeout=config.REQUEST_BUDGET_SEC):
        raise Conflict("이전 요청을 처리 중이에요. 잠시 후 다시 시도해 주세요.")
    try:
        yield deadline
    finally:
        session.lock.release()


def _session_request(fn):
    """동일 세션 요청은 순서대로 처리하고, 경보는 요청당 한 번만 조회한다."""
    @wraps(fn)
    def wrapped(session_id, *args, **kwargs):
        global _view_revision
        started = time.perf_counter()
        if not isinstance(session_id, str) or not session_id.strip() or not 1 <= len(session_id) <= 128:
            raise ValueError("session_id는 1~128자 문자열이어야 합니다.")
        with _reserved_session(session_id) as s, _locked_session(s) as deadline:
            s.last_seen = time.monotonic()
            get_token = request_gets.set({})
            from .parser import extract_lot, extract_zone
            raw_text = kwargs.get("text", args[0] if args else None) or ""
            raw_chip = kwargs.get("chip_id", args[1] if len(args) > 1 else None)
            chip_params = ({**CHIPS, **config.catalog.chips()}.get(raw_chip if isinstance(raw_chip, str) else None) or {}).get("params", {})
            focus = chip_params.get("lot_id") or (extract_lot(raw_text) if isinstance(raw_text, str) else None)
            zone = chip_params.get("zone") or (extract_zone(raw_text) if isinstance(raw_text, str) else None)
            lot_token = request_lot.set(focus if focus != "ambiguous" and not zone else None)
            evidence_token = _answer_evidence.set(None)
            token = _alert_snapshot.set(None)
            vehicle_token = _vehicle_snapshot.set({})
            occupancy_token = _occupancy_snapshot.set(None)
            session_token = _request_session.set((session_id, s))
            api_deadline = min(deadline, time.monotonic()+config.BACKEND_TIMEOUT_SEC) if isinstance(data.inner, ApiAdapter) else deadline
            deadline_token = request_deadline.set(api_deadline)
            try:
                _alert_snapshot.set(current_alert())
                if not current_alert().get("unavailable"):
                    s.alert_unavailable_seen = False
                expired = s.expire() if fn.__name__ == "updates" else False
                alert_cancelled = _apply_alert_policy(s)
                response = fn(session_id, *args, **kwargs)
                with _lock:
                    _view_revision += 1
                    response["state_version"] = _view_revision
                if "messages" in response:
                    response["chips"] = _response_chips(s, "default") if (expired or alert_cancelled or current_alert().get("level", 0) >= 2) else None
                elif "meta" in response:
                    latency = round((time.perf_counter() - started) * 1000, 1)
                    response["meta"]["latency_ms"] = latency
                    input_text = kwargs.get("text", args[0] if args else None)
                    input_chip = kwargs.get("chip_id", args[1] if len(args) > 1 else None)
                    with _lock:
                        LOG.append({"at": iso(now()), "session": session_id[:8], "input": "chip" if input_chip else "text",
                                    "text": (input_chip or input_text or "")[:200],
                                    "intent": response["meta"].get("intent", "start"),
                                    "source": response["meta"].get("source", "event"), "latency_ms": latency})
                if isinstance(data.inner, ApiAdapter):
                    payload = response.get("card") or {}
                    evidence = _answer_evidence.get() or payload.get("data", {})
                    if response.get("text"):
                        response["text"] = T.data_notes(response["text"], evidence)
                    for message in response.get("messages", []):
                        evidence = (message.get("card") or {}).get("data", {})
                        message["text"] = T.data_notes(message["text"], evidence)
                return response
            finally:
                request_gets.reset(get_token)
                request_lot.reset(lot_token)
                _answer_evidence.reset(evidence_token)
                _alert_snapshot.reset(token)
                _vehicle_snapshot.reset(vehicle_token)
                _occupancy_snapshot.reset(occupancy_token)
                _request_session.reset(session_token)
                request_deadline.reset(deadline_token)
    return wrapped


# ------------------------------------------------------------ 응답 조립
def _chips(chip_set) -> list[dict]:
    definitions = {**CHIPS, **config.catalog.chips()}
    active = _request_session.get()
    if active:
        for token, car in active[1].candidate_options.items():
            label = ("이 차 위치 보기" if len(active[1].candidate_options) == 1
                     else f"{T.place(car['lot_id'], car['spot_no'], car.get('space_id'))} 위치 보기")
            definitions["candidate:" + token] = {"label": label}
    if chip_set == "after_get_empty_spots":
        ids = ["lot_" + k for k in config.catalog.ids()][:3] + ["find_car"]
    elif chip_set == "default":
        ids = ["empty_all"] + ["zone:" + z["key"] for z in config.catalog.zones() if z.get("home")][:2] + ["find_car"]
    else:
        ids = SETS[chip_set] if isinstance(chip_set, str) else chip_set
    return [{"id": cid, "label": definitions[cid]["label"] if cid in definitions else "다시 선택"}
            for cid in ids][:4]

def _hide_destination_cancel(s, chips):
    """목적지가 없으면 '목적지 취소' 칩을 보이지 않는다."""
    if N.public(s):
        return chips
    return [c for c in chips if c["id"] != "destination_cancel"]


def _response_chips(s, chip_set):
    if current_alert().get("level", 0) >= 2:
        chip_set = "alert"
    elif s.pending and s.flow in (Flow.CONFIRM_VEHICLE, Flow.CONFIRM_ACTION):
        chip_set = "confirm"
    result = _hide_destination_cancel(s, _chips(chip_set))
    if s.pending:
        for c in result:
            if c["id"] in ("confirm_yes", "confirm_no"):
                c["confirmation_token"] = s.pending.token
                if s.flow == Flow.CONFIRM_ACTION:
                    c["label"] = ("모의 결제하기" if s.pending.intent == "pay_fee" else "호출 등록") if c["id"] == "confirm_yes" else "취소"
    return result


def _admin_suggestion_chips(s):
    base = "alert_check" if current_alert().get("level") == 1 else "parked" if s.car_id else "default"
    return ["call_admin"] + [c["id"] for c in _chips(base) if c["id"] != "call_admin"][:3]


def _apply_alert_policy(s):
    if current_alert().get("level", 0) >= 2 and s.pending and s.pending.intent == "pay_fee":
        s.clear()
        return True
    return False


def _respond(s: Session, text: str, card=None, chip_set="default", updated_at=None, meta=None, *, input_error=False) -> dict:
    alert = current_alert()
    level = alert.get("level", 0)
    if chip_set != "confirm":
        if level >= 2:
            chip_set = "alert"
        elif level == 1 and chip_set == "default":
            chip_set = "alert_check"
        elif chip_set == "default" and s.car_id:
            chip_set = "parked"
    if isinstance(data.inner, ApiAdapter) and not input_error:
        unavailable = bool(alert.get("unavailable"))
        if unavailable and not s.alert_unavailable_seen and T.ALERT_UNAVAILABLE not in text and "경보 상태를 확인할 수 없어요" not in text:
            text += "\n경보 상태를 확인할 수 없어요."
        s.alert_unavailable_seen = unavailable
    s.history.append(("bot", text))
    context = vehicle_context(s)
    if context.get("state")=="parked" and s.destination:
        N.clear(s)
    return {
        "text": text,
        "safety": T.safety_card(alert) if level >= 2 else None,
        "card": card,
        "chips": _response_chips(s, chip_set),
        "banner": T.banner(alert),
        "updated_at": updated_at or iso(now()),
        "alert_version": alert.get("version", 0),
        "meta": meta or {},
        "flow": s.flow.value,
        "confirmation_token": s.pending.token if s.pending else None,
        "runtime_id": RUNTIME_ID,
        "vehicle_context": context,
        "destination": N.public(s),
        "catalog": config.catalog.public(),
    }


def _stale_note(d: dict | None) -> str:
    return " (마지막으로 받은 정보예요. 갱신 중)" if d and d.get("stale") else ""


def journey(fee_d: dict) -> list[dict]:
    """이용 상태 추적 (배달 앱 주문 추적처럼): 입차 → 주차 중 → 결제 → 출차 확인."""
    paid, free = fee_d["paid"], fee_d["fee"] == 0
    settled = paid or free
    return [
        {"label": "입차", "state": "done", "detail": T.hhmm(fee_d["entry_at"])},
        {"label": "주차 중", "state": "done" if settled else "current", "detail": f"{fee_d['minutes']}분"},
        {"label": "결제", "state": "done" if settled else "todo",
         "detail": "완료" if paid else ("무료" if free else T.won(fee_d["fee"]))},
        {"label": "출차 확인", "state": "current" if settled else "todo",
         "detail": "현장 확인" if settled else "결제 후"},
    ]


# ------------------------------------------------------------ 기능별 처리 함수
def _occupancy():
    value=_occupancy_snapshot.get()
    if value is None:
        value=data.get_empty_spots();_occupancy_snapshot.set(value)
    _answer_evidence.set(value)
    return value


SPACE_STATE_TEXT = {"empty": "비어 있어요", "occupied": "사용 중이에요", "uncertain": "카메라로 확인 중이에요",
                    "unavailable": "지금 이용할 수 없는 칸이에요"}


def _space_answer(s, p, snapshot):
    """'개발원주차장 B-06 비었어?' 같은 칸 하나 질문. 칸 이름만으로는 주차장을 정하지 않는다(D-04)."""
    label, lot = p["space_id"], p.get("lot_id")
    if not lot or lot == "ambiguous":
        lots = [k for k in config.catalog.ids() if label in config.catalog.spaces(k)]
        if not lots:
            return f"{label} 칸을 찾을 수 없어요. 칸 번호를 확인해 주세요.", None, ["empty_all"], None
        return f"어느 주차장의 {label}인가요?", None, ["lot_" + k for k in lots][:4], None
    if lot not in snapshot["lots"]:
        return "해당 주차장 정보가 아직 연결되지 않았어요.", None, ["empty_all"], snapshot.get("updated_at")
    state = (snapshot.get("space_states") or {}).get(lot, {}).get(label)
    if state is None:
        if label not in config.catalog.spaces(lot):
            return f"{config.catalog.name(lot)}에는 {label} 칸이 없어요.", None, ["lot_" + lot, "empty_all"], snapshot.get("updated_at")
        return f"{config.catalog.name(lot)} {label} 칸 상태는 지금 확인할 수 없어요.", None, ["lot_" + lot, "empty_all"], snapshot.get("updated_at")
    zone = config.catalog.space_zone(lot, label)
    special = {"ev": " (전기차 전용 칸)", "accessible": " (장애인 전용 칸)"}.get(zone, "")
    return f"{config.catalog.name(lot)} {label}은 {SPACE_STATE_TEXT[state]}.{special}", None, ["lot_" + lot, "empty_all"], snapshot.get("updated_at")


def h_get_empty_spots(s,p):
    snapshot, alert = _occupancy(), current_alert()
    _answer_evidence.set(snapshot)
    if p.get("space_id") and not p.get("zone"):
        return _space_answer(s, p, snapshot)
    text, card, chips, updated = N.query(snapshot, alert, p)
    if isinstance(data.inner, ApiAdapter) and alert.get("unavailable") and (not p.get("lot_id") or p["lot_id"] in snapshot["lots"]):
        selected = snapshot["lots"].items() if not p.get("lot_id") else [(p["lot_id"], snapshot["lots"][p["lot_id"]])]
        text = " / ".join(f"{config.catalog.name(k)} 빈자리 {N.count(v, p.get('zone')) if N.count(v, p.get('zone')) is not None else '미확인'}자리, 확인 중 {v.get('uncertain', 0)}칸" for k, v in selected)
        text += "이에요."
        chips = ["empty_all", "call_admin"]
    if p.get("vacancy_notice"):
        text = "자리가 나면 알려드리는 기능은 없어요.\n" + text.replace("\n", " ")
    selected_lots = [p["lot_id"]] if p.get("lot_id") else snapshot["lots"]
    if p.get("zone") and any(snapshot["lots"].get(k, {}).get("zone_partial") for k in selected_lots):
        text += "\n구역 정보가 일부 칸만 설정돼 있어요"
    return text, card, chips, updated


def h_set_destination(s,p):
    previous = s.destination
    result = N.choose(s, _occupancy(), current_alert(), p, vehicle_context(s))
    if s.destination is not previous:
        notifications.discard_prefix(p["_sid"], "destination:")
    return result


def h_cancel_destination(s,p):
    N.clear(s);notifications.discard_prefix(p["_sid"],"destination:")
    return "목적지 안내를 종료했어요.",None,["empty_all","find_car"],None


def h_find_my_car(s, p):
    d = _find_vehicle(s.car_id)
    _answer_evidence.set(d)
    if not d:
        if current_alert().get("level",0)>=2:
            return "경보 중 차량 위치 확인은 관리자에게 요청해 주세요. 밖이면 들어오지 마세요. 차를 옮기려 하지 마세요.", None, "alert", None
        s.candidate_options.clear()
        try: cars = [c for c in data.list_cars() if c.get("car_id") is None]
        except DataUnavailable:
            return T.CAR_NOT_FOUND.format(no=s.car_id) + ("" if isinstance(data.inner, ApiAdapter) else " 번호 미인식 차량 목록도 확인하지 못했어요."), None, (["unrecognized", "call_admin"] if isinstance(data.inner, ApiAdapter) else ["call_admin"]), None
        cars.sort(key=lambda c: datetime.fromisoformat(c['entry_at']), reverse=True)
        if not cars: return T.CAR_NOT_FOUND.format(no=s.car_id), None, ["call_admin","change_vehicle"], None
        s.candidate_options = {uuid.uuid4().hex:c for c in cars[:3]}
        s.candidate_expires = time.monotonic()+120
        if len(cars)>3:
            return f"{s.car_id}번 차의 입차 기록이 없고, 번호 미인식 차량이 {len(cars)}대 있어요. 관리자에게 확인해 주세요.", None, ["candidate_admin"], None
        first=cars[0]
        text=f"{s.car_id}번 차의 입차 기록이 없어요.\n번호 미인식 차량이 {T.place(first['lot_id'], first['spot_no'], first.get('space_id'))}에 있어요 ({T.hhmm(first['entry_at'])} 입차). 혹시 이 차인가요?"
        return text, {"type":"unrecognized_candidates","data":{"cars":cars,"notice":"후보 위치만 안내해요. 요금·결제는 관리자 확인 후 가능해요."}}, ["candidate:"+token for token in s.candidate_options]+["candidate_admin"], None
    s.selected_candidate=None; s.candidate_options.clear()
    card = {"type": "car_location", "data": d}
    return T.car_location(d) + _stale_note(d), card, "after_find_my_car", d["updated_at"]


def _candidate_location(s, token):
    if current_alert().get("level",0)>=2:
        s.clear()
        return _respond(s,"경보 중 후보 위치 확인은 관리자에게 요청해 주세요. 밖이면 들어오지 마세요. 차를 옮기려 하지 마세요.",chip_set="alert")
    car=s.candidate_options.get(token)
    s.clear()
    if not car or time.monotonic()>=s.candidate_expires:
        return _respond(s,"이전 후보 안내가 만료됐어요. 내 차 위치를 다시 조회해 주세요.",chip_set=["find_car","call_admin"])
    try: current=data.list_cars(car['lot_id'])
    except DataUnavailable:
        return _respond(s,"후보 차량의 현재 위치를 확인하지 못했어요. 관리자에게 확인해 주세요.",chip_set=["candidate_admin"])
    if not any(c.get('car_id') is None and c['spot_no']==car['spot_no'] and c['entry_at']==car['entry_at'] for c in current):
        s.candidate_options.clear()
        return _respond(s,"후보 차량 정보가 바뀌었어요. 다시 조회해 주세요.",chip_set=["find_car","call_admin"])
    s.selected_candidate=deepcopy(car)
    return _respond(s,"번호 미인식 차량의 후보 위치예요. 본인 차량인지 현장에서 확인해 주세요.\n요금·결제는 관리자 확인 후 가능해요.",
        {"type":"unrecognized_candidates","data":{"cars":[car],"notice":"차량 번호와 연결되지 않은 위치 후보입니다."}},["candidate_admin","change_vehicle"])


def h_get_fee(s, p):
    d = data.get_fee(s.car_id)
    _answer_evidence.set(d)
    if not d:
        return T.CAR_NOT_FOUND.format(no=s.car_id), None, ["change_vehicle", "call_admin"], None
    if d.get("status") in {"unavailable", "error", "conflict"}:
        return T.fee_unavailable(d), None, ["fee", "call_admin"], None
    card = {"type": "fee", "data": {**d, "journey": journey(d)}}
    return T.fee(d) + _stale_note(d), card, "after_get_fee", d["updated_at"]




def h_fee_rule(s, p):
    """주차장별 요금 정책. api는 서버(/rate-policies), mock은 lots.json 설정. 코드에 요금표 없음."""
    result = data.get_rate_policies()
    policies = result.get("policies") if isinstance(result, dict) else None
    _answer_evidence.set(result if isinstance(result, dict) else None)
    if not policies:
        return "요금 정책을 확인할 수 없어요. 차량 요금 조회를 선택해 주세요.", None, ["fee", "call_admin"], None
    return T.fee_rule(policies, p.get("lot_id")), None, "after_fee_rule", None


def h_pay_fee(s, p):
    if current_alert().get("level", 0) >= 2:
        return "Lv2 경보 중에는 모의 결제를 중지해요. 대피 안내를 먼저 확인해 주세요.", None, "alert", None
    d = data.get_fee(s.car_id)
    _answer_evidence.set(d)
    if not d:
        return T.CAR_NOT_FOUND.format(no=s.car_id), None, "default", None
    if d.get("status") in {"unavailable", "error", "conflict"}:
        return T.fee_unavailable(d), None, ["fee", "call_admin"], None
    if d.get("stale"):
        return "최신 요금을 확인하지 못해 결제를 진행할 수 없어요. 잠시 후 다시 조회해 주세요.", None, "default", None
    visit = (s.car_id, d["entry_at"])
    if d["paid"]:
        _uncertain_payments.discard(visit)
        return T.fee(d), None, "after_pay_fee", d["updated_at"]
    if visit in _uncertain_payments:
        return "이전 결제의 처리 결과가 아직 불확실해요. 중복 결제를 막기 위해 관리자 확인이 필요해요.", None, ["fee", "call_admin"], None
    if d["fee"] == 0:
        return T.fee(d), None, "after_get_fee", d["updated_at"]
    if isinstance(data.inner, ApiAdapter) and not d.get("quote_id"):
        # 견적 없이는 결제 요청을 보내지 않는다(서버 필수 필드).
        return T.pay({"status": "quote_missing"}, s.car_id), None, ["fee", "call_admin"], d["updated_at"]
    s.begin(Flow.CONFIRM_ACTION, "pay_fee", {"amount": d["fee"], "entry_at": d["entry_at"], "idempotency_key": str(uuid.uuid4()),
            "session_id": d.get("session_id"), "quote_id": d.get("quote_id"), "quote_expires_at": d.get("quote_expires_at")}, s.car_id)
    card = {"type": "fee", "data": {**d, "journey": journey(d)}}
    return f"{s.car_id}번 차 요금은 {T.won(d['fee'])}이에요. 이 금액으로 모의 결제할까요?", card, "confirm", d["updated_at"]


def _execute_payment(s, pending):
    with _payment_lock:
        return _execute_payment_locked(s, pending)


def _quote_expired(pending) -> bool:
    expires = pending.params.get("quote_expires_at")
    if not expires:
        return False
    try:
        return now() >= datetime.fromisoformat(expires) - timedelta(seconds=5)
    except (TypeError, ValueError):
        return True


def _reconfirm(s):
    """견적이 바뀌면 자동 결제하지 않고 새 금액으로 다시 확인받는다(새 견적·새 멱등키)."""
    result = h_pay_fee(s, {})
    pending = s.pending
    if pending and pending.intent == "pay_fee":
        text = T.QUOTE_REFRESHED.format(amount=T.won(pending.params["amount"]))
        return (text, *result[1:])
    return result


def _execute_payment_locked(s, pending):
    if current_alert().get("level", 0) >= 2:
        return "경보로 미완료 결제를 취소했어요. 대피 안내를 확인해 주세요.", None, "alert", None
    if _quote_expired(pending):
        return _reconfirm(s)
    d = data.get_fee(pending.car_id)
    _answer_evidence.set(d)
    if not d:
        return T.CAR_NOT_FOUND.format(no=pending.car_id), None, "default", None
    if d.get("status") in {"unavailable", "error", "conflict"}:
        return T.fee_unavailable(d), None, ["fee", "call_admin"], None
    if d.get("stale"):
        return "최신 요금을 확인하지 못했어요. 결제는 실행하지 않았습니다.", None, "default", None
    if d["paid"]:
        return T.fee(d), None, "after_pay_fee", d["updated_at"]
    if not isinstance(data.inner, ApiAdapter) and (d["fee"], d["entry_at"]) != (pending.params["amount"], pending.params["entry_at"]):
        result = h_pay_fee(s, {})
        return ("확인 중 요금 또는 입차 기록이 바뀌었어요. " + result[0], *result[1:])
    visit = (pending.car_id, d["entry_at"])
    if visit in _uncertain_payments:
        return "이전 결제의 처리 결과가 불확실하여 추가 결제를 중지했어요. 관리자에게 확인해 주세요.", None, ["fee", "call_admin"], None
    operation_key = pending.params["idempotency_key"] if isinstance(data.inner, ApiAdapter) else uuid.uuid5(uuid.NAMESPACE_URL, f"parking:{pending.car_id}:{d['entry_at']}:{d['fee']}").hex
    # 요금 조회 중 새 경보가 생긴 경우에도 실행 직전에 다시 확인한다.
    token = _alert_snapshot.set(None)
    try:
        latest_alert = current_alert()
    finally:
        _alert_snapshot.reset(token)
    _alert_snapshot.set(latest_alert)
    if latest_alert.get("level", 0) >= 2:
        return "경보로 미완료 결제를 취소했어요. 대피 안내를 확인해 주세요.", None, "alert", None
    try:
        visit_args = {"session_id": pending.params.get("session_id")} if isinstance(data.inner, ApiAdapter) else {}
        r = data.pay(pending.car_id, idempotency_key=operation_key, **visit_args, quote_id=pending.params.get("quote_id"),
                     quote_amount=pending.params["amount"], entry_at=pending.params["entry_at"])
    except DataUnavailable:
        _uncertain_payments.add(visit)
        return "결제 요청의 처리 결과를 확인하지 못했어요. 다시 결제하지 말고 요금 조회 또는 관리자 확인을 해 주세요.", None, ["fee", "call_admin"], None
    if r["status"] in QUOTE_RETRY_STATUSES:
        return _reconfirm(s)
    if isinstance(data.inner, ApiAdapter):
        if r["status"] in {"conflict", "error", "unavailable", "payment_conflict", "invalid_state", "quote_missing"}:
            return T.pay(r, pending.car_id), None, ["fee", "call_admin"], None
        card = {"type": "payment", "data": {**r, "car_id": pending.car_id}}
        text = T.pay(r, pending.car_id)
        if r.get("amount") is not None and r["amount"] != pending.params["amount"]:
            text += " 금액이 갱신됐어요."
        return text, card, "after_pay_fee", None
    if r["status"] == "quote_changed":
        result = h_pay_fee(s, {})
        return ("서버에서 요금 변경을 확인했어요. " + result[0], *result[1:])
    if r["status"] == "not_found":
        return T.pay(r, pending.car_id), None, "default", None
    try:
        fee_d = data.get_fee(pending.car_id)
    except DataUnavailable:
        fee_d = None
    valid_receipt = (r["status"] in ("paid", "already_paid") and fee_d and not fee_d.get("stale")
                     and fee_d["paid"] and fee_d["entry_at"] == pending.params["entry_at"])
    if not valid_receipt:
        _uncertain_payments.add(visit)
        if fee_d is None or fee_d.get("stale"):
            card = {"type": "payment", "data": {**r, "car_id": pending.car_id,
                                                 "verification_pending": True, "journey": None}}
            return T.pay(r, pending.car_id) + " 현재 요금 상태 재조회는 미확인이에요. 다시 결제하지 말고 관리자에게 확인해 주세요.", card, ["fee", "call_admin"], None
        return "결제 결과와 현재 요금 상태가 일치하는지 확인하지 못했어요. 추가 결제 없이 관리자 확인이 필요해요.", None, ["fee", "call_admin"], None
    _uncertain_payments.discard(visit)
    card = {"type": "payment", "data": {**r, "car_id": pending.car_id, "journey": journey(fee_d)}}
    return T.pay(r, pending.car_id), card, "after_pay_fee", None


def h_exit_help(s, p):
    """출구·차단기 문의: 진단하지 않고 현장 관리자 연결을 안내한다(차량 번호·API 조회 없음)."""
    return T.EXIT_HELP, None, ["call_admin", "fee", "find_car"], None


def h_violations(s, p):
    """전용 칸 위반 차량 문의. 서버 경보의 restricted_space_violation만 근거로 답한다."""
    a = current_alert()
    _answer_evidence.set(a)
    return T.violations(a), None, "default", None


def _admin_summary(s: Session, car_id=None) -> str:
    """관리자에게 넘길 상황 요약 자동 작성 (안전 보고서 자동완성과 같은 발상)."""
    parts = []
    candidates = [s.selected_candidate] if s.selected_candidate else list(s.candidate_options.values())
    if candidates:
        parts.append("번호 미인식 후보 확인 요청("+", ".join(T.place(c['lot_id'], c['spot_no'], c.get('space_id')) for c in candidates)+")")
    car_id = car_id or s.car_id
    if car_id:
        try:
            car = _find_vehicle(car_id)
        except DataUnavailable:
            car = None
            parts.append("차량 위치 미확인")
        try:
            fee = data.get_fee(car_id)
        except DataUnavailable:
            fee = None
            parts.append("결제 상태 미확인")
        if car:
            parts.append(f"{car_id}번 차 · {T.place(car['lot_id'], car['spot_no'], car.get('space_id'))} · 입차 {T.hhmm(car['entry_at'])}" + _stale_note(car) + f" · 위치 기준 {car['updated_at']}")
        else:
            parts.append(f"{car_id}번 차 · 위치 미확인")
        if fee and fee.get("status") not in {"unavailable", "error", "conflict"}:
            parts.append(("결제 완료" if fee["paid"] else f"미결제 {T.won(fee['fee'])}") + _stale_note(fee) + f" · 요금 기준 {fee['updated_at']}")
    else:
        parts.append("차량 번호 미확인")
    skip = {CHIPS["confirm_yes"]["label"], CHIPS["confirm_no"]["label"], "네", "예", "응", "아니요"}
    user_msgs = [t for role, t in s.history if role == "user" and t not in skip][-3:]
    if user_msgs:
        parts.append("최근 문의: " + " / ".join(user_msgs))
    a = current_alert()
    inc_id = a.get("incident_id")
    location = T.alert_location(a) if a.get("api_result") else T.place(a.get('lot_id'), a.get('spot_no'))
    parts.append((f"경보 Lv{a['level']} {location} (상황 보고서 #{inc_id})" + _stale_note(a)) if inc_id else ("경보 상태 미확인" if a.get("unavailable") or a.get("stale") else "경보 없음"))
    return " · ".join(parts)


def _find_vehicle(car_id):
    """카드·안내가 같은 요청의 위치 결과를 공유한다. 실패한 조회도 반복하지 않는다."""
    snapshot = _vehicle_snapshot.get()
    if snapshot is None or car_id not in snapshot:
        try:
            car = data.find_car(car_id)
        except DataUnavailable:
            if snapshot is not None:
                snapshot[car_id] = {"_unavailable": True}
            raise
        if snapshot is not None:
            snapshot[car_id] = car
    else:
        car = snapshot[car_id]
    if car and car.get("_unavailable"):
        raise DataUnavailable("차량 위치를 확인할 수 없습니다.")
    return car


def vehicle_context(s):
    if not s.car_id:
        return {"car_id": None, "state": "unselected"}
    try:
        car = _find_vehicle(s.car_id)
    except DataUnavailable:
        return {"car_id": s.car_id, "state": "unavailable"}
    if car is None:
        return {"car_id": s.car_id, "state": "not_found"}
    return {**car, "state": "stale" if car.get("stale") else "parked"}


def public_call(call):
    return {k: deepcopy(v) for k, v in call.items() if not k.startswith("_")}




def h_call_admin(s, p):
    return _execute_admin(s, p)


def _execute_admin(s, p):
    a = current_alert()
    inc_id, car_id = a.get("incident_id"), p.get("car_id") or s.car_id
    with _lock:
        open_status = ("접수",)
        existing = next((c for c in reversed(ADMIN_CALLS) if c["_session"] == p["_sid"]
                         and c["car_id"] == car_id and c.get("incident_id") == inc_id
                         and c["status"] in open_status
                         and time.monotonic() - c.get("_last_at", 0) <= config.CALL_MERGE_SEC), None)
        if existing and existing.get("alert_level", 0) >= a.get("level", 0):
            existing["_last_at"] = time.monotonic()
            return f"관리자 화면에 이미 접수된 문의가 있어요. (문의 #{existing['id']})", {"type": "admin_call", "data": public_call(existing)}, "after_call_admin", None
    summary = _admin_summary(s, car_id)
    with _lock:
        if existing:
            call = existing
            call.update(summary=summary, alert_level=a.get("level", 0), revision=call["revision"] + 1, updated_at=iso(now()), _last_at=time.monotonic())
        else:
            call = {"id": len(ADMIN_CALLS) + 1, "at": iso(now()), "updated_at": iso(now()),
                    "car_id": car_id, "incident_id": inc_id, "alert_level": a.get("level", 0),
                    "summary": summary, "status": "접수", "revision": 1, "_session": p["_sid"], "_last_at": time.monotonic()}
            ADMIN_CALLS.append(call)
    incidents.link_admin_call(call["id"], inc_id)
    return "관리자에게 확인을 요청했어요. 차량 정보와 최근 문의를 요약해 관리자 화면에 함께 등록했어요.", {"type": "admin_call", "data": public_call(call)}, "after_call_admin", None




def h_call_status(s, p):
    with _lock:
        call = next((c for c in reversed(ADMIN_CALLS) if c["_session"] == p["_sid"]), None)
        if not call:
            return "접수된 관리자 문의가 없어요. 필요하면 관리자 호출을 눌러 주세요.", None, ["call_admin"], None
        return (f"관리자 화면에 문의가 접수돼 있어요. (문의 #{call['id']}) 처리 상황은 현장 관리자에게 확인해 주세요.",
                {"type": "admin_call", "data": public_call(call)}, "after_call_admin", call["updated_at"])


def h_get_alerts(s, p):
    a = current_alert()
    _answer_evidence.set(a)
    if a.get("unavailable"):
        return T.ALERT_UNAVAILABLE, None, "default", None
    if not a.get("level"):
        return (T.api_alert(a) if a.get("api_result") else T.NO_ALERT) + _stale_note(a), None, "default", None
    card = {"type": "alert", "data": a}
    text = (T.api_alert(a) if a.get("api_result") else T.alert_status(a)) + _stale_note(a)
    return text, card, "alert", None


def h_alert_near(s, p):
    a = current_alert()
    _answer_evidence.set(a)
    if a.get("unavailable"):
        return T.ALERT_UNAVAILABLE, None, "default", None
    if not a.get("level"):
        return (T.api_alert(a) if a.get("api_result") else T.NO_ALERT) + _stale_note(a), None, "default", None
    incidents.count("near_queries", a.get("incident_id"))
    car = _find_vehicle(s.car_id)
    return T.alert_near(a, car) + _stale_note(a if a.get("stale") else car), None, "alert", None


def h_evacuate(s, p):
    a = current_alert()
    _answer_evidence.set(a)
    if a.get("unavailable"):
        return T.ALERT_UNAVAILABLE, None, "default", None
    if not a.get("level"):
        return T.EVACUATE_NONE + _stale_note(a), None, "default", None
    incidents.count("evacuation_requests", a.get("incident_id"))
    return T.EVACUATE.format(exit=data.exit_for(a["lot_id"])) + _stale_note(a), None, "alert", None








HANDLERS = {
    "admin_help": lambda s, p: ("관리자 호출 비용·도착 시간 정보는 연결되지 않았어요. 실제 요청할 때 ‘관리자 불러줘’를 선택해 주세요.", None, ["call_status", "call_admin"], None),
    "payment_help": lambda s, p: ("현재는 모의 결제 시연만 지원해요. 실제 카드·현금 결제 수단은 연결되지 않았어요.", None, ["fee", "call_admin"], None),
    "payment_issue": lambda s, p: ("결제 오류 문의로 확인했어요. 새 결제를 실행하지 않고 요금·결제 상태를 확인해 주세요.", None, ["fee", "call_admin"], None),
    "set_destination": h_set_destination, "cancel_destination": h_cancel_destination,
    "call_status": h_call_status, "get_empty_spots": h_get_empty_spots, "find_my_car": h_find_my_car, "get_fee": h_get_fee,
    "fee_rule": h_fee_rule, "pay_fee": h_pay_fee,
    "exit_help": h_exit_help, "get_violations": h_violations, "call_admin": h_call_admin, "get_alerts": h_get_alerts,
    "alert_near_my_car": h_alert_near, "evacuation_guide": h_evacuate,
    "help": lambda s, p: (T.HELP, None, "default", None),
    "thanks": lambda s, p: (T.THANKS, None, "default", None),
}


# ------------------------------------------------------------ 대화 흐름
def _run(s: Session, sid: str, intent: str, params: dict, prefix: str = "") -> dict:
    text, card, chip_set, updated = HANDLERS[intent](s, params)
    s.last_intent, s.fail_count = intent, 0
    return _respond(s, (prefix + " " + text).strip(), card, chip_set, updated)


def _change_vehicle(s: Session, car_id: str | None) -> dict:
    s.selected_candidate = None
    s.candidate_options.clear()
    s.clear()
    s.car_id = None
    if car_id:
        s.begin(Flow.CONFIRM_VEHICLE, car_id=car_id)
        return _respond(s, T.CONFIRM_VEHICLE.format(no=car_id), chip_set="confirm")
    s.begin(Flow.ASK_VEHICLE)
    return _respond(s, T.ASK_VEHICLE)


def _cancel_action(s: Session, params: dict) -> dict:
    had_pending = s.pending is not None
    s.clear()
    message = T.CANCEL_PENDING if had_pending else T.CANCEL_NONE
    if params.get("cancel_target") == "admin":
        message = T.CANCEL_ADMIN + (" " + T.CANCEL_PENDING if had_pending else "")
    elif params.get("cancel_target") == "payment":
        message = T.CANCEL_PENDING if had_pending else T.CANCEL_PAYMENT
        if not had_pending and params.get("refund_info"):
            message += " " + T.REFUND_UNSUPPORTED
    return _respond(s, message)


def _dispatch(s: Session, sid: str, intent: str, params: dict, vehicle_only: bool, confirmation_token=None) -> dict:
    params = {**params, "_sid": sid}
    vno = params.get("car_id")
    if params.get("lot_id")=="ambiguous":
        s.clear();s.query_context={}
        return _respond(s,"주차장이 여러 곳이에요. 안내받을 곳 하나를 골라 주세요.",chip_set=["lot_"+k for k in config.catalog.ids()][:4])
    expired = s.expire()
    if vno == "invalid":
        s.clear()
        return _respond(s, T.INVALID_VEHICLE, input_error=True)
    if vno == "short":
        s.clear()
        return _respond(s, T.SHORT_VEHICLE, input_error=True)
    if (vno and config.DATA_SOURCE == "mock" and config.ASSIGNED_CAR_IDS is not None
            and vno not in config.ASSIGNED_CAR_IDS):
        s.clear()
        return _respond(s, T.UNASSIGNED_VEHICLE, input_error=True)
    if params.get("virtual_vehicle") and not vno and intent in NEEDS_VEHICLE | {"unknown"}:
        # 가상 주차장 검색 경로가 확정되기 전에는 조회하지 않는다(api_paths.json virtual_search=null).
        s.clear()
        return _respond(s, T.VIRTUAL_VEHICLE_PENDING, chip_set=["empty_all", "find_car"], input_error=True)
    if vno == "missing":
        previous = s.pending
        params = {k: v for k, v in (previous.params if previous else {}).items() if k != "car_id"}
        s.car_id = None
        s.begin(Flow.ASK_VEHICLE, previous.intent if previous else None, params)
        return _respond(s, T.ASK_DIFFERENT_VEHICLE, input_error=True)
    if vno == "ambiguous":
        s.clear()
        return _respond(s, "차량 번호가 여러 개예요. 대상 차량 하나를 다시 알려주세요.", input_error=True)
    if intent == "pay_fee" and current_alert().get("level", 0) >= 2:
        s.clear()
        return _respond(s, "Lv2 경보로 모의 결제를 중지했어요. 대피 안내를 먼저 확인해 주세요.", chip_set="alert")
    if s.selected_candidate and intent in ("get_fee","pay_fee") and not (vno and vno != s.car_id):
        s.clear()
        return _respond(s,"번호 미인식 후보의 요금·결제는 관리자 확인 후 가능해요.",chip_set=["candidate_admin","change_vehicle"])
    if intent == "change_vehicle":
        return _change_vehicle(s, vno)
    if intent == "cancel_action":
        return _cancel_action(s, params)
    if intent in ("confirm_yes", "confirm_no"):
        pending = s.pending
        if expired:
            return _respond(s, "확인 시간이 지나 요청을 종료했어요. 원하는 기능을 다시 선택해 주세요.")
        if not pending or s.flow not in (Flow.CONFIRM_VEHICLE, Flow.CONFIRM_ACTION):
            return _respond(s, T.NOTHING_TO_CONFIRM)
        if confirmation_token is not None and confirmation_token != pending.token:
            return _respond(s, "이전 질문의 확인 버튼이에요. 가장 최근 질문에 답해 주세요.")
        if intent == "confirm_no":
            if s.flow == Flow.CONFIRM_VEHICLE:
                s.begin(Flow.ASK_VEHICLE, pending.intent, pending.params)
                return _respond(s, T.VEHICLE_DENIED)
            s.clear()
            return _respond(s, "요청을 취소했어요.")
        s.clear()  # 일회성 확인을 먼저 소비: 재전송이 새 동작으로 이어지지 않음
        if pending.flow == Flow.CONFIRM_VEHICLE:
            s.car_id = pending.car_id
            s.selected_candidate=None; s.candidate_options.clear()
            if pending.intent:
                return _run(s, sid, pending.intent, pending.params)
            return _respond(s, T.VEHICLE_SET.format(no=s.car_id), chip_set="parked")
        if pending.car_id != s.car_id:
            return _respond(s, "차량이 변경되어 이전 요청을 취소했어요. 다시 선택해 주세요.")
        if pending.intent == "pay_fee":
            result = _execute_payment(s, pending)
        elif pending.intent == "call_admin":
            result = _execute_admin(s, pending.params)
        else:
            return _respond(s, "처리할 수 없는 요청이라 취소했어요.")
        s.last_intent = pending.intent
        return _respond(s, *result)
    if vehicle_only:
        previous = s.pending if s.flow in (Flow.ASK_VEHICLE, Flow.CONFIRM_VEHICLE) else None
        s.begin(Flow.CONFIRM_VEHICLE, previous.intent if previous else None,
                previous.params if previous else {}, vno)
        return _respond(s, T.CONFIRM_VEHICLE.format(no=vno), chip_set="confirm")
    if intent == "unknown":
        # 미확인 발화가 끼면 결제/호출 확인을 유지하지 않는다.
        if s.flow in (Flow.CONFIRM_ACTION, Flow.CONFIRM_VEHICLE):
            s.clear()
        s.fail_count += 1
        return _respond(s, T.UNKNOWN_AGAIN if s.fail_count >= 2 else T.UNKNOWN_FIRST,
                        chip_set="unknown_again" if s.fail_count >= 2 else "default",
                        meta={"fallback": min(s.fail_count, 2)})
    s.clear()
    if intent in NEEDS_VEHICLE:
        if vno and vno != s.car_id:
            s.begin(Flow.CONFIRM_VEHICLE, intent, params, vno)
            return _respond(s, T.CONFIRM_VEHICLE.format(no=vno), chip_set="confirm")
        if not s.car_id:
            s.begin(Flow.ASK_VEHICLE, intent, params)
            return _respond(s, T.ASK_VEHICLE)
    return _run(s, sid, intent, params)


@_session_request
def handle(session_id: str, text=None, chip_id=None, event=None, request_id=None,
           confirmation_token=None, runtime_id=None) -> dict:
    for value in (text, chip_id, event, request_id, confirmation_token, runtime_id):
        if value is not None and not isinstance(value, str):
            raise ValueError("입력 항목은 문자열이어야 합니다.")
    for name, value, limit in (("text",text,200),("chip_id",chip_id,128),("event",event,32),
                               ("confirmation_token",confirmation_token,128),("runtime_id",runtime_id,128)):
        if value is not None and len(value)>limit: raise ValueError(f"{name}는 {limit}자 이하여야 합니다.")
    if request_id is not None and not 1 <= len(request_id) <= 128:
        raise ValueError("request_id는 1~128자여야 합니다.")
    if runtime_id and runtime_id != RUNTIME_ID:
        raise Conflict("서버가 다시 시작됐어요. 화면을 새로고침해 주세요.")
    s = get_session(session_id)
    fingerprint = (text, chip_id, event, confirmation_token)
    cutoff = time.monotonic() - 600
    for key, record in list(s.requests.items()):
        if record["at"] < cutoff:
            del s.requests[key]
    if request_id and request_id in s.requests:
        record = s.requests[request_id]
        if record["fingerprint"] != fingerprint:
            raise Conflict("같은 request_id를 다른 요청에 사용할 수 없습니다.")
        saved = deepcopy(record["response"])
        saved.update(destination=N.public(s), catalog=config.catalog.public(), banner=T.banner(current_alert()), alert_version=current_alert().get("version", 0),
                     vehicle_context=vehicle_context(s), chips=_response_chips(s, "default"),
                     flow=s.flow.value, confirmation_token=s.pending.token if s.pending else None)
        saved["meta"]["replayed"] = True
        if saved["meta"].get("admin_suggestion") and not s.pending:
            saved["chips"] = _response_chips(s, _admin_suggestion_chips(s))
        if saved["confirmation_token"] != record["response"].get("confirmation_token") and s.pending:
            # 이전 확인 응답으로 현재의 다른 확인을 설명 없이 승인시키지 않는다.
            saved["text"], saved["card"] = _pending_display(s)
        elif record["response"].get("confirmation_token") and not s.pending:
            saved["text"], saved["card"] = "이전 요청의 확인 절차는 종료됐어요. 원하는 기능을 다시 선택해 주세요.", None
        elif not s.pending:
            saved["text"] = "이전 요청의 처리 결과예요.\n" + saved["text"]
        return saved
    resp = _handle_message(s, session_id, text, chip_id, event, confirmation_token)
    if request_id:
        s.requests[request_id] = {"at": time.monotonic(), "fingerprint": fingerprint, "response": deepcopy(resp)}
        while len(s.requests) > 100:
            s.requests.popitem(last=False)
    return resp


def _handle_message(s, session_id, text, chip_id, event, confirmation_token):
    if event == "start":
        if not s.history:
            a = current_alert()
            s.observed_alert = max(0, a.get("version", 0) - (1 if a.get("level") else 0))
        had_pending = s.pending is not None
        s.clear()
        return _respond(s, T.WELCOME + ("\n미완료 확인은 취소했어요. 원하는 기능을 다시 선택해 주세요." if had_pending else ""))
    if chip_id == "unrecognized":
        s.clear()
        try:
            data.list_cars()
        except DataUnavailable:
            return _respond(s, "번호 미인식 차량 목록을 확인하지 못했어요.", chip_set=["call_admin"])
        return _respond(s, "현재 번호 미인식 차량 목록은 관리자에게 확인해 주세요.", chip_set=["call_admin"])
    if chip_id and chip_id.startswith("candidate:"):
        response = _candidate_location(s, chip_id.split(":",1)[1])
        response["meta"].update(intent="find_my_car",source="chip")
        return response
    if chip_id:
        chip = {**CHIPS,**config.catalog.chips()}.get(chip_id)
        if not chip:
            return _respond(s, T.UNKNOWN_FIRST)
        if chip_id in ("confirm_yes", "confirm_no") and confirmation_token is None:
            return _respond(s, "가장 최근 확인 버튼을 다시 눌러 주세요.")
        s.history.append(("user", chip["label"]))
        intent, params, source, vehicle_only = chip["intent"], dict(chip["params"]), "chip", False
    else:
        text = (text or "").strip()[:200]
        s.history.append(("user", text))
        r = parse(text, allow_llm=current_alert().get("level", 0) < 2)
        if r.source == "llm":
            # 분류를 기다리는 동안 경보가 바뀌었으면 늦은 선택을 실행하지 않는다.
            _alert_snapshot.set(None)
            _alert_snapshot.set(current_alert())
            if current_alert().get("level", 0) >= 2:
                _apply_alert_policy(s)
                r = parse(text, allow_llm=False)
        intent, params, source, vehicle_only = r.intent, r.params, r.source, r.vehicle_only
    intent,params = N.resolve_context(s,intent,params,text,source)
    try:
        if source == "llm" and intent == "call_admin":
            # 모델의 선택만으로 접수하지 않는다. 기존 호출 칩의 명시적인 클릭을 기다린다.
            s.clear()
            resp = _respond(s, T.SUGGEST_ADMIN_CALL, chip_set=_admin_suggestion_chips(s),
                            meta={"admin_suggestion": True})
        else:
            resp = _dispatch(s, session_id, intent, params, vehicle_only, confirmation_token)
    except DataUnavailable:
        s.clear()
        resp = _respond(s, "현재 정보를 확인하지 못했어요. 요청은 자동 재시도하지 않습니다. 잠시 후 다시 조회해 주세요.", meta={"unavailable": True})
    resp["meta"].update({"intent": intent, "source": source})
    return resp


def _pending_display(s):
    p = s.pending
    if p.flow == Flow.CONFIRM_VEHICLE:
        return T.CONFIRM_VEHICLE.format(no=p.car_id), None
    if p.flow == Flow.ASK_VEHICLE:
        return T.ASK_VEHICLE, None
    return f"{p.car_id}번 차 · 입차 {T.hhmm(p.params['entry_at'])} · {T.won(p.params['amount'])} 모의 결제 확인입니다.", None


# ------------------------------------------------------------ 먼저 말 걸기 (화면이 2초마다 호출)


def _alert_history_text(level, lot_id, current_level):
    """받지 못하고 지나간 경보를 쉬운 말로. level 0(지나간 해제)은 빈 문자열 → 화면에 보내지 않음."""
    if not level:
        return ""
    where = config.catalog.name(lot_id) if lot_id else "주차장"
    if current_level == 0:
        return f"잠시 {where}에 경보가 있었고 지금은 해제됐어요."
    return f"앞서 {where}에 경보가 있었어요. 지금 상태는 상단 안내를 확인해 주세요."


def _compact_alert_history(messages, alert):
    """지나간 경보 메시지를 정리한다.
    - 지나간 해제 메시지는 보내지 않는다 (큐 커서는 응답 cursor로 함께 확인되므로 누락 문제 없음)
    - 같은 주차장의 지나간 경보는 마지막 것 하나만
    - 지금 해제 상태이고 해제 메시지가 함께 있으면 하나로 합친다
    """
    def is_history(m):
        meta = m.get("meta", {})
        return meta.get("notification") == "alert" and (meta.get("historical") or meta.get("alert_version") != alert.get("version", 0))
    out, seen_lots = [], set()
    for m in reversed(messages):
        if is_history(m):
            if not m.get("text"):
                continue
            lot = m["meta"].get("lot_id")
            if lot in seen_lots:
                continue
            seen_lots.add(lot)
        out.append(m)
    out.reverse()
    if alert.get("level", 0) == 0:
        clear = [m for m in out if m.get("meta", {}).get("notification") == "alert" and not is_history(m)
                 and not m["meta"].get("alert_level")]
        history = [m for m in out if is_history(m)]
        if clear and history:
            history[-1]["text"] += " 운영 재개 여부는 관리자 안내를 확인해 주세요."
            out = [m for m in out if m not in clear]
    return out


@_session_request
def updates(session_id: str, since_version: int = 0, cursor: int = 0, runtime_id=None) -> dict:
    s = get_session(session_id)
    resync = bool(runtime_id and runtime_id != RUNTIME_ID)
    if resync:
        cursor = 0
    a = current_alert()
    version = a.get("version", 0)
    with _alert_lock:
        events = [deepcopy(e) for e in _alert_events if e["version"] > s.observed_alert]
        history_gap = bool(_alert_events and s.observed_alert and s.observed_alert < _alert_events[0]["version"] - 1)
    if not a.get("stale") and not a.get("unavailable"):
        for event in events:
            level = event.get("level", 0)
            historical = event["version"] != version
            if historical:
                msg = _alert_history_text(level, event.get("lot_id"), a.get("level", 0))
                card, chip_set = None, "default"
            elif level == 0:
                msg, card, chip_set = (T.api_alert(event) if event.get("api_result") else T.ALERT_CLEARED), None, "default"
            else:
                card = {"type": "alert", "data": event}
                if event.get("api_result"):
                    msg, chip_set = T.api_alert(event), "alert" if level >= 2 else "alert_check"
                elif level == 1:
                    msg, chip_set = T.ALERT_PUSH_1.format(lot=config.catalog.name(event["lot_id"]), spot=config.catalog.space_label(event["lot_id"], event["spot_no"])), "alert_check"
                else:
                    msg, chip_set = T.ALERT_PUSH_2.format(lot=config.catalog.name(event["lot_id"]), spot=config.catalog.space_label(event["lot_id"], event["spot_no"]), exit=data.exit_for(event["lot_id"])), "alert"
            notifications.enqueue(session_id, {"text": msg, "card": card, "chips": _chips(chip_set),
                "updated_at": event["observed_at"], "meta": {"notification": "alert", "historical": historical,
                "incident_id": event.get("incident_id"), "alert_version": event["version"], "alert_level": level,
                "lot_id": event.get("lot_id"), "spot_no": event.get("spot_no")}}, f"alert:{event['version']}")
            s.observed_alert = event["version"]
    context=vehicle_context(s)
    if s.destination and context.get("state")=="parked":
        N.clear(s);notifications.discard_prefix(session_id,"destination:")
    if s.destination:
        try:N.monitor(s,session_id,_occupancy(),a,context,notifications)
        except DataUnavailable:N.monitor_unavailable(s,session_id,notifications)
    if not N.public(s):notifications.discard_prefix(session_id,"destination:")
    result = notifications.read(session_id, cursor)
    for acknowledged in result.pop("acknowledged"):
        inc_id = acknowledged.get("meta", {}).get("incident_id")
        if inc_id is not None:
            incidents.mark_notified(session_id, inc_id)
    for m in result["messages"]:
        # 큐에 남은 과거 경보도 해제 뒤 현재 대피 명령처럼 재생하지 않는다.
        if m.get("meta", {}).get("notification") == "alert":
            inc_id = m["meta"].get("incident_id")
            if m["meta"].get("alert_version") != version:
                # 이용자 문구에는 Lv·서버 같은 내부 표현을 쓰지 않는다.
                m.update(text=_alert_history_text(m["meta"].get("alert_level"), m["meta"].get("lot_id"), a.get("level", 0)), card=None)
                m["meta"]["historical"] = True
                m["chips"] = _chips("default")
        if "action_ids" in m:m["chips"]=_hide_destination_cancel(s,_chips(m.pop("action_ids")))
        m["actions"] = deepcopy(m.get("chips", []))
        m.update(banner=T.banner(a), alert_version=version, runtime_id=RUNTIME_ID,
                 safety=T.safety_card(a) if a.get("level", 0) >= 2 else None)
        if a.get("level", 0) >= 2:
            m["chips"] = _chips("alert")
            # 대피 주 행동은 모든 알림에서 유지한다.
            if m.get("meta", {}).get("notification") not in ("admin_call","destination"):
                m["actions"] = _chips("alert")
        elif s.pending:
            m["chips"] = _response_chips(s, "default")
    result["messages"] = _compact_alert_history(result["messages"], a)
    return {**result, "alert_version": version, "runtime_id": RUNTIME_ID,
            "resync_required": resync or history_gap or result["resync_required"],
            "banner": T.banner(a), "vehicle_context": context, "destination":N.public(s), "catalog":config.catalog.public()}


# ------------------------------------------------------------ 부가 API
def suggest(q: str, limit: int = 4) -> list[str]:
    """입력 자동완성: 앞부분 일치 → 포함 일치 순서."""
    q = (q or "").strip()
    if not q:
        return []
    suggestions = SUGGESTIONS+[config.catalog.name(k)+" 자리 있어?" for k in config.catalog.ids()]
    starts = [x for x in suggestions if x.startswith(q)]
    contains = [x for x in suggestions if len(q) >= 2 and q in x and x not in starts]
    return (starts + contains)[:limit]


def stats() -> dict:
    from .llm_router import stats as router_stats
    with _lock:
        records = list(LOG)
    n = len(records)
    by_intent = Counter(x["intent"] for x in records)
    return {
        "llm": {"engine": config.LLM_ENGINE, **router_stats()},
        "total": n,
        "chip_rate": round(sum(x["input"] == "chip" for x in records) / n, 3) if n else 0,
        "unknown_rate": round(by_intent.get("unknown", 0) / n, 3) if n else 0,
        "avg_latency_ms": round(sum(x["latency_ms"] for x in records) / n, 1) if n else 0,
        "by_intent": dict(by_intent.most_common()),
        "recent_unknown": [x["text"] for x in records if x["intent"] == "unknown"][-10:],
    }


def admin_calls() -> list[dict]:
    with _lock:
        return [public_call(c) for c in ADMIN_CALLS]


def reset_all() -> None:
    global RUNTIME_ID, _alert_revision, _alert_fingerprint, _last_alert, _alert_fetched_at, _view_revision
    acquired = []
    with _lock:
        if _session_users:
            raise Conflict("요청 처리 중입니다. 잠시 후 초기화해 주세요.")
        try:
            for session in SESSIONS.values():
                if not session.lock.acquire(blocking=False):
                    raise Conflict("요청 처리 중입니다. 잠시 후 초기화해 주세요.")
                acquired.append(session.lock)
            with _alert_lock:
                if _alert_refreshing:
                    raise Conflict("경보 조회 중입니다. 잠시 후 초기화해 주세요.")
                data.reset(); incidents.reset()
                SESSIONS.clear(); LOG.clear(); ADMIN_CALLS.clear()
                notifications.reset(); _uncertain_payments.clear()
                _alert_revision, _alert_fingerprint = 0, None
                _last_alert, _alert_fetched_at = None, 0
                _alert_events.clear()
                _view_revision = 0
                RUNTIME_ID = uuid.uuid4().hex
        finally:
            for lock in reversed(acquired):
                lock.release()
