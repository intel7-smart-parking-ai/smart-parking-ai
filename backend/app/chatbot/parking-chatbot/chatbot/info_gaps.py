"""주차장 질문이지만 챗봇이 조회할 데이터가 없는 질문(시설·거리·혼잡 예측)을 자연스럽게 안내한다.

원칙(루트 CLAUDE.md 5번): 조회 API나 설정 값에 없는 내용은 만들지 않는다.
- 시설 위치·개수(엘리베이터, 화장실, 청사 가는 길 등)는 lots.json의 주차장별 `guide`에
  팀이 확인한 문장을 넣었을 때만 그대로 답한다. 없으면 '등록되지 않음'과 대안을 안내한다.
- 출발·도착 시각 기준 혼잡 예측은 제공하지 않고, 지금 기준 빈자리만 함께 보여 준다.
"""
from __future__ import annotations

import re

from . import config

# (topic key, 화면 표기, 인식 패턴). 위에서부터 먼저 맞는 주제를 쓴다.
TOPICS: tuple[tuple[str, str, re.Pattern], ...] = (
    ("payment_help", "결제·차단기 오류 대응", re.compile(r"결제.{0,12}(?:실패|오류|중복)|중복\s*결제|차단기.{0,10}(?:안|않|고장)|결제했는데")),
    ("exit_rules", "결제 후 출차", re.compile(r"결제.{0,15}(?:언제|몇\s*분|출차|나가)|출차\s*유예|추가\s*정산|재정산")),
    ("lookup_help", "차량 조회 실패", re.compile(r"조회.{0,10}(?:실패|안\s*돼|안\s*되)|내\s*차.{0,10}(?:안\s*나|안\s*보)|번호.{0,8}(?:미인식|인식\s*안)|차량.{0,8}검색.{0,8}안")),
    ("discount_steps", "할인 신청 방법", re.compile(r"할인.{0,12}(?:어떻게|방법|증빙|서류|신청)|감면.{0,8}(?:신청|방법)|할인.{0,6}(?:안\s*돼|안\s*되|미적용)")),
    ("walking", "시설 이동 안내", re.compile(r"[A-D]\s*구역.{0,18}(?:어떻게|가는|이동)|(?:화장실|엘리베이터|출입구).{0,12}(?:가는\s*길|어떻게\s*가|이동\s*경로)")),
    ("ev_usage", "전기차 충전 이용", re.compile(r"충전.{0,12}(?:시작|종료|방법|요금|고장)|커넥터|충전기\s*사용")),
    ("lost_found", "문의·분실물", re.compile(r"분실|잃어버|문의\s*(?:창구|방법|시간)|연락처|고객센터")),
    ("vehicle_rules", "차량 이용 제한", re.compile(r"오토바이|캠핑카|대형차|대형버스|트럭|이륜차")),
    ("passes", "정기권·재입차", re.compile(r"정기권|재입차|다시\s*입차|나갔다.{0,8}(?:오면|오는데|들어)")),
    ("discount", "할인 정책", re.compile(r"할인|감면|경차\s*혜택|국가유공자\s*혜택")),
    ("reservation", "예약 정책", re.compile(r"예약|사전\s*신청")),
    ("overnight", "장기·야간 주차 정책", re.compile(r"장기\s*주차|숙박\s*주차|밤새|야간\s*주차|며칠\s*(?:동안\s*)?주차")),
    ("refund", "환불 정책", re.compile(r"환불\s*(?:정책|규정|기준|가능|방법)|환불.{0,6}(?:돼|되|받을)|취소\s*수수료")),
    ("terminal", "청사·터미널 가는 길", re.compile(r"(?:국제선|국내선)(?:\s*(?:청사|터미널))?|청사|터미널|탑승구|게이트|출국장|입국장")),
    ("elevator", "엘리베이터", re.compile(r"엘리베이터|엘베|승강기")),
    ("stairs", "계단·에스컬레이터", re.compile(r"계단|에스컬레이터")),
    ("restroom", "화장실", re.compile(r"화장실|toilet|restroom", re.I)),
    ("entrance", "건물 출입구", re.compile(r"(?:건물|매장|백화점|역사|청사)\s*(?:입구|출입구)|출입구")),
    ("hours", "운영 시간", re.compile(r"(?:운영|영업)\s*시간|몇\s*시\s*(?:까지|부터)\s*(?:해|열|운영|영업|이용)|문\s*(?:언제\s*)?(?:닫|열)")),
    ("shuttle", "셔틀·대중교통", re.compile(r"셔틀|리무진|(?:버스|지하철|택시)\s*(?:타는\s*곳|정류장|승강장|승차장)")),
    ("height", "진입 높이 제한", re.compile(r"높이\s*제한|진입\s*높이|차고\s*제한|높이가\s*몇")),
    ("amenity", "편의시설", re.compile(r"편의점|식당|카페|푸드코트|ATM|흡연", re.I)),
)

