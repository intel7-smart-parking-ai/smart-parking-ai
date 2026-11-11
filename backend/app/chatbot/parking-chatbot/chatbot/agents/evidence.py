"""Check operational claims against this turn's successful, scoped reads.

These checks cover numeric facts, known lot identities and action boundaries.
They are not a semantic correctness score. The original observations remain
available to the caller if a generated answer fails a check.
"""
from __future__ import annotations

import re
from collections.abc import Mapping

from .. import config

_NUMBER = re.compile(r"\d+(?:,\d{3})*(?:\.\d+)?")
_HORIZON = re.compile(r"(\d+)\s*(분|시간)\s*(?:뒤|후|전)")
_ACTION_DONE = re.compile(
    r"(?:결제|환불|호출|문의|목적지|예약).{0,12}?"
    r"(?:해드렸|해드렸습|처리했|진행했|등록했|접수했|실행했|설정했|변경했|완료했|보냈|했어요|했습니다|완료됐|완료되었|완료됐|접수됐|접수되었|등록됐|설정됐|예약됐)"
)
_PAID = re.compile(r"(결제.{0,5}(완료|승인|성공)|결제된|이미\s*결제|지불된|납부된)")
# 실행 주장: "실행 대상 단어 + 완료·과거 표현"이 같은 절에 있으면 실행했다고 말한 것으로 본다.
# 띄어쓰기·존댓말이 바뀌어도 잡히도록 표현 목록 대신 조합으로 판단한다.
_CLAUSE_SPLIT = re.compile(r"[.!?。\n]|(?<=[요다])\s*,|\s+(?:그리고|그래서|또한)\s+")
_ACTION_WORD = re.compile(r"결제|정산|납부|환불|관리자|직원|담당자|호출|연락|신고|119|접수|등록|예약|목적지|차단기|게이트|문\s*(?:을|를)?\s*열|출차\s*처리|문의")
_DONE_WORD = re.compile(
    r"(?:해|하여|처리해|진행해|완료해|접수해|등록해|호출해|연락해|신고해|변경해|설정해|열어|불러|바꿔|보내|요청해|전달해)\s*(?:드렸|줬|주었|놨|뒀|두었)"
    r"|했(?:어요|습니다|어|다|음|고)|하였|됐|되었|끝났|마쳤|완료(?:됐|되었|했|하였|입니다|예요|이에요|돼\s*있|되어\s*있)"
    r"|처리\s*(?:됐|되었|했|하였|완료)|불렀|열었|바꿨|보냈|알렸|요청했|전달했|접수돼\s*있|등록돼\s*있"
)
_NOT_DONE = re.compile(r"안\s*(?:됐|되|했)|못\s*(?:했|됐)|않았|않아|아직|전이에요|전입니다|아니에요|아닙니다|없어요|없습니다"
                       r"|하시면|하려면|원하시면|할\s*수|해야|할까요|드릴까요|드릴게요|해\s*드릴|되면|되지|하지\s*(?:마|않)|확인되지")
# '제가·저희가'는 낱말일 때만('결제가 완료됐어요'의 '제가'를 챗봇의 실행 주장으로 읽던 버그, 11/10)
_AGENCY = re.compile(r"드렸|(?<![가-힣])(?:제가|저희가)|챗봇이|해\s*줬|해\s*주었")
_PROMISE = re.compile(
    r"(?:관리자|직원|담당자|소방|119|구급|경찰).{0,15}"
    r"(?:갈|올|오실|가실|도착|출동|방문|연락\s*(?:드릴|할|줄|해\s*줄|올)).{0,6}"
    r"(?:거예요|겁니다|거에요|예정|드릴게요|할게요|줄게요|드립니다|갈게요|올게요|것입니다)")
