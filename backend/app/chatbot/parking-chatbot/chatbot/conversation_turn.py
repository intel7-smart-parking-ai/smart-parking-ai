"""Coordinate an ordinary message without giving the model a write capability."""
from dataclasses import dataclass
import re
import time

from . import agents, config, dialog_context, info_gaps, monitoring, plan, route, templates as T
from .knowledge import search as knowledge_search
from .turn_guard import LEAVING
from .adapters import request_deadline
from .agents.conversation import turn_extras as agent_extras
from .agents.evidence import contradicts_restricted_policy
from .dialog import Query, Reply, interpret, render_query
from .dialog.query import _STOP_PAYMENT
from .dialog.render import _parking_card, render_knowledge, render_observed_parking
from .parser import explicit_cars, extract_lots, extract_vehicle, guarded_intent, normalize_car_id
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


def plain_text(text):
    """모델 답의 마크다운(**굵게**, 목록 기호, 제목)을 화면용 일반 문장으로 바꾼다. 팀 화면은 글자를 그대로 보여 준다."""
    if not isinstance(text, str):
        return text
    text = re.sub(r"\*\*(.+?)\*\*|__(.+?)__", lambda m: m.group(1) or m.group(2), text)
    text = re.sub(r"(?m)^\s{0,3}#{1,6}\s*", "", text)
    text = re.sub(r"(?m)^\s*[-*•]\s+", "· ", text)
    text = text.replace("**", "")
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def readings(text):
    """대화 AI에 넘기는 참고 정보 = 이번 질문의 계획표(plan.Plan.hints). 결정권은 없고 반드시 도구로 조회한다."""
    return plan.for_text(text).hints()


_FOLLOW_UP = re.compile(r"그럼|그러면|거기|그\s*곳|그쪽|여기는|이곳")
_OWN_ZONE = {"ev": "ev", "accessible": "accessible"}
_CROWD = re.compile(r"꽉|찼|가득|붐비|붐벼|한산|널널|만차|여유")


_FAKE_BUTTON = re.compile(r"[ \t]*\[(?=[^\]\n]*(?:관리자|결제|호출|충전|빈자리|안내|요금|취소|조회|위치|버튼|출차|출구|입차|차단|하기))[^\]\n]{1,20}\][ \t]*")
_PAID_SAID = re.compile(r"이미\s*(?:결제|정산)|결제(?:가|는)?\s*(?:완료|끝|됐|되었|하셨|했)|정산(?:이|은)?\s*(?:완료|끝|됐)")


def _fix_facts(answer, evidence):
    """모델이 지시문을 어겨도 사실은 맞게: 결제 완료 차량은 결제했다고 먼저 말하고(봇 점검 🟠1),
    일부 주차장에만 없는 안내 자료는 '자료 없음'을 붙인다(🟡4)."""
    lead, tail = [], []
    for row in evidence:
        fee = (row.get("data") or {}).get("fee") if row.get("tool") == "vehicle_info" else None
        if (isinstance(fee, dict) and fee.get("paid") is True and not _PAID_SAID.search(answer)
                and re.search(r"\d[\d,]*\s*원|요금|금액|정산", answer)):
            amount = f"(결제 금액 {fee['fee']:,}원)" if type(fee.get("fee")) is int else ""
            lead.append(f"{fee.get('car_id')}번 차는 이미 결제했어요{amount}. 출구에서는 현장 안내를 따라 주세요.")
            answer = re.sub(r"현재\s*요금(은|이)", r"결제한 요금\1", answer)
        if row.get("tool") == "search_knowledge":
            for label, names in ((row.get("data") or {}).get("missing_by_lot") or {}).items():
                names = [n for n in names if n not in answer]
                if names:
                    tail.append(f"{'·'.join(names)}은 {label} 자료가 아직 없어요.")
    return "\n".join(dict.fromkeys(lead + [answer] + tail))


