"""계획표(Plan): 질문 하나를 여러 방식으로 읽어 한곳에 모은 것. 코드가 만든다(AI 호출 없음).

같은 계획표를 세 곳이 함께 본다.
- 대화 AI: hints()를 참고 정보로 받는다(결정권 없음, 반드시 도구로 조회)
- 규칙 답: AI가 실패하면 첫 규칙 해석이 문장과 맞는지 계획표로 다시 고른다(engine._plausible_reading)
- 경로 기록: summary()가 route의 'plan' 단계에 남는다

읽는 방법(서로 독립)
- rule: 키워드 규칙 해석(parser.parse)
- lots / zone / car: 문장에 나온 주차장·구역·차량 번호
- purpose_lot: 목적으로 짐작한 주차장(여행→공항, 쇼핑→백화점, 기차→부산역)
- wants: 질문이 원하는 것(빈자리·요금·위치·결제·요금 기준·규칙·경보·비교·나중 상황)
- talk: 문장에 자리·차·돈 이야기가 있는지(규칙 답을 고를 때 씀)
"""
from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass, field
import re

_current: ContextVar["Plan | None"] = ContextVar("turn_plan", default=None)

CAR_WORDS = re.compile(r"내\s*차|제\s*차|차량|번호|\d\s*번|차\s*(?:어디|위치|찾)|출차|결제|정산")
SPOT_WORDS = re.compile(r"주차장|자리|주차\s*(?:돼|되나|되냐|가능|ㄱㄴ)|빈\s*칸|빈칸|여유|만차|남은|남았|비었|비어|꽉|찼|가득|붐비|붐벼|한산|널널|"
                        r"어디\s*(?:에\s*)?(?:대면|댈|세우|세울|주차하면|주차할|주차해야)|대면\s*돼|댈\s*(?:데|곳)|세울\s*(?:데|곳)|주차할\s*(?:데|곳)")
MONEY_WORDS = re.compile(r"요금|주차비|비용|금액|돈|\d\s*원")
# '나 전기차야', '저 장애인 차량이에요', '제 차는 일반차예요' — 자기 차 종류를 알려 주는 말
_SELF_KIND = r"(전기차|장애인\s*(?:차량|차)?|일반\s*(?:차량|차))"
SELF_VEHICLE = re.compile(r"(?:^|\s)(?:나|난|저|전|내\s*차(?:는|가)?|제\s*차(?:는|가)?|우리\s*차(?:는|가)?)\s*" + _SELF_KIND
                          + r"|^\s*" + _SELF_KIND + r"\s*(?:야|이야|예요|이에요|입니다|인데|이고|라서)")
SELF_ONLY = re.compile(r"^\s*(?:(?:나|난|저|전|내\s*차(?:는|가)?|제\s*차(?:는|가)?|우리\s*차(?:는|가)?)\s*)?" + _SELF_KIND
                       + r"\s*(?:야|이야|예요|이에요|입니다|임|요)?\s*[.!~]*\s*$")


OWN_CAR = re.compile(r"(?<!\d)([1-9]\d)\s*번\s*(?:차(?:량)?\s*)?(?:이|은|가|이게|이야)?\s*(?:내|제|우리)\s*차")
LIGHT = re.compile(r"불\s*(?:이\s*)?(?:꺼져|꺼졌|켜져|켜졌|깜빡|들어와)|빨간\s*불|경보등|LED", re.I)
_DURATION = re.compile(r"(\d{1,2})\s*시간(?:\s*(\d{1,2})\s*분)?|(\d{1,3})\s*분\s*(?:만|동안|세우|주차|이면|하면)")
# '3일 주차', '이틀 동안', '하루 세우면' — 날짜('3일에 가면')와 헷갈리지 않게 뒤에 기간을 뜻하는 말이 올 때만
_DAYS = re.compile(r"(?:(\d{1,2})\s*일|(하루|이틀|사흘|나흘))\s*(?:동안|간|정도|쯤|주차|세우|세울|대|이면|하면|짜리|있|머물|맡)")
_DAY_WORDS = {"하루": 1, "이틀": 2, "사흘": 3, "나흘": 4}
MAX_ESTIMATE_MINUTES = 4320   # tools.read.estimate_fee가 계산하는 최대(3일)


