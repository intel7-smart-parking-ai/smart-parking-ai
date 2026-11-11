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
    """한글 숫자 1~99. 한 자리 말도 인식해 '두 자리로 말해 달라'고 안내하기 위해 포함한다."""
    m = {}
    for n in range(1, 100):
        if n < 10:
            word = _KO_UNITS[n]
        else:
            tens, ones = divmod(n, 10)
            word = ("" if tens == 1 else _KO_UNITS[tens]) + "십" + _KO_UNITS[ones]
        m[word] = n
    return m


KO_NUM = _ko_number_map()
_KO_PATTERN = "|".join(sorted(KO_NUM, key=len, reverse=True))

# 칸 이름(도면 번호 'B-06', 'B6')과 '12번 칸'의 숫자는 차량 번호가 아니다.
# 하이픈 표기(B-06)는 항상 칸, 붙여 쓴 표기(B6)는 칸 맥락일 때만 칸으로 본다('P9 주차장' 같은 주차장 ID와 구분).
_SPACE_LABEL = re.compile(r"(?<![A-Za-z0-9])[A-Za-z]\s*-\s*\d{1,2}(?!\d)|(?<![A-Za-z0-9])[A-Za-z]\d{1,2}(?!\d)(?=\s*(?:칸|자리|비었|비어|번\s*칸))")
_NOT_CAR = r"(?!\s*(?:칸|자리|층|분|시|%|원))"
RE_NUM_BEON = re.compile(r"(?<![\d.])(\d{1,2})\s*번" + _NOT_CAR)            # 27번
RE_NUM_CAR = re.compile(r"(?<![\d.])(\d{1,2})\s*(?:차량|차)(?!단)")        # 27 차, 27차량 (차단기 제외)
RE_NUM_ONLY = re.compile(r"^\s*(\d{1,2})\s*(?:번)?\s*(?:이요|이에요|예요|요)?\s*[.!]?\s*$")
RE_KO_CAR = re.compile(r"(?<![가-힣])([일이삼사오육칠팔구십]+)\s*번\s*(?:차량|차)")
RE_KO_ONLY = re.compile(r"^\s*([일이삼사오육칠팔구십]+)\s*번\s*(?:이요|요)?\s*$")
# Read the entire spoken number before '번'. Unsupported/mixed forms must not
# expose a valid suffix (e.g. '삼 십 번' -> 10, '백 십일 번' -> 11).
_SPOKEN_NUMBER_CHARS = "영공일이삼사오육칠팔구십백천만억점.0-9"
RE_SPOKEN_NUMBER = re.compile(
    r"(?<![가-힣A-Za-z0-9.])(?P<sign>(?:마이너스|플러스|[+-])\s*)?"
    rf"(?P<number>[{_SPOKEN_NUMBER_CHARS}](?:\s*[{_SPOKEN_NUMBER_CHARS}])*)\s*번" + _NOT_CAR
)
# 가상 주차장 차량: 번호판 '17가1724' 또는 화면 표시 4자리 '1724' (D-08)
RE_PLATE = re.compile(r"(?<!\d)(\d{2,3}[가-힣]\d{4})(?!\d)")
RE_VIRTUAL4 = re.compile(r"(?<![\d.])(\d{4})(?!\d)(?!\s*(?:년|원|분|시|초|%|층|칸|월|일))")


def normalize_car_id(value) -> str | None:
    """실물 차량 번호 규칙(D-06): '10'~'99' 두 자리 문자열. 맞지 않으면 None. int 변환 결과를 저장하지 않는다."""
    if not isinstance(value, str) or not re.fullmatch(r"[1-9][0-9]", value):
        return None
    return value if config.VEHICLE_MIN <= int(value) <= config.VEHICLE_MAX else None


def _classify(token: str) -> str:
    """숫자 문자열 → 정상 번호 / 'short'(한 자리·0으로 시작) / 'invalid'(그 밖)."""
    if normalize_car_id(token):
        return token
    if len(token) == 1 or token.startswith("0"):
        return "short"
    return "invalid"


def _classify_number(n: int | None) -> str:
    if n is None:
        return "invalid"
    return "short" if n < 10 else _classify(str(n))


def mask_spaces(text: str) -> str:
    return _SPACE_LABEL.sub(" ", text)


def extract_virtual(text: str) -> str | None:
    """가상 차량 번호판·4자리 번호를 문자열 그대로(앞자리 0 보존) 돌려준다."""
    plate = RE_PLATE.search(text)
    if plate:
        return plate[1]
    match = RE_VIRTUAL4.search(text)
    # 번호만 있고 무엇을 묻는지 알 수 없으면 가상 차량으로 보지 않는다. 출차 질문('1780 출차해도 돼?')도 차량 질문이다.
    # 질문 번역이 실패해 외국어 원문이 그대로 들어온 경우('Find car 8928')도 받는다(봇 점검 11/11).
    if match and (re.search(r"\d{4}\s*(?:번|차)", text) or re.fullmatch(r"\s*\d{4}\s*", text)
                  or re.search(r"어디|위치|요금|얼마|결제|정산|찾|출차|나가|나갈|빼|차량|내\s*차", text)
                  or re.search(r"(?i)\b(?:car|vehicle|plate|find|where|fee|pay|paid|payment|parked|exit|leave)\b"
                               r"|车|車|停|料金|どこ|支付|付款|支払|精算", text)):
        return match[1]
    return None