_NEAREST = re.compile(r"(?:가장|제일|젤|최대한)\s*가까운|가까운\s*(?:구역|자리|칸|쪽|곳|데)|어느\s*구역에\s*(?:대|세우|주차)")
_CROWD = re.compile(r"혼잡|붐비|붐빌|붐벼|복잡(?:할|하지|해)|자리\s*(?:있을|없을|남아\s*있을)|만차\s*(?:일|될|되)|널널할|여유\s*(?:있을|로울)")
_FUTURE = re.compile(r"\d+\s*시|시간\s*(?:전|뒤|후)|출발|도착|비행기|주말|평일|연휴|명절|내일|모레|이따|나중에|저녁에|아침에|오전에|오후에|"
                     r"예상|예측|쯤")
_NOW = re.compile(r"지금|현재|방금")

# 주차장 이름이 없을 때 문맥으로 짐작하는 단서 → 그 단어를 별칭으로 가진 주차장.
_LOT_HINTS: tuple[tuple[str, re.Pattern], ...] = (
    ("공항", re.compile(r"비행기|항공|출국|입국|탑승|국제선|국내선|청사|여행|해외|여권|면세|휴가")),
    ("백화점", re.compile(r"쇼핑|백화점")),
    # 단독으로 쓴 '역'은 목적이 아니라 부산역의 별명이다(plan.NICKNAMES). '전기차'의 '기차'는 제외
    ("부산역", re.compile(r"(?<!전)기차|열차|KTX|SRT|ktx|srt")),
)

# 시설 질문인데 이미 지원하는 기능 단어가 있으면 그 기능이 먼저다(요금·결제·경보 등).
_SUPPORTED = re.compile(r"경보|화재|불\s*났|연기|대피|피난|비상|요금|얼마|결제|정산|관리자|직원|호출|내\s*차|제\s*차|출차|차단기")


OVERVIEW = re.compile(r"운영\s*(?:정책|규정|안내)|이용\s*(?:정책|규정|수칙)|시연\s*(?:운영\s*)?정책|시설\s*(?:안내|정보)|정책\s*(?:사항|전체)")


def topic(text: str):
    if OVERVIEW.search(text):
        return "overview", "이용 정책"
    for key, label, pattern in TOPICS:
        if pattern.search(text):
            return key, label
    return None


def hinted_lots(text: str) -> list:
    """문맥 단서가 가리키는 주차장 전부(문장에 나온 순서). '기차 먼저, 나중에 비행기' → ['B', 'C']."""
    found = []
    for alias, pattern in _LOT_HINTS:
        m = pattern.search(text)
        if not m:
            continue
        for key, row in config.catalog.lots().items():
            names = [row.get("name", "")] + list(row.get("aliases", []))
            if any(alias in name for name in names) and key not in [k for _, k in found]:
                found.append((m.start(), key))
    return [k for _, k in sorted(found)]


def hinted_lot(text: str) -> str | None:
    """'비행기 출발인데…'처럼 이름 없이 말한 주차장. 단서가 한 곳만 가리킬 때만 정한다."""
    found = []
    for alias, pattern in _LOT_HINTS:
        if not pattern.search(text):
            continue
        for key, row in config.catalog.lots().items():
            names = [row.get("name", "")] + list(row.get("aliases", []))
            if any(alias in name for name in names) and key not in found:
                found.append(key)
    return found[0] if len(found) == 1 else None


# '영화관으로 가는 가장 가까운 주차면', '화장실이랑 제일 가까운 자리' → 영화관·화장실. 조사가 붙은 명사만(동사 '하는데' 제외).
_NEAR_PLACE = re.compile(r"([가-힣A-Za-z0-9]{2,12}?)(?:으로|로|에서|이랑|랑|와|과|하고|이|가|에)\s*(?:가는|가기|가려면|갈\s*때)?\s*(?:제일|가장)\s*가까운")
_NOT_PLACE = re.compile(r"주차|공항|부산역|백화점|신세계|개발원|구역|자리|칸|면$|길$|곳$|데$|쪽$")


def near_place(text):
    """'○○(으)로/이랑 가장 가까운' 질문의 ○○(장소). 주차장·구역 이름이나 장소가 아니면 None."""
    m = _NEAR_PLACE.search(text or "")
    if not m or _NOT_PLACE.search(m[1]):
        return None
    return m[1]


