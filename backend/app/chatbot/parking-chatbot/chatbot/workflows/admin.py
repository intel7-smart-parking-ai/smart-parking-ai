"""Merge and retain administrator calls within a session and incident."""
from copy import deepcopy
import time

from .. import config, incidents, templates as T
from ..adapters import DataUnavailable, iso
from ..contracts import LOCATION_PENDING
from ..retention import prune_finished, warn_if_protected
from .views import stale_note


def summary(s, car_id, *, adapter, find_vehicle, alert, chips) -> str:
    """관리자에게 넘길 상황 요약 자동 작성 (안전 보고서 자동완성과 같은 발상)."""
    parts = []
    candidates = [s.selected_candidate] if s.selected_candidate else list(s.candidate_options.values())
    if candidates:
        parts.append("번호 미인식 후보 확인 요청("+", ".join(T.place(c['lot_id'], c['spot_no'], c.get('space_id')) for c in candidates)+")")
    car_id = car_id or s.car_id
    if car_id:
        try:
            car = find_vehicle(car_id)
        except DataUnavailable:
            car = None
            parts.append("차량 위치 미확인")
        try:
            fee = adapter.get_fee(car_id)
        except DataUnavailable:
            fee = None
            parts.append("결제 상태 미확인")
        if car and car.get("status") == LOCATION_PENDING:
            parts.append(f"{car_id}번 차 · 입차 확인 중(칸 배정 전)")
        elif car:
            parts.append(f"{car_id}번 차 · {T.place(car['lot_id'], car['spot_no'], car.get('space_id'))} · 입차 {T.hhmm(car['entry_at'])}" + stale_note(car) + f" · 위치 기준 {car['updated_at']}")
        else:
            parts.append(f"{car_id}번 차 · 위치 미확인")
        if fee and fee.get("status") not in {"unavailable", "error", "conflict"}:
            parts.append(("결제 완료" if fee["paid"] else f"미결제 {T.won(fee['fee'])}") + stale_note(fee) + f" · 요금 기준 {fee['updated_at']}")
    else:
        parts.append("차량 번호 미확인")
    skip = {chips["confirm_yes"]["label"], chips["confirm_no"]["label"], "네", "예", "응", "아니요"}
    user_msgs = [t for role, t in s.history if role == "user" and t not in skip][-3:]
    if user_msgs:
        parts.append("최근 문의: " + " / ".join(user_msgs))
    a = alert
    inc_id = a.get("incident_id")
    location = T.alert_location(a) if a.get("api_result") else T.place(a.get('lot_id'), a.get('spot_no'))
    parts.append((f"경보 Lv{a['level']} {location} (상황 보고서 #{inc_id})" + stale_note(a)) if inc_id else ("경보 상태 미확인" if a.get("unavailable") or a.get("stale") else "경보 없음"))
    return " · ".join(parts)


def public_call(call):
    return {k: deepcopy(v) for k, v in call.items() if not k.startswith("_")}

class AdminCalls:
    def __init__(self, lock):
        self.lock = lock
        self.calls = []
        self.sequence = 0
        self.pruned = 0

    def reset(self):
        with self.lock:
            self.calls.clear()
            self.sequence = 0
            self.pruned = 0

    def register(self, session, params, alert, summarize, now):
        a = alert
        inc_id, car_id = a.get("incident_id"), params.get("car_id") or session.car_id
        with self.lock:
            open_status = ("접수",)
            existing = next((c for c in reversed(self.calls) if c["_session"] == params["_sid"]
                             and c["car_id"] == car_id and c.get("incident_id") == inc_id
                             and c["status"] in open_status
                             and time.monotonic() - c.get("_last_at", 0) <= config.CALL_MERGE_SEC), None)
            if existing and existing.get("alert_level", 0) >= a.get("level", 0):
                existing["_last_at"] = time.monotonic()
                return f"관리자 화면에 이미 접수된 문의가 있어요. (문의 #{existing['id']})", {"type": "admin_call", "data": public_call(existing)}, "after_call_admin", None
        summary = summarize(session, car_id)
        with self.lock:
            if existing:
                call = existing
                call.update(summary=summary, alert_level=a.get("level", 0), revision=call["revision"] + 1, updated_at=iso(now()), _last_at=time.monotonic())
            else:
                self.sequence += 1
                call = {"id": self.sequence, "at": iso(now()), "updated_at": iso(now()),
                        "car_id": car_id, "incident_id": inc_id, "alert_level": a.get("level", 0),
                        "summary": summary, "status": "접수", "revision": 1, "_session": params["_sid"], "_last_at": time.monotonic()}
                self.calls.append(call)
                self.pruned += prune_finished(
                    self.calls, config.ADMIN_CALL_LIMIT, lambda item: item["status"] in {"완료", "해결"}
                )
                warn_if_protected("admin_calls", len(self.calls), config.ADMIN_CALL_LIMIT)
        incidents.link_admin_call(call["id"], inc_id)
        return "관리자에게 확인을 요청했어요. 차량 정보와 최근 문의를 요약해 관리자 화면에 함께 등록했어요.", {"type": "admin_call", "data": public_call(call)}, "after_call_admin", None

    def snapshot(self, sid):
        """읽기 전용: 이 세션의 마지막 관리자 문의 번호·접수 시각. 새 접수를 만들지 않는다."""
        with self.lock:
            call = next((c for c in reversed(self.calls) if c["_session"] == sid), None)
            return {"id": call["id"], "at": call.get("at")} if call else {"id": None}

    def status(self, sid):
        with self.lock:
            call = next((c for c in reversed(self.calls) if c["_session"] == sid), None)
            if not call:
                return "접수된 관리자 문의가 없어요. 필요하면 관리자 호출을 눌러 주세요.", None, ["call_admin"], None
            return (f"관리자 화면에 문의가 접수돼 있어요. (문의 #{call['id']}) 처리 상황은 현장 관리자에게 확인해 주세요.",
                    {"type": "admin_call", "data": public_call(call)}, "after_call_admin", call["updated_at"])