_FULLWIDTH = str.maketrans("０１２３４５６７８９", "0123456789")


_NATIVE_TENS = {"열": 10, "스물": 20, "서른": 30, "마흔": 40, "쉰": 50, "예순": 60, "일흔": 70, "여든": 80, "아흔": 90}
_NATIVE_UNITS = {"하나": 1, "한": 1, "둘": 2, "두": 2, "셋": 3, "세": 3, "넷": 4, "네": 4, "다섯": 5, "여섯": 6,
                 "일곱": 7, "여덟": 8, "아홉": 9}
# '스물세번 차', '서른두 번 차량'. 뒤에 차·차량이 올 때만 바꾼다('열 번 물어봤어'는 차량 번호가 아님).
RE_NATIVE_CAR = re.compile(r"(?<![가-힣])(열|스물|서른|마흔|쉰|예순|일흔|여든|아흔)\s*"
                           r"(하나|한|둘|두|셋|세|넷|네|다섯|여섯|일곱|여덟|아홉)?\s*번(?=\s*(?:차량|차)(?!단))")


def _native_car_numbers(text: str) -> str:
    return RE_NATIVE_CAR.sub(lambda m: f"{_NATIVE_TENS[m[1]] + _NATIVE_UNITS.get(m[2] or '', 0)}번", text)


def _spoken_vehicle_text(text: str) -> tuple[str, bool]:
    """Normalize scoped Sino-Korean numbers; preserve the user's stored text.

    Only two-digit cardinal readings are expanded. Single-syllable words such
    as '이번' keep their existing handling, so '이번 요금' is not car number 2.
    Digit-by-digit, signed, decimal, mixed and out-of-range readings are rejected
    as a whole instead of falling back to a previously selected vehicle.
    """
    invalid = False
    text = _native_car_numbers(text)

    def replace(match):
        nonlocal invalid
        token = re.sub(r"\s+", "", match["number"])
        if match["sign"]:
            invalid = True
        elif token.isascii() and token.isdigit():
            invalid |= bool(re.search(r"\s", match["number"]))
        else:
            value = KO_NUM.get(token)
            if value is None:
                invalid = True
            elif value >= 10:
                return f"{value}번"
        return match[0]

    return RE_SPOKEN_NUMBER.sub(replace, text), invalid


def explicit_cars(text: str) -> list:
    """문장에 사용자가 직접 쓴 두 자리 차량 번호 전부('23번이랑 41번' → ['23', '41']). 범위 밖·잘못된 번호는 뺀다."""
    if not isinstance(text, str):
        return []
    text = mask_spaces(RE_PLATE.sub(" ", text.translate(_FULLWIDTH)))
    text, _ = _spoken_vehicle_text(text)
    found = [m[1] for rx in (RE_NUM_BEON, RE_NUM_CAR) for m in rx.finditer(text)]
    return [c for c in dict.fromkeys(normalize_car_id(x) for x in found) if c]


def extract_vehicle(text: str) -> tuple[str | None, bool]:
    """(차량번호 'NN', 번호만 단독으로 말했는지). 한 자리·0 시작은 'short', 그 밖의 범위 밖은 'invalid'."""
    text = mask_spaces(RE_PLATE.sub(" ", text.translate(_FULLWIDTH)))
    text, invalid_spoken = _spoken_vehicle_text(text)
    if invalid_spoken:
        return "invalid", False
    correction = re.search(r"(?<![\d.])\d{1,2}\s*번(?:\s*차(?:량)?)?\s*말고", text)
    if correction:
        tail = text[correction.end():].strip()
        replacement, only = extract_vehicle(tail)
        only = only or bool(re.fullmatch(r"\d{1,2}\s*번\s*(?:차(?:량)?)?(?:이에요|이요|예요|요)?[.!]?", tail))
        return replacement or "missing", only
    # Malformed explicit numbers must never fall back to the previously selected car.
    tokens = re.findall(r"(?<!\d)([+-]?\d+(?:\.\d+)?)\s*(?:번|차량|차(?!단))" + _NOT_CAR, text)
    if any(token.startswith(("-", "+")) or "." in token or len(token) in (3,) or len(token) > 4 for token in tokens):
        return "invalid", False
    if re.fullmatch(r"\s*[+-]?\d{3}\s*", text) or re.fullmatch(r"\s*[+-]?\d{5,}\s*", text) or re.fullmatch(r"\s*-\d+\s*", text):
        return "invalid", False
    if re.search(r"\d{1,2}\s*번(?:\s*차(?:량)?)?\s*말고", text):
        return "missing", False  # 거절한 번호는 새 대상 후보로 사용하지 않는다.
    numbers = {m[1] for rx in (RE_NUM_BEON, RE_NUM_CAR) for m in rx.finditer(text)}
    if len(numbers) > 1:
        return "ambiguous", False
    for rx, only in ((RE_NUM_ONLY, True), (RE_NUM_BEON, False), (RE_NUM_CAR, False)):
        m = rx.search(text)
        if m:
            return _classify(m.group(1)), (only or bool(re.fullmatch(r"아니(?:요)?\s*\d{1,2}\s*번(?:이에요|이요|예요|요)?[.!]?", text.strip())))
    for rx, only in ((RE_KO_ONLY, True), (RE_KO_CAR, False)):
        m = rx.search(text)
        if m:
            return _classify_number(KO_NUM.get(m.group(1))), only
    return None, False


