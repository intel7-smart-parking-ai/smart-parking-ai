"""Present bounded fallback answers using only this turn's tool observations."""
from dataclasses import dataclass, field

from .. import config, templates as T
from ..contracts import LOCATION_PENDING
from ..workflows.views import fee_card
from .query import Query


@dataclass
class Reply:
    text: str
    card: dict | None = None
    chip_set: str | list = "default"
    updated_at: str | None = None
    meta: dict = field(default_factory=dict)


def _vehicle(query, tools):
    observation = tools.vehicle_info(query.car_id, include_fee=query.wants_fee or query.wants_payment)
    facts = observation["data"]
    errors = facts.get("errors", {})
    car, fee = facts.get("location"), facts.get("fee")
    lines, card = [], None
    if query.wants_location:
        if car:
            if car.get("status") == LOCATION_PENDING:
                lines.append(T.car_pending(car))
            else:
                lines.append(T.car_location(car))
                card = {"type": "car_location", "data": car}
        else:
            lines.append("차량 위치: " + errors.get("location", observation.get("error", "확인할 수 없어요.")))
    if query.wants_fee or query.wants_payment:
        if fee:
            amount = T.won(fee["fee"])
            lines.append(f"결제한 금액은 {amount}이에요." if fee.get("paid") else f"현재 주차 요금은 {amount}이에요.")
            if not card:
                card = fee_card(tools.fee_for_view(fee))
            if fee.get("payment_blocked"):
                lines.append("서버가 모의 결제를 차단했어요. 대피 안내를 먼저 확인해 주세요.")
        else:
            lines.append("차량 요금: " + errors.get("fee", observation.get("error", "확인할 수 없어요.")))
    if observation["source"].get("stale"):
        lines.append("마지막으로 받은 정보이며 현재 상태를 다시 확인해야 해요.")
    chips = ["fee", "call_admin"] if errors else ["find_car", "fee", "pay", "call_admin"]
    blocked = bool(fee and fee.get("payment_blocked"))
    return Reply("\n".join(lines), card, "alert" if blocked else chips,
                 observation["source"].get("updated_at"),
                 {"intent": "find_my_car" if query.wants_location else "get_fee",
                  "car_id": query.car_id, "partial": bool(facts.get("partial")),
                  "payment_blocked": blocked, "fee_verified": bool(fee and not fee.get("stale")),
                  "source_evidence": observation["source"]})


def _parking_card(rows, zone):
    if not rows:
        return None
    lots = {
        row["lot_id"]: {"name": row["name"], "display_empty": row["empty"],
                        "display_uncertain": row["uncertain"], "status_reason": row["reason"],
                        "empty": row["empty"], "total": row["total"]}
        for row in rows
    }
    return {"type": "empty_spots", "data": {
        "lots": lots, "zone": zone, "focus": rows[0]["lot_id"] if len(rows) == 1 else None,
        "zone_labels": {z["key"]: z["name"] for z in config.catalog.zones()},
        "blocked_lots": [r["lot_id"] for r in rows if r["availability"] in ("restricted", "unknown")],
    }}


def _parking_status_line(row, zone):
    zone_label = config.catalog.zone_name(zone) if zone else ""
    empty = row.get("empty")
    vacancy = f"{zone_label} 빈자리 {empty}칸".strip() if empty is not None else "빈자리 미확인"
    # 카메라 확인 중인 칸이 있으면 함께 알린다(빈자리 0이어도 만차로 단정할 수 없다. 봇 점검 11/11)
    pending = row.get("uncertain")
    camera = f"카메라 확인 중 {pending}칸" if type(pending) is int and pending > 0 else None
    # 확인 중인 칸이 있으면 '만차'라고 쓰지 않는다('빈자리 0칸, 만차, 카메라 확인 중 12칸'은 앞뒤가 안 맞는다)
    why = None if camera and row.get("reason") in ("만차", "확인된 빈자리 없음") else row.get("reason")
    if camera and empty == 0:
        vacancy = f"{zone_label} 확인된 빈자리 없음".strip()
    return ", ".join(filter(None, (vacancy, why, camera)))