_PAY_WORD = re.compile(r"결제|정산|납부")
_CALL_WORD = re.compile(r"관리자|직원|담당자|호출|접수|등록|문의")
# 비교 문장: 단위 숫자 뒤에 오는 비교 표현이면 코드가 계산한 차이 값과만 맞춘다.
_COMPARE_AFTER = re.compile(r"\s*(?:정도\s*)?(?:더|차이|덜|적|많|싸|저렴|비싸|높|낮|넉넉)")
_COMPARE_BEFORE = re.compile(r"(?:차이(?:는|가)?|더)\s*$")
_RATE_PAIR = re.compile(r"(\d+)\s*분(?:에|당|마다|까지|동안)?\s*(?:기본\s*)?(\d{1,3}(?:,\d{3})+|\d+)\s*원")
_DIFF_KEYS = ("empty_diff", "base_fee_diff", "increase")
# 도구 없이 답해도 되는 대화(인사·감사·자기소개)인지 판단할 때 쓰는 단어.
_DATA_QUESTION = re.compile(r"자리|칸|만차|요금|얼마|결제|정산|위치|어디|경보|화재|불\s*났|연기|대피|출차|입차|차단기|"
                            r"관리자|직원|호출|엘리베이터|화장실|시설|몇\s*시|\d")
_STATE_CLAIM = re.compile(r"넉넉|여유|만차|비어|남아|붐비|한산|무료|공짜|싸요|비싸|저렴|있어요|없어요|있습니다|없습니다|가능해요|가능합니다")
_GUIDANCE_DONE = re.compile(r"(?:예약|환불|관리자\s*호출|문의\s*(?:접수|등록)|목적지\s*(?:변경|설정)).{0,4}(?:완료|성공)")
_NEGATED_DONE = re.compile(r"(?:(?:미|안\s*)완료|완료(?:로|라고)?\s*(?:단정할|볼 수 없))")
_SLOT = re.compile(r"(?<![A-Za-z0-9])([A-Za-z]-\d{2})(?!\d)")
_CLAIM_UNITS = re.compile(r"(\d+(?:,\d{3})*)\s*(원|자리|칸|대)(?![A-Za-z])")
_FULL_CLAIM = re.compile(
    r"만차(?:예요|입니다|여서|라서|라\s|\s*상태(?:예요|입니다|여서)|(?=[.!,。]|$))"
    r"|(?:빈\s*)?자리(?:가|는|도)\s*없(?:어요|습니다|어서|어\s|다고)"
)


def readable(observation):
    result = observation["result"]
    return result.get("ok") is True and isinstance(result.get("data"), dict)


def _strings(value):
    if isinstance(value, Mapping):
        for v in value.values():
            yield from _strings(v)
    elif isinstance(value, (list, tuple)):
        for v in value:
            yield from _strings(v)
    elif value is not None and not isinstance(value, bool):
        yield str(value)


def _numbers(value):
    return {match.group().replace(",", "") for s in _strings(value)
            for match in _NUMBER.finditer(s)}


def _entries(data):
    if isinstance(data.get("rows"), list):
        rows = [r for r in data["rows"] if isinstance(r, Mapping)]
        if isinstance(data.get("summary"), Mapping):
            rows.append(dict(data["summary"]))
        return rows
    if isinstance(data.get("matches"), list):
        rows = [r for r in data["matches"] if isinstance(r, Mapping)]
        # '등록되지 않음'도 확인된 사실이다(없는 시설을 없다고 말하는 근거).
        absence = {k: data[k] for k in ("missing", "unregistered") if data.get(k)}
        return rows + ([absence] if absence else [])
    # Vehicle identity and the fee record may carry separate lot/time fields.
    if "car_id" in data and ("location" in data or "fee" in data):
        return [dict(car_id=data["car_id"], **{k: v for k, v in record.items() if k != "car_id"})
                for record in (data.get("location"), data.get("fee")) if isinstance(record, Mapping)]
    # 가상 주차장 차량(번호판·뒤 4자리). 실물 car_id와 섞지 않는다.
    if "vehicle_number" in data and ("location" in data or "fee" in data):
        identity = {k: data[k] for k in ("vehicle_number", "plate_number") if data.get(k)}
        lot = (data.get("location") or {}).get("lot_id") if isinstance(data.get("location"), Mapping) else None
        if lot:
            identity["lot_id"] = lot   # 요금 기록도 그 차가 있는 주차장의 값이다(주차장별 값 대조)
        records = [dict(identity, **record) for record in (data.get("location"), data.get("fee"))
                   if isinstance(record, Mapping)]
        return records or [identity]
    return [data] if data else []