_PARKING_CONTEXT = re.compile(r"주차|자리|빈|만차|칸|요금|얼마|결제|정산|안내|출구|경보|화재|대피|차\s*(?:어디|위치)|널널|여유|가득|붐비|붐벼|붐빌|혼잡|들어가|댈|세울")


def extract_lots(text: str) -> list[str]:
    """문장에 나온 주차장 전부(비교 질문용). 규칙은 extract_lot과 같다."""
    text = mask_spaces(text)
    context = bool(_PARKING_CONTEXT.search(text))
    found = []
    for key, aliases, contextual, names in config.catalog.lot_patterns(config.DATA_SOURCE == "api"):
        if (any(pattern.search(text) for pattern in aliases)
                or (context and any(pattern.search(text) for pattern in contextual))
                or any(name in text for name in names)):
            found.append(key)
    return found


def extract_lot(text: str) -> str | None:
    """주차장 지정. 칸 이름(B-06)의 글자는 주차장이 아니다. 한 글자 이름(가·나·다·라)은 바로 뒤에 '주차장'이 있을 때만,
    여러 글자 별칭(개발원·부산역·북항·신세계…)은 주차장 질문 맥락에서만 인정한다."""
    if "말고" in text:
        replacement=extract_lot(text.rsplit("말고",1)[1])
        if replacement:return replacement
    found = extract_lots(text)
    return found[0] if len(found) == 1 else ("ambiguous" if found else None)


_SECTION = re.compile(r"(?<![A-Za-z0-9])([A-Za-z])\s*구역")


def extract_section(text: str):
    """'김해공항 B구역' → ('C', 'B'). 다른 주차장 이름과 함께 쓴 경우에만 글자를 주차장 안 구역으로 본다.
    'B구역'만 쓰면 기존처럼 B 주차장으로 해석한다(맥락 보완은 dialog_context)."""
    m = _SECTION.search(text)
    if not m:
        return None
    letter = m[1].upper()
    lot = extract_lot(text[:m.start()] + " 구역 " + text[m.end():])
    if not lot or lot == "ambiguous":
        return None
    prefix = letter + "-"
    return (lot, letter) if any(x.startswith(prefix) for x in config.catalog.spaces(lot)) else None


def extract_space(text: str) -> str | None:
    """'B-06'·'B6 칸' → 'B-06'. 주차장 글자와 무관하다(항상 주차장과 함께 식별)."""
    from .catalog import normalize_space
    match = re.search(r"(?<![A-Za-z0-9])([A-Za-z]\s*-?\s*\d{1,2})(?!\d)(?=\s*(?:칸|자리|번\s*칸|비었|비어|있|에|은|는|이|가|쪽|\?|$|\s))", text)
    return normalize_space(match[1].replace(" ", "")) if match else None


def extract_zone(text):
    for key, pattern in config.catalog.zone_patterns():
        if pattern.search(text):
            return key
    return None


