"""Coordinate an ordinary message without giving the model a write capability."""
from dataclasses import dataclass
import re
import time

from . import agents, config, dialog_context, info_gaps, monitoring, plan, route, route_check, templates as T
from .knowledge import search as knowledge_search
from .turn_guard import LEAVING
from .adapters import request_deadline
from .agents.conversation import turn_extras as agent_extras
from .agents.evidence import contradicts_restricted_policy
from .dialog import Query, Reply, interpret, render_query
from .dialog.query import _STOP_PAYMENT
from .dialog.render import _parking_card, render_knowledge, render_observed_parking
from .parser import explicit_cars, extract_lots, extract_vehicle, extract_virtual, guarded_intent, normalize_car_id
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


def _said_lots(text, p):
    """이 문장이 가리키는 주차장: 이름·별명으로 말한 곳 + 목적으로 말한 곳(기차→부산역, 여행→공항, 쇼핑→백화점).
    이름으로 말한 곳이 있어도 목적으로 말한 다른 곳을 빼지 않는다('백화점 갔다가 저녁에 기차 타야 해' → 신세계·부산역)."""
    return list(dict.fromkeys([*p.lots, *p.purpose_lots, *info_gaps.hinted_lots(text)]))


# 주차할 곳을 묻는 말('어디 세우면 돼?', '어디가 편해?', '세울 데 있어?')과 이미 세운 차를 찾는 말('내 차 어디 있어?', '위치', '찾아줘')
_PARK_WHERE = re.compile(r"어디(?:에|에다|가|로|다)?\s*(?:세우|세울|세워야|대(?:면|야|는|도)|댈|주차\s*(?:해|하|할)|파킹|편해|편하|좋아|좋을|나아|낫|가야)|"
                         r"(?:세우|세울|댈|주차할)\s*(?:곳|데|만한)")
_FIND_CAR = re.compile(r"(?:내|제|우리)\s*차|차(?:량)?\s*(?:가\s*)?(?:어디\s*)?(?:있|위치)|찾아|세워\s*(?:뒀|놨|둔)|주차\s*(?:했|해\s*(?:뒀|놨))|댔")
_FAKE_BUTTON = re.compile(r"[ \t]*\[(?=[^\]\n]*(?:관리자|결제|호출|충전|빈자리|안내|요금|취소|조회|위치|버튼|출차|출구|입차|차단|하기))[^\]\n]{1,20}\][ \t]*")



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
    virtual = virtual_number(text, session)
    if virtual:
        calls.append(("virtual_vehicle_info", {"vehicle_number": virtual}))
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
        lots = _said_lots(text, p) or ([focus] if short_follow_up else [])
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
        # '그 차가 없다'가 확인된 조회(not_found)도 넘긴다. 전에는 버려서 대화 AI가 같은 조회를 다시 하고(호출 2번),
        # '입차 기록을 찾지 못했어요'라는 맞는 답도 근거 없음으로 버려졌다(11/11 내부 검사: 125턴 중 14턴).
        if isinstance(result, dict) and (result.get("ok") is True or _confirmed_absent(result)):
            done.append({"tool": name, "args": {k: v for k, v in args.items() if v is not None}, "result": result})
    return done


_ABSENT_VEHICLE = object()   # _model_reply: 물은 차의 입차 기록이 없어 대화 AI를 부르지 않았다


def _confirmed_absent(result):
    return isinstance(result, dict) and isinstance(result.get("data"), dict) and result["data"].get("not_found") is True


def _simple_vehicle_turn(text):
    """차 한 대의 위치·요금·결제·출차만 묻는 문장인가(다른 것을 함께 묻지 않는다)."""
    p = plan.for_text(text)
    return (len(explicit_cars(text)) <= 1
            and not (set(p.wants) - {"location", "fee", "payment", "future"} or p.zone or p.lots or p.purpose_lots))


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
        missing = _missing(query, _observed_rows(observations), _accept_zones(session), _absent_cars(observations)) if query else ""
        if notice and not any(row["tool"] == "service_notice" and row["result"].get("ok") for row in observations):
            missing = ", ".join(x for x in (missing, "확정 안내(service_notice)") if x)
        return missing
    prefetched = _prefetch(text, session, toolbox, target, notice)
    if (notice is None and _simple_vehicle_turn(text)
            and any(row["tool"] == "vehicle_info" and _confirmed_absent(row["result"]) for row in prefetched)):
        # 스티커 차량 한 대만 물었는데 입차 기록이 없다 → 규칙 답이 같은 내용을 바로 낸다(번호 미인식 후보 안내 포함).
        # 다른 것을 함께 물었으면 대화 AI가 '그 차는 못 찾았다'와 나머지 답을 함께 쓴다.
        if token is not None:
            request_deadline.reset(token)
        return _ABSENT_VEHICLE
    extra = {"coverage": coverage if (query or notice) else None,
             "prefetched": prefetched,
             "avoid": _recent_answers(session),
             "callbacks": turn_monitor.callbacks() if turn_monitor else None}
    extras_token = agent_extras.set(extra)
    checking = route_check.pending()   # [실험] 뒤로 미뤄 둔 가로채기 확인 → 대화 AI와 함께 기다린다
    try:
        if checking is None:
            return agents.run_conversation(text, session.history, state, tools)
        result = agents.run_conversation(text, session.history, state, tools, guard=checking.future,
                                         guard_wait=max(0.0, checking.started + checking.timeout - time.monotonic()))
        if route_check.settle(checking):
            raise route_check.RuleKeeps(checking.intent)   # 규칙이 맞다 → 엔진이 규칙 처리로 다시 한다
        return result
    finally:
        agent_extras.reset(extras_token)
        if token is not None:
            request_deadline.reset(token)


