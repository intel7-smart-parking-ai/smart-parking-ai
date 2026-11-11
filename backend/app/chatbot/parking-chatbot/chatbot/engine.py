"""챗봇 엔진: 입력(칩/문장/이벤트) → 응답 JSON.

응답 형식 (화면은 이것만 그리면 됨):
{ "text", "card", "chips":[{id,label}], "banner", "updated_at", "alert_version", "meta" }

칩·승인·경보는 업무 절차로, 일반 대화는 내부 도구를 사용하는 에이전트로 보낸다.
"""
from __future__ import annotations

import json
import sys
import re
import threading
import time
import uuid
from contextvars import ContextVar
from contextlib import contextmanager
from copy import deepcopy
from functools import wraps
from collections import Counter, deque
from pathlib import Path

from . import config, incidents, monitoring, navigation as N, templates as T
from . import dialog_context as DC
from . import boundaries
from . import route_check
from . import info_gaps
from . import route
from . import forecast_chat
from .charging_intent import wants_status as _wants_charging
from .turn_guard import guard as _turn_guard
from . import plan
from .contracts import LOCATION_PENDING, timestamp, alerts_source
from .adapters import ApiAdapter, request_gets, request_lot, DataUnavailable, build_adapter, iso, now, request_deadline, request_timeout
from .parser import parse
from .state import Session, Flow, Conflict
from .notifications import Notifications
from .llm_limits import session_id as llm_session_id
from .snapshots import freeze
from .workflows import admin, parking
from .workflows.payment import PaymentWorkflow
from .workflows.vehicle import VehicleLookup
from .workflows.views import fee_card as _fee_card, journey, stale_note as _stale_note
from .handlers.exit_help import h_exit_help

CHIP_DATA = freeze(json.loads((Path(__file__).parent / "chips.json").read_text(encoding="utf-8")))
CHIPS, SETS, SUGGESTIONS = CHIP_DATA["chips"], CHIP_DATA["sets"], CHIP_DATA["suggestions"]

NEEDS_VEHICLE = {"find_my_car", "get_fee", "pay_fee", "alert_near_my_car"}

data = build_adapter()
_lock = threading.RLock()
_alert_snapshot = ContextVar("alert_snapshot", default=None)
_vehicle_snapshot = ContextVar("vehicle_snapshot", default=None)
_occupancy_snapshot = ContextVar("occupancy_snapshot", default=None)
_answer_evidence = ContextVar("answer_evidence", default=None)
_request_session = ContextVar("request_session", default=None)
LOG = deque(maxlen=2000)
_admin_store = admin.AdminCalls(_lock)
ADMIN_CALLS = _admin_store.calls
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
_payment_condition = threading.Condition()
_payment_active = False
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
            if len(SESSIONS) >= config.MAX_SESSIONS and not _evict_oldest_idle(sid):
                raise Conflict("접속 세션이 많습니다. 잠시 후 다시 접속해 주세요.")
            SESSIONS[sid] = Session()
        return SESSIONS[sid]


def _evict_oldest_idle(keep):
    """한도에 닿으면 처리 중이 아닌 세션 중 가장 오래 안 쓴 것을 정리한다(_lock 안에서 호출).

    11/10 점검 P0-3: 예전에는 한도(200)에서 새 대화를 거절해, QR·키오스크 사용자가 쌓이면
    최대 SESSION_IDLE_SEC(2시간) 동안 모든 신규 사용자가 챗봇을 쓸 수 없었다.
    """
    def order(item):
        s = item[1]
        busy = bool(s.pending and time.monotonic() < s.pending.expires_at) or bool(s.destination)
        return busy, s.last_seen   # 확인(결제·차량)이나 목적지 안내가 진행 중인 대화는 맨 나중에
    for key, old in sorted(SESSIONS.items(), key=order):
        if key == keep or _session_users[key] or not old.lock.acquire(blocking=False):
            continue
        try:
            SESSIONS.pop(key, None)
            notifications.forget(key)
            return True
        finally:
            old.lock.release()
    return False


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
                return {**deepcopy(_last_alert or {"level": 0}), "version": _alert_revision,
                        "unavailable": True, "http_status": 0}
            return deepcopy(_last_alert)
        if (config.DATA_SOURCE == "api" and _last_alert is not None
                and time.monotonic() - _alert_fetched_at < config.ALERT_SHARE_SEC):
            return deepcopy(_last_alert)
        _alert_refreshing = True
    # 동시에 들어온 조회는 한 결과를 공유한다. 네트워크 I/O 중 상태 잠금은 해제.
    read_token = request_gets.set({}) if request_gets.get() is None else None
    refreshed_alert = None
    try:
        try:
            a = data.get_alert()
        except DataUnavailable:
            a = {**(_last_alert or {"level": 0}), "version": _alert_revision, "unavailable": True, "http_status": 0}
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
        refreshed_alert = a
        return deepcopy(a)
    finally:
        if read_token is not None:
            request_gets.reset(read_token)
        with _alert_condition:
            # 예기치 않은 예외에서도 대기자를 해제한다.
            _last_alert = deepcopy(refreshed_alert if refreshed_alert is not None else {
                **(_last_alert or {"level": 0}), "version": _alert_revision,
                "unavailable": True, "http_status": 0,
            })
            _alert_fetched_at = time.monotonic()
            _alert_refreshing = False
            _alert_condition.notify_all()


# 호출자가 정한 처리 마감 시각(monotonic). 번역처럼 응답 뒤에 할 일이 남은 요청이 화면 제한 안에 끝나도록 쓴다.
budget_cap: ContextVar[float | None] = ContextVar("chat_budget_cap", default=None)


@contextmanager
def _locked_session(session):
    """잠금 대기도 요청 예산에 포함해 동일 세션의 요청 누적을 제한한다."""
    deadline = time.monotonic() + config.REQUEST_BUDGET_SEC
    cap = budget_cap.get()
    if cap is not None:
        deadline = min(deadline, max(cap, time.monotonic() + 1.0))
    if not session.lock.acquire(timeout=config.REQUEST_BUDGET_SEC):
        raise Conflict("이전 요청을 처리 중이에요. 잠시 후 다시 시도해 주세요.")
    try:
        yield deadline
    finally:
        session.lock.release()


_turn_deadline = ContextVar("turn_deadline", default=None)


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
            chip_params = ({**CHIPS, **config.catalog.chip_snapshot()}.get(raw_chip if isinstance(raw_chip, str) else None) or {}).get("params", {})
            focus = chip_params.get("lot_id") or (extract_lot(raw_text) if isinstance(raw_text, str) else None)
            zone = chip_params.get("zone") or (extract_zone(raw_text) if isinstance(raw_text, str) else None)
            lot_token = request_lot.set(focus if focus != "ambiguous" and not zone else None)
            evidence_token = _answer_evidence.set(None)
            token = _alert_snapshot.set(None)
            vehicle_token = _vehicle_snapshot.set({})
            occupancy_token = _occupancy_snapshot.set(None)
            session_token = _request_session.set((session_id, s))
            llm_token = llm_session_id.set(session_id)
            api_deadline = min(deadline, time.monotonic()+config.BACKEND_TIMEOUT_SEC) if isinstance(data.inner, ApiAdapter) else deadline
            deadline_token = request_deadline.set(api_deadline)
            turn_token = _turn_deadline.set(deadline)
            try:
                _alert_snapshot.set(current_alert())
                source = alerts_source(current_alert())
                if source == "ok" or source != s.alerts_source:
                    s.alert_unavailable_seen = False
                s.alerts_source = source
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
                llm_session_id.reset(llm_token)
                request_deadline.reset(deadline_token)
                _turn_deadline.reset(turn_token)
    return wrapped


# ------------------------------------------------------------ 응답 조립
def _chips(chip_set) -> list[dict]:
    definitions = {**CHIPS, **config.catalog.chip_snapshot()}
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


def _payment_blocked(evidence):
    return bool(evidence and (evidence.get("payment_blocked") is True
                             or evidence.get("error_code") == "EVACUATION_ACTIVE"))


