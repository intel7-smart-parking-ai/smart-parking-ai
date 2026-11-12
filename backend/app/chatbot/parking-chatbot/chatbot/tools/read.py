"""Expose only data present in the project's query API or approved settings."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from functools import wraps
import math

from .. import config, navigation
from ..info_gaps import near_place
from ..adapters import (DataUnavailable, FeeCalculatorUnavailable, MockAdapter, default_policy, fee_details,
                        request_lot, request_timeout)
from ..contracts import timestamp
from ..knowledge import TOPIC_KEYS, search
from ..parser import normalize_car_id


# ---------------------------------------------------------------- 코드가 계산하는 표현값
# 모델이 직접 계산하지 않도록 차이·경과 시간·시각 표현을 도구 결과에 미리 넣는다.
# 근거 검사(agents/evidence.py)는 이 값들도 이번 턴의 근거로 인정한다.

def clock_text(value):
    """ISO 시각 → '오후 2시 5분' (KST). 해석할 수 없으면 None."""
    try:
        dt = timestamp(value).astimezone(config.KST)
    except (TypeError, ValueError, OverflowError):
        return None
    half = "오전" if dt.hour < 12 else "오후"
    hour = dt.hour % 12 or 12
    return f"{half} {hour}시" + (f" {dt.minute}분" if dt.minute else "")


def duration_text(minutes):
    """분 → '1시간 15분', '45분', '1일 2시간'."""
    if type(minutes) not in (int, float) or minutes < 0 or not math.isfinite(minutes):
        return None
    minutes = int(math.ceil(minutes))
    days, rest = divmod(minutes, 1440)
    hours, mins = divmod(rest, 60)
    parts = [f"{days}일"] if days else []
    if hours:
        parts.append(f"{hours}시간")
    if mins or not parts:
        parts.append(f"{mins}분")
    return " ".join(parts)


def _elapsed_minutes(location, fee):
    """서버가 준 경과 분을 우선한다. 없으면 입차 시각과 현재 시각으로 계산한다."""
    for record in (fee, location):
        value = (record or {}).get("minutes")
        if type(value) is int and value >= 0:
            return value
    entry = (fee or {}).get("entry_at") or (location or {}).get("entry_at")
    try:
        delta = datetime.now(config.KST) - timestamp(entry)
    except (TypeError, ValueError, OverflowError):
        return None
    minutes = delta.total_seconds() / 60
    return int(math.ceil(minutes)) if minutes >= 0 else None


def _subject(name):
    """이름 뒤 주격 조사(이/가)."""
    last = (name or "")[-1:]
    if "가" <= last <= "힣":
        return name + ("이" if (ord(last) - 0xAC00) % 28 else "가")
    return name + "이(가)"


_POLICY_KEYS = ("base_minutes", "base_fee", "unit_minutes", "extra_fee", "daily_cap")


def _valid_policy(policy):
    return isinstance(policy, dict) and all(type(policy.get(k)) is int and policy[k] > 0 for k in _POLICY_KEYS)


def future_fee(current_fee, current_minutes, policy, minutes_ahead, entry_at=None, basis_at=None):
    """지금부터 minutes_ahead분 뒤 (금액, 총 분). billing 계산 함수만 쓴다(규칙 4).
    서버가 준 지금 금액이 같은 함수·같은 정책의 결과와 다르면(할인·감면·결제 영수증 등) 만들지 않는다(None).
    서버가 입차·기준 시각을 주면 billing과 똑같이 실제 경과 초에 더해 분을 올림한다(방금 입차한 차의 1분 차이 방지)."""
    if (not _valid_policy(policy) or type(current_fee) is not int or type(current_minutes) is not int
            or current_minutes < 0 or type(minutes_ahead) is not int or minutes_ahead < 1):
        return None
    total = current_minutes + minutes_ahead
    try:
        elapsed = (timestamp(basis_at) - timestamp(entry_at)).total_seconds()
        total = max(1, math.ceil(max(0.0, elapsed) / 60 + minutes_ahead))
    except (TypeError, ValueError, OverflowError):
        pass
    try:
        if fee_details(max(1, current_minutes), policy)[0] != current_fee:
            return None
        return fee_details(total, policy)[0], total
    except FeeCalculatorUnavailable:
        return None


def policy_text(policy):
    """요금 정책을 사람이 읽는 한 줄로. 숫자는 정책 값 그대로."""
    if not _valid_policy(policy):
        return None
    text = (f"처음 {policy['base_minutes']}분 {policy['base_fee']:,}원, 이후 {policy['unit_minutes']}분마다 "
            f"{policy['extra_fee']:,}원, 하루 최대 {policy['daily_cap']:,}원")
    free = policy.get("free_minutes")
    if type(free) is int and free > 0:
        # 무료 회차(#140): 서버 값 그대로. 대화 AI가 '5분 이내 무료(0원)'를 말할 근거가 된다
        text += f" (입차 후 {free}분 이내 출차는 무료, 0원)"
    return text


def _project(row, fields):
    return {key: row[key] for key in fields if key in row}


def _same_visit(location, fee):
    """Two independent reads may straddle departure or a new visit."""
    if location.get("lot_id") and fee.get("lot_id") and location["lot_id"] != fee["lot_id"]:
        return False
    session_linked = location.get("session_id") and fee.get("session_id")
    if session_linked and location["session_id"] != fee["session_id"]:
        return False
    entry_linked = location.get("entry_at") and fee.get("entry_at")
    if entry_linked:
        try:
            return timestamp(location["entry_at"]) == timestamp(fee["entry_at"])
        except (TypeError, ValueError, OverflowError):
            return False
    return bool(session_linked)


_READ_FAILURES = (DataUnavailable, TimeoutError, OSError, ValueError)
_LOCATION_FIELDS = ("car_id", "lot_id", "space_id", "spot_no", "entry_at", "session_id",
                    "vehicle_type", "updated_at", "status", "stale", "is_mock", "data_source")
_FEE_FIELDS = ("car_id", "lot_id", "session_id", "entry_at", "fee", "minutes", "paid",
               "payment_status", "payment_blocked", "updated_at", "stale", "is_mock", "data_source",
               "free_applied", "free_minutes")      # 무료 회차(#140): 0원인 이유를 대화 AI가 알게 한다


_NOT_FOUND = "해당 차량의 입차 기록을 찾지 못했습니다."


def _total_summary(rows):
    """여러 주차장 빈자리의 합계를 코드가 계산해 넘긴다(규칙 답의 '총 N자리'와 같은 기준: 경보·이용 제한·확인 불가는 뺀다).

    모델이 직접 더하면 틀릴 수 있고, 근거 검사는 조회 결과에 없는 숫자를 막기 때문에 맞게 더한 답도 버려졌다
    (11/11 실측: "총 91자리입니다" → unsupported_number → 규칙 답으로 대체, 3~5초 지연).
    """
    counted = [row for row in rows if row.get("availability") in ("available", "full") or row.get("reason") == navigation.NO_CONFIRMED]
    excluded = [row["name"] for row in rows if row not in counted]
    summary = {"total_empty": sum(row["empty"] for row in counted if type(row.get("empty")) is int),
               "total_empty_rule": "여러 주차장 빈자리의 합계. 합계를 말할 때는 이 값을 그대로 쓴다(직접 더하지 않는다)."}
    if excluded:
        summary["total_excludes"] = excluded       # 경보·이용 제한·확인 불가라 합계에서 뺀 주차장
    return summary


class ReadTools:
    TOOL_NAMES = ("parking_status", "vehicle_info", "fee_policies", "compare_lots", "estimate_fee",
                  "alert_summary", "violations", "call_status", "search_knowledge", "propose_action")

    def __init__(self, adapter, alert_provider, occupancy_provider=None, vehicle_provider=None,
                 *, allowed_car_id=None, allowed_lots=None, admin_status_provider=None, notice=None,
                 extra_car_ids=(), virtual_numbers=()):
        self.adapter = adapter
        self._occupancy = occupancy_provider or adapter.get_empty_spots
        self._vehicle = vehicle_provider or adapter.find_car
        self._alert = alert_provider
        self._admin_status = admin_status_provider
        self._car = normalize_car_id(allowed_car_id)
        # 사용자가 이번 문장에 직접 쓴 다른 차량 번호(두 차 비교). 위치·요금 조회만 허용하고 결제 제안 등은 기준 차량만.
        self._extra_cars = tuple(c for c in (normalize_car_id(x) for x in extra_car_ids or ()) if c and c != self._car)
        self._lots = None if allowed_lots is None else tuple(dict.fromkeys(allowed_lots))
        self._snapshot = {}
        self._notice = notice if isinstance(notice, dict) and notice.get("text") else None
        # 가상 주차장 차량: 사용자가 말한(또는 이 대화에서 이미 조회한) 번호판·뒤 4자리만 조회할 수 있다
        self._virtual = tuple(dict.fromkeys(str(x).replace(" ", "") for x in virtual_numbers or () if x))

    def service_notice(self) -> dict:
        """This turn's approved service notice (fixed policy/guidance the service already decided, e.g. no reservations,
        how to find a forgotten car number, a space that does not exist). Read it first and keep its meaning;
        combine it with other reads (vacancy, fees) and the conversation to answer naturally."""
        if not self._notice:
            return self._finish("service_notice", {}, kind="internal_knowledge", error="이번 질문에는 확정 안내가 없습니다.")
        row = {"topic": self._notice.get("topic"), "text": self._notice["text"]}
        return self._finish("service_notice", {"rows": [row]}, kind="internal_knowledge")

    def _read_once(self, key, read):
        if key not in self._snapshot:
            try:
                self._snapshot[key] = deepcopy(read())
            except _READ_FAILURES as exc:
                self._snapshot[key] = exc
        value = self._snapshot[key]
        if isinstance(value, Exception):
            raise value
        return value

    def _scope(self, requested):
        allowed = list(config.catalog.ids() if self._lots is None else self._lots)
        if requested is None:
            return allowed if allowed and all(config.catalog.canonical_id(x) == x for x in allowed) else None
        if not isinstance(requested, list) or not requested:
            return None
        ids = [config.catalog.canonical_id(x) for x in requested]
        if any(x not in allowed or x is None for x in ids):
            return None
        return list(dict.fromkeys(ids))

    def _finish(self, tool, data, *, raw=None, lots=(), error=None, kind=None, **extra):
        raw = raw or {}
        mock = isinstance(getattr(self.adapter, "inner", self.adapter), MockAdapter)
        is_mock = mock or bool(raw.get("is_mock")) or raw.get("data_source") == "mock"
        source = {"kind": kind or ("simulation" if is_mock else "internal_api"), "scope": list(lots),
                  "updated_at": raw.get("updated_at") or raw.get("as_of"), "is_mock": is_mock,
                  "stale": bool(raw.get("stale"))}
        result = {"tool": tool, "ok": error is None, "data": data, "source": source, **extra}
        if error is not None:
            result["error"] = error
        return result

    def parking_status(self, lot_ids: list[str] | None = None, zone: str | None = None) -> dict:
        """Read current vacancy, uncertainty and entry restrictions for candidate lot IDs. zone can be general, ev or accessible."""
        lots = self._scope(lot_ids)
        if lots is None or (zone is not None and zone not in {z["key"] for z in config.catalog.zones()}):
            return self._finish("parking_status", {}, error="질문의 주차장 또는 구역을 확인해 주세요.")
        try:
            request_timeout(config.REQUEST_BUDGET_SEC)
            scoped = request_lot.set(lots[0] if len(lots) == 1 else None)
            try:
                snapshot = self._read_once(("occupancy", request_lot.get()), self._occupancy)
            finally:
                request_lot.reset(scoped)
            if not isinstance(snapshot, dict) or not isinstance(snapshot.get("lots"), dict):
                raise DataUnavailable("주차장 상태 미확인")
        except _READ_FAILURES:
            return self._finish("parking_status", {}, lots=lots, error="현재 주차 상태를 조회하지 못했습니다.")
        try:
            alert = self._alert()
            if not isinstance(alert, dict):
                raise DataUnavailable("경보 미확인")
        except _READ_FAILURES:
            alert = {"unavailable": True}
        rows = []
        for lot_id in lots:
            lot = snapshot["lots"].get(lot_id) or {}
            part = lot if zone is None else (lot.get("zones") or {}).get(zone, {})
            reason = navigation.reason(snapshot, alert, lot_id, zone)
            if zone and lot.get("zone_partial") and reason in (None, "만차"):
                reason = "일부 칸의 구역 정보 미확인"
            if alert.get("unavailable") and reason in (None, "만차"):
                reason = "경보 상태 미확인"
            if reason is None:
                availability = "available"
            elif reason == "만차":
                availability = "full"
            elif reason == "경보" or reason.startswith("운영 제한"):
                availability = "restricted"
            else:
                availability = "unknown"
            row = {"lot_id": lot_id, "name": lot.get("name") or config.catalog.name(lot_id),
                   "zone": zone, **{key: part.get(key) for key in ("empty","total","uncertain","unavailable")},
                   "availability": availability, "reason": reason}
            pending = part.get("uncertain")
            if type(pending) is int and pending > 0:
                # 카메라가 끊기면 칸이 '확인 필요'가 되고 빈자리 수는 0으로 온다. 그건 만차가 아니라 '모르는 상태'다
                # (봇 점검 11/11: 12칸 모두 확인 필요인 주차장을 대화 AI가 "만차라서 자리가 없어요"라고 답함)
                if reason == "만차":
                    # '만차'·'full'을 넘기면 모델이 그대로 말한다 → 사유와 상태를 바꿔 넘긴다(#120 후속)
                    row["reason"], row["availability"] = navigation.NO_CONFIRMED, "unknown"
                if row["empty"] == 0:
                    row["camera_check"] = (f"카메라 확인 중 {pending}칸. 이 칸들은 비었는지 알 수 없다. "
                                           "이 주차장은 '만차'라고 하지 않는다. "
                                           "'확인된 빈자리는 없고 카메라 확인 중인 칸이 있다'고 말한다.")
                else:
                    # 빈자리가 있는 주차장: 확인 중인 칸은 빈자리 수와 따로 센다
                    # (11/11 실측: "21개 (카메라 확인 중인 칸 1개 포함)", "정확한 빈자리는 20개"라고 답함)
                    row["camera_check"] = (f"카메라 확인 중 {pending}칸. 이 칸들은 빈자리 {row['empty']}칸에 들어 있지 않다(따로 센다). "
                                           "비었는지 알 수 없다. 빈자리 수는 그대로 말하고, 확인 중인 칸을 빼거나 더하지 않는다.")
            rows.append(row)
        alert_summary = _project(alert, ("level", "lot_id", "stale", "unavailable"))
        if alert.get("unavailable") and not alert.get("level"):
            alert_summary.pop("level", None)
        data = {"rows": rows, "alert": alert_summary}
        if len(rows) >= 2:
            data["summary"] = _total_summary(rows)
        return self._finish("parking_status", data, lots=lots, raw=snapshot)

    def vehicle_info(self, car_id: str, include_fee: bool = True) -> dict:
        """Read the selected two-digit vehicle's location and optionally its fee. Use only the vehicle ID provided by the user/current session."""
        if car_id not in ((self._car,) if self._car else ()) + self._extra_cars or type(include_fee) is not bool:
            return self._finish("vehicle_info", {}, error="차량번호를 확인해 주세요.")
        errors = {}
        def read_part(key, lookup, fields):
            try:
                request_timeout(config.REQUEST_BUDGET_SEC)
                raw = self._read_once((key, car_id), lambda: lookup(car_id))
            except _READ_FAILURES:
                raw = {"unavailable": True}
            if raw is None:
                errors[key] = _NOT_FOUND
            elif not isinstance(raw, dict) or raw.get("unavailable") or raw.get("status") in {"error", "conflict", "unavailable"}:
                errors[key] = "현재 조회할 수 없습니다."
            elif raw.get("car_id") != car_id:
                errors[key] = "차량 식별자가 일치하지 않습니다."
            elif self._lots is not None and raw.get("lot_id") not in self._lots:
                errors[key] = "요청한 주차장 범위에서 확인되지 않았습니다."
            else:
                return _project(raw, fields)
            return None
        location = read_part("location", self._vehicle, _LOCATION_FIELDS)
        fee = read_part("fee", self.adapter.get_fee, _FEE_FIELDS) if include_fee else None
        if fee and location and not _same_visit(location, fee):
            errors["fee"] = "위치와 요금이 같은 주차 기록인지 확인할 수 없습니다."
            fee = None
        available = [x for x in (location, fee) if x]
        for record in available:
            # 표시용 값은 코드가 만든다(오후 2시 5분, 1시간 15분). 원본 필드는 그대로 둔다.
            entry = clock_text(record.get("entry_at")) if record.get("entry_at") else None
            if entry:
                record["entry_time_text"] = entry
        elapsed = _elapsed_minutes(location, fee) if available else None
        if elapsed is not None:
            for record in available:
                record["elapsed_text"] = duration_text(elapsed)
        if location and location.get("lot_id"):
            location["lot_name"] = config.catalog.name(location["lot_id"])
        payload = {"car_id": car_id, "location": location, "fee": fee, "errors": errors,
                   "partial": bool(errors and available)}
        if not available and errors and set(errors.values()) == {_NOT_FOUND}:
            # 조회는 됐고 입차 기록이 없다는 것이 확인됐다(조회 실패와 다르다) → '입차 기록을 찾지 못했어요'라고 답할 근거
            payload["not_found"] = True
        raw = {"stale": any(x.get("stale") for x in available),
               "is_mock": any(x.get("is_mock") or x.get("data_source") == "mock" for x in available),
               "updated_at": available[0].get("updated_at") if len(available) == 1 else None}
        lots = tuple(dict.fromkeys(x["lot_id"] for x in available if x.get("lot_id")))
        return self._finish("vehicle_info", payload, raw=raw, lots=lots,
                            error=None if available else " ".join(dict.fromkeys(errors.values())))

    def virtual_vehicle_info(self, vehicle_number: str) -> dict:
        """Read a vehicle parked in a virtual lot (김해공항·신세계) by its license plate or the plate's last four digits:
        where it is, when it entered, the fee so far and whether it is paid. Use only the number the user gave.
        This assistant cannot pay for or release these vehicles; follow data.payment_guide when the user asks to pay or leave.
        data.fee.fee is already the amount due if the vehicle leaves now: do not call estimate_fee for these vehicles."""
        raw = str(vehicle_number or "").replace(" ", "")
        allowed = {x[-4:] for x in self._virtual}
        if not raw or raw[-4:] not in allowed or not raw[-4:].isdigit():
            return self._finish("virtual_vehicle_info", {}, error="사용자가 말한 차량 번호만 조회할 수 있습니다.")
        number = raw[-4:]
        plate = raw if not raw.isdigit() else next((x for x in self._virtual if x[-4:] == number and not x.isdigit()), None)
        try:
            request_timeout(config.REQUEST_BUDGET_SEC)
            found = self._read_once(("virtual", number, plate), lambda: self.adapter.find_virtual(number, plate))
        except _READ_FAILURES:
            found = {"status": "unavailable"}
        if isinstance(found, dict) and found.get("status") == "unavailable":
            return self._finish("virtual_vehicle_info", {}, error="가상 주차장 차량 정보를 지금 조회할 수 없습니다.")
        if not found or not found.get("lot_id"):
            # not_found: 조회는 됐고 그 차가 없다는 것이 확인됐다 → '찾지 못했어요'라고 답할 근거(근거 검사가 허용)
            return self._finish("virtual_vehicle_info", {"vehicle_number": number, "plate_number": plate, "found": False,
                                                         "not_found": True},
                                error="그 번호로 주차 중인 가상 주차장 차량을 찾지 못했습니다.")
        try:
            fee = self._read_once(("virtual_fee", number, plate), lambda: self.adapter.virtual_fee(found))
        except _READ_FAILURES:
            fee = None
        location = {"lot_id": found["lot_id"], "lot_name": config.catalog.name(found["lot_id"]),
                    "space_id": found.get("space_id"), "entry_at": found.get("entry_at")}
        entry = clock_text(found.get("entry_at")) if found.get("entry_at") else None
        if entry:
            location["entry_time_text"] = entry
        errors = {}
        fee_view = None
        if isinstance(fee, dict) and type(fee.get("fee")) is int:
            fee_view = {"fee": fee["fee"], "minutes": fee.get("minutes"), "paid": bool(fee.get("paid")),
                        "payment_status": fee.get("payment_status")}
            if type(fee.get("minutes")) is int:
                fee_view["elapsed_text"] = location["elapsed_text"] = duration_text(fee["minutes"])
        else:
            errors["fee"] = "요금을 지금 조회할 수 없습니다."
        payload = {"vehicle_number": number, "plate_number": found.get("plate_number"), "location": location,
                   "fee": fee_view, "errors": errors, "partial": bool(errors),
                   "payment_guide": "가상 주차장 차량의 모의 결제는 화면의 차량번호 조회에서 한다. 챗봇은 이 차량을 결제하거나 출차시킬 수 없다.",
                   "fee_guide": "fee.fee가 지금 출차하면 내는 금액이다. 이 차량에는 estimate_fee를 쓰지 않는다."}
        raw_meta = {"is_mock": found.get("data_source") == "mock" or bool(found.get("is_mock")),
                    "updated_at": found.get("updated_at"), "stale": bool(found.get("stale"))}
        return self._finish("virtual_vehicle_info", payload, raw=raw_meta, lots=(found["lot_id"],))

    def fee_for_view(self, fee):
        """Recover the receipt for the UI without including it in model messages."""
        raw = self._snapshot.get(("fee", fee["car_id"]))
        if fee.get("paid") and isinstance(raw, dict) and raw.get("payment"):
            return {**fee, "payment": deepcopy(raw["payment"])}
        return fee

    def fee_policies(self, lot_ids: list[str] | None = None) -> dict:
        """Look up base fee and time, increments and caps for each requested lot; these are policies, not a vehicle's final fee."""
        lots = self._scope(lot_ids)
        if lots is None:
            return self._finish("fee_policies", {}, error="비교할 주차장을 확인해 주세요.")
        try:
            request_timeout(config.REQUEST_BUDGET_SEC)
            raw = self._read_once(("fee_policies",), self.adapter.get_rate_policies)
        except _READ_FAILURES:
            raw = {}
        policies = (raw or {}).get("policies") or {}
        rows = []
        for lot in lots:
            policy = policies.get(lot) or {}
            row = {"lot_id": lot, "name": config.catalog.name(lot),
                   "status": "ok" if "base_fee" in policy else "missing",
                   **_project(policy, _POLICY_KEYS)}
            if type(policy.get("free_minutes")) is int:
                row["free_minutes"] = policy["free_minutes"]      # 무료 회차 분(0 = 없음)
            if policy_text(policy):
                row["policy_text"] = policy_text(policy)
            rows.append(row)
        has_policy = any(r["status"] == "ok" for r in rows)
        return self._finish("fee_policies", {"rows": rows}, raw=raw, lots=lots,
                            error=None if has_policy else "해당 주차장의 요금 정책을 확인할 수 없습니다.")

    def search_knowledge(self, query: str = "", lot_ids: list[str] | None = None,
                         topics: list[str] | None = None) -> dict:
        """Read approved service rules and the per-lot facility guide. topics returns whole topics:
        restricted_spaces(일반차의 전기차·장애인 전용칸 이용), uncertain_space(확인 중 칸), vehicle_pending(입차 확인 중),
        forecast(미래 혼잡 예측 범위), payment(요금·결제 절차), capabilities(챗봇이 할 수 있는 일), facility(주차장 시설 안내).
        Absence means the information is not registered."""
        lots = self._scope(lot_ids)
        topics = list(topics or [])
        bad_topics = [t for t in topics if t not in TOPIC_KEYS]
        if (lots is None or not isinstance(query, str) or len(query) > 400 or bad_topics
                or (not query.strip() and not topics)):
            return self._finish("search_knowledge", {}, kind="internal_knowledge",
                                error="질문 또는 주제(topics)와 장소를 확인해 주세요.")
        matches, unregistered = search(query, lots, topics)
        data = {"matches": matches, "missing": not bool(matches), "unregistered": unregistered,
                "available_topics": list(TOPIC_KEYS)}
        # 일부 주차장에만 등록된 시설 주제(예: 운영 시간은 김해공항·신세계만) → 나머지는 '자료 없음'(봇 점검 11/10 16:40)
        from ..knowledge import TOPICS as _TOPICS
        labels = {key: label for key, label, _ in _TOPICS}
        by_topic = {}
        for row in matches:
            if row.get("lot_id") and row.get("id") in labels:
                by_topic.setdefault(row["id"], set()).add(row["lot_id"])
        missing = {labels[key]: [config.catalog.name(lot) for lot in lots if lot not in have]
                   for key, have in by_topic.items() if len(lots) > 1 and set(lots) - have}
        if missing:
            data["missing_by_lot"] = missing
        place = near_place(query)
        if place and not any(place in str(row) for row in matches):
            # '영화관으로 가는 가장 가까운 곳'처럼 물은 장소가 안내 자료에 없다 → 위치·방향을 말하면 안 된다
            data["not_in_guide"] = [place]
        return self._finish("search_knowledge", data, kind="internal_knowledge", lots=lots)

    # ------------------------------------------------------------ 계산·요약 도구 (읽기 전용)

    def compare_lots(self, lot_ids: list[str] | None = None, zone: str | None = None) -> dict:
        """Compare candidate lots in one call: vacancy, base fee and code-computed differences/rankings.
        Use the summary values for '몇 자리 더', '얼마 더 싼', '몇 곳' statements. zone can be general, ev or accessible."""
        status = self.parking_status(lot_ids, zone)
        if not status["ok"]:
            return self._finish("compare_lots", {}, lots=status["source"]["scope"], error=status.get("error"))
        rates = self.fee_policies([r["lot_id"] for r in status["data"]["rows"]])
        policy_rows = {r["lot_id"]: r for r in (rates["data"].get("rows") or [])} if rates["ok"] else {}
        rows = []
        for row in status["data"]["rows"]:
            policy = policy_rows.get(row["lot_id"]) or {}
            rows.append({**row, **{k: policy[k] for k in (*_POLICY_KEYS, "policy_text") if k in policy}})
        open_rows = [r for r in rows if r["availability"] == "available" and type(r.get("empty")) is int]
        fee_rows = [r for r in rows if type(r.get("base_fee")) is int]
        summary = {"lot_count": len(rows), "lots_with_space": sum(1 for r in open_rows if r["empty"] > 0),
                   "zone": zone, "fee_compared": bool(fee_rows)}
        if len(rows) >= 2:
            summary.update(_total_summary(rows))
        if open_rows:
            ranked = sorted(open_rows, key=lambda r: -r["empty"])
            summary["most_spaces"] = ranked[0]["name"]
            summary["rank_by_spaces"] = [r["name"] for r in ranked]
            if len(open_rows) >= 2:
                summary["empty_diff"] = ranked[0]["empty"] - ranked[-1]["empty"]
        if fee_rows:
            ranked = sorted(fee_rows, key=lambda r: r["base_fee"])
            summary["cheapest_base"] = ranked[0]["name"]
            summary["rank_by_base_fee"] = [r["name"] for r in ranked]
            if len(fee_rows) >= 2:
                summary["base_fee_diff"] = ranked[-1]["base_fee"] - ranked[0]["base_fee"]
        if len(rows) == 2:
            a, b = rows
            notes = []
            if a in open_rows and b in open_rows and a["empty"] != b["empty"]:
                more, less = (a, b) if a["empty"] > b["empty"] else (b, a)
                notes.append(f"{_subject(more['name'])} {less['name']}보다 빈자리 {more['empty'] - less['empty']}칸 많음")
            if type(a.get("base_fee")) is int and type(b.get("base_fee")) is int and a["base_fee"] != b["base_fee"]:
                cheap, dear = (a, b) if a["base_fee"] < b["base_fee"] else (b, a)
                notes.append(f"{cheap['name']} 기본요금이 {dear['base_fee'] - cheap['base_fee']:,}원 저렴함")
            summary["notes"] = notes
        if not rates["ok"]:
            summary["fee_error"] = rates.get("error")
        source = status["source"]
        return {"tool": "compare_lots", "ok": True, "data": {"rows": rows, "summary": summary, "alert": status["data"].get("alert")},
                "source": source}

    def estimate_fee(self, minutes: int, car_id: str | None = None, lot_id: str | None = None) -> dict:
        """Fee estimate. With car_id: the selected car's fee if it leaves `minutes` from now (billing function, checked against the server fee).
        With lot_id only: billing's fee for parking `minutes` in total at that lot. Always an estimate, never a quote or payment."""
        if type(minutes) is not int or not 1 <= minutes <= 4320 or (car_id is None) == (lot_id is None):
            return self._finish("estimate_fee", {}, error="예상 시간(분)과 차량 또는 주차장 하나를 확인해 주세요.")
        try:
            request_timeout(config.REQUEST_BUDGET_SEC)
            rates = self._read_once(("fee_policies",), self.adapter.get_rate_policies)
        except _READ_FAILURES:
            rates = {}
        policies = (rates or {}).get("policies") or {}
        if lot_id is not None:
            lots = self._scope([lot_id])
            if lots is None:
                return self._finish("estimate_fee", {}, error="주차장을 확인해 주세요.")
            policy = policies.get(lots[0])
            if not _valid_policy(policy):
                return self._finish("estimate_fee", {}, lots=lots, error="해당 주차장의 요금 정책을 확인할 수 없습니다.")
            try:
                amount = fee_details(minutes, policy)[0]   # billing 계산 함수 결과 그대로
            except FeeCalculatorUnavailable:
                return self._finish("estimate_fee", {"lot_id": lots[0], "name": config.catalog.name(lots[0]),
                                                     "policy_text": policy_text(policy)}, lots=lots,
                                    error="예상 금액은 계산할 수 없습니다. 요금 기준만 안내하세요.")
            data = {"lot_id": lots[0], "name": config.catalog.name(lots[0]), "total_minutes": minutes,
                    "total_time_text": duration_text(minutes), "estimated_fee": amount,
                    "policy_text": policy_text(policy), "estimate": True}
            return self._finish("estimate_fee", data, raw=rates, lots=lots)
        info = self.vehicle_info(car_id, True)
        fee = (info.get("data") or {}).get("fee")
        if not info["ok"] or not fee or type(fee.get("fee")) is not int:
            return self._finish("estimate_fee", {}, error="현재 요금을 확인할 수 없어 예상 요금을 계산하지 못했습니다.")
        if fee.get("paid"):
            return self._finish("estimate_fee", {}, error="이미 결제된 주차 기록이라 예상 요금을 계산하지 않습니다.")
        # 미래 시각 서버 조회(fee?now=)는 쓰지 않는다: 시연 서버가 now를 무시하고 견적만 쌓인다(봇 점검 11/10).
        raw = self._snapshot.get(("fee", car_id))
        policy = (raw or {}).get("rate_policy") if isinstance(raw, dict) else None
        policy = policy if _valid_policy(policy) else policies.get(fee.get("lot_id"))
        current = _elapsed_minutes(None, fee)
        future = (future_fee(fee["fee"], current, policy, minutes, fee.get("entry_at"), (raw or {}).get("updated_at"))
                  if current is not None and isinstance(raw, dict) else None)
        if future is None:
            return self._finish("estimate_fee", {}, error="서버 요금과 맞춰 볼 수 없어 예상 요금을 계산하지 않습니다.")
        amount, total = future
        data = {"car_id": car_id, "lot_id": fee.get("lot_id"), "name": config.catalog.name(fee.get("lot_id")),
                "current_fee": fee["fee"], "after_minutes": minutes, "after_time_text": duration_text(minutes),
                "total_minutes": total, "total_time_text": duration_text(total), "estimated_fee": amount,
                "increase": amount - fee["fee"], "policy_text": policy_text(policy), "estimate": True}
        return self._finish("estimate_fee", data, lots=(fee.get("lot_id"),) if fee.get("lot_id") else (),
                            raw={k: fee.get(k) for k in ("updated_at", "stale", "is_mock", "data_source")})

    def _alert_now(self):
        try:
            alert = self._alert()
            if not isinstance(alert, dict):
                raise DataUnavailable("경보 미확인")
            return alert
        except _READ_FAILURES:
            return {"unavailable": True}

    def alert_summary(self) -> dict:
        """Read the current mock-fire (LED) alert level and place, and whether it concerns the selected car.
        guidance and near_my_car are approved sentences; reuse their meaning."""
        from .. import templates as T
        alert = self._alert_now()
        if alert.get("unavailable"):
            return self._finish("alert_summary", {}, error="경보 상태를 확인하지 못했습니다. 경보가 없다고 단정할 수 없습니다.")
        level = alert.get("level") or 0
        data = {"level": level, "level_text": {0: "경보 없음", 1: "이상 신호 확인 중(경보 아님)"}.get(level, "모의 화재(LED) 경보"),
                "stale": bool(alert.get("stale")), "is_mock_fire": True}
        if level:
            data["place"] = T.alert_location(alert)
            data["lot_id"] = alert.get("lot_id")
            started = clock_text(alert.get("started_at")) if alert.get("started_at") else None
            if started:
                data["started_time_text"] = started
            data["manager_checking"] = bool(alert.get("manager_checking"))
            if level >= 2:
                data["guidance"] = T.safety_guidance(alert)
            if self._car:
                try:
                    request_timeout(config.REQUEST_BUDGET_SEC)
                    car = self._read_once(("location", self._car), lambda: self._vehicle(self._car))
                except _READ_FAILURES:
                    car = None
                if isinstance(car, dict) and car.get("car_id") == self._car and car.get("lot_id") and car.get("spot_no") is not None:
                    data["near_my_car"] = T.alert_near(alert, car)
        return self._finish("alert_summary", data, raw=alert)

    def violations(self, lot_ids: list[str] | None = None) -> dict:
        """Read reported general cars parked in EV/accessible-only spaces (server alerts only)."""
        lots = self._scope(lot_ids)
        if lots is None:
            return self._finish("violations", {}, error="주차장을 확인해 주세요.")
        alert = self._alert_now()
        if alert.get("unavailable") or not alert.get("api_result"):
            return self._finish("violations", {}, lots=lots, error="위반 차량 정보는 아직 확인할 수 없습니다.")
        zone_names = {"ev": "전기차", "accessible": "장애인"}
        rows = []
        for item in alert.get("violations") or []:
            if item.get("lot_id") not in lots:
                continue
            zone = item.get("space_type") or config.catalog.space_zone(item.get("lot_id"), item.get("space_id"))
            rows.append({"lot_id": item.get("lot_id"), "name": config.catalog.name(item.get("lot_id")),
                         "space_id": item.get("space_id"), "zone": zone_names.get(zone, "전용")})
        return self._finish("violations", {"rows": rows, "count": len(rows)}, raw=alert, lots=lots)

    def call_status(self) -> dict:
        """Read whether this chat session already registered an admin request. Reading never creates a call."""
        if self._admin_status is None:
            return self._finish("call_status", {}, error="관리자 문의 상태를 확인할 수 없습니다.")
        try:
            status = self._admin_status()
        except _READ_FAILURES:
            return self._finish("call_status", {}, error="관리자 문의 상태를 확인할 수 없습니다.")
        data = {"has_call": bool(status and status.get("id") is not None)}
        if data["has_call"]:
            data["call_no"] = status["id"]
            registered = clock_text(status.get("at")) if status.get("at") else None
            if registered:
                data["registered_time_text"] = registered
            data["note"] = "처리 상황은 현장 관리자에게 확인"
        return self._finish("call_status", data, kind="session")

    def propose_action(self, intent: str, params: dict | None = None) -> dict:
        """Propose pay_fee, call_admin or set_destination for a user's explicit request. The server will verify the proposal separately."""
        params = {} if params is None else params
        fields = {"pay_fee": {"car_id"}, "call_admin": set(), "set_destination": {"lot_id", "zone"}}
        valid = intent in fields and isinstance(params, dict) and not set(params) - fields[intent]
        if valid and "car_id" in params:
            valid = self._car is not None and params["car_id"] == self._car
        if valid and intent == "set_destination":
            valid = self._scope([params.get("lot_id")]) is not None
            if "zone" in params:
                valid = valid and params["zone"] in [z["key"] for z in config.catalog.zones()]
        if not valid:
            return self._finish("propose_action", {}, kind="proposal", error="제안한 작업의 대상과 조건을 확인해 주세요.", executed=False)
        return self._finish("propose_action", {}, kind="proposal", executed=False,
                            proposal={"intent": intent, "params": dict(params)})

    def langchain_tools(self):
        from langchain_core.tools import StructuredTool

        def bind(fn):
            # The async LangChain graph runs in a dedicated request worker. Inline
            # reads inherit its deadline and context, and do not outlive the turn.
            @wraps(fn)
            async def invoke(*args, **kwargs):
                return fn(*args, **kwargs)
            return StructuredTool.from_function(func=fn, coroutine=invoke, name=fn.__name__)
        # 가상 차량 도구는 이번 턴에 가상 차량 번호가 있을 때만 보인다(다른 턴의 도구 목록·지시문은 그대로)
        names = (self.TOOL_NAMES + (("service_notice",) if self._notice else ())
                 + (("virtual_vehicle_info",) if self._virtual else ()))
        return [bind(getattr(self, name)) for name in names]