def _asked_lots(request):
    """사용자 문장에 나온 주차장(이름·별칭). 답에서 그 이름을 다시 말하는 것은 조회 없이도 허용한다."""
    return {lot for _, lot in _mentions(request or "")}


def _mentions(answer):
    found = []
    for lot_id, lot in config.catalog.lots().items():
        names = [lot.get("name", ""), *lot.get("aliases", []), *lot.get("loose_aliases", [])]
        names = {n for n in names if len(n) > 1}
        if names:
            # 짧은 별칭(2글자 이하: '기차', '공항', '센텀')은 다른 단어의 일부면 주차장이 아니다('전기차'의 '기차').
            pattern = "|".join((r"(?<![가-힣])" if len(v) <= 2 else "") + re.escape(v)
                               for v in sorted(names, key=len, reverse=True))
            found.extend((match.start(), lot_id) for match in re.finditer(pattern, answer, re.I))
    return sorted(found)


def _clauses(answer):
    return [c for c in _CLAUSE_SPLIT.split(answer) if c and c.strip()]


def action_claim(answer, observations=()):
    """하지 않은 실행을 했다고 말하거나 관리자 도착 등을 약속하면 사유를 돌려준다.
    이미 결제된 기록·이미 접수된 관리자 문의를 '되어 있다'고 설명하는 것은 허용한다(에이전트가 한 일로 말하지 않을 때)."""
    reads = [row["result"]["data"] for row in observations
             if readable(row) and row["tool"] not in {"ask_clarification", "propose_action"}]
    has_paid = any(isinstance(d.get("fee"), Mapping) and d["fee"].get("paid") is True for d in reads)
    has_call = any(d.get("has_call") is True for d in reads)
    if _PROMISE.search(answer):
        return "unsupported_promise"
    for clause in _clauses(answer):
        if not (_ACTION_WORD.search(clause) and _DONE_WORD.search(clause)) or _NOT_DONE.search(clause):
            continue
        agency = bool(_AGENCY.search(clause))
        if not agency and has_paid and _PAY_WORD.search(clause) and not _CALL_WORD.search(clause):
            continue
        if not agency and has_call and _CALL_WORD.search(clause) and not _PAY_WORD.search(clause):
            continue
        return "unsupported_action"
    return None