def minutes(text):
    """'3시간이면', '1시간 30분', '90분 세우면', '3일 주차', '이틀 동안' → 분. 없으면 None. 3일을 넘으면 -1."""
    text = text or ""
    d = _DAYS.search(text)
    if d:
        days = int(d[1]) if d[1] else _DAY_WORDS[d[2]]
        total = days * 1440
        return total if 1 <= total <= MAX_ESTIMATE_MINUTES else (-1 if total > MAX_ESTIMATE_MINUTES else None)
    m = _DURATION.search(text)
    if not m:
        return None
    total = int(m[1]) * 60 + int(m[2] or 0) if m[1] else int(m[3])
    return total if 1 <= total <= MAX_ESTIMATE_MINUTES else None


_AHEAD = re.compile(r"(?:(\d{1,2})\s*시간(?:\s*(\d{1,2})\s*분|\s*(반))?|(\d{1,3})\s*분)\s*(?:뒤|후|더|있다가|지나서|지나면)")


def ahead_minutes(text):
    """'60분 뒤에 나가면', '1시간 후', '1시간 반 뒤', '30분 더 있으면' → 지금부터 몇 분 뒤. 없으면 None."""
    m = _AHEAD.search(text or "")
    if not m:
        return None
    total = int(m[1]) * 60 + (int(m[2]) if m[2] else 30 if m[3] else 0) if m[1] else int(m[4])
    return total if 1 <= total <= MAX_ESTIMATE_MINUTES else None


# 별명: 정식 이름 없이 부르는 말 → 주차장. 주차장 명부(lots.json) 별칭에 없는 짧은 말만 둔다.
NICKNAMES = (
    ("B", re.compile(r"(?<![가-힣])역(?=\s|에|앞|의|쪽|근처|주차|이랑|랑|하고|이나|이든|$)|역전|역\s*광장")),
    ("C", re.compile(r"(?<![가-힣])김해(?!\s*공항)(?=\s|에|쪽|주차|$)|비행기\s*타는\s*(?:곳|데)")),
    ("D", re.compile(r"(?<![가-힣])센텀(?=\s|에|쪽|주차|$)|쇼핑몰")),
)
# 정식 이름(문장에 있으면 짐작이 아님)
_OFFICIAL = {"A": ("개발원",), "B": ("부산역",), "C": ("김해공항",), "D": ("신세계",)}


def nickname_lots(text):
    """별명으로 부른 주차장 {lot: 쓴 말}. 정식 이름을 함께 말했으면 짐작이 아니므로 뺀다."""
    compact = re.sub(r"\s+", "", text or "")
    out = {}
    for lot, pattern in NICKNAMES:
        m = pattern.search(text or "")
        if m and not any(name in compact for name in _OFFICIAL.get(lot, ())):
            out[lot] = m[0].strip()
    return out


def self_vehicle(text):
    """자기 차 종류를 말했으면 'ev'/'accessible'/'general', 아니면 None."""
    m = SELF_VEHICLE.search(text or "")
    if not m:
        return None
    word = next(g for g in m.groups() if g)
    return "ev" if word.startswith("전기") else "accessible" if word.startswith("장애") else "general"


@dataclass(frozen=True)
class Plan:
    text: str
    rule: dict | None = None
    lots: tuple = ()
    zone: str | None = None
    car: str | None = None
    purpose_lot: str | None = None
    wants: tuple = ()
    talk: dict = field(default_factory=dict)
    self_vehicle: str | None = None   # 이번 문장에서 사용자가 말한 자기 차 종류
    self_only: bool = False           # 문장이 그 말뿐인지('나 전기차야')
    own_car: str | None = None        # '23번이 내 차야'
    purpose_lots: tuple = ()          # 목적 단서가 가리키는 주차장 전부(여러 목적)
    minutes: int | None = None        # '3시간이면' → 180
    guessed: dict = field(default_factory=dict)   # 별명으로 짐작한 주차장 {lot: 쓴 말}

    @property
    def single_lot(self):
        return self.lots[0] if len(self.lots) == 1 else None

    def hints(self) -> dict:
        """대화 AI에 넘기는 참고 정보(빈 값은 뺀다)."""
        # 규칙 의도(self.rule)는 넣지 않는다: '얼마나 여유'를 요금으로 읽는 식의 오판이 AI 판단을 끌고 갈 수 있다.
        out = {"mentioned_lots": list(self.lots), "zone": self.zone, "car_number": self.car,
               "purpose_lot": self.purpose_lot, "wants": list(self.wants), "said_vehicle_type": self.self_vehicle,
               "guessed_lots": self.guessed}
        return {k: v for k, v in out.items() if v not in (None, [], "", {})}

    def summary(self) -> str:
        parts = [f"wants={'+'.join(self.wants) or '-'}"]
        if self.lots:
            parts.append("lots=" + "+".join(self.lots))
        if self.purpose_lot:
            parts.append(f"purpose={self.purpose_lot}")
        if self.guessed:
            parts.append("guessed=" + "+".join(f"{k}({v})" for k, v in self.guessed.items()))
        if self.zone:
            parts.append(f"zone={self.zone}")
        if self.car:
            parts.append(f"car={self.car}")
        if self.self_vehicle:
            parts.append(f"my_vehicle={self.self_vehicle}")
        return " ".join(parts)