# ------------------------------------------------------------ 의도 규칙 (위에서부터 먼저 맞는 것)
RULES: list[tuple[str, re.Pattern]] = [
    ("get_empty_spots", re.compile(r"몇\s*번째|대기|자리\s*알림|알림\s*(?:꺼|끄|취소)|자리\s*(?:가\s*)?(?:나면|생기면)|빈자리\s*생기면|기다릴게")),
    ("get_fee", re.compile(r"(?:결제|정산)\s*말고\s*(?:요금|금액|주차비)")),
    ("call_status", re.compile(r"(?:문의|호출).{0,12}(?:처리|됐|되었)|(?:관리자|직원).{0,12}(?:불렀|언제|확인했|호출됐)")),
    ("cancel_action", re.compile(r"^(?:일단\s*)?취소(?:할래요|해줘|해주세요)?[.!?]?$|(?:결제|정산|관리자|직원|상담원|호출).{0,15}(?:하지\s*말|말아|안\s*해도|필요\s*없|부르지\s*(?:마|말))")),
    ("call_status", re.compile(r"(?:문의|호출)\s*(?:상태|진행|현황)|관리자.{0,5}확인했")),
    ("change_vehicle", re.compile(r"차량\s*변경|내\s*차\s*바꿔|(?:차|차량).{0,8}(?:로|으로)\s*바꿔")),
    ("confirm_yes", re.compile(r"^\s*(네\s*[,，]?\s*(?:맞아요|맞습니다)|네네|넵|넹|네|예|응|어|맞아|맞아요|맞습니다|ㅇㅇ|ㅇㅋ|yes|ok|오케이)\s*[.!]?\s*$", re.I)),
    ("confirm_no", re.compile(r"^\s*(아뇨\s*틀렸어요|아니에요|아니|아니요|아뇨|틀려|틀렸어|no)\s*[.!]?\s*$", re.I)),
    ("cancel_action", re.compile(r"^\s*(취소|그만|잘못\s*말했어|진행\s*중\s*요청\s*취소)[.!?]?\s*$|(?:결제|정산|관리자|호출).{0,15}(취소|하지\s*마|안\s*할|부르지\s*(?:마|말))")),
    ("call_admin", re.compile(r"관리자|직원|상담원|사람\s*(좀\s*)?(불러|연결)|호출")),
    ("exit_help", re.compile(r"(출차|나가|출구|차단기|빠져).{0,8}(안\s*돼|안\s*되|못|막|안\s*열|안\s*올라|먹통|오류|에러|안\s*나가|안됨)|안\s*열려|못\s*나가")),
    ("evacuation_guide", re.compile(r"대피|피난|어디로\s*(가|피해)|도망")),
    ("alert_near_my_car", re.compile(r"(내\s*차|제\s*차).{0,10}(근처|가까|옆|괜찮|위험|안전)|(근처|가까).{0,6}(경보|화재|불)")),
    ("get_violations", re.compile(r"위반|불법\s*주차|전용\s*칸.{0,8}(?:일반\s*차|아무나|댄|세운|주차된)")),
    ("get_alerts", re.compile(r"경보|화재|불\s*났|불이|연기|위험|비상|이상\s*있")),
    ("find_my_car", re.compile(r"(이용|주차|내)\s*(현황|상태)|진행\s*(상황|상태)|어디까지\s*(됐|했)")),
    ("get_fee", re.compile(r"(?:결제|정산).{0,8}(상태|했|됐|된|완료|여부|확인|내역)")),
    ("pay_fee", re.compile(r"결제|정산|계산\s*(할|해)|지불|내고\s*싶|낼게|낼래|납부")),
    ("fee_rule", re.compile(r"요금\s*(기준|표|체계|정책|계산\s*기준)|주차비\s*(?:기준|표|체계|정책)|(?:하루|일일)\s*최대|최대\s*(?:요금|주차비)|요금\s*상한|얼마씩|기본\s*요금|무료\s*시간|무료\s*주차|몇\s*분.{0,4}무료|요금.{0,8}어떻게|\d+\s*분에\s*얼마|시간당")),
    ("get_fee", re.compile(r"요금|얼마|비용|주차비|돈|금액")),
    ("find_my_car", re.compile(r"(내|제)\s*차.{0,6}(어디|위치|찾|몇\s*번)|어디\s*(에\s*)?(주차|댔|세웠)|차(?:량)?\s*(위치|찾|어디)|차가?\s*안\s*보")),
    ("get_empty_spots", re.compile(r"빈\s*자리|빈\s*칸|자리|비었|비어|주차\s*(가능|할\s*수|할\s*(곳|데))|만차|남았|남은|공간|댈\s*(곳|데)")),
    ("thanks", re.compile(r"고마|감사|땡큐|thank", re.I)),
    ("help", re.compile(r"도움|뭘\s*할|뭐\s*할|뭐\s*해|기능|사용법|도와|help|안녕|하이|hello", re.I)),
]

KNOWN_INTENTS = {name for name, _ in RULES} | {"unknown"}

# 실행 대상별 언어 보호. LLM 경로에도 같은 검사를 적용한다.
ADMIN_TARGET = r"(?:담당자|관리자|직원|상담원)(?:님|분)?"
ADMIN = re.compile(ADMIN_TARGET + r"|호출|사람.{0,4}연결")
ADMIN_NEGATIVE = re.compile(r"(?:보내|불러|부르|호출\s*(?:해|하)?|연결\s*(?:해|하)?)\s*(?:주\s*)?지\s*(?:마|말|않)|" + ADMIN_TARGET + r".{0,12}(?:필요\s*없|안\s*부를|안\s*불러도|하지\s*않|안\s*연결|안\s*(?:오셔도|와도)|오실\s*필요\s*없)")
ADMIN_DEFER = re.compile(ADMIN_TARGET + r".{0,16}(?:오지\s*(?:마|말)|(?:나중에|이따(?:가)?)\s*(?:불러|부르|호출))")
ADMIN_INSTEAD = re.compile(r"(?:관리자|직원|상담원|호출)(?:은|는|을|를)?\s*말고\s*")
ADMIN_REQUEST = re.compile(r"불러|불러\s*주|부탁|연결해|연결\s*해|호출해|호출\s*해|호출\s*부탁")
ADMIN_QUESTION = re.compile(r"하면|부르면|불러도|부를\s*때|비용|돈이|돈\s*들|유료|무료|가능한|되나요|방법|어떻게|계세요|있나요|어디\s*(?:계|있)")
PAY_PROBLEM = re.compile(r"(?:결제|정산).{0,8}(?:안\s*돼|안\s*되|안됨|오류|에러|실패)|(?:이중|중복)\s*(?:결제|정산)|(?:돈|요금|주차비|금액).{0,6}(?:두\s*번|2\s*번)\s*빠져\s*나")
PAY_METHOD = re.compile(r"(?:카드|현금|간편).{0,8}(?:결제|정산).{0,6}(?:돼|되|가능)|결제\s*수단|(?:결제|정산).{0,8}(?:방법|어떻게)|"
                        r"(?:카드|현금|계좌\s*이체|이체|간편\s*결제)\S*\s*(?:말고\s*)?\S{0,6}\s*(?:낼|내도|내면|내야|받|돼|되나|가능)")