def _strip_fake_buttons(text):
    """본문의 '[결제하기] [관리자 호출]' 같은 버튼 흉내. 실제 버튼은 화면이 칩으로 따로 붙인다."""
    cleaned = _FAKE_BUTTON.sub(" ", text or "")
    cleaned = re.sub(r"[ \t]+\n", "\n", re.sub(r"[ \t]{2,}", " ", cleaned))
    return re.sub(r"\n{3,}", "\n\n", cleaned).strip() or text


def _prefetch(text, session, toolbox, target, notice=None):
    """확실한 조회를 코드가 먼저 실행한다 → [{"tool", "args", "result"}].

    규칙의 '의도'로 정하지 않고 문장에서 바로 보이는 단서(말한 주차장·별명·목적, 말한 차량, 구역, 기간)로만 고른다.
    조회가 실패하거나 단서가 약하면 아무것도 하지 않는다(그때는 예전처럼 AI가 도구를 고른다).
    """
    if not config.LLM_PREFETCH:
        return []
    p = plan.for_text(text)
    wants = set(p.wants)
    zone = p.zone or _OWN_ZONE.get(getattr(session, "vehicle_type", None))
    calls = [("service_notice", {})] if notice else []
    cars = explicit_cars(text)
    if cars or p.own_car:
        for car in (cars or [p.own_car])[:2]:
            calls.append(("vehicle_info", {"car_id": car, "include_fee": True}))
    elif target and (wants & {"fee", "location", "payment"} or LEAVING.search(text)) and not p.lots and not p.purpose_lots:
        calls.append(("vehicle_info", {"car_id": target, "include_fee": True}))
        if p.minutes and p.minutes > 0:
            calls.append(("estimate_fee", {"minutes": p.minutes, "car_id": target}))
    else:
        focus = dialog_context.focus(session)
        short_follow_up = bool(focus and (_FOLLOW_UP.search(text) or (len(text.strip()) <= 12 and (wants or p.minutes))))
        lots = list(p.lots) or list(p.purpose_lots) or ([focus] if short_follow_up else [])
        # 시설·이용 규칙 질문('몇 시까지 해?', '실외 주차장은?')은 안내 자료를 먼저 찾아 넘긴다
        gap = info_gaps.classify(text)
        facility = bool(gap and gap[0] == "facility")
        try:
            unregistered = knowledge_search(text, list(config.catalog.ids()), None)[1]
        except Exception:
            unregistered = []
        nearest = bool(gap and gap[0] == "nearest") or bool(info_gaps.near_place(text))
        if not notice and (facility or nearest or unregistered or "policy" in wants):
            calls.append(("search_knowledge", {"query": text[:400], "lot_ids": lots or None}))
        asks_space = not facility and bool(wants & {"vacancy", "rates", "compare"} or _CROWD.search(text)
                                           or p.talk.get("spot") or p.self_only)
        only_fee = bool(wants & {"fee", "rates"}) and not wants & {"vacancy", "compare"}
        if p.minutes and p.minutes > 0 and len(lots) == 1:
            calls.append(("estimate_fee", {"minutes": p.minutes, "lot_id": lots[0]}))
        elif len(lots) == 1 and only_fee:
            calls.append(("fee_policies", {"lot_ids": lots}))
        elif lots and (len(lots) > 1 or wants & {"rates", "compare"}):
            calls.append(("compare_lots", {"lot_ids": lots, "zone": zone}))
        elif lots and (asks_space or "future" in wants or notice):
            # 확정 안내 질문('거기 예약 돼?')도 그 주차장의 지금 빈자리를 함께 넘겨 자연스럽게 잇게 한다
            calls.append(("parking_status", {"lot_ids": lots, "zone": zone}))
        elif not lots and asks_space and "alert" not in wants:
            calls.append(("compare_lots", {"lot_ids": None, "zone": zone}))
        elif "alert" in wants and not lots:
            calls.append(("alert_summary", {}))
    done = []
    for name, args in calls[:3]:
        try:
            result = getattr(toolbox, name)(**args)
        except Exception:
            continue
        if isinstance(result, dict) and result.get("ok") is True:
            done.append({"tool": name, "args": {k: v for k, v in args.items() if v is not None}, "result": result})
    return done