def _safety_response(alert, evidence=None):
    banner = T.banner(alert)
    if alert.get("level", 0) >= 2:
        return T.safety_card(alert), banner
    if _payment_blocked(evidence):
        # A payment prohibition is authoritative, but does not supply alarm facts.
        text = "서버에서 모의 결제를 차단했어요. 대피 안내를 먼저 확인해 주세요."
        if banner:
            text += "\n" + banner["text"]
        return T.safety_card(evidence), {"level": (banner or alert).get("level", 0), "text": text}
    return None, banner


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
    safety, banner = _safety_response(alert, _answer_evidence.get())
    return {
        "text": text,
        "safety": safety,
        "card": card,
        "chips": _response_chips(s, chip_set),
        "banner": banner,
        "updated_at": updated_at or iso(now()),
        "alert_version": alert.get("version", 0),
        "meta": meta or {},
        "flow": s.flow.value,
        "confirmation_token": s.pending.token if s.pending else None,
        "runtime_id": RUNTIME_ID,
        "vehicle_context": context,
        "destination": N.public(s),
        "catalog": config.catalog.public_snapshot(),
    }


def busy_response(runtime_id=None):
    """Return the existing fallback immediately, without backend or session work."""
    if runtime_id and runtime_id != RUNTIME_ID:
        raise Conflict("서버가 다시 시작됐어요. 화면을 새로고침해 주세요.")
    token = _alert_snapshot.set({**(_last_alert or {"level": 0}), "unavailable": True})
    try:
        response = _respond(Session(), T.REQUEST_UNAVAILABLE, meta={"unavailable": True, "busy": True})
        response["state_version"] = _view_revision
        return response
    finally:
        _alert_snapshot.reset(token)


# Request snapshots and workflow bindings

def _occupancy():
    value=_occupancy_snapshot.get()
    if value is None:
        value=data.get_empty_spots();_occupancy_snapshot.set(value)
    _answer_evidence.set(value)
    return value


_PURPOSE_LABEL = {"C": "여행·출국이라면 공항에 있는", "B": "역·기차 이용이라면 기차역에 있는", "D": "쇼핑이라면 백화점에 있는"}


def _topic_particle(word):
    """'영화관' → '영화관은', '엘리베이터' → '엘리베이터는' (마지막 글자 받침 기준)."""
    last = word[-1:] if word else ""
    if "가" <= last <= "힣":
        return word + ("은" if (ord(last) - 0xAC00) % 28 else "는")
    return word + "은(는)"


def h_get_empty_spots(s, p):
    result = parking.empty_spots(s, p, _occupancy(), current_alert(), api=isinstance(data.inner, ApiAdapter))
    lot = p.get("lot_id")
    if p.get("profile_ack") and isinstance(result, tuple) and isinstance(result[0], str):
        said = {"ev": "전기차", "accessible": "장애인 차량", "general": "일반 차량"}[p["profile_ack"]]
        lead = f"{said}시군요. 이 대화에서는 {said} 기준으로 안내할게요.\n"
        if p["profile_ack"] == "general":
            lead += "일반 차량은 전기차·장애인 전용 칸에 주차할 수 없어요.\n"
        result = (lead + result[0],) + tuple(result[1:])
    if p.get("facility_missing") and isinstance(result, tuple) and isinstance(result[0], str):
        lead = f"{_topic_particle(p['facility_missing'])} 아직 안내 자료에 없어요. 대신 지금 빈자리를 알려 드릴게요.\n"
        result = (lead + result[0],) + tuple(result[1:])
    if p.get("purpose_hint") and lot in _PURPOSE_LABEL and isinstance(result, tuple) and isinstance(result[0], str):
        # '여행 가는데 어디가 좋아?' → 목적에 맞는 주차장을 고른 이유를 앞에 붙인다(사실은 그대로).
        lead = f"{_PURPOSE_LABEL[lot]} {config.catalog.name(lot)}을 안내할게요. 출발 전에 다시 확인해 주세요.\n"
        result = (lead + result[0],) + tuple(result[1:])
    return result


def h_set_destination(s,p):
    previous = s.destination
    result = N.choose(s, _occupancy(), current_alert(), p, vehicle_context(s))
    if s.destination is not previous:
        notifications.discard_prefix(p["_sid"], "destination:")
    return result


def h_cancel_destination(s,p):
    N.clear(s);notifications.discard_prefix(p["_sid"],"destination:")
    return "목적지 안내를 종료했어요.",None,["empty_all","find_car"],None


def _vehicles():
    return VehicleLookup(data, current_alert, _find_vehicle, _answer_evidence.set, _respond)


def h_find_my_car(s, p):
    return _vehicles().location(s, p)


def _candidate_location(s, token):
    return _vehicles().select_candidate(s, token)


def _refresh_workflow_alert():
    token = _alert_snapshot.set(None)
    read_token = request_gets.set({})
    try:
        alert = current_alert()
    finally:
        request_gets.reset(read_token)
        _alert_snapshot.reset(token)
    _alert_snapshot.set(alert)
    return alert


def _billing():
    return PaymentWorkflow(data, current_alert, _refresh_workflow_alert,
                           _answer_evidence.set, _uncertain_payments, now)


def _ahead_line(fee, lot_id, ahead):
    """'60분 뒤에 나가면 예상 요금은 X원' — 서버 지금 금액이 billing 결과와 같을 때만(tools.read.future_fee). 아니면 None."""
    from .tools.read import duration_text, future_fee
    if not ahead or not isinstance(fee, dict) or fee.get("paid"):
        return None
    policy = fee.get("rate_policy")
    if not isinstance(policy, dict):
        try:
            policy = ((data.get_rate_policies() or {}).get("policies") or {}).get(lot_id)
        except DataUnavailable:
            policy = None
    future = future_fee(fee.get("fee"), fee.get("minutes"), policy, ahead, fee.get("entry_at"), fee.get("updated_at"))
    if future is None:
        return None
    return f"{duration_text(ahead)} 뒤에 나가면 예상 요금은 {future[0]:,}원이에요. 실제 금액은 출차·결제 때 서버가 정해요."


def h_get_fee(s, p):
    result = _billing().fee(s, p)
    ahead = plan.ahead_minutes(s.history[-1][1] if s.history else "")
    if ahead and s.car_id and isinstance(result, tuple) and isinstance(result[0], str):
        # AI가 꺼졌거나 실패해도 '34번 차 60분 뒤 얼마?'에 미래 금액을 답한다(봇 점검 11/10 🟠3)
        try:
            fee = data.get_fee(s.car_id)
        except DataUnavailable:
            fee = None
        line = _ahead_line(fee, (fee or {}).get("lot_id"), ahead) if isinstance(fee, dict) else None
        if line:
            result = (result[0] + "\n" + line,) + tuple(result[1:])
    return result


def h_fee_rule(s, p):
    if p.get("fee_rank") and not p.get("only_lots") and s.history:
        said = plan.for_text(s.history[-1][1])
        named = list(dict.fromkeys(list(said.lots) + list(plan.nickname_lots(said.text))))
        if len(named) >= 2:
            p = {**p, "only_lots": named}   # 물은 주차장끼리만(봇 점검 11/10 🟡)
    result = _billing().fee_rule(s, p)
    minutes, lot = p.get("estimate_minutes"), p.get("lot_id")
    if minutes == -1 and isinstance(result, tuple) and isinstance(result[0], str):
        result = ("예상 요금은 3일까지만 계산해 드려요. 그보다 길면 아래 요금 기준과 하루 최대 요금을 참고해 주세요.\n"
                  + result[0],) + tuple(result[1:])
    elif minutes and lot and isinstance(result, tuple) and isinstance(result[0], str):
        # '3시간이면?' → 서버 요금 정책으로 코드가 계산한 예상 요금(결제 금액 아님)
        from .tools.read import ReadTools
        est = ReadTools(data, current_alert).estimate_fee(minutes, lot_id=lot)
        info = est.get("data") or {}
        if est.get("ok") and type(info.get("estimated_fee")) is int:
            line = (f"{info['name']}에 {info['total_time_text']} 세우면 예상 요금은 {info['estimated_fee']:,}원이에요. "
                    "실제 금액은 출차·결제 때 서버가 정해요.")
            result = (line + "\n" + result[0],) + tuple(result[1:])
    return result