PAY_NEGATIVE = re.compile(r"(?:결제|정산|계산|지불|납부).{0,15}(?:안\s*해(?:요|$)|그만|하지\s*마|하지\s*말|말아|안\s*할|필요\s*없|안\s*해도|취소)|(?:이따(?:가)?|나중에|다음에)\s*(?:(?:결제|정산|계산|지불|납부)(?:는|을)?\s*)?(?:낼게|낼래|할게|할래)")


def guarded_intent(text):
    """제한된 복합 요청만 지원. 실제 경보 변경은 센서/시연 API만 담당한다."""
    admin = bool(ADMIN.search(text))
    admin_no = bool(ADMIN_NEGATIVE.search(text) or ADMIN_DEFER.search(text))
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
        return "exit_help"
    if re.search(r"불\s*(?:이\s*)?안\s*났", text):
        return "get_alerts"
    return None


KNOWN_INTENTS.update({"admin_help", "payment_help", "payment_issue", "set_destination", "cancel_destination"})


_QUERY_NUMBER = r"(?:\d{1,3}|[일이삼사오육칠팔구십]+(?:\s*[일이삼사오육칠팔구십]+)*)"
_VEHICLE_RANGE = re.compile(
    rf"(?<![\d.]){_QUERY_NUMBER}\s*번\s*대|"
    rf"(?<![\d.]){_QUERY_NUMBER}\s*(?:번\s*)?(?:[~～〜∼–-]|부터)\s*{_QUERY_NUMBER}(?!\d)"
)
_CAPABILITY_QUESTION = re.compile(
    r"(?:무슨|어떤)\s*질문|(?:뭘|뭐(?:를)?)\s*(?:물어|질문)|"
    r"(?:질문할|물어볼)\s*수\s*있는\s*(?:내용|것|게|항목)|"
    r"(?:할|물어볼)\s*수\s*있는\s*(?:게|것|내용).{0,8}(?:뭐|무엇)|"
    r"질문\s*(?:예시|목록|종류)"
)
_VEHICLE_PRESENCE = re.compile(
    r"(?:번\s*(?:차량|차)?|차량|차)(?:은|는|이|가)?\s*(?:(?:지금|현재)\s*)?"
    r"(?:(?:주차(?:돼|되어|해)?\s*|입차(?:해|돼|되어)?\s*|들어와\s*)?있|주차\s*중)"
    r"(?:나(?:요)?|니|어(?:요)?|어요|습니까|을까(?:요)?|는지(?:\s*(?:확인|알려)\s*(?:해\s*)?줘)?)?"
    r"[\s?？!！.]*$"
)


def _vehicle_query_scope(text):
    """Do not turn a vehicle range/list into one sticker or a bulk action."""
    query = mask_spaces(text)
    vehicle_context = re.search(r"차량|차들|차\s*번호|번호|번\s*대|번(?:\s|은|는|이|가|까|안|있|[?？]|$)", query)
    if vehicle_context and not re.search(r"번\s*(?:칸|자리)|\d\s*(?:원|분|시|초|층|%)", query) and _VEHICLE_RANGE.search(query):
        return "range"
    if re.search(r"(?:차량|차들).{0,18}(?:목록|리스트|번호들|뭐\s*(?:있|있는|야)|어떤|전부|전체)|"
                 r"(?:어떤|무슨|모든|전체)\s*(?:차량|차들)", query):
        return "list"
    return None


@dataclass
class ParseResult:
    intent: str
    params: dict = field(default_factory=dict)
    source: str = "rule"          # rule / llm / none
    vehicle_only: bool = False    # "07"처럼 번호만 말한 경우


_COLLOQUIAL = (
    (re.compile(r"잇(?=[어나니는을냐음습])"), "있"),
    # 봇 점검 11/11: '내차 1780 어딧어', '빈짜리 몇개야'
    (re.compile(r"어[딧딨딛](?=[어지나냐니음는습])"), "어디 있"),
    (re.compile(r"빈\s*짜리"), "빈자리"),
    (re.compile(r"요[굼큼]"), "요금"),
    (re.compile(r"결재(?=[해할하를가는도 ]|$)"), "결제"),
    (re.compile(r"출챠"), "출차"),
    (re.compile(r"내차(?=\s|\d|$)"), "내 차"),
)


