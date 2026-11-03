"""자유 입력 문장 → 의도(intent) + 값(params).

1단계 규칙(정규식·키워드)으로 먼저 처리하고, 규칙이 모르면 LLM(llm.py)에 넘긴다.
LLM은 '분류기'로만 쓰고, 답변 문장은 templates.py가 만든다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import config

# ------------------------------------------------------------ 값 추출
_KO_UNITS = ["", "일", "이", "삼", "사", "오", "육", "칠", "팔", "구"]


def _ko_number_map() -> dict[str, int]:
    m = {}
    for n in range(config.VEHICLE_MIN, config.VEHICLE_MAX + 1):
        if n < 10:
            word = _KO_UNITS[n]
        else:
            tens, ones = divmod(n, 10)
            word = ("" if tens == 1 else _KO_UNITS[tens]) + "십" + _KO_UNITS[ones]
        m[word] = n
    return m


KO_NUM = _ko_number_map()
_KO_PATTERN = "|".join(sorted(KO_NUM, key=len, reverse=True))

RE_NUM_BEON = re.compile(r"(?<!\d)(\d{1,2})\s*번")                  # 7번, 07번
RE_NUM_CAR = re.compile(r"(?<!\d)(\d{1,2})\s*(?:차량|차)(?!단)")      # 07 차, 7차량 (차단기 제외)
RE_NUM_ONLY = re.compile(r"^\s*(\d{1,2})\s*(?:번)?\s*(?:이요|이에요|예요|요)?\s*[.!]?\s*$")
RE_KO_CAR = re.compile(r"(?<![가-힣])([일이삼사오육칠팔구십]+)\s*번\s*(?:차량|차)")
RE_KO_ONLY = re.compile(r"^\s*([일이삼사오육칠팔구십]+)\s*번\s*(?:이요|요)?\s*$")


def extract_vehicle(text: str) -> tuple[str | None, bool]:
    """(차량번호 'NN', 번호만 단독으로 말했는지). 범위 밖이면 'invalid'."""
    correction = re.search(r"(?:\d{1,2}\s*번(?:\s*차(?:량)?)?\s*)말고\s*(\d{1,2})\s*번", text)
    if correction:
        n = int(correction[1])
        rest = text[correction.end():].strip()
        only = bool(re.fullmatch(r"(?:차(?:량)?)?(?:이에요|이요|예요|요)?[.!]?", rest))
        return (f"{n:02d}" if config.VEHICLE_MIN <= n <= config.VEHICLE_MAX else "invalid"), only
    if re.search(r"\d{1,2}\s*번(?:\s*차(?:량)?)?\s*말고", text):
        return "missing", False  # 거절한 번호는 새 대상 후보로 사용하지 않는다.
    numbers = {int(m[1]) for m in RE_NUM_BEON.finditer(text)}
    if len(numbers) > 1:
        return "ambiguous", False
    for rx, only in ((RE_NUM_ONLY, True), (RE_NUM_BEON, False), (RE_NUM_CAR, False)):
        m = rx.search(text)
        if m:
            n = int(m.group(1))
            return (f"{n:02d}" if config.VEHICLE_MIN <= n <= config.VEHICLE_MAX else "invalid"), (only or bool(re.fullmatch(r"아니(?:요)?\s*\d{1,2}\s*번(?:이에요|이요|예요|요)?[.!]?", text.strip())))
    for rx, only in ((RE_KO_ONLY, True), (RE_KO_CAR, False)):
        m = rx.search(text)
        if m:
            n = KO_NUM.get(m.group(1))
            return (f"{n:02d}" if n else "invalid"), only
    return None, False


def extract_lot(text: str) -> str | None:
    if "말고" in text:
        replacement=extract_lot(text.rsplit("말고",1)[1])
        if replacement:return replacement
    found=[]
    for lot in config.catalog.lots().values():
        key=lot["lot_id"]
        matched=bool(re.search(r"(?<![a-z0-9_])"+re.escape(key)+r"(?![a-z0-9_])",text,re.I))
        matched=matched or any(re.search(r"(?<![가-힣])"+re.escape(alias)+r"\s*(?:주차장|구역|동)",text) for alias in lot.get("aliases",[]))
        if matched or lot["name"] in text:found.append(key)
    return found[0] if len(found)==1 else ("ambiguous" if found else None)


def extract_zone(text):
    for z in config.catalog.zones():
        for alias in z.get("aliases",[]):
            rx = r"(?<![a-z])"+re.escape(alias)+r"(?![a-z])" if alias.isascii() else re.escape(alias)
            if re.search(rx,text,re.I):return z["key"]
    return None


# ------------------------------------------------------------ 의도 규칙 (위에서부터 먼저 맞는 것)
RULES: list[tuple[str, re.Pattern]] = [
    ("get_empty_spots", re.compile(r"몇\s*번째|대기|자리\s*알림|알림\s*(?:꺼|끄|취소)|자리\s*(?:가\s*)?(?:나면|생기면)|빈자리\s*생기면|기다릴게")),
    ("get_fee", re.compile(r"(?:결제|정산)\s*말고\s*(?:요금|금액|주차비)")),
    ("call_status", re.compile(r"(?:문의|호출).{0,12}(?:처리|됐|되었)|(?:관리자|직원).{0,12}(?:불렀|언제|확인했|호출됐)")),
    ("cancel_action", re.compile(r"^(?:일단\s*)?취소(?:할래요|해줘|해주세요)?[.!?]?$|(?:결제|정산|관리자|직원|상담원|호출).{0,15}(?:하지\s*말|말아|안\s*해도|필요\s*없|부르지\s*(?:마|말))")),
    ("call_status", re.compile(r"(?:문의|호출)\s*(?:상태|진행|현황)|관리자.{0,5}확인했")),
    ("change_vehicle", re.compile(r"차량\s*변경|내\s*차\s*바꿔|(?:차|차량).{0,8}(?:로|으로)\s*바꿔")),
    ("confirm_yes", re.compile(r"^\s*(네\s*[,，]?\s*(?:맞아요|맞습니다)|네|예|응|어|맞아|맞아요|맞습니다|ㅇㅇ|ㅇㅋ|yes|ok|오케이)\s*[.!]?\s*$", re.I)),
    ("confirm_no", re.compile(r"^\s*(아뇨\s*틀렸어요|아니에요|아니|아니요|아뇨|틀려|틀렸어|no)\s*[.!]?\s*$", re.I)),
    ("cancel_action", re.compile(r"^\s*(취소|그만|잘못\s*말했어|진행\s*중\s*요청\s*취소)[.!?]?\s*$|(?:결제|정산|관리자|호출).{0,15}(취소|하지\s*마|안\s*할|부르지\s*(?:마|말))")),
    ("call_admin", re.compile(r"관리자|직원|상담원|사람\s*(좀\s*)?(불러|연결)|호출")),
    ("check_exit_issue", re.compile(r"(출차|나가|출구|차단기|빠져).{0,8}(안\s*돼|안\s*되|못|막|안\s*열|안\s*올라|먹통|오류|에러|안\s*나가|안됨)|안\s*열려|못\s*나가")),
    ("evacuation_guide", re.compile(r"대피|피난|어디로\s*(가|피해)|도망")),
    ("alert_near_my_car", re.compile(r"(내\s*차|제\s*차).{0,10}(근처|가까|옆|괜찮|위험|안전)|(근처|가까).{0,6}(경보|화재|불)")),
    ("get_alerts", re.compile(r"경보|화재|불\s*났|불이|연기|위험|비상|이상\s*있")),
    ("find_my_car", re.compile(r"(이용|주차|내)\s*(현황|상태)|진행\s*(상황|상태)|어디까지\s*(됐|했)")),
    ("get_fee", re.compile(r"(?:결제|정산).{0,8}(상태|했|됐|된|완료|여부|확인|내역)")),
    ("pay_fee", re.compile(r"결제|정산|계산\s*(할|해)|지불|내고\s*싶|낼게|낼래|납부")),
    ("fee_rule", re.compile(r"요금\s*(기준|표|체계|정책|계산\s*기준)|얼마씩|기본\s*요금|무료\s*시간|무료\s*주차|몇\s*분.{0,4}무료|요금.{0,8}어떻게|\d+\s*분에\s*얼마|시간당")),
    ("get_fee", re.compile(r"요금|얼마|비용|주차비|돈|금액")),
    ("find_my_car", re.compile(r"(내|제)\s*차.{0,6}(어디|위치|찾|몇\s*번)|어디\s*(에\s*)?(주차|댔|세웠)|차(?:량)?\s*(위치|찾|어디)|차가?\s*안\s*보")),
    ("get_empty_spots", re.compile(r"빈\s*자리|빈\s*칸|자리|비었|비어|주차\s*(가능|할\s*수|할\s*(곳|데))|만차|남았|남은|공간|댈\s*(곳|데)")),
    ("thanks", re.compile(r"고마|감사|땡큐|thank", re.I)),
    ("help", re.compile(r"도움|뭘\s*할|뭐\s*할|뭐\s*해|기능|사용법|도와|help|안녕|하이|hello", re.I)),
]

KNOWN_INTENTS = {name for name, _ in RULES} | {"unknown"}

# 실행 대상별 언어 보호. LLM 경로에도 같은 검사를 적용한다.
ADMIN = re.compile(r"담당자|관리자|직원|상담원|호출|사람.{0,4}연결")
ADMIN_NEGATIVE = re.compile(r"(?:보내|불러|부르|호출\s*(?:해|하)?|연결\s*(?:해|하)?)\s*(?:주\s*)?지\s*(?:마|말|않)|(?:관리자|직원|상담원).{0,12}(?:필요\s*없|안\s*부를|안\s*불러도|하지\s*않|안\s*연결)")
ADMIN_INSTEAD = re.compile(r"(?:관리자|직원|상담원|호출)(?:은|는|을|를)?\s*말고\s*")
ADMIN_REQUEST = re.compile(r"불러|불러\s*주|부탁|연결해|연결\s*해|호출해|호출\s*해|호출\s*부탁")
ADMIN_QUESTION = re.compile(r"하면|비용|돈이|유료|무료|가능한|되나요|방법|어떻게")
PAY_PROBLEM = re.compile(r"(?:결제|정산).{0,8}(?:안\s*돼|안\s*되|안됨|오류|에러|실패)")
PAY_METHOD = re.compile(r"(?:카드|현금|간편).{0,8}(?:결제|정산).{0,6}(?:돼|되|가능)|결제\s*수단")
PAY_NEGATIVE = re.compile(r"(?:결제|정산).{0,15}(?:하지\s*마|하지\s*말|말아|안\s*할|필요\s*없|안\s*해도|취소)")


def guarded_intent(text):
    """제한된 복합 요청만 지원. 실제 경보 변경은 센서/시연 API만 담당한다."""
    admin = bool(ADMIN.search(text))
    admin_no = bool(ADMIN_NEGATIVE.search(text))
    if admin_no and re.search(r"대피|피난", text):
        return "evacuation_guide"
    if admin and not admin_no and ADMIN_REQUEST.search(text) and not ADMIN_QUESTION.search(text):
        return "call_admin"
    if admin and re.search(r"연기|불\s*났|화재|경보", text):
        return "get_alerts"
    if admin_no:
        return "cancel_action"
    if admin and ADMIN_QUESTION.search(text):
        return "admin_help"
    if admin and re.search(r"불렀|아직\s*안\s*왔|언제|확인했|호출됐", text):
        return "call_status"
    if re.search(r"(?:결제|정산).{0,6}(?:안\s*해도|안\s*하고|하지\s*않고).{0,12}(?:나가|나갈|출차).{0,8}(?:수|돼|되|가능)", text):
        return "fee_rule"
    if PAY_METHOD.search(text):
        return "payment_help"
    if PAY_PROBLEM.search(text):
        return "payment_issue"
    if PAY_NEGATIVE.search(text):
        return "cancel_action"
    if re.search(r"(?:차(?:가|량이)?\s*)?안\s*빠져|못\s*빠져", text):
        return "check_exit_issue"
    if re.search(r"불\s*(?:이\s*)?안\s*났", text):
        return "get_alerts"
    return None


KNOWN_INTENTS.update({"admin_help", "payment_help", "payment_issue", "set_destination", "cancel_destination"})


@dataclass
class ParseResult:
    intent: str
    params: dict = field(default_factory=dict)
    source: str = "rule"          # rule / llm / none
    vehicle_only: bool = False    # "07"처럼 번호만 말한 경우


def parse_rules(text: str) -> ParseResult:
    excluded = ADMIN_INSTEAD.search(text)
    if excluded:
        tail = text[excluded.end():].strip()
        # '관리자 말고 요금'은 남은 요청만 분류한다. 앞의 대상은 실행하지 않는다.
        return parse_rules(tail) if tail else ParseResult("cancel_action", {"cancel_target": "admin"})
    vehicle, only = extract_vehicle(text)
    lot = extract_lot(text)
    zone = extract_zone(text)
    params = {}
    if re.search(r"몇\s*번째|대기|자리\s*알림|알림\s*(?:꺼|끄|취소)|자리\s*(?:가\s*)?(?:나면|생기면)|빈자리\s*생기면|기다릴게", text):
        params["vacancy_notice"] = True
    if vehicle:
        params["vehicle_no"] = vehicle
    if lot:
        params["lot_id"] = lot

    if zone:
        params["zone"] = zone
    if re.search(r"목적지.{0,6}(?:취소|해제|지워)|안내\s*(?:하지\s*(?:마|말)|취소)|(?:로|으로)\s*(?:가지\s*(?:마|말)|안\s*갈)",text):
        return ParseResult("cancel_destination",params,"rule")
    if lot and re.search(r"안내|(?:로|으로)\s*(?:갈|가겠|가줘)",text) and not re.search(r"경보|대피|호출|관리자|결제",text):
        return ParseResult("set_destination",params,"rule")
    guarded = guarded_intent(text)
    if guarded:
        if guarded == "cancel_action":
            if ADMIN.search(text) or ADMIN_NEGATIVE.search(text):
                params["cancel_target"] = "admin"
            elif re.search(r"결제|정산", text):
                params["cancel_target"] = "payment"
                params["refund_info"] = bool(re.search(r"취소|환불", text))
        return ParseResult(guarded, params, "rule")

    for intent, rx in RULES:
        if rx.search(text):
            return ParseResult(intent, params, "rule")
    if zone:
        return ParseResult("get_empty_spots",params,"rule")
    if vehicle:
        return ParseResult("unknown", params, "rule", vehicle_only=only)
    if lot:  # "B 주차장은?" → 빈자리로 해석
        return ParseResult("get_empty_spots", params, "rule")
    return ParseResult("unknown", params, "none")


def parse(text: str) -> ParseResult:
    result = parse_rules(text)
    if result.intent == "unknown" and not result.vehicle_only and not ADMIN_INSTEAD.search(text):
        from .llm import classify  # 규칙이 모를 때만 LLM 사용
        llm_result = classify(text)
        if (isinstance(llm_result, dict)
                and isinstance(llm_result.get("intent"), str)
                and llm_result["intent"] in KNOWN_INTENTS - {"confirm_yes", "confirm_no"}):
            raw = llm_result.get("params") or {}
            if not isinstance(raw, dict):
                return result
            params = {}
            lot = raw.get("lot_id")
            if config.catalog.canonical_id(lot):
                params["lot_id"] = config.catalog.canonical_id(lot)
            vehicle = raw.get("vehicle_no")
            if isinstance(vehicle, (str, int)) and not isinstance(vehicle, bool):
                value = str(vehicle)
                if re.fullmatch(r"[0-9]{1,2}", value):
                    n = int(value)
                    params["vehicle_no"] = f"{n:02d}" if config.VEHICLE_MIN <= n <= config.VEHICLE_MAX else "invalid"
            params.update(result.params)  # 사용자 문장에서 추출한 값을 우선
            intent = guarded_intent(text) or llm_result["intent"]
            # LLM은 사용자가 쓰지 않은 실행 대상 번호를 만들어 넣을 수 없다.
            if intent in ("pay_fee", "call_admin"):
                params.pop("vehicle_no", None)
                params.update(result.params)
            return ParseResult(intent, params, "llm")
    return result
