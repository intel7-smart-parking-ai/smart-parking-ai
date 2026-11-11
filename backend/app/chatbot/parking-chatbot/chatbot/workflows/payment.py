"""Prepare server quotes and execute one explicitly confirmed mock payment."""
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
import uuid

from .. import config, insights as I, templates as T
from ..adapters import ApiAdapter, CachedAdapter, DataUnavailable, PaymentNotSent
from ..contracts import timestamp
from ..retention import warn_if_protected
from ..state import Flow
from .views import fee_card, journey, stale_note

QUOTE_RETRY_STATUSES = {"quote_expired", "quote_mismatch", "quote_not_found"}


@dataclass
class PaymentWorkflow:
    """Per-request ports; the caller owns the payment slot and shared visit set."""

    adapter: CachedAdapter
    current_alert: Callable
    refresh_alert: Callable
    record_evidence: Callable
    uncertain_visits: set
    now: Callable

    def fee(self, s, p):
        d = self.adapter.get_fee(s.car_id)
        self.record_evidence(d)
        if not d:
            return T.CAR_NOT_FOUND.format(no=s.car_id), None, ["change_vehicle", "call_admin"], None
        if d.get("status") in {"unavailable", "error", "conflict"}:
            return T.fee_unavailable(d), None, ["fee", "call_admin"], None
        card = fee_card(d)
        if d.get("payment_blocked") is True:
            text, _, chips, updated = self.blocked(s, d)
            return T.fee(d) + "\n" + text, card, chips, updated
        return T.fee(d) + stale_note(d), card, "after_get_fee", d["updated_at"]

    def blocked(self, s, evidence):
        """Use the server's payment prohibition without inferring an alarm location."""
        s.clear()
        self.record_evidence(evidence)
        # Reuse established mock-fire guidance; do not invent a level, time or spot.
        return (T.safety_card(evidence)["title"] + "\n" + T.safety_guidance(evidence),
                None, "alert", evidence.get("updated_at"))

    def fee_rule(self, s, p):
        """주차장별 요금 정책. api는 서버(/rate-policies), mock은 lots.json 설정. 코드에 요금표 없음."""
        result = self.adapter.get_rate_policies()
        policies = result.get("policies") if isinstance(result, dict) else None
        self.record_evidence(result if isinstance(result, dict) else None)
        if not policies:
            return "요금 정책을 확인할 수 없어요. 차량 요금 조회를 선택해 주세요.", None, ["fee", "call_admin"], None
        if p.get("fee_rank"):
            only = p.get("only_lots")
            ranked = {k: v for k, v in policies.items() if not only or k in only} or policies
            return I.fee_rank(ranked, high=p["fee_rank"] == "high"), None, "after_fee_rule", None
        return T.fee_rule(policies, p.get("lot_id"), table=bool(p.get("fee_table"))), None, "after_fee_rule", None

    def prepare(self, s, p):
        if self.current_alert().get("level", 0) >= 2:
            return "Lv2 경보 중에는 모의 결제를 중지해요. 대피 안내를 먼저 확인해 주세요.", None, "alert", None
        d = self.adapter.get_fee(s.car_id)
        self.record_evidence(d)
        if not d:
            return T.CAR_NOT_FOUND.format(no=s.car_id), None, "default", None
        if d.get("payment_blocked") is True:
            return self.blocked(s, d)
        if d.get("status") in {"unavailable", "error", "conflict"}:
            return T.fee_unavailable(d), None, ["fee", "call_admin"], None
        if d.get("stale"):
            return "최신 요금을 확인하지 못해 결제를 진행할 수 없어요. 잠시 후 다시 조회해 주세요.", None, "default", None
        visit = (s.car_id, d["entry_at"])
        if d["paid"]:
            self.uncertain_visits.discard(visit)
            return T.fee(d), fee_card(d) if d.get("payment") else None, "after_pay_fee", d["updated_at"]
        if visit in self.uncertain_visits:
            return "이전 결제의 처리 결과가 아직 불확실해요. 중복 결제를 막기 위해 관리자 확인이 필요해요.", None, ["fee", "call_admin"], None
        if d["fee"] == 0:
            return T.fee(d), None, "after_get_fee", d["updated_at"]
        if isinstance(self.adapter.inner, ApiAdapter) and not d.get("quote_id"):
            # 견적 없이는 결제 요청을 보내지 않는다(서버 필수 필드).
            return T.pay({"status": "quote_missing"}, s.car_id), None, ["fee", "call_admin"], d["updated_at"]
        s.begin(Flow.CONFIRM_ACTION, "pay_fee", {"amount": d["fee"], "entry_at": d["entry_at"], "idempotency_key": str(uuid.uuid4()),
                "session_id": d.get("session_id"), "quote_id": d.get("quote_id"), "quote_expires_at": d.get("quote_expires_at")}, s.car_id)
        card = {"type": "fee", "data": {**d, "journey": journey(d)}}
        return f"{s.car_id}번 차 요금은 {T.won(d['fee'])}이에요. 이 금액으로 모의 결제할까요?", card, "confirm", d["updated_at"]

    def quote_expired(self, pending) -> bool:
        expires = pending.params.get("quote_expires_at")
        if not expires:
            return False
        try:
            return self.now() >= timestamp(expires) - timedelta(seconds=5)
        except (TypeError, ValueError):
            return True

    def reconfirm(self, s):
        """견적이 바뀌면 자동 결제하지 않고 새 금액으로 다시 확인받는다(새 견적·새 멱등키)."""
        result = self.prepare(s, {})
        pending = s.pending
        if pending and pending.intent == "pay_fee":
            text = T.QUOTE_REFRESHED.format(amount=T.won(pending.params["amount"]))
            return (text, *result[1:])
        return result

    def complete(self, s, pending):
        if self.current_alert().get("level", 0) >= 2:
            return "경보로 미완료 결제를 취소했어요. 대피 안내를 확인해 주세요.", None, "alert", None
        if self.quote_expired(pending):
            return self.reconfirm(s)
        d = self.adapter.get_fee(pending.car_id)
        self.record_evidence(d)
        if not d:
            return T.CAR_NOT_FOUND.format(no=pending.car_id), None, "default", None
        if d.get("payment_blocked") is True:
            return self.blocked(s, d)
        if d.get("status") in {"unavailable", "error", "conflict"}:
            return T.fee_unavailable(d), None, ["fee", "call_admin"], None
        if d.get("stale"):
            return "최신 요금을 확인하지 못했어요. 결제는 실행하지 않았습니다.", None, "default", None
        if isinstance(self.adapter.inner, ApiAdapter) and d.get("session_id") != pending.params.get("session_id"):
            return T.pay({"status": "unavailable"}, pending.car_id), None, ["fee", "call_admin"], None
        if d["paid"]:
            return T.fee(d), fee_card(d) if d.get("payment") else None, "after_pay_fee", d["updated_at"]
        if not isinstance(self.adapter.inner, ApiAdapter) and (d["fee"], d["entry_at"]) != (pending.params["amount"], pending.params["entry_at"]):
            result = self.prepare(s, {})
            return ("확인 중 요금 또는 입차 기록이 바뀌었어요. " + result[0], *result[1:])
        visit = (pending.car_id, d["entry_at"])
        if visit in self.uncertain_visits:
            return T.PAYMENT_CAPACITY, None, ["fee", "call_admin"], None
        if len(self.uncertain_visits) >= config.UNCERTAIN_PAYMENT_LIMIT:
            warn_if_protected("uncertain_payments", len(self.uncertain_visits), config.UNCERTAIN_PAYMENT_LIMIT, full=True)
            return T.PAYMENT_CAPACITY, None, ["fee", "call_admin"], None
        operation_key = pending.params["idempotency_key"] if isinstance(self.adapter.inner, ApiAdapter) else uuid.uuid5(uuid.NAMESPACE_URL, f"parking:{pending.car_id}:{d['entry_at']}:{d['fee']}").hex
        # 요금 조회 중 새 경보가 생긴 경우에도 실행 직전에 다시 확인한다.
        latest_alert = self.refresh_alert()
        if latest_alert.get("level", 0) >= 2:
            return "경보로 미완료 결제를 취소했어요. 대피 안내를 확인해 주세요.", None, "alert", None
        try:
            visit_args = {"session_id": pending.params.get("session_id")} if isinstance(self.adapter.inner, ApiAdapter) else {}
            r = self.adapter.pay(pending.car_id, idempotency_key=operation_key, **visit_args, quote_id=pending.params.get("quote_id"),
                         quote_amount=pending.params["amount"], entry_at=pending.params["entry_at"])
        except PaymentNotSent:
            return T.pay({"status": "unavailable"}, pending.car_id), None, ["fee", "call_admin"], None
        except DataUnavailable:
            self.uncertain_visits.add(visit)
            return "결제 요청의 처리 결과를 확인하지 못했어요. 다시 결제하지 말고 요금 조회 또는 관리자 확인을 해 주세요.", None, ["fee", "call_admin"], None
        if r.get("error_code") == "EVACUATION_ACTIVE":
            return self.blocked(s, r)
        if r["status"] in QUOTE_RETRY_STATUSES:
            return self.reconfirm(s)
        if isinstance(self.adapter.inner, ApiAdapter):
            if r["status"] in {"conflict", "error", "unavailable", "payment_conflict", "invalid_state", "quote_missing"}:
                return T.pay(r, pending.car_id), None, ["fee", "call_admin"], None
            card = {"type": "payment", "data": {**r, "car_id": pending.car_id}}
            text = T.pay(r, pending.car_id)
            if r.get("amount") is not None and r["amount"] != pending.params["amount"]:
                text += " 금액이 갱신됐어요."
            return text, card, "after_pay_fee", None
        if r["status"] == "quote_changed":
            result = self.prepare(s, {})
            return ("서버에서 요금 변경을 확인했어요. " + result[0], *result[1:])
        if r["status"] == "not_found":
            return T.pay(r, pending.car_id), None, "default", None
        try:
            fee_d = self.adapter.get_fee(pending.car_id)
        except DataUnavailable:
            fee_d = None
        valid_receipt = (r["status"] in ("paid", "already_paid") and fee_d and not fee_d.get("stale")
                         and fee_d["paid"] and fee_d["entry_at"] == pending.params["entry_at"])
        if not valid_receipt:
            self.uncertain_visits.add(visit)
            if fee_d is None or fee_d.get("stale"):
                card = {"type": "payment", "data": {**r, "car_id": pending.car_id,
                                                     "verification_pending": True, "journey": None}}
                return T.pay(r, pending.car_id) + " 현재 요금 상태 재조회는 미확인이에요. 다시 결제하지 말고 관리자에게 확인해 주세요.", card, ["fee", "call_admin"], None
            return "결제 결과와 현재 요금 상태가 일치하는지 확인하지 못했어요. 추가 결제 없이 관리자 확인이 필요해요.", None, ["fee", "call_admin"], None
        self.uncertain_visits.discard(visit)
        card = {"type": "payment", "data": {**r, "car_id": pending.car_id, "journey": journey(fee_d)}}
        return T.pay(r, pending.car_id), card, "after_pay_fee", None
