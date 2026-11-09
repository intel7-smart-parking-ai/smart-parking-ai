"""Integrate scoped, read-only agents with the existing chat session and safety gates."""
import re
import time

from .workflow import ReportFailure, report
from .query import PAYMENT_STATUS_PATTERN, lot_choice, parse as parse_query

ACTIONS = re.compile(r"불러|호출|대피|피난|불\s*났|연기|살려|결제\s*해|취소\s*해|예약\s*해")


def respond(engine, session, text, **kwargs):
    # Model time must not consume the final safety recheck's API time budget.
    token = engine.request_deadline.set(time.monotonic() + engine.config.BACKEND_TIMEOUT_SEC)
    try:
        engine._alert_snapshot.set(None)
        if engine.current_alert().get("level", 0) >= 2:
            return engine._respond(session, "경보가 발생했어요. 현장 안전 안내를 먼저 따라 주세요.", chip_set="alert")
        return engine._respond(session, text, **kwargs)
    finally:
        engine.request_deadline.reset(token)


def requested(text):
    if ACTIONS.search(text):
        return False
    if re.search(r"보고서|정리|요약|종합|매출|현황|전체", text) and re.search(r"매출|수익|경보|빈자리|빈 자리|요금", text):
        return True
    if re.search(r"(?<!\d)\d{4}(?!\d)", text) and re.search(r"위치|어디|찾|요금|주차비|" + PAYMENT_STATUS_PATTERN, text):
        return True
    if re.search(r"내\s*차|제\s*차|그\s*차|\d+\s*번\s*차", text) and re.search(r"요금|주차비|" + PAYMENT_STATUS_PATTERN, text):
        return True
    scope = parse_query(text)
    if scope.charging_status:
        return True
    return bool(scope.topics or re.search(r"요금\s*(?:정책|표)|기본\s*요금", text)
                or (re.search(r"빈자리|빈 자리|남은 자리", text) and
                    (scope.section or scope.space_type)))


def handle_input(engine, session, text, chip_id, event):
    if event:
        return None
    if chip_id:
        pending = session.agent_followup
        fresh = pending and time.monotonic() - pending.get("at", 0) <= engine.config.QUERY_CONTEXT_TTL_SEC
        if fresh and chip_id.startswith("lot_") and chip_id[4:] in engine.config.catalog.ids():
            return handle(engine, session, engine.config.catalog.name(chip_id[4:]))
        session.agent_followup = {}
        return None
    return handle(engine, session, text) if text else None