def render_observed_parking(observation):
    rows = observation["data"]["rows"]
    zone = rows[0]["zone"]
    return Reply(
        "\n".join(f"{row['name']} · {_parking_status_line(row, zone)}" for row in rows),
        _parking_card(rows, zone), ["lot_" + row["lot_id"] for row in rows][:4],
        observation["source"].get("updated_at"),
        {"intent": "get_empty_spots", "candidates": [row["lot_id"] for row in rows],
         "zone": zone, "source_evidence": observation["source"]},
    )


def render_knowledge(observation):
    statements = [((config.catalog.name(r["lot_id"]) + " · ") if r.get("lot_id") else "") + r["text"]
                  for r in observation["data"].get("matches") or []]
    return " ".join(statements) if statements else observation.get(
        "error", "해당 내용은 내부 이용 안내에 등록되어 있지 않아요.")


def _parking(query, tools):
    scope = list(query.candidates) or None
    status = tools.parking_status(scope, query.zone)
    rows = status["data"].get("rows", [])
    rates = tools.fee_policies(scope) if query.wants_rates else None
    policies = {p["lot_id"]: p for p in rates["data"].get("rows", [])} if rates else {}
    lines = []
    if query.future:
        lines.append("혼잡 예측은 아직 제공하지 않아요. 지금 확인한 상태예요.")
    if not status["ok"]:
        lines.append(status["error"])
    names = list(dict.fromkeys([r["lot_id"] for r in rows] + list(policies)))
    for lot_id in names:
        row = next((r for r in rows if r["lot_id"] == lot_id), {})
        details = []
        if row:
            details.append(_parking_status_line(row, query.zone))
        p = policies.get(lot_id, {})
        if p.get("status") == "ok":
            duration = f"처음 {p['base_minutes']}분 " if p.get("base_minutes") is not None else ""
            details.append(f"기본요금 {duration}{T.won(p['base_fee'])}")
        elif rates:
            details.append("기본요금 미확인")
        label = ("지금 기준으로 " if query.future and len(names) == 1 else "") + config.catalog.name(lot_id)
        lines.append(label + " · " + ", ".join(details))
    if rates and not rates["ok"] and not policies:
        lines.append(rates["error"])
    if query.prefer_low_rate:
        candidates = [(policies[r["lot_id"]]["base_fee"], r["name"]) for r in rows
                      if r["availability"] == "available" and policies.get(r["lot_id"], {}).get("status") == "ok"]
        if candidates:
            cheapest = min(fee for fee, _ in candidates)
            chosen = "·".join(name for fee, name in candidates if fee == cheapest)
            lines.append(f"이용 가능 여부와 요금이 확인된 후보 중 기본요금이 가장 낮은 곳은 {chosen}이에요.")
        else:
            lines.append("현재 이용 가능 여부와 요금을 함께 확인한 후보가 없어 추천하기 어려워요.")
    elif query.compare and rows:
        lines.append("빈자리와 기본요금 중 더 중요한 기준으로 선택해 주세요.")
    if query.future:
        available = [r["name"] for r in rows if r["availability"] == "available"]
        if len(available) > 1:
            lines.append(f"먼저 본 곳이 만차이면 {', '.join(available[1:])}의 빈자리를 다시 확인해 보세요.")
        else:
            lines.append("출발 전에 다시 조회해 주세요. 만차이면 당시 이용 가능한 다른 주차장을 비교해 드릴게요.")
    return Reply("\n".join(lines), _parking_card(rows, query.zone),
                 ["lot_" + r["lot_id"] for r in rows][:4] or "default",
                 status["source"].get("updated_at"),
                 {"intent": "get_empty_spots", "candidates": list(query.candidates),
                  "zone": query.zone, "source_evidence": status["source"]})


def render_query(query: Query, tools) -> Reply:
    if query.clarification or query.needs_vehicle:
        return Reply(query.clarification or T.ASK_VEHICLE)
    if query.wants_location or query.wants_fee or query.wants_payment:
        return _vehicle(query, tools)
    reply = _parking(query, tools) if query.wants_parking else Reply("", meta={"intent": "help"})
    if query.policy_query:
        record = tools.search_knowledge(query.policy_query, list(query.candidates) or None)
        reply.text = "\n".join(filter(None, [reply.text, render_knowledge(record)]))
    return reply