def _observed(result):
    return _observed_rows(result.tool_results)


def _observed_rows(tool_results):
    return [row["result"] for row in tool_results
            if row["result"].get("ok") and row["tool"] not in {"ask_clarification", "propose_action"}]


def _absent_cars(tool_results):
    """이번 턴에 '입차 기록 없음'이 확인된 스티커 차량 번호."""
    return {row["result"]["data"].get("car_id") for row in tool_results
            if row["tool"] == "vehicle_info" and _confirmed_absent(row["result"])}


def _missing(query, evidence, accept_zones=(), absent=()):
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
    # 조회했더니 그 차가 없다 → 위치·요금은 '빠진 조회'가 아니다(없다는 것이 답이다)
    if (query.wants_location or query.wants_fee) and query.car_id not in absent \
            and not (estimated and query.wants_fee and not query.wants_location):
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


def virtual_number(text, session=None):
    """이번 문장의 가상 차량 번호(번호판·뒤 4자리). 없으면 이 대화에서 방금 조회한 가상 차량('그 차 요금은?')."""
    said = extract_virtual(text) if isinstance(text, str) else None
    if said:
        return said.replace(" ", "")
    remembered = getattr(session, "virtual_vehicle", None) if session is not None else None
    if isinstance(remembered, dict) and re.search(r"그\s*차|그\s*차량|그거|걔|아까\s*(?:그|차)", text or ""):
        return remembered.get("plate_number") or remembered.get("vehicle_number")
    return None


def virtual_compound(text):
    """가상 차량 번호와 함께 다른 것도 묻는 문장인가('1724 얼마 나왔어, 근처에 충전 자리도 있어?').

    위치·요금·출차·결제 여부만 묻는 문장은 규칙이 서버 값으로 바로 답한다(빠르고 항상 같다).
    그 밖의 것을 함께 물으면 대화 AI가 가상 차량 조회 도구와 다른 조회를 엮어 답한다.
    """
    if not isinstance(text, str) or not extract_virtual(text):
        return False
    p = plan.for_text(text)
    return bool(set(p.wants) - {"location", "fee", "payment", "future"} or p.zone or p.lots or p.purpose_lots)


def reply_to(text, session, adapter, alert, vehicle, refresh_alert, admin_status=None, notice=None):
    query = interpret(text, session)
    virtual = virtual_number(text, session)
    if virtual and (virtual_compound(text) or not extract_virtual(text)):
        query = None   # 가상 차량 + 다른 질문 → 차량 번호를 되묻지 않고 대화 AI가 조회 도구로 답한다
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
    if query and query.needs_vehicle and single_vehicle and _PARK_WHERE.search(text) and not _FIND_CAR.search(text):
        # '아버지가 장애인 차량이야. 어디 세우는 게 나아?'는 내 차 찾기가 아니라 주차할 곳 질문 → 대화 AI(실모델 확인 11/10)
        route.mark("plan", "park_where", why="'차량·어디'지만 주차할 곳을 묻는 문장 → 대화 AI")
        query = None
    if query and query.needs_vehicle and single_vehicle and not route_check.keep(text, "find_my_car"):
        query = None   # [실험] AI 확인이 내 차 찾기가 아니라고 함 → 대화 AI로
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
    allowed = tuple(dict.fromkeys([*candidates, *_said_lots(text, turn_plan)])) if candidates else ()
    toolbox = ReadTools(adapter, alert, vehicle_provider=vehicle, allowed_car_id=target,
                        allowed_lots=allowed or None, admin_status_provider=admin_status, notice=notice,
                        extra_car_ids=cars[1:] if len(cars) > 1 else (),
                        virtual_numbers=(virtual,) if virtual else ())
    result = _model_reply(text, session, toolbox, target, candidates, query, notice)
    if result is _ABSENT_VEHICLE:
        result = None
        route.mark("agent", "skip", why="입차 기록 없음(규칙 답)")
    elif result is None:
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
        answer = _strip_fake_buttons(plain_text(result.text))
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
    seen_virtual = next((r["data"] for r in evidence if r["tool"] == "virtual_vehicle_info"
                         and isinstance(r["data"].get("location"), dict)), None)
    if seen_virtual and result and result.status == "ok":
        # 관리자 호출 요약·'그 차' 이어 묻기에 쓴다(규칙 답 _virtual_vehicle_reply와 같은 기록)
        session.virtual_vehicle = {"plate_number": seen_virtual.get("plate_number"),
                                   "vehicle_number": seen_virtual.get("vehicle_number"),
                                   "lot_id": seen_virtual["location"].get("lot_id"),
                                   "space_id": seen_virtual["location"].get("space_id")}
        reply.meta["virtual_vehicle"] = True
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