def _car_talk(text):
    """차 이야기: 차·번호 단어, 또는 가상 주차장 번호판(17가1724)·뒤 4자리."""
    from .parser import RE_PLATE, extract_virtual
    return bool(CAR_WORDS.search(text) or RE_PLATE.search(text) or extract_virtual(text))


def _wants(text, zone=None):
    from .dialog.query import _ALERT, _CHOICE, _FEE, _FUTURE, _LOCATION, _PARKING, _PAYMENT, _POLICY
    from .parser import _VACANCY_AMOUNT
    money = bool(MONEY_WORDS.search(text))
    checks = (
        ("vacancy", (bool(_PARKING.search(text)) or bool(zone)) and not (money and not SPOT_WORDS.search(text))
         and not (_car_talk(text) and not SPOT_WORDS.search(text))),
        ("fee", bool(_FEE.search(text)) and (money or not _VACANCY_AMOUNT.search(text))),
        ("location", bool(_LOCATION.search(text)) and _car_talk(text)),
        ("payment", bool(_PAYMENT.search(text))),
        ("rates", bool(re.search(r"요금\s*(?:기준|표|체계|정책)|기본\s*요금|시간당|최대\s*요금|싸|싼|저렴|비싸", text))),
        ("policy", bool(_POLICY.search(text))),
        ("alert", bool(_ALERT.search(text)) or bool(LIGHT.search(text))),
        ("compare", bool(_CHOICE.search(text))),
        ("future", bool(_FUTURE.search(text))),
    )
    return tuple(name for name, hit in checks if hit)


def make(text: str) -> Plan:
    """문장 하나의 계획표. 실패해도 빈 계획표를 돌려준다(답을 막지 않음)."""
    if not isinstance(text, str) or not text.strip():
        return Plan(text or "")
    from .info_gaps import hinted_lot, hinted_lots
    from .parser import extract_lots, extract_vehicle, extract_zone, normalize_car_id, parse
    try:
        rule = parse(text, allow_llm=False)
        rule_reading = {"intent": rule.intent, **{k: v for k, v in rule.params.items()
                                                  if k in ("lot_id", "zone", "car_id", "rank", "purpose_hint")}}
    except Exception:
        rule_reading = None
    # 별칭(공항·신세계)은 주차 맥락이 있어야 인정되므로, 대화 해석(dialog.query)과 같게 맥락을 붙여 읽는다.
    lots = tuple(extract_lots(text + " 빈자리"))
    nick = nickname_lots(text)
    lots = lots + tuple(k for k in nick if k not in lots)
    zone = extract_zone(text)
    car_raw, _ = extract_vehicle(text)
    return Plan(
        text=text,
        rule=rule_reading,
        lots=lots,
        zone=zone,
        car=normalize_car_id(car_raw) if car_raw else None,
        purpose_lot=None if lots else hinted_lot(text),
        wants=_wants(text, zone),
        self_vehicle=self_vehicle(text),
        own_car=(OWN_CAR.search(text) or [None, None])[1],
        purpose_lots=() if lots else tuple(hinted_lots(text)),
        guessed=nick,   # 애매한 별명만(역·김해·센텀). '공항'·'백화점'처럼 하나뿐인 게 분명한 말은 짐작 문구 없이
        minutes=minutes(text),
        self_only=bool(SELF_ONLY.match(text)),
        talk={"spot": bool(SPOT_WORDS.search(text)), "car": _car_talk(text),
              "money": bool(MONEY_WORDS.search(text))},
    )


def use(plan: Plan):
    """이번 질문의 계획표로 정한다(엔진이 질문마다 한 번)."""
    return _current.set(plan)


def release(token):
    try:
        _current.reset(token)
    except (ValueError, LookupError):
        pass


def for_text(text: str) -> Plan:
    """이번 질문의 계획표. 문장이 바뀌었으면(맥락 보완 등) 새로 만든다."""
    current = _current.get()
    return current if current is not None and current.text == text else make(text)
