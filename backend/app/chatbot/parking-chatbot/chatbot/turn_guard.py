"""규칙 앞단 안전·허용 여부·취소 확인·없는 칸 처리 (v3.2 이식).

판정 함수는 김채린 브랜치(codex/parkon-chatbot-safety-and-maps)의 turn_policy.py를 그대로 가져왔고,
이 파일은 그 결과를 엔진 앞단에 연결하면서 일반 조회 문장이 잘못 걸리지 않게 거른다.
"""
import re

from . import config
from . import turn_policy as policy

RED_LIGHT = re.compile(r"빨간\s*불.{0,12}(?:깜빡|켜|들어)")
BARE_CANCEL = re.compile(r"^(?:그거|그것|이거|저거)?\s*취소(?:해\s*줘|할래|해)?[.!?\s]*$")
SPACE = re.compile(r"(?<![A-Za-z0-9])([A-Da-d])\s*-\s*(\d{1,2})(?!\d)")
# '주차 연기', '예약 연기'처럼 미룬다는 뜻의 '연기'는 화재 신고가 아니다.
POSTPONE = re.compile(r"(?:주차|예약|결제|출차|입차|시간|일정)\s*연기|연기\s*(?:해|할|하|좀|가능|나중)")
# 허용 여부는 '대도 돼?/해도 돼?/괜찮아?'처럼 묻는 문장만. '가능한 곳 있어?'는 빈자리 조회다.
PERMISSION_ASK = re.compile(r"대(?:도|면)|해도|되(?:나|니|요|ㄹ까)|돼|괜찮|할\s*수\s*있")
AVAILABILITY = re.compile(r"곳|어디|몇|남은|주차장\s*\?|자리\s*있")
IDENTITY = re.compile(r"(?:너|넌|당신|챗봇|봇)\s*(?:은|는)?\s*(?:이름|누구|뭐\s*하는|정체)|(?:이름|정체)\s*(?:이|은|는|가)?\s*(?:뭐|무엇|뭔)|자기\s*소개|who\s+are\s+you|your\s+name", re.I)
GREETING = re.compile(r"^(?:안녕(?:하세요|하십니까)?|반가워(?:요)?|하이|hello|hi|hey)[!.~?\s]*$", re.I)
INTRO = "저는 PARK:ON 주차 안내 챗봇이에요. 빈자리, 내 차 위치, 요금·모의 결제, 모의 화재 경보 안내를 도와드려요. 아래 버튼을 누르거나 편하게 물어보세요."


def _lot_in(text):
    compact = re.sub(r"\s+", "", text)
    return next((key for key in config.catalog.ids()
                 if re.sub(r"\s+|주차장$", "", config.catalog.name(key)) in compact), None)


def guard(s, text):
    """(답, meta) 또는 (None, 엔진에 넘길 문장)."""
    if IDENTITY.search(text) or GREETING.fullmatch(text.strip()):
        return INTRO, {"intent": "self_intro"}
    hazard = policy.emergency(text)
    if hazard and not POSTPONE.search(text):
        return hazard, {"intent": "emergency", "chips": ["call_admin"]}
    if PERMISSION_ASK.search(text) and not AVAILABILITY.search(text):
        permission = policy.parking_permission(text)
        if permission:
            return permission, {"intent": "parking_permission"}
    if not s.pending and BARE_CANCEL.fullmatch(text.strip()):
        return "어떤 요청을 취소할까요? 취소할 대상을 알려주세요.", {"intent": "cancel_clarify"}
    found = SPACE.search(text)
    if found:
        lot = _lot_in(text)
        if lot:
            label = f"{found[1].upper()}-{int(found[2]):02d}"
            spaces = config.catalog.spaces(lot)
            if spaces and label not in spaces:
                return f"{config.catalog.name(lot)}에는 {label} 칸이 없어요.", {"intent": "space_missing"}
    if RED_LIGHT.search(text):
        return None, "지금 경보 있어?"
    return None, text