def normalize_colloquial(text: str) -> str:
    """흔한 구어체 오타만 보정한다(잇어→있어, 어딧어→어디 있어, 빈짜리→빈자리). 차량 번호·주차장 이름은 바꾸지 않는다."""
    for pattern, repl in _COLLOQUIAL:
        text = pattern.sub(repl, text)
    return text


# 목적지 설정은 '개발원 주차장으로 안내해줘', '공항 주차장으로 갈게', '신세계로 가줘'처럼 주차장 이름 바로 뒤에
# '(으)로 + 안내·가·갈·이동'이 올 때만. 요금·빈자리·가까운 곳 질문이 섞이면 조회 질문이다(대화 AI·규칙 답).
_DESTINATION = re.compile(r"(?:주차장|공항|역|백화점|신세계|개발원|(?<![A-Za-z])[A-Da-d])\s*(?:으로|로)\s*(?:안내|가[자줘요]?|갈[게래거]|갈\s*(?:게|래|거)|이동)")
_NOT_DESTINATION = re.compile(r"경보|대피|호출|관리자|결제|요금|얼마|빈자리|자리\s*있|가까운|몇\s*(?:시|자리|칸)|붐|혼잡|어디")


def parse_rules(text: str) -> ParseResult:
    text = normalize_colloquial(text)
    return _with_info_gap(text, _with_analytics(text, _parse_rules_core(text)))


# 우리 데이터(점유 스냅샷·요금 정책)로 계산해 답할 수 있는 질문: 최다/비교/점유율/구역/요금 비교
_FEE_RANK = re.compile(r"(?:제일|가장|젤|최고로)\s*(?:싼|싸|저렴|비싼|비싸)|(?:싼|저렴한|비싼)\s*(?:곳|데|주차장)|"
                       r"(?:요금|주차비)\s*비교|어디가\s*(?:더\s*|제일\s*|가장\s*)?(?:싸|저렴|비싸)")
_RANK = re.compile(r"(?:제일|가장|젤|최고로)\s*(?:널널|한산|여유|비어|비었|빈|많이?\s*(?:남|비)|많은|많아|많이)|(?:덜|안)\s*붐비|"
                   r"(?:빈\s*자리|자리|빈\s*칸|칸)\s*(?:가|이)?\s*(?:제일|가장|젤|더)\s*많|"
                   r"어디가\s*(?:더\s*|제일\s*|가장\s*)?(?:널널|한산|여유|나아|낫|좋|비었|비어|많|덜)|어디로\s*가(?:야|면|는\s*게)|"
                   r"(?:주차장|주차\s*할\s*(?:곳|데)|빈\s*자리)\s*(?:을|를)?\s*(?:좀\s*|하나\s*)?추천|"
                   r"(?:어떤|어느|무슨)\s*주차장\S*\s*(?:\S+\s*){0,3}(?:좋을까|좋아|좋은|나을까|나아|추천|괜찮)")
# 목적만 말한 추천 질문('여행 가는데 어디 대?'). 목적이 한 주차장을 가리키면 그곳 빈자리로 답한다.
_PURPOSE_ASK = re.compile(r"어디\s*(?:에\s*)?(?:대|세우|주차|가)|(?:추천|좋을까|좋아\?|나을까|괜찮을까)")
_COMPARE = re.compile(r"중에|중\s|(?:이)?랑|하고|비교|vs|각각|둘\s*다|어디가|어느\s*(?:곳|쪽|주차장)|더\s*(?:많|여유|널널|한산|비)", re.I)
_OCCUPANCY = re.compile(r"얼마나\s*(?:찼|차\s*있|차있|들어\s*차|붐비|사람)|몇\s*(?:퍼센트|%|프로)|점유율|이용률|혼잡도|"
                        r"꽉\s*(?:찼|차\s*있)|몇\s*대\s*(?:있|주차|들어|세워)")
_VACANCY_AMOUNT = re.compile(r"얼마나\s*(?:\S+\s*){0,2}?(?:여유|남|비어|비었|빈|있|넉넉|한산|많)|(?:자리|칸|공간)\S*\s*얼마나")
_SAFETY = re.compile(r"경보|화재|불\s*났|연기|대피|피난|비상|관리자|직원|호출|결제|정산")


_FEE_TABLE = re.compile(r"(?:요금|주차비)\s*(?:을|를|좀|은|는|에\s*대해)?\s*(?:안내|설명|소개)|(?:요금|주차비)\s*(?:정보|표|체계|정책)|"
                        r"주차\s*요금\s*(?:을|를|좀)?\s*(?:알려|보여|궁금)")
_OWN_FEE = re.compile(r"(?:내|제|우리)\s*(?:차|요금|주차)|나온|나왔|지금까지|내야|낼\s|정산|결제|\d")


def asks_fee_table(text: str) -> bool:
    """주차장별 요금표를 묻는 말인가('주차요금 안내해줘', '요금표 보여줘'). 내 차 요금·차량 번호·기간이 섞이면 아니다."""
    return bool(_FEE_TABLE.search(text)) and not _OWN_FEE.search(text)


