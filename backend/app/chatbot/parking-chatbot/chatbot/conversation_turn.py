"""Coordinate an ordinary message without giving the model a write capability."""
from dataclasses import dataclass
import time

from . import agents, config, dialog_context, monitoring, templates as T
from .adapters import request_deadline
from .agents.conversation import turn_extras as agent_extras
from .agents.evidence import contradicts_restricted_policy
from .dialog import Query, Reply, interpret, render_query
from .dialog.query import _STOP_PAYMENT
from .dialog.render import _parking_card, render_knowledge, render_observed_parking
from .parser import extract_lots, extract_vehicle, guarded_intent, normalize_car_id
from .tools import ReadTools
from .workflows.views import fee_card


@dataclass
class Turn:
    reply: Reply
    query: Query | None
    payment: bool = False
    suggest_admin: bool = False
    ask_vehicle: bool = False
    car_id: str | None = None


def _recent_answers(session, count=2):
    """직전 봇 답(반복 방지용). 대화 기록의 마지막 사용자 입력은 이번 질문이다."""
    answers = [content for role, content in list(session.history)[-8:] if role == "bot" and isinstance(content, str)]
    return answers[-count:]


def _model_reply(text, session, toolbox, target, candidates, query=None):
    if config.LLM_PROVIDER == "none" or config.LLM_ENGINE != "langchain" or not config.api_key(config.LLM_PROVIDER):
        return None
    try:
        tools = toolbox.langchain_tools()
    except ImportError:
        return None
    state = {
        "car_id": target,
        "candidates": list(candidates),
        "focus_lot": dialog_context.focus(session),
        "offered_lots": dialog_context.offered(session),
        "lots": {k: config.catalog.name(k) for k in config.catalog.ids()},
    }
    # Give the model part of the shared turn budget and reserve time for verified fallback.
    deadline = request_deadline.get()
    token = None
    if deadline is not None:
        remaining = deadline - time.monotonic()
        if remaining <= 0.5:
            return None
        # 확인된 대체 답을 만들 시간만 남기고(최대 0.7초) 나머지는 모델 답(고쳐 쓰기 포함)에 쓴다.
        token = request_deadline.set(deadline - min(0.7, remaining / 4))
    turn_monitor = monitoring.current()
    extra = {"coverage": (lambda observations: _missing(query, _observed_rows(observations))) if query else None,
             "avoid": _recent_answers(session),
             "callbacks": turn_monitor.callbacks() if turn_monitor else None}
    extras_token = agent_extras.set(extra)
    try:
        return agents.run_conversation(text, session.history, state, tools)
    finally:
        agent_extras.reset(extras_token)
        if token is not None:
            request_deadline.reset(token)


def _observed(result):
    return _observed_rows(result.tool_results)


def _observed_rows(tool_results):
    return [row["result"] for row in tool_results
            if row["result"].get("ok") and row["tool"] not in {"ask_clarification", "propose_action"}]


def _missing(query, evidence):
    """질문에서 요청했는데 이번 턴에 조회하지 못한 항목(사람이 읽는 이름). 모두 있으면 빈 문자열."""
    if not query:
        return ""
    by_tool = {}
    for row in evidence:
        by_tool.setdefault(row["tool"], []).append(row["data"])
    missing = []
    estimated = any(d.get("car_id") == query.car_id and type(d.get("current_fee")) is int
                    for d in by_tool.get("estimate_fee", []))
    if (query.wants_location or query.wants_fee) and not (estimated and query.wants_fee and not query.wants_location):
        if not any(
            d.get("car_id") == query.car_id
            and (not query.wants_location or d.get("location"))
            and (not query.wants_fee or d.get("fee"))
            and not d.get("partial")
            for d in by_tool.get("vehicle_info", [])
        ):
            missing.append(f"{query.car_id}번 차 " + ("위치·요금" if query.wants_location and query.wants_fee
                                                     else "위치" if query.wants_location else "요금") + "(vehicle_info)")
    compared = [r for data in by_tool.get("compare_lots", []) for r in data.get("rows", [])]
    for tool, wanted, label in (("parking_status", query.wants_parking, "빈자리(parking_status 또는 compare_lots)"),
                                ("fee_policies", query.wants_rates, "기본요금(fee_policies 또는 compare_lots)")):
        if wanted:
            rows = [r for data in by_tool.get(tool, []) for r in data.get("rows", [])]
            rows += [r for r in compared if tool == "parking_status" or "base_fee" in r]
            if tool == "parking_status":
                rows = [r for r in rows if r.get("zone") == query.zone]
            if not rows or set(query.candidates) - {r["lot_id"] for r in rows}:
                names = "·".join(config.catalog.name(c) for c in query.candidates) or "후보 주차장"
                missing.append(f"{names} {label}" + (f", 구역 {query.zone}" if tool == "parking_status" and query.zone else ""))
    if query.policy_query and "search_knowledge" not in by_tool:
        missing.append("이용 규칙 안내(search_knowledge)")
    return ", ".join(missing)


def _covers(query, evidence):
    return not _missing(query, evidence)


def _observation_card(evidence, toolbox):
    for row in evidence:
        data = row["data"]
        if row["tool"] in {"parking_status", "compare_lots"} and data.get("rows"):
            return _parking_card(data["rows"], data["rows"][0]["zone"])
        if row["tool"] == "vehicle_info":
            location = data.get("location")
            if location and location.get("space_id"):
                return {"type": "car_location", "data": location}
            if data.get("fee"):
                return fee_card(toolbox.fee_for_view(data["fee"]))
    return None