def h_pay_fee(s, p):
    return _billing().prepare(s, p)


def _execute_payment(s, pending):
    """Reserve the single payment slot while releasing its condition during I/O."""
    global _payment_active
    with _payment_condition:
        try:
            remaining = request_timeout(config.REQUEST_BUDGET_SEC)
        except TimeoutError:
            remaining = 0
        if not _payment_condition.wait_for(lambda: not _payment_active, timeout=remaining):
            return T.pay({"status": "unavailable"}, pending.car_id), None, ["fee", "call_admin"], None
        _payment_active = True
    try:
        return _execute_payment_locked(s, pending)
    finally:
        with _payment_condition:
            _payment_active = False
            _payment_condition.notify()


def _execute_payment_locked(s, pending):
    return _billing().complete(s, pending)


def h_violations(s, p):
    """전용 칸 위반 차량 문의. 서버 경보의 restricted_space_violation만 근거로 답한다."""
    a = current_alert()
    _answer_evidence.set(a)
    return T.violations(a), None, "default", None


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
    if car.get("status") == LOCATION_PENDING:
        return {"car_id": s.car_id, "lot_id": car.get("lot_id"), "state": "pending"}
    return {**car, "state": "stale" if car.get("stale") else "parked"}


def _admin_summary(s, car_id=None):
    return admin.summary(s, car_id, adapter=data, find_vehicle=_find_vehicle,
                         alert=current_alert(), chips=CHIPS)


def _execute_admin(s, p):
    return _admin_store.register(s, p, current_alert(), _admin_summary, now)


def h_call_status(s, p):
    return _admin_store.status(p["_sid"])


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
    if car and car.get("status") == LOCATION_PENDING:
        car = None  # 칸 배정 전이면 위치를 추측하지 않는다.
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


def h_help(s, p):
    if p.get("info_gap") == "facility":
        from . import info_gaps
        text, chips = info_gaps.facility_answer(p)
        return text, None, chips, None
    scope = p.get("vehicle_query_help")
    if scope == "range":
        text = "차량 번호 범위나 번호대별 목록 조회는 아직 지원하지 않아요. 두 자리 차량 번호 하나를 알려주시면 현재 위치를 확인해 드릴게요."
    elif scope == "list":
        text = "입차한 차량 전체의 번호 목록은 아직 제공하지 않아요. 두 자리 차량 번호 하나의 위치나 주차장 빈자리를 확인할 수 있어요."
    else:
        text = T.HELP
    if scope or p.get("help_examples"):
        text += "\n예: ‘30번 차량은 있나요?’, ‘23번 차 어디 있어?’, ‘A 주차장 빈자리’, ‘요금 얼마야?’\n결제는 실제 청구 없는 모의 결제이며, 차량과 요금을 확인한 뒤 화면에서 승인해 주세요."
    return text, None, "default", None