def _with_analytics(text: str, result: ParseResult) -> ParseResult:
    """데이터 집계형 질문을 표시한다. 새 intent는 없고 기존 get_empty_spots/fee_rule에 params만 더한다."""
    if result.intent not in ("unknown", "get_empty_spots", "get_fee", "fee_rule") or result.vehicle_only:
        return result
    if result.params.get("car_id") or result.params.get("virtual_vehicle") or result.params.get("vacancy_notice") or _SAFETY.search(text):
        return result
    params = {k: v for k, v in result.params.items() if k in ("lot_id", "zone")}
    lots = extract_lots(text)
    if asks_fee_table(text) and not lots and not params.get("lot_id"):
        # 화면의 '주차요금' 버튼("주차요금 안내해줘")은 요금표를 묻는 말이다. 전에는 내 차 요금으로 보고 차량 번호를 물었다(11/11 시연).
        # 내 차 요금은 '요금 얼마야', '23번 요금'처럼 따로 묻는다.
        return ParseResult("fee_rule", {"fee_table": True}, "rule")
    if _FEE_RANK.search(text):
        return ParseResult("fee_rule", {"fee_rank": "high" if re.search(r"비싼|비싸", text) else "low"}, "rule")
    occupancy = bool(_OCCUPANCY.search(text)) and params.get("lot_id") != "ambiguous"
    if result.intent in ("get_fee", "fee_rule"):
        # '얼마나 찼어'의 '얼마'는 요금이 아니다. 돈 관련 단어가 없을 때만 점유율로 본다.
        money = re.search(r"요금|비용|주차비|돈|금액|\d\s*원", text)
        if result.intent == "get_fee" and occupancy and not money:
            return ParseResult("get_empty_spots", {**params, "occupancy": True}, "rule")
        # '자리가 얼마나 여유가 있어?', '얼마나 남았어?'의 '얼마'도 요금이 아니라 빈자리 질문이다.
        if result.intent == "get_fee" and not money and _VACANCY_AMOUNT.search(text):
            if not lots and not params.get("lot_id"):
                from .info_gaps import hinted_lot
                hint = hinted_lot(text)
                if hint:
                    return ParseResult("get_empty_spots", {**params, "lot_id": hint, "purpose_hint": True}, "rule")
            return ParseResult("get_empty_spots", params, "rule")
        return result
    section = extract_section(text)
    if section:
        return ParseResult("get_empty_spots", {**params, "lot_id": section[0], "section": section[1]}, "rule")
    if len(lots) >= 2 and (_COMPARE.search(text) or _RANK.search(text)):
        params.pop("lot_id", None)
        return ParseResult("get_empty_spots", {**params, "rank": "compare", "lots": lots}, "rule")
    if not lots and not params.get("lot_id") and (_RANK.search(text) or _PURPOSE_ASK.search(text)):
        from .info_gaps import hinted_lot
        hint = hinted_lot(text)
        if hint:
            return ParseResult("get_empty_spots", {**params, "lot_id": hint, "purpose_hint": True}, "rule")
    if _RANK.search(text):
        return ParseResult("get_empty_spots", {**params, "rank": "most"}, "rule")
    if occupancy:
        return ParseResult("get_empty_spots", {**params, "occupancy": True}, "rule")
    return result


def _with_info_gap(text: str, result: ParseResult) -> ParseResult:
    """시설·거리·혼잡 예측처럼 데이터가 없는 주차장 질문을 일반 실패 대신 정직한 안내로 보낸다.

    지원 기능(경보·요금·결제·차량 등)으로 이미 분류된 문장은 건드리지 않는다. 새 intent·칩·카드 type은 없다.
    """
    if result.intent not in ("unknown", "get_empty_spots") or result.vehicle_only:
        return result
    if any(k in result.params for k in ("car_id", "space_id", "vacancy_notice", "virtual_vehicle",
                                        "rank", "occupancy", "section", "fee_rank")):
        return result
    from . import info_gaps
    found = info_gaps.classify(text)
    if not found:
        return result
    gap, key, label = found
    params = {k: v for k, v in result.params.items() if k in ("lot_id", "zone")}
    if not params.get("lot_id"):
        hinted = info_gaps.hinted_lot(text)
        if hinted:
            params["lot_id"] = hinted
    if gap == "crowd_now":
        # 주차장을 알 때만 현재 빈자리로 답한다. 비교 질문('어디가 덜 붐벼?')은 기존 LLM 분류 경로를 유지한다.
        return ParseResult("get_empty_spots", params, "rule") if params.get("lot_id") else result
    params.update({"info_gap": gap, **({"info_topic": key, "info_label": label} if key else {})})
    if gap == "facility":
        return ParseResult("help", params, "rule")
    return ParseResult("get_empty_spots", params, "rule")


