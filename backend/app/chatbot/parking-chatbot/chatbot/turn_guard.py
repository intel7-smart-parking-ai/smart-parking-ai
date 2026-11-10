"""대화 에이전트·키워드 규칙 앞에서 바로 답하는 짧은 확인(v3.3).

출처: 김채린 브랜치의 turn_policy.py(판정 정규식, 원본 그대로)와 feature/KAN-9-chatbot-v32-safety-port의
turn_guard.py. v3.3 검토에서 아래를 고쳐 옮겼다.

- Lv2 모의 화재 경보 중에는 끼어들지 않는다. 경보 위치·출구가 담긴 기존 대피 안내가 우선이다.
- 연기·불 신고에는 대피 안내와 함께 지금 시스템의 모의 화재 경보 상태를 같이 말한다.
- 허용 여부 답은 팀 결정 D-05 문장만 사실로 쓴다. 정해지지 않은 기준(전기차·장애인 차량의 일반 칸 이용 등)은
  '안내 자료에 없음'으로 답한다.
- 진행 중인 확인(결제·차량 선택)은 지우지 않는다. 안전 신고만 진행 중인 확인을 끝낸다.
- 없는 칸 검사는 별칭(공항·백화점 등)으로 말한 주차장도 알아본다.
- '주차장 이름이 뭐야'는 자기소개가 아니라 주차장 목록 질문이다.
"""
from __future__ import annotations

import re

from . import config
from . import turn_policy as policy
from .knowledge import RULES
from .parser import extract_lots

BARE_CANCEL = re.compile(r"^(?:그거|그것|이거|저거)?\s*취소(?:해\s*줘|할래|해)?[.!?\s]*$")
SPACE = re.compile(r"(?<![A-Za-z0-9])([A-Da-d])\s*-\s*(\d{1,2})(?!\d)")
# '주차 연기', '예약 연기', '연기 학원'처럼 화재와 무관한 '연기'.
POSTPONE = re.compile(r"(?:주차|예약|결제|출차|입차|시간|일정)\s*연기|연기\s*(?:해|할|하|좀|가능|나중|학원|수업|연습)|배우|담배\s*연기")
# 허용 여부는 '대도 돼?/해도 돼?/괜찮아?'처럼 묻는 문장만. '가능한 곳 있어?'는 빈자리 조회다.
PERMISSION_ASK = re.compile(r"대(?:도|면)|해도|되(?:나|니|요|ㄹ까)|돼|괜찮|할\s*수\s*있")
AVAILABILITY = re.compile(r"곳|어디|몇|남은|주차장\s*\?|자리\s*있")
IDENTITY = re.compile(r"(?:너|넌|당신|챗봇|봇)\s*(?:은|는|의)?\s*(?:이름|누구|뭐\s*하는|정체)|"
                      r"^\s*(?:네\s*)?(?:이름|정체)\s*(?:이|은|는|가)?\s*(?:뭐|무엇|뭔)|자기\s*소개|"
                      r"who\s+are\s+you|your\s+name", re.I)
GREETING = re.compile(r"^(?:안녕(?:하세요|하십니까)?|반가워(?:요)?|하이|hello|hi|hey)[!.~?\s]*$", re.I)
LOT_NAMES = re.compile(r"주차장\s*(?:은|는|이)?\s*(?:이름|목록|종류|어디\s*어디|몇\s*곳|몇\s*개|뭐\s*뭐|뭐\s*있)")
# '이제 나가려고 하는데 뭐 해야 돼?', '출차하려면 어떻게 해?' — 출차 전 요금 확인·사전 정산 안내(윤명수 시제품 56번 취지)
LEAVING = re.compile(r"(?:이제\s*)?(?:나가려|나갈\s*(?:건데|거|려|게)|나가기\s*전|출차\s*(?:하려|할게|할래|하려면|하고\s*싶|전에?)|"
                     r"집에\s*(?:가|갈)|빼\s*(?:가려|갈게|려고)|차\s*빼)")
LEAVING_REPLY = ("출차 전에 요금을 확인하고, 미리 모의 결제(사전 정산)를 하시면 돼요. 결제는 견적을 보여 드린 뒤 승인하셔야 진행돼요.\n"
                 "차량 번호를 알려 주시면 요금부터 보여 드릴게요. 실물 주차장은 차 지붕 스티커 두 자리, 김해공항·신세계는 번호판 뒤 4자리예요.")
# '스크래치 났어', '차가 긁혔어', '누가 박았어' — 손상·접촉 사고는 챗봇이 판단할 수 없고 현장 확인이 필요하다(봇 점검 11/10)
DAMAGE = re.compile(r"스크래치|긁(?:혔|힌|었|어)|찍혔|파손|부서졌|깨졌|찌그러|흠집|문콕|접촉\s*사고|사고\s*(?:났|나서)|"
                    r"(?:박았|박혔|들이받|부딪)")
DAMAGE_REPLY = ("차량 손상이나 접촉 사고는 제가 확인하거나 판단할 수 없어요. 현장 관리자가 직접 확인해야 해요.\n"
                "아래 '관리자 호출'을 누르면 조회한 차량 정보와 최근 문의가 관리자 화면에 함께 전달돼요.")
FORGOT_NUMBER = re.compile(r"(?:차\s*)?번호\S*\s*(?:를|가|도)?\s*(?:까먹|잊어|잊었|모르|기억\s*(?:안|이\s*안|나지\s*않))")
FORGOT_REPLY = ("차 지붕에 붙은 두 자리 숫자 스티커가 차량 번호예요(10~99). 김해공항·신세계 차량은 번호판 뒤 4자리로도 찾을 수 있어요.\n"
                "확인이 어려우면 아래 '관리자 호출'로 도움을 받을 수 있어요.")