def no_tool_reply_ok(request, answer):
    """조회 없이 답해도 되는 짧은 대화(인사·감사·자기소개)인지. 운영 사실이나 실행 주장은 하나도 없어야 한다."""
    if not isinstance(answer, str) or not answer.strip():
        return False
    # 되묻기: '어디로 가시는지 알려주시면 찾아 드릴게요'처럼 사용자에게 묻기만 하는 답은 운영 사실을 말하지 않는다.
    # 차량 번호를 묻는 답은 제외한다(번호 입력 흐름은 규칙이 연다). 숫자·칸·상태·실행 주장 검사는 아래에서 그대로 받는다.
    asks_back = bool(_ASK_BACK.search(answer)) and not _ASKS_VEHICLE.search(answer) and not _COMPARES.search(answer)
    if len(answer) > (320 if asks_back else 220):
        return False
    if _NUMBER.search(answer) or _SLOT.search(answer):
        return False
    # 주차장 이름은 '쇼핑이면 신세계백화점주차장'처럼 성격 안내일 때만 허용: 거절·자료 없음 안내가 함께 있어야 한다
    # 운영 질문('어디가 싸?')에서 주차장 이름을 말하면 조회가 필요하다('개발원 정보는 없어요'도 틀린 말).
    # 단, '김해공항에 가시나요? 부산역에 가시나요?'처럼 여러 곳을 고르라고 묻는 문장의 이름은 주장이 아니다.
    named = _mentions(answer)
    if asks_back and named:
        outside = _mentions(_QUESTION_SENTENCE.sub(lambda m: " " * len(m[0]), answer))
        if not outside and len({lot for _, lot in named}) >= 2:
            named = []
    if named and (_DATA_QUESTION.search(request or "") or not _REFUSAL.search(answer)):
        return False
    # 운영 질문이어도 '실외 여부는 안내 자료에 없어요'처럼 자료 없음만 말하는 답은 허용한다(주차장·숫자·상태 주장 없음).
    if _DATA_QUESTION.search(request or "") and not _REFUSAL.search(answer) and not asks_back:
        return False
    # '날씨는 확인해 드릴 수 없어요'처럼 능력 밖이라는 안내는 주차 상태 주장이 아니다.
    if _STATE_CLAIM.search(_OFFER.sub(" ", _REFUSAL.sub(" ", answer))) or action_claim(answer) or _ACTION_DONE.search(answer):
        return False
    return True


# 사용자에게 되묻는 답: 물음표로 끝나는 문장이 있거나 '알려 주시면/말씀해 주세요'로 정보를 청한다.
_ASK_BACK = re.compile(r"\?|(?:알려|말씀해|골라|선택해)\s*주(?:시면|세요|실래요|시겠어요)")
_QUESTION_SENTENCE = re.compile(r"[^.!?。\n]*\?")   # 물음표로 끝나는 문장 하나
_ASKS_VEHICLE = re.compile(r"차량\s*번호|번호판|두\s*자리|스티커")
# 되묻는 척하며 비교 결과를 말하는 답('개발원이 제일 싼데 거기로 갈까요?')은 되묻기로 보지 않는다.
_COMPARES = re.compile(r"제일|가장|더\s|보다|싼|저렴|비싼|가까운|가깝|빠른|빠르|널널|한가")
_REFUSAL = re.compile(r"(?:확인|안내|알려|답|도와|제공|예측)\S*\s*(?:드릴|드리기|해\s*드릴|할)?\s*(?:수\s*)?"
                      r"(?:없어요|없습니다|어려워요|어렵습니다|없지만|없어서)|(?:정보|기능)(?:는|이|가)?\s*없(?:어요|습니다|어서|지만)|"
                      # '안내 자료에 없어요'(정보 없음)는 운영 상태 주장이 아니다.
                      r"(?:자료|안내\s*자료|정보)(?:에|에는|는|가)?\s*(?:아직\s*)?없(?:어요|습니다|어서|지만)")
# '확인해 드릴 수 있어요'(도움 제안)도 상태 주장이 아니다. 단, 이것만으로는 운영 질문의 답이 되지 않는다.
_OFFER = re.compile(r"(?:확인|조회|안내|알려|도와|찾아)\S*\s*(?:드릴|드려\s*볼|볼)?\s*수\s*(?:있어요|있습니다)")


# '아래 버튼을 누르면 출차를 진행할 수 있어요', '차단기를 열어 드릴게요' — 챗봇에 없는 기능(봇 점검 11/10 🟠2)
_EXIT_CLAIM = re.compile(r"(?:버튼|아래|여기서|제가|챗봇).{0,15}출차(?:를|가)?\s*(?:진행|처리|할\s*수|하실\s*수)|"
                         r"출차(?:를)?\s*(?:도와|처리해|진행해|해)\s*드|차단기.{0,8}(?:열어|올려)\s*드|출구(?:를)?\s*(?:열어|개방해)\s*드|\[\s*출차")