def _parse_rules_core(text: str) -> ParseResult:
    excluded = ADMIN_INSTEAD.search(text)
    if excluded:
        tail = text[excluded.end():].strip()
        # '관리자 말고 요금'은 남은 요청만 분류한다. 앞의 대상은 실행하지 않는다.
        return parse_rules(tail) if tail else ParseResult("cancel_action", {"cancel_target": "admin"})
    vehicle, only = extract_vehicle(text)
    lot = extract_lot(text)
    zone = extract_zone(text)
    space = extract_space(text)
    virtual = extract_virtual(text)
    params = {}
    if space:
        params["space_id"] = space
    if virtual:
        params["virtual_vehicle"] = virtual
    if re.search(r"몇\s*번째|대기|자리\s*알림|알림\s*(?:꺼|끄|취소)|자리\s*(?:가\s*)?(?:나면|생기면)|빈자리\s*생기면|기다릴게", text):
        params["vacancy_notice"] = True
    if vehicle:
        params["car_id"] = vehicle
    if lot:
        params["lot_id"] = lot

    if zone:
        params["zone"] = zone
    if re.search(r"목적지.{0,6}(?:취소|해제|지워)|안내\s*(?:하지\s*(?:마|말)|취소)|(?:로|으로)\s*(?:가지\s*(?:마|말)|안\s*갈)",text):
        return ParseResult("cancel_destination",params,"rule")
    if not lot and _DESTINATION.search(text) and not _NOT_DESTINATION.search(text):
        # [실험] "신세계로 가자", "공항으로 갈게": 가겠다는 말 자체가 주차장 맥락이다(별칭은 맥락이 있어야 읽힌다).
        # 가는 말로만 된 문장일 때만 받는다("부산역으로 가는 버스 있어?"는 그대로 대화 AI로).
        from . import route_check
        named = extract_lot(text + " 주차") if route_check.sure(text, "set_destination") else None
        if named and named != "ambiguous":
            lot = params["lot_id"] = named
    if lot and _DESTINATION.search(text) and not _NOT_DESTINATION.search(text):
        return ParseResult("set_destination",params,"rule")
    guarded = guarded_intent(text)
    scope = _vehicle_query_scope(text)
    if scope:
        params.pop("car_id", None)
        # Cancellation and alarm guidance retain priority over query limitations.
        if guarded != "cancel_action":
            if re.search(r"대피|피난", text):
                return ParseResult("evacuation_guide", params, "rule")
            if re.search(r"경보|화재|연기|비상", text):
                return ParseResult("get_alerts", params, "rule")
            return ParseResult("help", {**params, "vehicle_query_help": scope}, "rule")
    if guarded:
        if guarded == "cancel_action":
            if ADMIN.search(text) or ADMIN_NEGATIVE.search(text):
                params["cancel_target"] = "admin"
            elif PAY_NEGATIVE.search(text):
                params["cancel_target"] = "payment"
                params["refund_info"] = bool(re.search(r"취소|환불", text))
        return ParseResult(guarded, params, "rule")

    if _CAPABILITY_QUESTION.search(text):
        # Examples of supported questions must not become a payment/admin request.
        return ParseResult("help", {"help_examples": True}, "rule")
    for intent, rx in RULES:
        if rx.search(text):
            return ParseResult(intent, params, "rule")
    if normalize_car_id(vehicle) and re.search(r"번\s*위치(?:\s*(?:알려\s*줘|알려\s*주세요|확인\s*해\s*줘))?[\s?？!！.]*$", text):
        return ParseResult("find_my_car", params, "rule")
    if (vehicle or re.search(r"(?:내|제)\s*차", text)) and _VEHICLE_PRESENCE.search(text):
        return ParseResult("find_my_car", params, "rule")
    if zone:
        return ParseResult("get_empty_spots",params,"rule")
    if vehicle:
        return ParseResult("unknown", params, "rule", vehicle_only=only)
    if virtual:
        return ParseResult("find_my_car", params, "rule")
    if space:
        return ParseResult("get_empty_spots", params, "rule")
    if lot:  # "B 주차장은?" → 빈자리로 해석
        return ParseResult("get_empty_spots", params, "rule")
    return ParseResult("unknown", params, "none")


def parse(text: str, *, allow_llm: bool = True) -> ParseResult:
    result = parse_rules(text)
    if allow_llm and result.intent == "unknown" and not result.vehicle_only and not ADMIN_INSTEAD.search(text):
        if config.LLM_ENGINE == "langchain":
            from .llm_router import route as classify
        else:
            from .llm import classify  # 되돌리기용 직접 호출 경로
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
            zone = raw.get("zone")
            if isinstance(zone, str) and zone in {z["key"] for z in config.catalog.zones()}:
                params["zone"] = zone
            if config.DATA_SOURCE != "api":
                vehicle = raw.get("car_id")
                if isinstance(vehicle, str) and re.fullmatch(r"[0-9]{1,2}", vehicle):
                    params["car_id"] = _classify(vehicle)
            params.update(result.params)  # 사용자 문장에서 추출한 값을 우선
            intent = guarded_intent(text) or llm_result["intent"]
            # LLM은 사용자가 쓰지 않은 실행 대상 번호를 만들어 넣을 수 없다.
            if intent in ("pay_fee", "call_admin"):
                params.pop("car_id", None)
                params.update(result.params)
            return ParseResult(intent, params, "llm")
    return result