def classify(text: str):
    """(gap, topic_key, topic_label) 또는 None.

    gap: 'facility'(시설 정보), 'nearest'(특정 시설과 가까운 구역), 'forecast'(시각 기준 혼잡 예측),
         'crowd_now'(지금 붐비는지 — 현재 빈자리로 답할 수 있음)
    """
    found = topic(text)
    if found and found[0] == "reservation" and re.search(r"취소|예약해|예약\s*(?:해\s*줘|해\s*주세요)|세차|식당|호텔", text):
        return None
    policy = found and found[0] in {"discount", "reservation", "overnight", "refund", "overview", "payment_help", "exit_rules", "lookup_help", "discount_steps", "walking", "ev_usage", "lost_found", "vehicle_rules", "passes"}
    if _SUPPORTED.search(text) and not policy:
        return None
    if found and found[0] != "overview" and _NEAREST.search(text):
        # 거리 질문은 사용자가 말한 목적지 표현(예: '국제선 청사')을 그대로 쓴다.
        place = next(p for k, _, p in TOPICS if k == found[0]).search(text)[0]
        return ("nearest", found[0], re.sub(r"\s+", " ", place).strip())
    if found:
        return ("facility", *found)
    if _CROWD.search(text):
        if _FUTURE.search(text) and not _NOW.search(text):
            return ("forecast", None, None)
        return ("crowd_now", None, None)
    return None


def _lot_guide(lot_id, key):
    if not lot_id or lot_id == "ambiguous":
        return None
    row = config.catalog.lot(lot_id) or {}
    guide = row.get("guide") or {}
    value = guide.get(key) if isinstance(guide, dict) else None
    return value.strip() if isinstance(value, str) and value.strip() else None


def prefix(params) -> str:
    """빈자리 답변 앞에 붙일 안내. 빈 문자열이면 붙이지 않는다."""
    gap, key, label = params.get("info_gap"), params.get("info_topic"), params.get("info_label")
    lot = params.get("lot_id")
    name = config.catalog.name(lot) if lot and lot != "ambiguous" else None
    if gap == "forecast":
        return "출발·도착 시각에 맞춘 혼잡 예측은 아직 제공하지 않아요. 실제 과거 데이터로 예측 정확도를 검증하는 단계이며, 합성 데이터 실험 결과를 실제 빈자리 예측으로 안내하지 않습니다.\n지금 기준으로"
    if gap == "nearest":
        guide = _lot_guide(lot, key)
        if guide:
            return f"{name} {label} 안내: {guide}"
        return f"{label}까지의 거리 정보가 아직 등록되지 않아서, 가장 가까운 구역을 콕 집어 추천해 드리기는 어려워요."
    return ""


def suffix(params) -> str:
    if params.get("info_gap") == "forecast":
        return "도착하기 직전에 다시 물어보시면 그때 기준으로 알려드릴게요."
    return ""


def facility_answer(params):
    """시설 질문의 (문장, 칩) — help 경로에서 사용."""
    key, label, lot = params.get("info_topic"), params.get("info_label") or "시설", params.get("lot_id")
    has_lot = bool(lot) and lot != "ambiguous" and lot in config.catalog.ids()
    if key == "overview" and has_lot:
        guide = config.catalog.lot(lot).get("guide") or {}
        lines = [f"{title}: {guide[key].strip()}" for key, title, _ in TOPICS
                 if isinstance(guide.get(key), str) and guide[key].strip()]
        if lines:
            return config.catalog.name(lot) + " 이용 정책\n" + "\n".join(lines), ["lot_" + lot, "empty_all", "call_admin"]
    guide = _lot_guide(lot, key) if has_lot else None
    if guide:
        text = guide if lot == "C" and key == "reservation" else f"{config.catalog.name(lot)} {label} 안내: {guide}"
    elif has_lot:
        text = (f"{config.catalog.name(lot)}의 {label} 정보는 아직 챗봇에 등록되지 않았어요. "
                "현장 안내판이나 관리자에게 확인해 주세요.\n제가 바로 알려드릴 수 있는 건 빈자리, 내 차 위치, 요금·모의 결제예요.")
    else:
        text = f"어느 주차장의 {label} 정보를 확인할까요? 주차장을 선택하거나 이름을 알려주세요."
        return text, ["lot_" + key for key in config.catalog.ids()][:4]
    chips = (["lot_" + lot] if has_lot else []) + ["empty_all", "find_car", "call_admin"]
    return text, chips[:4]