# 결제가 끝난 차량을 아직 낼 돈이 있는 것처럼 말함(봇 점검 11/10 🟠1: '현재 요금은 3,500원이에요')
_UNPAID_TALK = re.compile(r"현재\s*요금|지금\s*나가면|지금까지\s*요금|내셔야|내야\s*(?:할|해|하는)|결제하(?:시면|셔야)|결제할\s*금액|미결제")
_PAID_SAID = re.compile(r"이미\s*(?:결제|정산)|결제(?:가|는|를)?\s*(?:완료|끝|마치|됐|되었|하셨|했)|정산(?:이|은)?\s*(?:완료|끝|됐)")
# 장소의 위치·방향 주장('영화관 방향 표시는 몰 쪽 A·C구역', '영화관은 2층')
_PLACE_CLAIM = re.compile(r"구역|쪽|방향|층|근처|가까|위치|옆|앞|건너|표시")


_POLICY_PERMIT = re.compile(r"(?:일반\s*(?:차|차량)|전기차가?\s*아니|비전기차|장애인\s*차량이?\s*아니)[^.!?\n]{0,40}"
                            r"(?:할\s*수\s*있|가능|돼요|됩니다|괜찮|대셔도|세우셔도|하셔도)")
_POLICY_DENY = re.compile(r"없|안\s*돼|안\s*됩|불가|못|금지|안\s*되")


def contradicts_restricted_policy(answer):
    """팀 결정 D-05(일반차는 전기차·장애인 전용 칸 이용 불가)와 반대로 말하는지."""
    for clause in _clauses(answer):
        if _POLICY_PERMIT.search(clause) and not _POLICY_DENY.search(clause):
            return True
    return False


REPAIR_HINTS = {
    "unsupported_number": "답에 이번 조회 결과에 없는 숫자가 있습니다. 차이·개수·경과 시간·예상 요금은 compare_lots, vehicle_info의 *_text, estimate_fee 결과 값만 옮겨 쓰세요. 필요하면 그 도구를 먼저 호출하세요.",
    "unsupported_location": "답에 조회되지 않은 칸 번호가 있습니다. 조회 결과의 칸 번호만 쓰세요.",
    "unsupported_lot": "답에 이번에 조회하지 않은 주차장이 있습니다. 조회한 주차장만 말하거나 그 주차장을 조회하세요.",
    "mismatched_lot_value": "어떤 주차장의 숫자를 다른 주차장 값으로 말했습니다. 주차장마다 조회 결과의 값을 그대로 짝지어 쓰세요. 차이는 compare_lots 요약 값을 쓰세요.",
    "mismatched_rate": "'N분에 M원' 표현이 요금 정책과 맞지 않습니다. policy_text 또는 정책 값을 그대로 쓰세요.",
    "contradictory_availability": "조회 결과에 빈자리가 있는데 만차·자리 없음이라고 했습니다. 조회 결과대로 다시 쓰세요.",
    "unsupported_action": "결제·관리자 호출·접수·변경을 실제로 하지 않았는데 한 것처럼 말했습니다. 실행은 사용자가 화면 버튼으로 확인해야 진행된다고 안내하세요.",
    "unsupported_payment": "결제가 완료되지 않았는데 완료된 것처럼 말했습니다. 결제는 사용자가 승인해야 진행된다고 안내하세요.",
    "unsupported_promise": "관리자·직원의 도착이나 연락을 약속할 수 없습니다. 약속 없이 필요한 안내만 하세요.",
    "repeated": "직전 답과 같은 문장입니다. 새로 조회한 내용을 바탕으로 다른 표현으로 답하세요.",
    "missing_goal": "질문에서 요청한 정보 중 일부를 조회하지 않았습니다.",
    "no_evidence": "조회 없이 빈자리·요금·추천 같은 운영 정보를 말했습니다. 목적에 맞는 주차장(주차장 성격 참고)을 parking_status나 compare_lots로 먼저 조회한 뒤 그 값으로 답하세요. 인사나 범위 밖 질문이면 운영 정보 없이 짧게 답하세요.",
    "empty_answer": "답이 비어 있습니다. 질문에 맞는 조회를 한 뒤 사용자에게 보일 답을 쓰세요.",
    "paid_not_said": "이 차량은 이미 결제가 끝났습니다(vehicle_info의 fee.paid). '이미 결제했어요'와 결제 금액을 말하고, '현재 요금'·'지금 나가면' 같은 미결제 표현은 쓰지 마세요.",
    "unsupported_exit": "출차·차단기 개방·입출차 처리는 챗봇이 할 수 없습니다. 결제가 끝났으면 '출구에서 현장 안내를 따라 주세요'라고 하고, 버튼 이름을 본문에 쓰지 마세요.",
    "unsupported_place": "안내 자료에 없는 장소(not_in_guide)의 위치·방향·가까운 구역을 말했습니다. 그 장소는 '안내 자료에 없어요'라고만 하고, 조회한 빈자리나 자료에 있는 시설(엘리베이터 등)로 도우세요.",
}