def handle(engine, session, text):
    if re.search(r"(?:자리|주차칸).{0,12}보장", text):
        from . import spaces
        session.clear()
        session.agent_followup = {}
        session.history.append(("user", text))
        reply = "주차 자리를 보장할 수 없어요."
        if engine.current_alert(refresh=True).get("level", 0) >= 2:
            return respond(engine, session, reply)
        if re.search(r"국제선", text) and re.search(r"충전|전기차", text):
            query = parse_query("김해공항 P2 전기차 빈자리")
            try:
                reply += "\n" + spaces.format_values(spaces.fetch(None, query), query)
            except Exception:
                reply += " 현재 빈자리를 확인하지 못했어요."
        else:
            reply += " 주차장을 알려주시면 현재 빈자리를 확인할 수 있어요."
        return respond(engine, session, reply)
    pending = session.agent_followup
    session.agent_followup = {}
    question = text
    selected_lot = None
    fresh = pending and time.monotonic() - pending.get("at", 0) <= engine.config.QUERY_CONTEXT_TTL_SEC
    if fresh and not ACTIONS.search(text):
        selected_lot = lot_choice(text)
        if selected_lot:
            question = pending["question"]
        elif re.fullmatch(r"\s*(?:[1-9]\d|\d{4})\s*(?:번|이요|요)?[?.!]*", text):
            number = re.search(r"\d+", text)[0]
            original = pending['question']
            for candidate in pending.get('vehicle_candidates', []):
                original = re.sub(r"(?<!\d)" + re.escape(candidate) + r"(?!\d)", "", original)
            question = f"{number}번 차량 {original}"
            selected_lot = pending.get("lot")
    if question == text and not ACTIONS.search(text):
        if re.fullmatch(r"\s*\d{4}\s*[?.!]*", text):
            question = f"{text.strip(' ?. !')} 차량 위치"
        multilingual_fee = re.fullmatch(
            r"\s*(?:how\s+much\s+(?:is\s+it\s+)?for\s+(?:car|vehicle)\s+([1-9]\d|\d{4})"
            r"|([1-9]\d|\d{4})\s*(?:号车|号车辆|车辆)?\s*(?:的)?\s*停车费(?:是)?多少(?:钱)?"
            r"|([1-9]\d|\d{4})\s*(?:番)?(?:の)?(?:車)?の?駐車料金はいくら(?:ですか)?)\s*[?.!？。！]*",
            text, re.I,
        )
        indirect_fee = re.search(r"(?<!\d)([1-9]\d|\d{4})\s*(?:번\s*)?차(?:량)?.*오래\s*(?:세워|주차).*많이\s*나왔", text)
        match = multilingual_fee or indirect_fee
        if match:
            number = next(value for value in match.groups() if value)
            vehicle = f"{number}번 차" if len(number) == 2 else f"{number} 차량"
            question = f"{vehicle} 현재 요금과 결제 상태"
        elif re.search(r"요금|주차비", text) and re.search(r"위치|어디", text):
            scope = parse_query(text)
            if scope.vehicle_id and not scope.vehicle_problem:
                question = f"{text} · 결제 상태"
    if question == text and not requested(text):
        return None
    if engine.current_alert().get("level", 0) >= 2:
        return engine._respond(session, "경보 중에는 현장 안전 안내를 먼저 따라 주세요.", chip_set="alert")
    question = engine.DC.rewrite(session, question)
    query = parse_query(question, selected_lot=selected_lot, confirmed_car=session.car_id)
    if query.vehicle_problem and query.vehicle_candidates:
        session.clear()
        session.history.append(("user", text))
        session.agent_followup = {"question": question, "at": time.monotonic(),
                                  "vehicle_candidates": query.vehicle_candidates,
                                  "lot": query.lots[0] if len(query.lots) == 1 else None}
        return respond(engine, session, "차량번호가 여러 개예요. 조회할 번호를 하나만 알려주세요.",
                       meta={"intent": "vehicle_clarification", "needs_clarification": True})
    if not query.lots and query.topics:
        focus = engine.DC.focus(session)
        if focus:
            query.lots = [focus]
    memory = session.agent_vehicle
    if (not query.vehicle_id and not query.vehicle_problem and re.search(r"그\s*차", text)
            and memory and time.monotonic() - memory["at"] <= engine.config.QUERY_CONTEXT_TTL_SEC):
        query.vehicle_id, query.vehicle_kind = memory["id"], memory["kind"]
    session.clear()
    session.history.append(("user", text))
    try:
        result = report(question, query=query)
    except ReportFailure as exc:
        return respond(engine, session, "요청한 정보를 확인하지 못했어요. 잠시 후 다시 요청해 주세요.",
                               meta={"intent": "operations_report", "source": "langgraph",
                                     "trace_id": exc.trace_id, "report_error": exc.agent})
    except Exception:
        return respond(engine, session, "조회 연결 설정을 확인하지 못했어요.",
                               meta={"intent": "operations_report", "source": "langgraph", "unavailable": True})
    needs_choice = any(card.get("needs_clarification") for card in result["cards"])
    if needs_choice:
        session.agent_followup = {"question": question, "at": time.monotonic(),
                                  "vehicle_candidates": query.vehicle_candidates,
                                  "lot": query.lots[0] if len(query.lots) == 1 else None}
    if query.resolved_vehicle:
        session.agent_vehicle = {"id": query.vehicle_id, "kind": query.vehicle_kind, "at": time.monotonic()}
        engine.DC.remember(session, query.resolved_vehicle["lot_id"])
    if len(query.lots) == 1:
        engine.DC.remember(session, query.lots[0])
    parts = [result["clarification"]] if result["clarification"] else []
    for card in result["cards"]:
        if len(result["cards"]) == 1:
            parts.append(card.get("error") or card["summary"])
        else:
            parts.extend(["", card["title"], card.get("error") or card["summary"]])
    choices = "default"
    if needs_choice and (query.topics or query.section or query.vehicle_id):
        choices = ["lot_" + lot for lot in engine.config.catalog.ids()]
    map_lots = query.lots or ([query.resolved_vehicle["lot_id"]] if query.resolved_vehicle else [])
    if not map_lots and "spaces" in result["tasks"] and any(card.get("title") == "현재 빈자리" and "values" in card for card in result["cards"]):
        map_lots = list(engine.config.catalog.ids())
    map_card = None
    if map_lots and any(task in result["tasks"] for task in ("policies", "spaces", "vehicle")) and not needs_choice:
        map_card = {"type": "parking_map", "data": {"lot_ids": map_lots,
                    "filter": (query.space_type or "empty") if "spaces" in result["tasks"] else "all",
                    "reservation_available": "C" in map_lots and any(key in ("reservation", "overnight") for key, _ in query.topics),
                    "space_id": query.resolved_vehicle.get("space_id") if query.resolved_vehicle else None}}
        if query.route_plan:
            map_card['data']['route'] = query.route_plan
            map_card['data']['space_id'] = query.route_plan['space_id']
        if query.individual_fee and query.resolved_vehicle and set(result["tasks"]) == {"vehicle", "fees"}:
            values = {card["title"]: card.get("values", {}) for card in result["cards"]}
            fee = values.get("요금·결제 조회", {})
            vehicle = values.get("차량 위치", {})
            if "결제 상태" in fee and "주차칸" in vehicle:
                map_card["data"]["payment_summary"] = {
                    "vehicle_number": query.resolved_vehicle.get("plate_number") or query.vehicle_id,
                    "lot_id": query.resolved_vehicle["lot_id"],
                    "lot_name": vehicle.get("주차장"),
                    "space_id": vehicle["주차칸"],
                    "amount": fee["현재 요금(원)"],
                    "status": fee["결제 상태"],
                    "blocked": fee["결제 차단"] == "경보로 결제 중단",
                    "observed_at": vehicle.get("조회 기준 시각"),
                }
    return respond(engine, session, "\n".join(parts), card=map_card, chip_set=choices, meta={
        "intent": "operations_report", "source": "langgraph", "trace_id": result["trace_id"],
        "permission_denied": any(card.get("permission_denied") for card in result["cards"]),
        "prompt_versions": result["prompt_versions"], "report_tasks": result["tasks"],
        "report_source": result["source"]})