def reply_to(text, session, adapter, alert, vehicle, refresh_alert, admin_status=None):
    query = interpret(text, session)
    explicit, only = extract_vehicle(text)
    previous = session.pending
    resumed = bool(only and previous and previous.intent in {"dialog_read", "dialog_agent"})
    if only and not resumed:
        return None
    if query and query.clarification:
        return Turn(Reply(query.clarification, meta={"intent": "clarify"}), query)
    single_vehicle = query and (query.wants_location != query.wants_fee) and not query.wants_payment
    if query and query.needs_vehicle:
        if single_vehicle:
            return None
        return Turn(Reply(T.ASK_VEHICLE, meta={"intent": "ask_vehicle"}), query, ask_vehicle=True)

    target = query.car_id if query and query.car_id else normalize_car_id(explicit) or session.car_id
    candidates = query.candidates if query else extract_lots(text + " 빈자리")
    if session.selected_candidate and target == session.car_id:
        target = None
    toolbox = ReadTools(adapter, alert, vehicle_provider=vehicle, allowed_car_id=target,
                        allowed_lots=candidates or None, admin_status_provider=admin_status)
    result = _model_reply(text, session, toolbox, target, candidates, query)
    turn_monitor = monitoring.current()
    if turn_monitor and result is not None:
        turn_monitor.note(tools=[row["tool"] for row in result.tool_results],
                          agent={k: v for k, v in result.metadata.items()
                                 if k in {"model_calls", "tool_calls", "input_tokens", "output_tokens", "latency_ms",
                                          "grounding_error", "repair_reason", "repair_failed", "repaired",
                                          "repeated", "smalltalk", "missing_goal"}} | {"status": result.status})
    if result and (result.status in {"ok", "clarify"} or result.metadata.get("model_calls")) and refresh_alert().get("level", 0) >= 2:
        return None

    if (result is None or result.status not in {"ok", "clarify"}) and single_vehicle and not resumed:
        return None
    if result and result.status == "clarify" and not query:
        clarification = next((row["result"]["data"].get("topic") for row in result.tool_results
                              if row["tool"] == "ask_clarification" and row["result"].get("ok")), None)
        return Turn(Reply(result.text, meta={"source": "llm", "intent": "clarify"}), None,
                    ask_vehicle=clarification == "vehicle")

    reply = render_query(query, toolbox) if query else None
    evidence = _observed(result) if result else []
    if reply is None and result and result.metadata.get("grounding_error") == "contradictory_availability":
        parking = next((r for r in evidence if r["tool"] == "parking_status" and r["data"].get("rows")), None)
        if parking:
            reply = render_observed_parking(parking)
    # 팀 결정 D-05와 반대되는 모델 답(일반차도 전용 칸 이용 가능)은 내보내지 않고 승인된 안내로 대체한다.
    policy_conflict = bool(result and result.status == "ok" and contradicts_restricted_policy(result.text))
    if policy_conflict:
        result.metadata["grounding_error"] = "contradicts_policy"
        if reply is None and evidence:
            knowledge = next((r for r in evidence if r["tool"] == "search_knowledge"), None)
            if knowledge:
                reply = Reply(render_knowledge(knowledge), meta={"intent": "policy"})
    if result and result.status == "ok" and not policy_conflict and _covers(query, evidence):
        meta = dict(reply.meta) if reply else {}
        if evidence:
            meta["source_evidence"] = next((r["source"] for r in evidence if r["source"].get("is_mock")), evidence[0]["source"])
        answer = result.text
        if any((r["data"].get("fee") or {}).get("payment_blocked") is True for r in evidence):
            if not meta.get("payment_blocked"):
                answer += "\n서버가 모의 결제를 차단했어요. 대피 안내를 먼저 확인해 주세요."
            meta["payment_blocked"] = True
        reply = Reply(answer, card=reply.card if reply else _observation_card(evidence, toolbox),
                      chip_set="alert" if meta.get("payment_blocked") else reply.chip_set if reply else "default",
                      updated_at=reply.updated_at if reply else meta.get("source_evidence", {}).get("updated_at"),
                      meta={**meta, "source": "llm"})
    if reply is None:
        return None
    if result:
        reply.meta.update(agent_status=result.status, model_calls=result.metadata.get("model_calls", 0))
        for key in ("repaired", "repeated", "smalltalk", "grounding_error", "repair_reason"):
            if result.metadata.get(key):
                reply.meta[key] = result.metadata[key]
    reply.meta.setdefault("source", "rule")
    proposed_admin = bool(result and result.status == "ok" and not (query and query.policy_query)
                          and guarded_intent(text) != "cancel_action"
                          and any(p["intent"] == "call_admin" for p in result.proposals))
    observed_car = target if any(r["tool"] == "vehicle_info" and r["data"].get("car_id") == target
                                 for r in evidence) else None
    payment_turn = bool(query and query.wants_payment and reply.meta.get("fee_verified"))
    # 모델이 결제를 제안했으면 결제 칩만 보여 준다. 칩을 누르면 기존 견적 → 사용자 승인 절차를 그대로 탄다.
    if (result and result.status == "ok" and observed_car and not payment_turn and not proposed_admin
            and not _STOP_PAYMENT.search(text) and guarded_intent(text) != "cancel_action"
            and any(p["intent"] == "pay_fee" and p.get("params", {}).get("car_id") == observed_car
                    for p in result.proposals)):
        reply.chip_set = ["pay", "find_car", "call_admin"]
        reply.meta["pay_suggestion"] = True
    return Turn(reply, query, bool(query and query.wants_payment and reply.meta.get("fee_verified")),
                proposed_admin, car_id=observed_car)