def _model_reply(text, session, toolbox, target, candidates, query=None, notice=None):
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
        "readings": readings(text),
        "user_vehicle_type": getattr(session, "vehicle_type", None),
        "service_notice_topic": (notice or {}).get("topic"),
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
    def coverage(observations):
        missing = _missing(query, _observed_rows(observations), _accept_zones(session)) if query else ""
        if notice and not any(row["tool"] == "service_notice" and row["result"].get("ok") for row in observations):
            missing = ", ".join(x for x in (missing, "확정 안내(service_notice)") if x)
        return missing
    prefetched = _prefetch(text, session, toolbox, target, notice)
    extra = {"coverage": coverage if (query or notice) else None,
             "prefetched": prefetched,
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


def _missing(query, evidence, accept_zones=()):
    """질문에서 요청했는데 이번 턴에 조회하지 못한 항목(사람이 읽는 이름). 모두 있으면 빈 문자열.

    accept_zones: 구역을 말하지 않은 빈자리 질문에서 '전체' 대신 인정할 구역(일반, 사용자가 말한 차 종류의 구역).
    """
    if not query:
        return ""
    ok_zones = {query.zone} if query.zone else {None, "general", *accept_zones}
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
                rows = [r for r in rows if r.get("zone") in ok_zones]
            if not rows or set(query.candidates) - {r["lot_id"] for r in rows}:
                names = "·".join(config.catalog.name(c) for c in query.candidates) or "후보 주차장"
                missing.append(f"{names} {label}" + (f", 구역 {query.zone}" if tool == "parking_status" and query.zone else ""))
    if query.policy_query and "search_knowledge" not in by_tool:
        missing.append("이용 규칙 안내(search_knowledge)")
    return ", ".join(missing)


def _accept_zones(session):
    return {"ev": ("ev",), "accessible": ("accessible",)}.get(getattr(session, "vehicle_type", None), ())


def _covers(query, evidence, session=None):
    return not _missing(query, evidence, _accept_zones(session))


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


def reply_to(text, session, adapter, alert, vehicle, refresh_alert, admin_status=None, notice=None):
    query = interpret(text, session)
    explicit, only = extract_vehicle(text)
    previous = session.pending
    resumed = bool(only and previous and previous.intent in {"dialog_read", "dialog_agent"})
    if only and not resumed:
        route.mark("agent", "skip", why="차량 번호만 입력(업무 흐름)")
        return None
    if query and query.clarification and explicit == "ambiguous" and len(explicit_cars(text)) > 1:
        query = None   # '23번이랑 41번 비교' — 되묻지 않고 대화 AI가 두 차를 각각 조회
    if query and query.clarification:
        route.mark("plan", "clarify", why=query.clarification)
        return Turn(Reply(query.clarification, meta={"intent": "clarify"}), query)
    single_vehicle = query and (query.wants_location != query.wants_fee) and not query.wants_payment
    if query and query.needs_vehicle:
        if single_vehicle:
            route.mark("agent", "skip", why="차량 번호가 필요한 단일 조회(규칙 처리)")
            return None
        route.mark("plan", "ask_vehicle", why="위치·요금을 함께 물었지만 차량 번호 없음")
        return Turn(Reply(T.ASK_VEHICLE, meta={"intent": "ask_vehicle"}), query, ask_vehicle=True)

    cars = explicit_cars(text)
    target = query.car_id if query and query.car_id else normalize_car_id(explicit) or (cars[0] if len(cars) > 1 else None)         or session.car_id
    candidates = query.candidates if query else extract_lots(text + " 빈자리")
    if session.selected_candidate and target == session.car_id:
        target = None
    turn_plan = plan.for_text(text)
    allowed = tuple(dict.fromkeys([*candidates, *turn_plan.lots, *turn_plan.purpose_lots])) if candidates else ()
    toolbox = ReadTools(adapter, alert, vehicle_provider=vehicle, allowed_car_id=target,
                        allowed_lots=allowed or None, admin_status_provider=admin_status, notice=notice,
                        extra_car_ids=cars[1:] if len(cars) > 1 else ())
    result = _model_reply(text, session, toolbox, target, candidates, query, notice)
    if result is None:
        route.mark("agent", "off", why="모델 꺼짐·호출 한도·남은 시간 부족")
    else:
        meta = result.metadata
        route.mark("agent", result.status, calls=meta.get("model_calls"), prefetched=meta.get("prefetched"),
                   tools=[row["tool"] for row in result.tool_results] or None,
                   problem=meta.get("grounding_error") or meta.get("repair_failed"),
                   repaired=meta.get("repair_reason") if meta.get("repaired") else None)
    turn_monitor = monitoring.current()
    if turn_monitor and result is not None:
        turn_monitor.note(tools=[row["tool"] for row in result.tool_results],
                          agent={k: v for k, v in result.metadata.items()
                                 if k in {"model_calls", "tool_calls", "input_tokens", "output_tokens", "latency_ms",
                                          "grounding_error", "repair_reason", "repair_failed", "repaired",
                                          "repeated", "smalltalk", "missing_goal", "prefetched",
                                          "retried_timeout", "calls"}}
                          | {"status": result.status,
                             "drafts": [{**d, "text": monitoring.capture(d.get("text"))}
                                        for d in result.metadata.get("drafts") or []] or None})
    if result and (result.status in {"ok", "clarify"} or result.metadata.get("model_calls")) and refresh_alert().get("level", 0) >= 2:
        route.mark("agent", "dropped", why="답하는 동안 Lv2 경보 발생")
        return None

    if (result is None or result.status not in {"ok", "clarify"}) and single_vehicle and not resumed:
        return None
    if result and result.status == "clarify" and not query:
        clarification = next((row["result"]["data"].get("topic") for row in result.tool_results
                              if row["tool"] == "ask_clarification" and row["result"].get("ok")), None)
        return Turn(Reply(plain_text(result.text), meta={"source": "llm", "intent": "clarify"}), None,
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
    if result and result.status == "ok" and not policy_conflict and _covers(query, evidence, session):
        meta = dict(reply.meta) if reply else {}
        if evidence:
            meta["source_evidence"] = next((r["source"] for r in evidence if r["source"].get("is_mock")), evidence[0]["source"])
        answer = _fix_facts(_strip_fake_buttons(plain_text(result.text)), evidence)
        if any((r["data"].get("fee") or {}).get("payment_blocked") is True for r in evidence):
            if not meta.get("payment_blocked"):
                answer += "\n서버가 모의 결제를 차단했어요. 대피 안내를 먼저 확인해 주세요."
            meta["payment_blocked"] = True
        reply = Reply(answer, card=reply.card if reply else _observation_card(evidence, toolbox),
                      chip_set="alert" if meta.get("payment_blocked") else reply.chip_set if reply else "default",
                      updated_at=reply.updated_at if reply else meta.get("source_evidence", {}).get("updated_at"),
                      meta={**meta, "source": "llm"})
    if reply is None:
        if result is not None:
            route.mark("agent", "dropped", why="검사 통과 답 없음 → 규칙 답")
        return None
    route.mark("agent", "answer", source="llm" if reply.meta.get("source") == "llm" else "rule_render",
               policy="D-05로 대체" if policy_conflict else None)
    if result:
        reply.meta.update(agent_status=result.status, model_calls=result.metadata.get("model_calls", 0),
                          prefetched=list(result.metadata.get("prefetched") or []))
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