INTRO = "저는 PARK:ON 주차 안내 챗봇이에요. 빈자리, 내 차 위치, 요금·모의 결제, 모의 화재 경보 안내를 도와드려요. 아래 버튼을 누르거나 편하게 물어보세요."

_D05 = next(rule.text.split(". ")[0].rstrip(".") + "." for rule in RULES if rule.key == "restricted_spaces")
_LABEL = {"ev": "전기차 전용 칸", "accessible": "장애인 전용 칸", "general": "일반 칸"}
_OWNER = {"ev": "전기차", "accessible": "장애인 차량"}
_FOR = {"ev": "전기차를", "accessible": "장애인 차량을"}


def _vehicle_kind(text, zone_start):
    found = re.search(r"(일반|전기차|장애인)(?:\s*(?:차량|차))?\s*(?:인데|인데요|라서|이라|이고|도|는)", text[:zone_start])
    return policy.KINDS[found[1]] if found else None


def parking_permission(text):
    """전용 칸 허용 여부. 팀 결정 D-05(일반 차량은 전기차·장애인 전용 칸 불가)만 사실로 쓴다."""
    zone = policy.ZONE.search(text)
    if not zone or not policy.PERMISSION.search(text):
        return None
    target, kind = policy.KINDS[zone[1]], _vehicle_kind(text, zone.start())
    if target == "general":
        if kind in (None, "general"):
            return "일반 차량은 일반 칸에 주차할 수 있어요."
        return (f"{_OWNER[kind]}의 일반 칸 이용 기준은 아직 안내 자료에 없어요. 현장 표지판을 따라 주세요. "
                f"{_D05}")
    if kind == target:
        return f"{_LABEL[target]}은 {_FOR[target]} 위한 칸이에요. 지금 빈자리는 '{_LABEL[target]} 빈자리'로 확인할 수 있어요."
    if kind in (None, "general"):
        return f"{_D05} 잠깐 세우는 것도 마찬가지예요." if re.search(r"잠깐|잠시|\d+\s*분만", text) else _D05
    return f"{_LABEL[target]}은 {_FOR[target]} 위한 칸이에요. 다른 전용 차량의 이용 기준은 안내 자료에 없어서 현장 안내를 따라 주세요."


def _alert_line(alert):
    level = (alert or {}).get("level") or 0
    if level >= 1:
        lot = (alert or {}).get("lot_id")
        where = config.catalog.name(lot) if lot else "주차장"
        return f"지금 {where}에 모의 화재 경보가 있어요. 안내 버튼과 화면 배너를 확인해 주세요."
    return "지금 시스템에 들어온 모의 화재 경보는 없어요. 그래도 연기나 불이 보이면 위 안내를 먼저 따라 주세요."


def _missing_space(text):
    found = SPACE.search(text)
    if not found:
        return None
    lots = extract_lots(SPACE.sub(" ", text) + " 주차장 빈자리")   # 별칭은 주차 맥락이 있어야 인정된다
    if len(lots) != 1:
        return None
    lot = lots[0]
    label = f"{found[1].upper()}-{int(found[2]):02d}"
    spaces = config.catalog.spaces(lot)
    if spaces and label not in spaces:
        return f"{config.catalog.name(lot)}에는 {label} 칸이 없어요. 칸 번호를 다시 확인해 주세요."
    return None


def guard(s, text, alert=None):
    """(답, meta) 또는 (None, 엔진에 넘길 문장). meta['clear']가 True면 진행 중인 확인을 끝낸다."""
    level = (alert or {}).get("level") or 0
    if level >= 2:
        return None, text   # 대피 안내가 우선(기존 Lv2 흐름)
    hazard = policy.emergency(text)
    if hazard and not POSTPONE.search(text):
        return hazard + "\n" + _alert_line(alert), {"intent": "emergency", "chips": ["call_admin"], "clear": True}
    if IDENTITY.search(text) or GREETING.fullmatch(text.strip()):
        return INTRO, {"intent": "self_intro"}
    if LOT_NAMES.search(text) and not extract_lots(text):
        names = "·".join(config.catalog.name(k) for k in config.catalog.ids())
        return f"안내할 수 있는 주차장은 {names}, 모두 {len(config.catalog.ids())}곳이에요.", {"intent": "lot_names"}
    if PERMISSION_ASK.search(text) and not AVAILABILITY.search(text):
        permission = parking_permission(text)
        if permission:
            return permission, {"intent": "parking_permission"}
    has_car = re.search(r"(?<!\d)\d{2,4}\s*(?:번|차)|\d{2,3}[가-힣]\d{4}", text) or getattr(s, "car_id", None)
    if LEAVING.search(text) and not has_car and not getattr(s, "pending", None):
        return LEAVING_REPLY, {"intent": "exit_payment", "chips": ["fee", "find_car"]}
    if FORGOT_NUMBER.search(text):
        return FORGOT_REPLY, {"intent": "forgot_number", "chips": ["call_admin", "find_car"]}
    if DAMAGE.search(text) and not re.search(r"화재|불|연기", text):
        return DAMAGE_REPLY, {"intent": "vehicle_damage", "chips": ["call_admin", "find_car"]}
    if not getattr(s, "pending", None) and BARE_CANCEL.fullmatch(text.strip()):
        return "어떤 요청을 취소할까요? 취소할 대상을 알려주세요.", {"intent": "cancel_clarify"}
    missing = _missing_space(text)
    if missing:
        return missing, {"intent": "space_missing"}
    return None, text