HANDLERS = {
    "admin_help": lambda s, p: ("관리자 호출 비용·도착 시간 정보는 연결되지 않았어요. 실제 요청할 때 ‘관리자 불러줘’를 선택해 주세요.", None, ["call_status", "call_admin"], None),
    "payment_help": lambda s, p: ("현재는 모의 결제 시연만 지원해요. 실제 카드·현금 결제 수단은 연결되지 않았어요.", None, ["fee", "call_admin"], None),
    "payment_issue": lambda s, p: ("결제 오류 문의로 확인했어요. 새 결제를 실행하지 않고 요금·결제 상태를 확인해 주세요.", None, ["fee", "call_admin"], None),
    "set_destination": h_set_destination, "cancel_destination": h_cancel_destination,
    "call_status": h_call_status, "get_empty_spots": h_get_empty_spots, "find_my_car": h_find_my_car, "get_fee": h_get_fee,
    "fee_rule": h_fee_rule, "pay_fee": h_pay_fee,
    "exit_help": h_exit_help, "get_violations": h_violations, "call_admin": _execute_admin, "get_alerts": h_get_alerts,
    "alert_near_my_car": h_alert_near, "evacuation_guide": h_evacuate,
    "help": h_help,
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


def _confirm_pending(s, sid, intent, expired, confirmation_token):
    """Consume the matching confirmation before executing the selected action."""
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


def _validate_dispatch_input(s, intent, params, vno):
    """Expire pending state and return an early reply for invalid input."""
    if params.get("lot_id")=="ambiguous":
        s.clear()
        s.query_context = {}
        # 방금 동률로 제시한 주차장이 있으면 그 후보만 버튼으로 보여 준다.
        choices = DC.offered(s) or config.catalog.ids()
        chips = [("go:" if intent == "set_destination" else "lot_") + k for k in choices]
        return False, _respond(s,"주차장이 여러 곳이에요. 안내받을 곳 하나를 골라 주세요.",chip_set=chips[:4])
    expired = s.expire()
    if vno == "invalid":
        s.clear()
        return expired, _respond(s, T.INVALID_VEHICLE, input_error=True)
    if vno == "short":
        s.clear()
        return expired, _respond(s, T.SHORT_VEHICLE, input_error=True)
    if (vno and config.DATA_SOURCE == "mock" and config.ASSIGNED_CAR_IDS is not None
            and vno not in config.ASSIGNED_CAR_IDS):
        s.clear()
        return expired, _respond(s, T.UNASSIGNED_VEHICLE, input_error=True)
    if params.get("virtual_vehicle") and not vno and intent in NEEDS_VEHICLE | {"unknown"}:
        # 가상 C/D 차량: 번호판 뒤 4자리로 서버 조회(API_CONTRACT · vehicle-search). 결제는 화면의 결제 흐름을 쓴다.
        s.clear()
        return expired, _virtual_vehicle_reply(s, str(params["virtual_vehicle"]), intent)
    if vno == "missing":
        previous = s.pending
        params = {k: v for k, v in (previous.params if previous else {}).items() if k != "car_id"}
        s.car_id = None
        intent_to_resume = previous.intent if previous else (
            s.last_intent if s.last_intent in {"find_my_car", "get_fee"} else None)
        s.begin(Flow.ASK_VEHICLE, intent_to_resume, params)
        return expired, _respond(s, T.ASK_DIFFERENT_VEHICLE, input_error=True)
    if vno == "ambiguous":
        s.clear()
        return expired, _respond(s, "차량 번호가 여러 개예요. 대상 차량 하나를 다시 알려주세요.", input_error=True)
    return expired, None


def _dispatch(s: Session, sid: str, intent: str, params: dict, vehicle_only: bool, confirmation_token=None) -> dict:
    params = {**params, "_sid": sid}
    vno = params.get("car_id")
    expired, rejected = _validate_dispatch_input(s, intent, params, vno)
    if rejected is not None:
        return rejected
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
        return _confirm_pending(s, sid, intent, expired, confirmation_token)
    if vehicle_only and s.flow == Flow.ASK_VEHICLE and s.pending.intent in {"find_my_car", "get_fee", "alert_near_my_car"}:
        previous = s.pending
        s.clear()
        s.car_id = vno
        return _run(s, sid, previous.intent, previous.params)
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
        hint_text = s.history[-1][1] if s.history and s.history[-1][0] == "user" else ""
        if params.get("out_of_scope"):
            # 무슨 말인지는 알지만 주차 안내 범위 밖('근처 맛집 알려줘')
            return _respond(s, T.OUT_OF_SCOPE, meta={"out_of_scope": True})
        response = _respond(s, T.UNKNOWN_AGAIN if s.fail_count >= 2 else T.UNKNOWN_FIRST,
                            chip_set="unknown_again" if s.fail_count >= 2 else "default",
                            meta={"fallback": min(s.fail_count, 2)})
        if config.FALLBACK_HINTS and s.fail_count == 1 and current_alert().get("level", 0) < 2:
            from .fallback_hints import select
            ids = select(hint_text, [chip["id"] for chip in response["chips"]], CHIPS)
            response["chips"] = _response_chips(s, ids)
        return response
    s.clear()
    if intent in NEEDS_VEHICLE:
        if vno and vno != s.car_id and intent != "pay_fee":
            s.car_id = vno
            s.selected_candidate = None
            s.candidate_options.clear()
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
        alert = current_alert()
        blocked = record.get("payment_block_evidence") if not s.pending else None
        safety, banner = _safety_response(alert, blocked)
        saved.update(destination=N.public(s), catalog=config.catalog.public_snapshot(), banner=banner, alert_version=alert.get("version", 0),
                     safety=safety,
                     vehicle_context=vehicle_context(s), chips=_response_chips(s, "alert" if blocked else "default"),
                     flow=s.flow.value, confirmation_token=s.pending.token if s.pending else None)
        saved["meta"]["replayed"] = True
        saved["meta"]["route"] = "replay:" + str(saved["meta"].get("route") or "-")
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
    with monitoring.turn(session_id, text=text, chip_id=chip_id, event=event, request_id=request_id) as monitor:
        route_token, plan_token = route.begin(), plan.use(None)
        try:
            resp = _handle_checked(s, session_id, text, chip_id, event, confirmation_token)
        finally:
            steps = route.steps()
            route.end(route_token)
            plan.release(plan_token)
        if isinstance(resp.get("meta"), dict):
            resp["meta"]["route"] = route.summary(steps)
        if monitor:
            monitor.note(route=steps)
            monitor.record(resp)
    if request_id:
        s.requests[request_id] = {"at": time.monotonic(), "fingerprint": fingerprint, "response": deepcopy(resp)}
        if _payment_blocked(_answer_evidence.get()):
            s.requests[request_id]["payment_block_evidence"] = deepcopy(_answer_evidence.get())
        while len(s.requests) > 100:
            s.requests.popitem(last=False)
    return resp


def _seed_alert(s):
    """새 대화의 경보 기준점. 지금 진행 중인 경보만 알림으로 보내고, '경보 없음' 상태는 알림으로 재생하지 않는다.
    팀 화면은 start 없이 바로 질문하므로 첫 요청(또는 첫 updates)에서 잡는다."""
    if s.alert_seeded:
        return
    a = current_alert()
    s.observed_alert = max(0, a.get("version", 0) - (1 if a.get("level") else 0))
    s.alert_seeded = True


_OUT_OF_SCOPE = re.compile(r"맛집|음식점|식당|카페|날씨|기온|비\s*와|뉴스|주식|환율|호텔|숙소|영화|노래|게임|"
                           r"관광|여행지|명소|쇼핑\s*목록|버스\s*시간|지하철|택시")


def _with_guess_note(resp, params):
    """주차장을 별명으로 짐작했으면('역', '공항', '김해') 답 앞에 '부산역주차장을 말씀하시는 거라면,'을 붙인다."""
    current = plan._current.get()
    lot = params.get("lot_id") if isinstance(params, dict) else None
    if not current or not lot or lot not in current.guessed or params.get("purpose_hint") or params.get("profile_ack"):
        return resp
    if isinstance(resp, dict) and isinstance(resp.get("text"), str) and "말씀하시는 거라면" not in resp["text"]:
        resp["text"] = f"{config.catalog.name(lot)}을 말씀하시는 거라면,\n" + resp["text"]
        resp.setdefault("meta", {})["guessed_lot"] = lot
    return resp


# 안전·보안·상태 변경이 아닌 '안내성' 가로채기. 확정 안내로 대화 AI에 넘긴다.
# (연기 신고·결제·관리자 접수·지시 무시·남의 차·매출은 지금처럼 코드가 바로 답한다)
NOTICE_TOPICS = {"self_intro", "lot_names", "forgot_number", "parking_permission", "space_missing", "cancel_clarify",
                 "reservation", "discount", "admin_where", "unknown_lot", "admin_help", "payment_help", "exit_payment",
                 "vehicle_damage"}
# AI 답이 확정 안내의 핵심을 지켰는지(모두 맞아야 함). 못 지키면 정해진 문장을 쓴다.
_NOTICE_KEEP = {
    "forgot_number": (r"스티커",),
    "vehicle_damage": (r"관리자", r"확인|판단"),
    "cancel_clarify": (r"취소",),
    # 같은 뜻의 다른 말('미리 자리를 잡아 둘 수는 없어요')도 안내를 지킨 것으로 본다
    "reservation": (r"예약|보장|미리\s*(?:잡|맡)|확보|선점", r"없|안\s*돼|않|불가|어려"),
    "discount": (r"할인|감면|깎", r"없|수\s*없|안\s*돼|않|어려|불가"),
    "admin_where": (r"호출",),
    "admin_help": (r"관리자",),
    "payment_help": (r"모의",),
    "exit_payment": (r"결제|정산", r"번호|요금"),
    "lot_names": (r"개발원", r"부산역", r"김해|공항", r"신세계|백화점"),
}


def _agent_usable():
    return config.LLM_PROVIDER != "none" and config.LLM_ENGINE == "langchain" and bool(config.api_key(config.LLM_PROVIDER))


def _notice_kept(notice, answer):
    if not isinstance(answer, str) or not answer.strip():
        return False
    patterns = list(_NOTICE_KEEP.get(notice["topic"], ()))
    fixed = notice["text"]
    if notice["topic"] == "parking_permission" and "수 없" in fixed:
        patterns.append(r"수\s*없|안\s*돼|불가|못")
    if notice["topic"] == "space_missing":
        label = re.search(r"[A-D]-\d{2}", fixed)
        patterns += [re.escape(label[0]) if label else r"칸", r"없"]
    if notice["topic"] == "unknown_lot":
        patterns += [re.escape(fixed.split(" 주차장")[0].split("은 ")[0]) if " 주차장" in fixed else r"주차장", r"등록|없"]
    return all(re.search(p, answer) for p in patterns)


def _notice_turn(s, session_id, text, notice):
    """확정 안내를 근거로 대화 AI가 답하게 한다. AI가 꺼졌거나 실패하거나 안내를 지키지 못하면 정해진 문장."""
    from .conversation_turn import reply_to
    deadline_token = request_deadline.set(_turn_deadline.get())
    turn = None
    try:
        turn = reply_to(text, s, data, current_alert, _find_vehicle, _refresh_workflow_alert,
                        admin_status=lambda: _admin_store.snapshot(session_id),
                        notice={"topic": notice["topic"], "text": notice["text"]})
    except DataUnavailable:
        turn = None
    finally:
        _restore_backend_deadline(deadline_token)
    if turn is not None and turn.reply.meta.get("source") == "llm" and _notice_kept(notice, turn.reply.text):
        route.mark("agent", "answer_with_notice", topic=notice["topic"])
        # 안내 질문에서는 AI의 작업 제안(관리자 호출·결제 버튼)을 쓰지 않는다. 버튼은 정해진 안내의 버튼
        turn.suggest_admin = turn.payment = False
        turn.reply.meta.pop("pay_suggestion", None)
        if "chips" in notice:
            turn.reply.chip_set = notice["chips"]
        turn.reply.meta.update(intent=notice["topic"], notice=notice["topic"])
        return _conversation_response(s, session_id, turn)
    if turn is not None and turn.reply.meta.get("source") == "llm":
        current = monitoring.current()
        if current:
            current.note(notice_dropped={"topic": notice["topic"], "text": monitoring.capture(turn.reply.text)})
    route.mark("rule", notice["topic"], why="AI 답 없음 또는 확정 안내를 지키지 않음 → 정해진 문장")
    if "boundary" in notice:
        return _boundary_response(s, session_id, notice["boundary"], notice["rule"])
    meta = dict(notice.get("meta") or {})
    if meta.pop("clear", False):
        s.clear()
    return _respond(s, notice["text"], chip_set=notice.get("chips", "default"), meta=meta)


_SPACE_TALK = re.compile(r"자리|빈\s*칸|빈칸|비어|비었|남은|남았|만차|여유")
_FACILITY_LABEL = {"지하 여부": "실내·실외(지하) 여부"}


def _plausible_reading(s, r, text):
    """모델 없이 규칙으로 답할 때, 첫 해석이 문장과 안 맞으면 계획표(plan)를 보고 더 맞는 해석으로 바꾼다.

    - 차량이 필요한 해석(요금·내 차)인데 차·번호·돈 이야기가 없고 자리 이야기만 있으면 → 빈자리
    - 이해 못 함인데 자리·주차장 이야기가 있으면 → 언급·목적 주차장의 빈자리(없으면 전체 현황)
    - 목적만 말한 빈자리 질문 → 목적에 맞는 주차장으로 좁힘
    """
    if r.source not in ("rule", "none") or s.pending or r.vehicle_only or not isinstance(text, str):
        return r
    from .parser import ParseResult
    p = plan.for_text(text)
    if (r.intent == "find_my_car" and not r.params.get("car_id")
            and re.search(r"비었|비어|빈\s*(?:자리|칸|곳)|자리\s*(?:있|남)|칸\s*(?:있|남)", text)):
        # '장애인 주차 어디가 비었어?'는 내 차 찾기가 아니라 빈자리(봇 점검 11/10, AI 시간 초과 뒤 규칙 답)
        return ParseResult("get_empty_spots", {k: v for k, v in r.params.items() if k in ("zone", "lot_id")}, "rule")
    if r.intent == "find_my_car" and not r.params.get("car_id"):
        # [실험] '차 어디에 대는 게 좋아?'는 세워 둔 차를 찾는 말이 아니라 세울 곳을 묻는 말 → 차량 번호를 묻지 않고 빈자리
        # (11/11: 대화 AI 답이 근거 검사에 걸린 뒤 '차량 번호를 알려주세요'로 떨어졌다)
        from .conversation_turn import _PARK_WHERE
        parked = re.search(r"있(?:어|나|지|는)|위치|찾아|세워\s*(?:뒀|놨|둔)|주차\s*(?:했|해\s*(?:뒀|놨))|댔", text)
        if _PARK_WHERE.search(text) and not parked:
            return ParseResult("get_empty_spots", {k: v for k, v in r.params.items() if k in ("zone", "lot_id")}, "rule")
    own_zone = {"ev": "ev", "accessible": "accessible"}.get(s.vehicle_type)
    focus_lot = DC.focus(s)
    from .turn_guard import LEAVING
    car_for_exit = p.car or (s.car_id if LEAVING.search(text) else None)
    if LEAVING.search(text) and car_for_exit and r.intent in ("unknown", "help", "find_my_car", "get_fee"):
        # '23번 차 출차할게' → 출차 전 요금 확인(결제는 버튼·확인 절차로)
        return ParseResult("get_fee", {"car_id": car_for_exit}, "rule")
    # '23번이 내 차야' → 그 차 위치
    if p.own_car and r.intent in ("unknown", "help", "get_fee", "find_my_car") and not p.wants:
        return ParseResult("find_my_car", {"car_id": p.own_car}, "rule")
    # '결제는 하지 말고 23번 요금만' → 요금 조회(취소가 아님)
    if r.intent == "cancel_action" and p.car and "fee" in p.wants:
        return ParseResult("get_fee", {"car_id": p.car}, "rule")
    # 차 없이 묻는 요금('김해공항 요금은?', 공항 이야기 뒤 '요금은?', '3시간이면?') → 그 주차장 요금 기준(+예상 요금)
    if r.intent in ("get_fee", "unknown") and not r.params.get("car_id") and not s.car_id and not p.talk.get("car") \
            and ("fee" in p.wants or p.minutes):
        lot = p.single_lot or r.params.get("lot_id") or focus_lot
        if lot in config.catalog.ids():
            return ParseResult("fee_rule", {"lot_id": lot, **({"estimate_minutes": p.minutes} if p.minutes else {})}, "rule")
    # '불 꺼져 있던데 정상이야?', '빨간 불' → 경보 상태
    if r.intent == "unknown" and "alert" in p.wants:
        return ParseResult("get_alerts", {}, "rule")
    if p.self_only and p.self_vehicle:
        # '나 전기차야'만 말함 → 그 차가 쓸 칸의 빈자리
        zone = {"ev": "ev", "accessible": "accessible"}.get(p.self_vehicle)
        return ParseResult("get_empty_spots", {**({"zone": zone} if zone else {}), "profile_ack": p.self_vehicle}, "rule")
    if r.intent == "get_empty_spots" and own_zone and not r.params.get("zone") and not p.talk.get("spot"):
        # 전기차·장애인 차량이라고 말한 사용자의 추천 질문('여행 가는데 어디가 좋아?')도 그 칸 기준
        r = ParseResult("get_empty_spots", {**r.params, "zone": own_zone}, "rule")
    if not p.talk.get("spot"):
        # 주차와 무관한 주제(맛집·날씨 등)를 물었을 때만. 낯선 표현의 주차 질문은 기존 '이해 못함 + 추천 버튼'으로 둔다.
        if r.intent == "unknown" and _OUT_OF_SCOPE.search(text):
            return ParseResult("unknown", {"out_of_scope": True}, "rule")
        return r
    # '제일 싼 데, 근데 자리도 있어야 돼' → 요금 기준만 보는 답 대신 전 주차장의 빈자리·기본요금 비교
    if "rates" in p.wants and _SPACE_TALK.search(text) and not p.lots and r.intent in ("fee_rule", "get_empty_spots", "unknown") \
            and not r.params.get("lots"):
        return ParseResult("get_empty_spots", {"rank": "compare", "lots": list(config.catalog.ids()),
                                               **({"zone": p.zone or own_zone} if (p.zone or own_zone) else {})}, "rule")
    # 목적이 여럿('기차 먼저, 나중에 비행기') → 그 주차장들 비교
    if len(p.purpose_lots) >= 2 and r.intent in ("unknown", "get_empty_spots") and not r.params.get("lot_id"):
        return ParseResult("get_empty_spots", {"rank": "compare", "lots": list(p.purpose_lots),
                                               **({"zone": p.zone or own_zone} if (p.zone or own_zone) else {})}, "rule")
    # 규칙이 주차장을 놓친 경우('백화점 꽉 찼어?') 계획표의 주차장을 채운다
    if r.intent == "get_empty_spots" and p.single_lot and not r.params.get("lot_id") and not r.params.get("lots") \
            and r.params.get("rank") != "compare":
        r = ParseResult("get_empty_spots", {**{k: v for k, v in r.params.items() if k != "rank"}, "lot_id": p.single_lot}, "rule")
    hint = p.purpose_lot
    params = {k: v for k, v in (("lot_id", p.single_lot or hint), ("zone", p.zone or own_zone)) if v}
    if hint:
        params["purpose_hint"] = True
    needs_car = r.intent in ("get_fee", "find_my_car") and not r.params.get("car_id") and not s.car_id
    if needs_car and not p.talk.get("car") and not p.talk.get("money"):
        return ParseResult("get_empty_spots", params, "rule")
    if r.intent == "unknown":
        # '실외 주차장은 어디야?'처럼 등록되지 않은 시설 정보를 물었으면, 없다고 밝힌 뒤 빈자리를 안내
        from .knowledge import search as _knowledge_search
        try:
            _, unregistered = _knowledge_search(text, list(config.catalog.ids()), None)
        except Exception:
            unregistered = []
        if unregistered:
            params["facility_missing"] = "·".join(_FACILITY_LABEL.get(u, u) for u in unregistered)
        return ParseResult("get_empty_spots", params, "rule")
    near = info_gaps.near_place(text)
    if r.intent == "get_empty_spots" and near and not r.params.get("facility_missing"):
        from .knowledge import search as _knowledge_search
        try:
            found, _ = _knowledge_search(text, list(config.catalog.ids()), None)
        except Exception:
            found = [None]
        if not any(near in str(row) for row in found or []):
            r = ParseResult("get_empty_spots", {**r.params, "facility_missing": near}, "rule")
    if r.intent == "get_empty_spots" and hint and not r.params.get("lot_id") and not r.params.get("rank"):
        return ParseResult("get_empty_spots", {**r.params, "lot_id": hint, "purpose_hint": True,
                                               **({"zone": own_zone} if own_zone and not r.params.get("zone") else {})}, "rule")
    if r.intent == "get_empty_spots" and own_zone and not r.params.get("zone") and not r.params.get("rank"):
        # 전기차·장애인 차량이라고 말한 사용자의 빈자리 질문은 그 칸 기준
        return ParseResult("get_empty_spots", {**r.params, "zone": own_zone}, "rule")
    return r


def _restore_backend_deadline(token):
    """대화 AI를 시도한 뒤, 이어지는 규칙 답이 서버를 조회할 수 있게 조회 마감을 다시 잡는다.

    서버 조회 마감은 턴 시작 + BACKEND_TIMEOUT_SEC(2초)다. 대화 AI가 2초 넘게 쓴 뒤 규칙 답으로 넘어가면
    그 마감이 이미 지나 있어, 새 조회가 전부 시간 초과 → "현재 정보를 확인하지 못했어요"가 됐다(11/11 실제 서버 확인).
    턴 전체 마감(REQUEST_BUDGET_SEC)은 그대로 지킨다.
    """
    request_deadline.reset(token)
    turn = _turn_deadline.get()
    if turn is not None and isinstance(data.inner, ApiAdapter):
        request_deadline.set(min(turn, time.monotonic() + config.BACKEND_TIMEOUT_SEC))


def _handle_checked(s, session_id, text, chip_id, event, confirmation_token):
    """[실험] 가로채기 확인을 대화 AI와 함께 돌린 턴을 마무리한다(LLM_ROUTE_CHECK_PARALLEL=1일 때만 달라진다).

    확인을 뒤로 미루면 일단 대화 AI 쪽으로 간다. 확인이 '규칙이 맞다'로 끝나면 그 사이 남긴 대화 기록을 되돌리고
    같은 문장을 규칙 처리로 다시 한다(이번 턴의 확인 결과를 기억하므로 두 번째에는 묻지 않는다).
    """
    check_token = route_check.begin_turn()
    try:
        try:
            resp = _handle_message(s, session_id, text, chip_id, event, confirmation_token)
            unsettled = route_check.pending()   # 대화 AI까지 가지 않고 끝난 턴(정해진 답 등)
            if unsettled is None or not route_check.settle(unsettled):
                return resp
        except route_check.RuleKeeps:
            pass
        said = ("user", (text or "").strip()[:200])
        while s.history and s.history.pop() != said:
            pass
        route.mark("check", "redo", why="확인 결과 규칙이 맞음 → 규칙 처리로 다시")
        turn = _turn_deadline.get()
        if turn is not None and isinstance(data.inner, ApiAdapter):
            # 확인을 기다리는 동안 서버 조회 마감(턴 시작 + 2초)이 지났을 수 있다 → 규칙 답이 조회할 시간을 다시 잡는다
            request_deadline.set(min(turn, time.monotonic() + config.BACKEND_TIMEOUT_SEC))
        return _handle_message(s, session_id, text, chip_id, event, confirmation_token)
    finally:
        route_check.end_turn(check_token)


def _handle_message(s, session_id, text, chip_id, event, confirmation_token):
    if event == "start" and not s.history:
        s.alert_seeded = False
    _seed_alert(s)
    if event == "start":
        route.mark("start", "welcome")
        had_pending = s.pending is not None
        s.clear()
        return _respond(s, T.WELCOME + ("\n미완료 확인은 취소했어요. 원하는 기능을 다시 선택해 주세요." if had_pending else ""))
    if chip_id:
        route.mark("chip", chip_id.split(":", 1)[0])
    if chip_id == "unrecognized":
        s.clear()
        try:
            if isinstance(data.inner, ApiAdapter):
                data.list_cars(candidates=True)
            else:
                data.list_cars()
        except DataUnavailable:
            return _respond(s, "번호 미인식 차량 목록을 확인하지 못했어요.", chip_set=["call_admin"])
        return _respond(s, "현재 번호 미인식 차량 목록은 관리자에게 확인해 주세요.", chip_set=["call_admin"])
    if chip_id and chip_id.startswith("candidate:"):
        response = _candidate_location(s, chip_id.split(":",1)[1])
        response["meta"].update(intent="find_my_car",source="chip")
        return response
    if chip_id:
        chip = {**CHIPS,**config.catalog.chip_snapshot()}.get(chip_id)
        if not chip:
            return _respond(s, T.UNKNOWN_FIRST)
        if chip_id in ("confirm_yes", "confirm_no") and confirmation_token is None:
            return _respond(s, "가장 최근 확인 버튼을 다시 눌러 주세요.")
        s.history.append(("user", chip["label"]))
        intent, params, source, vehicle_only = chip["intent"], dict(chip["params"]), "chip", False
    else:
        text = (text or "").strip()[:200]
        s.history.append(("user", text))
        # 흔한 오타는 여기서 한 번 고쳐, 안전 확인·계획표·규칙·대화 AI가 같은 문장을 본다(기록에는 원문을 남긴다)
        from .parser import normalize_colloquial
        text = normalize_colloquial(text)
        # 안전 신고·허용 여부·없는 칸·자기소개처럼 짧게 확정되는 질문(turn_guard). Lv2 경보 중에는 대피 안내가 우선이라 건너뛴다.
        alert_now = current_alert()
        _reply, _meta = _turn_guard(s, text, alert_now)
        notice = None
        if _reply is not None:
            route.mark("guard", _meta.get("intent", "answer"))
            if _meta.get("intent") in NOTICE_TOPICS and _agent_usable() and alert_now.get("level", 0) < 2:
                # 안내성 답은 확정 안내로 대화 AI에 넘긴다(앞 대화·다른 조회와 엮어 자연스럽게). 못 지키면 아래에서 정해진 문장
                notice = {"topic": _meta["intent"], "text": _reply, "chips": _meta.pop("chips", "default"), "meta": _meta}
            else:
                if _meta.pop("clear", False):
                    s.clear()
                return _respond(s, _reply, chip_set=_meta.pop("chips", "default"), meta=_meta)
        else:
            route.mark("guard", "pass", why="Lv2 경보 중" if alert_now.get("level", 0) >= 2 else None)
        # 계획표: 이 문장을 여러 방식으로 읽은 결과. 대화 AI 참고 정보와 규칙 답 고르기가 같은 것을 본다.
        turn_plan = plan.make(text)
        plan.use(turn_plan)
        if turn_plan.self_vehicle:
            s.vehicle_type = turn_plan.self_vehicle   # '나 전기차야' → 이후 안내 기준
        if turn_plan.own_car:   # OWN_CAR 정규식이 10~99만 받는다
            s.car_id = turn_plan.own_car               # '23번이 내 차야' → 이후 '얼마 나왔어?'의 대상
        route.mark("plan", "+".join(turn_plan.wants) or "none", why=turn_plan.summary())
        # 충전 현황 질문은 화면의 충전 현황 카드(칸별 충전 상태·남은 시간)로 연결한다 (김채린 charging_intent 이식).
        if notice is None and _wants_charging(text) and route_check.keep(
                text, "charging_status", urgent=alert_now.get("level", 0) >= 2):
            route.mark("rule", "charging_status", why="충전 현황 질문 → 충전 현황 카드")
            from .parser import extract_lots
            _lots = extract_lots(text + " 주차")
            if len(_lots) > 1:
                return _respond(s, "어느 주차장의 충전 현황을 열까요?", chip_set=["lot_" + lot for lot in _lots])
            if current_alert().get("level", 0) >= 2:
                return _respond(s, "경보 중에는 현장 안전 안내를 먼저 따라 주세요.", chip_set="alert")
            _named = _lots[0] if _lots else DC.focus(s)
            if _named and _named not in ("C", "D"):
                return _respond(s, f"칸별 충전 현황은 김해공항·신세계 주차장에서 볼 수 있어요. {config.catalog.name(_named)}은 "
                                   "전기차 칸 빈자리로 안내해 드릴게요. ‘전기차 빈자리’라고 물어봐 주세요.",
                                meta={"intent": "charging_status", "source": "rule"})
            _lot = _named or "C"
            _which = "" if _named else "주차장을 말씀하지 않아 김해공항부터 보여 드려요. 신세계는 ‘신세계 충전 현황’이라고 해 주세요.\n"
            return _respond(
                s, f"{_which}{config.catalog.name(_lot)} 충전 현황이에요. 아래 ‘충전 현황’ 버튼을 누르면 칸별 충전 상태와 남은 시간을 볼 수 있어요.",
                card={"type": "charging_status", "data": {"lot_id": _lot}},
                meta={"intent": "charging_status", "source": "rule"})
        # 혼잡도 예측(C+안): 미래 시점의 혼잡 질문 → 서버 예측 조회 결과만으로 답한다.
        from .parser import extract_lots as _extract_lots
        _forecast = None if notice is not None else forecast_chat.answer(sys.modules[__name__], s, text, _extract_lots(text + " 주차"))
        if _forecast is not None:
            route.mark("rule", "congestion_forecast", why="특정 미래 시점 혼잡 → 서버 예측 조회")
            return _forecast
        from .parser import explicit_cars, extract_vehicle, extract_virtual, normalize_car_id
        from .conversation_turn import reply_to
        raw_id, only = extract_vehicle(text)
        # Buttons, exact confirmation words and identified workflows stay deterministic.
        rule = parse(text, allow_llm=False)
        # 서비스 범위 밖 요청(예약·할인·매출·타인 차량·지시 무시·미등록 주차장)은 모델·키워드보다 먼저 정해진 답을 낸다.
        boundary = boundaries.match(text)
        if boundary and notice is None and boundary.key in NOTICE_TOPICS and _agent_usable() \
                and alert_now.get("level", 0) < 2:
            route.mark("boundary", boundary.key)
            notice = {"topic": boundary.key, "text": boundary.text, "boundary": boundary, "rule": rule}
        elif boundary:
            route.mark("boundary", boundary.key)
            return _boundary_response(s, session_id, boundary, rule)
        elif notice is None:
            route.mark("boundary", "pass")
        if notice is not None:
            return _notice_turn(s, session_id, text, notice)
        # 상태를 바꾸거나 안전·현장 대응이 걸린 요청만 규칙이 바로 처리한다.
        # 경보·위반·문의 상태 같은 조회 질문은 대화 에이전트가 다른 자료와 묶어 답할 수 있다
        # (모델을 쓸 수 없으면 아래 규칙 처리로 그대로 대체된다).
        controlled = rule.intent in {"confirm_yes", "confirm_no", "change_vehicle",
                                     "cancel_destination", "set_destination", "admin_help",
                                     "evacuation_guide", "exit_help", "payment_help"}
        # [실험] 목적지 설정으로 읽었지만 AI 확인이 아니라고 하면 가로채지 않고 대화 AI로 넘긴다
        if rule.intent == "set_destination" and not route_check.keep(text, "set_destination",
                                                                     urgent=alert_now.get("level", 0) >= 2):
            controlled = False
        # '44번 차 결제해줘'처럼 결제만 요청하면 모델을 거치지 않고 바로 확인 절차로 간다(요금 질문이 섞이면 대화 에이전트).
        if rule.intent == "pay_fee" and not re.search(r"얼마|요금|금액|알려", text):
            controlled = True
        if rule.intent == "call_admin" and text.rstrip(" .!?") in {"관리자 불러줘", "관리자 호출", "직원 불러줘"}:
            controlled = True
        if rule.intent in ("admin_help", "payment_help") and text and _agent_usable() and alert_now.get("level", 0) < 2:
            # 관리자 비용·결제 수단 질문은 상태를 바꾸지 않는 안내 → 확정 안내로 대화 AI(실제 호출·결제는 버튼으로만)
            fixed, _, chips, _ = HANDLERS[rule.intent](s, {})
            route.mark("workflow", rule.intent, why="안내성 → 확정 안내로 대화 AI")
            return _notice_turn(s, session_id, text, {"topic": rule.intent, "text": fixed, "chips": chips,
                                                      "meta": {"intent": rule.intent}})
        if rule.intent not in {"confirm_yes", "confirm_no"} and s.expire():
            s.dialog_query = None
        if (only and text.startswith("아니") and normalize_car_id(raw_id)
                and not s.pending and s.last_intent in {"find_my_car", "get_fee"}):
            s.begin(Flow.ASK_VEHICLE, s.last_intent)
        # 번호를 여러 개 말한 것('23번이랑 41번')은 잘못된 번호가 아니다 → 대화 AI가 각각 조회(직접 쓴 번호만)
        multi_cars = raw_id == "ambiguous" and len(explicit_cars(text)) > 1
        invalid_id = raw_id and not multi_cars and (not normalize_car_id(raw_id) or
            (config.DATA_SOURCE == "mock" and config.ASSIGNED_CAR_IDS is not None and raw_id not in config.ASSIGNED_CAR_IDS))
        skip = ("workflow" if controlled else "invalid_car_id" if invalid_id else "virtual_vehicle" if extract_virtual(text)
                else "alert_lv2" if current_alert().get("level", 0) >= 2 else None)
        if controlled:
            route.mark("workflow", rule.intent, why="상태를 바꾸거나 안전이 걸린 요청은 규칙이 처리")
        elif skip:
            route.mark("agent", "skip", why=skip)
        if not skip:
            deadline_token = request_deadline.set(_turn_deadline.get()) if (
                config.LLM_PROVIDER != "none" and config.LLM_ENGINE == "langchain") else None
            try:
                turn = reply_to(text, s, data, current_alert, _find_vehicle, _refresh_workflow_alert,
                                admin_status=lambda: _admin_store.snapshot(session_id))
                if turn is not None:
                    return _conversation_response(s, session_id, turn)
            except DataUnavailable:
                route.mark("agent", "data_unavailable")
                s.clear()
                return _respond(s, T.REQUEST_UNAVAILABLE, meta={"unavailable": True})
            finally:
                if deadline_token is not None:
                    _restore_backend_deadline(deadline_token)
        text = DC.rewrite(s, text)
        r = parse(text, allow_llm=config.LLM_ENGINE == "direct" and current_alert().get("level", 0) < 2)
        r = DC.fill(s, r, text)
        first = r.intent
        r = _plausible_reading(s, r, text)
        # 대화 AI를 시도했다가 넘어온 경우만 '대체(fallback)', 처음부터 규칙이 맡은 요청은 'rule'.
        route.mark("fallback" if not skip else "rule", r.intent, why=f"첫 해석 {first} → 문장에 맞게 변경" if r.intent != first else None,
                   lot=r.params.get("lot_id"), purpose=bool(r.params.get("purpose_hint")) or None)
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
        resp = _respond(s, T.REQUEST_UNAVAILABLE, meta={"unavailable": True})
    if intent in DC.LOT_INTENTS and params.get("lot_id") in config.catalog.ids():
        DC.remember(s, params["lot_id"])
    resp = _with_guess_note(resp, params)
    resp["meta"].update({"intent": intent, "source": source})
    return resp


def _virtual_vehicle_reply(s, raw, intent):
    from .tools.read import clock_text, duration_text
    plate = raw.replace(" ", "") if not raw.isdigit() else None
    number = raw[-4:]
    virtual_names = "·".join(config.catalog.name(k) for k in config.catalog.ids()
                             if (config.catalog.lot(k) or {}).get("kind") == "virtual")
    try:
        location = data.find_virtual(number, plate)
    except DataUnavailable:
        location = {"status": "unavailable"}
    if isinstance(location, dict) and location.get("status") == "unavailable":
        return _respond(s, "가상 주차장 차량 정보를 지금 확인할 수 없어요. 잠시 후 다시 시도해 주세요.",
                        chip_set=["empty_all", "call_admin"])
    if not location:
        return _respond(s, f"차량번호 {plate or '뒤 4자리 ' + number}(으)로 주차 중인 차량을 찾지 못했어요.\n"
                           f"가상 주차장({virtual_names})에 주차한 차량만 번호판으로 조회돼요. 번호를 다시 확인해 주세요.",
                        chip_set=["find_car", "empty_all", "call_admin"])
    lot_name = config.catalog.name(location["lot_id"])
    label = location.get("plate_number") or number
    lines = [f"{label} 차량은 {lot_name} {location['space_id']}에 있어요."]
    fee = None
    try:
        fee = data.virtual_fee(location)
    except DataUnavailable:
        fee = None
    entry = clock_text(location.get("entry_at")) if location.get("entry_at") else None
    minutes = (fee or {}).get("minutes")
    if entry:
        lines.append(f"{entry}에 들어와서 " + (f"{duration_text(minutes)}째 주차 중이에요." if minutes else "주차 중이에요."))
    if fee and intent in ("get_fee", "pay_fee", "find_my_car", "unknown"):
        lines.append(f"지금까지 요금은 {fee['fee']:,}원이에요." + (" 이미 결제됐어요." if fee.get("paid") else ""))
        ahead = plan.ahead_minutes(s.history[-1][1] if s.history else "")
        line = _ahead_line(fee, location["lot_id"], ahead) if intent in ("get_fee", "unknown") else None
        if line:
            lines.append(line)   # '6172 차 60분 뒤 얼마?'(봇 점검 11/10 🟠1)
    # 관리자 호출 요약에 이 차를 넣는다(실물 car_id가 없는 가상 차량, 봇 점검 🟠2)
    s.virtual_vehicle = {"plate_number": location.get("plate_number"), "vehicle_number": number,
                         "lot_id": location["lot_id"], "space_id": location.get("space_id")}
    if intent == "pay_fee" and not (fee or {}).get("paid"):
        lines.append("가상 주차장 차량의 모의 결제는 화면의 차량번호 조회에서 진행해 주세요.")
    from .parser import normalize_colloquial
    from .turn_guard import LEAVING
    if fee and intent != "pay_fee" and LEAVING.search(normalize_colloquial(s.history[-1][1] if s.history else "")):
        # '1780 출차해도 돼?' — 서버의 결제 여부만 근거로 답한다(봇 점검 11/11: '잘 이해하지 못했어요')
        lines.append("결제가 끝난 차량이라 출차하시면 돼요." if fee.get("paid")
                     else "출차 전에 요금을 먼저 결제해 주세요. 가상 주차장 차량의 모의 결제는 화면의 차량번호 조회에서 진행해요.")
    # car_location 카드는 실물 car_id 계약이라 쓰지 않는다(가상 차량은 문장으로 안내).
    resp = _respond(s, "\n".join(lines), chip_set=["empty_all", "fee_rule", "call_admin"],
                    updated_at=location.get("updated_at"))
    resp["meta"].update({"virtual_vehicle": True})
    return resp


def _boundary_response(s, sid, boundary, rule):
    s.clear()
    if boundary.with_spots:
        params = {k: v for k, v in (rule.params or {}).items() if k in ("lot_id", "zone")}
        try:
            resp = _dispatch(s, sid, "get_empty_spots", params, False)
        except DataUnavailable:
            resp = None
        if resp is not None:
            resp["text"] = boundary.text + "\n" + resp["text"]
            if s.history and s.history[-1][0] == "bot":
                s.history[-1] = ("bot", resp["text"])
            resp["meta"].update({"intent": "boundary", "boundary": boundary.key, "source": "rule"})
            return resp
    resp = _respond(s, boundary.text, chip_set=boundary.chips)
    resp["meta"].update({"intent": "boundary", "boundary": boundary.key, "source": "rule"})
    return resp


def _conversation_response(s, sid, turn):
    q, reply = turn.query, turn.reply
    s.clear()
    s.dialog_query = q
    if turn.ask_vehicle:
        s.begin(Flow.ASK_VEHICLE, "dialog_read" if q else "dialog_agent")
    target = q.car_id if q and not q.clarification else turn.car_id
    if target:
        if s.selected_candidate and target == s.car_id and q and (q.wants_fee or turn.payment):
            return _respond(s, "번호 미인식 후보의 요금·결제는 관리자 확인 후 가능해요.",
                            chip_set=["candidate_admin", "change_vehicle"])
        if target != s.car_id:
            s.selected_candidate = None
            s.candidate_options.clear()
        s.car_id = target
    if q and q.candidates:
        DC.remember(s, q.candidates[0] if len(q.candidates) == 1 else None, offered_lots=q.candidates)
    else:
        # 계획표에 주차장이 하나만 나왔거나 목적 주차장이 있으면 '거기'의 기준으로 기억한다
        last_text = next((m for who, m in reversed(s.history) if who == "user"), "")
        turn_plan = plan.for_text(last_text)
        if turn_plan.single_lot or turn_plan.purpose_lot:
            DC.remember(s, turn_plan.single_lot or turn_plan.purpose_lot)
    text, card, chips, updated = reply.text, reply.card, reply.chip_set, reply.updated_at
    evidence = reply.meta.get("source_evidence", {})
    if evidence or reply.meta.get("payment_blocked"):
        _answer_evidence.set({**evidence, "payment_blocked": bool(reply.meta.get("payment_blocked"))})
    if turn.payment and s.car_id and not q.needs_vehicle and not q.clarification:
        quote, fee_card, chips, at = h_pay_fee(s, {"_sid": sid})
        text = text + "\n" + quote
        card, updated = fee_card or card, at or updated
    elif turn.suggest_admin:
        chips = _admin_suggestion_chips(s)
        reply.meta["admin_suggestion"] = True
    s.fail_count = 0
    s.last_intent = reply.meta.get("intent", "conversation")
    return _respond(s, text, card, chips, updated, meta={"intent": s.last_intent, **reply.meta})


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
    _seed_alert(s)
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
            "banner": T.banner(a), "vehicle_context": context, "destination":N.public(s), "catalog":config.catalog.public_snapshot()}


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
    from .agents import stats as agent_stats
    from .llm_router import stats as router_stats
    with _lock:
        records = list(LOG)
        admin_pruned = _admin_store.pruned
    n = len(records)
    by_intent = Counter(x["intent"] for x in records)
    return {
        "llm": {"engine": config.LLM_ENGINE, **router_stats(), "conversation": agent_stats()},
        "monitoring": monitoring.stats(),
        "total": n,
        "alerts_source": alerts_source(_last_alert) if _last_alert is not None else "unavailable",
        "backend_breaker": data.inner.breaker_stats() if isinstance(data.inner, ApiAdapter) else {},
        "retention": {"admin_calls_pruned": admin_pruned, "incidents_pruned": incidents.pruned_count(),
                      "uncertain_payments": len(_uncertain_payments)},
        "chip_rate": round(sum(x["input"] == "chip" for x in records) / n, 3) if n else 0,
        "unknown_rate": round(by_intent.get("unknown", 0) / n, 3) if n else 0,
        "avg_latency_ms": round(sum(x["latency_ms"] for x in records) / n, 1) if n else 0,
        "by_intent": dict(by_intent.most_common()),
        "recent_unknown": [x["text"] for x in records if x["intent"] == "unknown"][-10:],
    }


def admin_calls() -> list[dict]:
    with _lock:
        return [admin.public_call(c) for c in ADMIN_CALLS]


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
                SESSIONS.clear()
                LOG.clear()
                _admin_store.reset()
                notifications.reset(); _uncertain_payments.clear()
                _alert_revision, _alert_fingerprint = 0, None
                _last_alert, _alert_fetched_at = None, 0
                _alert_events.clear()
                _view_revision = 0
                RUNTIME_ID = uuid.uuid4().hex
        finally:
            for lock in reversed(acquired):
                lock.release()