def check_answer(answer, request, observations):
    """Return a reason for a deterministic repair/fallback, else None."""
    reads = [row["result"]["data"] for row in observations
             if readable(row) and row["tool"] not in {"ask_clarification", "propose_action"}]
    entries = [row for data in reads for row in _entries(data)]
    proposals = [row for row in observations if row["tool"] == "propose_action" and readable(row)]
    if not entries and not proposals:
        return "no_evidence"
    # A proposal cannot prove that a new action executed. A previously paid fee
    # can explain status, but still cannot prove that this assistant issued it.
    claim = action_claim(answer, observations)
    if claim:
        return claim
    has_paid = any(isinstance(data.get("fee"), dict) and data["fee"].get("paid") is True
                   for data in reads)
    if _ACTION_DONE.search(answer) or _GUIDANCE_DONE.search(answer):
        if not (has_paid and not re.search(r"호출|문의|목적지|예약|환불", answer)):
            return "unsupported_action"
    if _PAID.search(_NEGATED_DONE.sub("", answer)) and not has_paid:
        return "unsupported_payment"

    facts = set(_strings(entries))
    # Accept a horizon that the user actually asked about; it is never a forecast.
    request_horizons = {(m[1], m[2]) for m in _HORIZON.finditer(request)}
    checked_answer = _HORIZON.sub(lambda m: "" if (m[1], m[2]) in request_horizons else m[0], answer)
    allowed_numbers = _numbers(list(facts))
    for m in _NUMBER.finditer(checked_answer):
        if m[0].replace(",", "") not in allowed_numbers:
            return "unsupported_number"
    if any(not any(m[1].upper() in fact.upper() for fact in facts) for m in _SLOT.finditer(answer)):
        return "unsupported_location"

    for sentence in re.split(r"(?<=[.!?。])\s+|\n+", answer):
        if _EXIT_CLAIM.search(sentence) and not re.search(r"수\s*없|못\s*해|못합|어려|불가|않", sentence):
            return "unsupported_exit"
    paid_cars = [data["fee"] for data in reads if isinstance(data.get("fee"), dict) and data["fee"].get("paid") is True]
    unpaid_cars = [data["fee"] for data in reads if isinstance(data.get("fee"), dict) and data["fee"].get("paid") is False]
    if paid_cars and not unpaid_cars and _UNPAID_TALK.search(answer) and not _PAID_SAID.search(answer):
        return "paid_not_said"
    missing_places = {place for observation in observations
                      if observation["tool"] == "search_knowledge" and readable(observation)
                      for place in observation["result"]["data"].get("not_in_guide") or []}
    for sentence in re.split(r"(?<=[.!?。])\s+|\n+", answer):
        if (any(place in sentence for place in missing_places) and _PLACE_CLAIM.search(sentence)
                and not _REFUSAL.search(sentence) and not re.search(r"알\s*수\s*없|모르|확인\s*(?:할|드릴)\s*수\s*없", sentence)):
            return "unsupported_place"

    lot_entries = [row for row in entries if row.get("lot_id")]
    # 자료 없음만 말하는 문장(숫자·상태 주장 없음)의 주차장 이름은 조회 대상이 아니어도 된다(봇 점검 11/10: 운영 시간)
    # 위치 기반 검사(만차·숫자를 '바로 앞 주차장'에 묶음)와 어긋나지 않게, 지우지 않고 같은 길이의 공백으로 가린다.
    claimed = list(answer)
    for sentence in re.finditer(r"[^.!?。\n]+[.!?。]*", answer):
        s = sentence[0]
        if _REFUSAL.search(s) and not _NUMBER.search(s) and not _STATE_CLAIM.search(_REFUSAL.sub(" ", s)):
            claimed[sentence.start():sentence.end()] = " " * len(s)
    mentions = _mentions("".join(claimed))
    if lot_entries:
        lot_ids = {row["lot_id"] for row in lot_entries} | _asked_lots(request)
        if any(lot not in lot_ids for _, lot in mentions):
            return "unsupported_lot"
    status_rows = [row for observation in observations
                   if observation["tool"] in {"parking_status", "compare_lots"} and readable(observation)
                   for row in observation["result"]["data"].get("rows") or [] if isinstance(row, Mapping)]
    for claim in _FULL_CLAIM.finditer(answer):
        preceding = [lot for start, lot in mentions if start <= claim.start()]
        scoped = [row for row in status_rows if not preceding or row["lot_id"] == preceding[-1]]
        if scoped and all(row.get("availability") == "available" for row in scoped):
            return "contradictory_availability"
    # 비교 숫자는 코드가 계산한 차이 값과만 맞춘다("1,000원 더 싸요"가 우연히 다른 값과 겹쳐 통과하지 않게).
    diffs = {str(row[k]) for row in entries for k in _DIFF_KEYS if type(row.get(k)) is int}
    diffs |= {n for row in entries for note in (row.get("notes") or []) if isinstance(note, str) for n in _numbers(note)}
    # 'N분에 M원'은 같은 정책의 (기본 시간, 기본요금) 또는 (추가 단위, 추가요금) 짝이어야 한다.
    pairs = set()
    for row in entries:
        if all(type(row.get(k)) is int for k in ("base_minutes", "base_fee")):
            pairs.add((row.get("lot_id"), row["base_minutes"], row["base_fee"]))
        if all(type(row.get(k)) is int for k in ("unit_minutes", "extra_fee")):
            pairs.add((row.get("lot_id"), row["unit_minutes"], row["extra_fee"]))
        for text in _strings(row):
            for pm in _RATE_PAIR.finditer(text):
                pairs.add((row.get("lot_id"), int(pm[1]), int(pm[2].replace(",", ""))))
    for pm in _RATE_PAIR.finditer(answer):
        preceding = [lot for start, lot in mentions if start <= pm.start()]
        lot = preceding[-1] if preceding else None
        wanted = (int(pm[1]), int(pm[2].replace(",", "")))
        if not any((lot is None or row_lot in (None, lot)) and (minutes, fee) == wanted for row_lot, minutes, fee in pairs):
            return "mismatched_rate"
    # Bind a displayed operational quantity to its preceding lot when its row is
    # available. This catches reassigning D's fee or count to C in a comparison.
    for m in _CLAIM_UNITS.finditer(answer):
        value = m[1].replace(",", "")
        if _COMPARE_AFTER.match(answer, m.end()) or _COMPARE_BEFORE.search(answer[max(0, m.start() - 8):m.start()]):
            if value not in diffs:
                return "mismatched_lot_value"
            continue
        preceding = [lot for start, lot in mentions if start <= m.start()]
        if not preceding:
            continue
        selected = [row for row in lot_entries if row["lot_id"] == preceding[-1]]
        if selected and value not in _numbers(selected):
            return "mismatched_lot_value"
    return None
