"""Shared card formatting for verified vehicle and billing evidence."""
from .. import templates as T

def stale_note(d: dict | None) -> str:
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


def fee_card(d):
    receipt = d.get("payment")
    if d.get("paid") and receipt and not d.get("stale") and not d.get("payment_blocked"):
        return {"type": "payment", "data": {**receipt, "car_id": d["car_id"]}}
    return {"type": "fee", "data": {**d, "journey": journey(d)}}
