from .source import count, read
from .query import Clarification
from urllib.parse import quote


def fetch(_day, query=None):
    if query and query.individual_fee:
        return vehicle_fee(query)
    data = read("/api/v1/rate-policies", {"items": [
        {"lot_id": "A", "base_minutes": 30, "base_fee": 1000,
         "unit_minutes": 10, "extra_fee": 500, "daily_cap": 10000}
    ]})
    if not isinstance(data["items"], list) or not data["items"]:
        raise ValueError("Missing fee policies")
    values = {}
    requested = query.lots if query else []
    if query and not requested and query.resolved_vehicle:
        requested = [query.resolved_vehicle["lot_id"]]
    labels = {"base_minutes": "기본 시간(분)", "base_fee": "기본 요금(원)",
              "unit_minutes": "추가 단위(분)", "extra_fee": "추가 요금(원)",
              "daily_cap": "일 상한(원)"}
    seen = set()
    for row in data["items"]:
        lot = row["lot_id"]
        if lot not in ("A", "B", "C", "D") or lot in seen:
            raise ValueError("Invalid fee policy lot")
        seen.add(lot)
        if requested and lot not in requested:
            continue
        for field, label in labels.items():
            values[f"{lot} {label}"] = count(row[field])
        if row["unit_minutes"] == 0:
            raise ValueError("Invalid fee unit")
    if not values:
        raise Clarification("지정한 주차장의 요금 정책을 확인하지 못했어요.")
    return values


def vehicle_fee(query):
    car = query.resolved_vehicle
    if not car or not car.get("session_id"):
        raise Clarification("차량 한 대의 입차 세션이 확인돼야 요금을 조회할 수 있어요. 차량 번호와 주차장을 확인해 주세요.")
    session_id = car["session_id"]
    if not isinstance(session_id, str) or len(session_id) > 128:
        raise ValueError("Invalid session identity")
    data = read("/api/v1/sessions/" + quote(session_id, safe="") + "/fee", {
        "session_id": session_id, "lot_id": car["lot_id"], "space_id": car["space_id"],
        "amount_krw": 1500, "payment_status": "unpaid", "payment": None, "payment_blocked": False})
    if (data["session_id"] != session_id or data["lot_id"] != car["lot_id"]
            or data["space_id"] != car["space_id"]):
        raise ValueError("Fee response belongs to another vehicle")
    statuses = {"unpaid": "미결제", "completed": "모의 결제 완료", "canceled": "모의 결제 취소"}
    if data["payment_status"] not in statuses or type(data["payment_blocked"]) is not bool:
        raise ValueError("Invalid payment state")
    if data["payment_status"] == "completed" and not data.get("payment"):
        raise ValueError("Missing payment receipt")
    return {"차량 번호": query.vehicle_id, "현재 요금(원)": count(data["amount_krw"]),
            "결제 상태": statuses[data["payment_status"]],
            "결제 차단": "경보로 결제 중단" if data["payment_blocked"] else "서버 차단 없음"}
