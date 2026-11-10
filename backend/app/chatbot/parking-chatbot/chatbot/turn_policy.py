"""Guards and typed intent extraction before the general chat router."""
import re

from .parser import RE_PLATE, extract_vehicle


FIRE = re.compile(r"연기.{0,12}(?:나|보여|차|가득)|(?:타는|탄|타고\s*있는)\s*냄새|불(?:이|길이)?\s*(?:보여|번져|번지|나고|붙|났어\s*[!.]?$)|\bsmoke\b|burning smell", re.I)
INJURY = re.compile(r"다쳤|부상|피가\s*(?:나|흘)|의식이\s*없|injur(?:y|ed)", re.I)
ACTION_CANCEL = re.compile(r"^(?:아[,.\s]*)?(?:이제\s*)?(?:됐어|됬어|그만|아니(?:요)?|취소(?:해\s*줘|할래)?|안\s*할래|그냥\s*둘게)(?:[,.\s]*(?:취소(?:해\s*줘|할래)?|그냥\s*둘게))?[.!?\s]*$")
ACTION_YES = re.compile(r"^(?:네|예|응|그래|좋아|yes|ok)[.!?\s]*$", re.I)
REFERENCE = re.compile(r"(?:아까|방금)?\s*(?:그|저)\s*(?:차|차량|거)|(?:첫\s*번째|처음)\s*(?:차|거|것)|(?:두\s*번째|마지막)\s*(?:차|거|것)")
VEHICLE_NUMBER = re.compile(r"(?<!\d)([1-9]\d)\s*번(?!\s*(?:칸|출구|입구|구역|게이트|자리))")
PERMISSION = re.compile(r"대(?:도|면)|주차(?:해도|할\s*수|해도\s*되)|이용(?:해도|할\s*수)|허용|가능|괜찮")
ZONE = re.compile(r"(전기차|충전|장애인|일반)\s*(?:차량)?\s*(?:전용\s*)?(?:주차\s*)?(?:칸|자리|구역|면)")
KINDS = {"전기차": "ev", "충전": "ev", "장애인": "accessible", "일반": "general"}
LABELS = {"ev": "전기차 전용 칸", "accessible": "장애인 전용 칸", "general": "일반 주차칸"}
ALLOWED = {
    "general": frozenset({"general"}),
    "ev": frozenset({"general", "ev"}),
    "accessible": frozenset({"general", "accessible"}),
}


def emergency(text):
    if FIRE.search(text):
        return "연기나 불이 보이거나 타는 냄새가 나면 즉시 안전한 곳으로 대피하세요. 안전한 곳에서 119에 신고하고 현장 관리자에게도 알리세요."
    if INJURY.search(text):
        return "다친 사람이 있으면 현장 안전을 먼저 확보하세요. 긴급한 부상이나 의식 저하가 있으면 즉시 119에 신고하고 현장 관리자에게 알리세요."
    return None


def vehicle_mentions(text):
    if RE_PLATE.search(text):
        return []
    numbers = list(dict.fromkeys(VEHICLE_NUMBER.findall(text)))
    if not numbers:
        number, _ = extract_vehicle(text)
        if number and number.isascii() and number.isdigit() and len(number) == 2:
            numbers = [number]
    return numbers


def parking_permission(text):
    zone = ZONE.search(text)
    if not zone or not PERMISSION.search(text):
        return None
    target = KINDS[zone[1]]
    before = text[:zone.start()]
    vehicle = re.search(r"(일반|전기차|장애인)(?:\s*(?:차량|차))?\s*(?:인데|인데요|라서|이라|이고|도|는)", before)
    kind = KINDS[vehicle[1]] if vehicle else None
    if target == "general":
        return "전기차를 포함해 일반 주차칸에 주차할 수 있어요. 현장에 별도 이용 제한이 있으면 표지판을 따라 주세요."
    if target == "accessible":
        if kind in ("general", "ev"):
            return "장애인 전용 칸에 주차할 수 없어요."
        return "장애인 전용 칸은 장애인 주차표지가 있고 보행상 장애인이 탑승한 차량만 이용할 수 있어요. 잠깐이라도 이 조건을 충족하지 않으면 주차할 수 없어요."
    if kind is None:
        return "전기차 전용 칸은 해당 전용 칸의 이용 조건을 충족한 차량만 주차할 수 있어요. 일반차는 빈칸이어도 이용할 수 없어요."
    allowed = target in ALLOWED[kind]
    return f"{LABELS[target]}에 주차할 수 있어요." if allowed else f"{LABELS[target]}에는 빈칸이어도 주차할 수 없어요."
