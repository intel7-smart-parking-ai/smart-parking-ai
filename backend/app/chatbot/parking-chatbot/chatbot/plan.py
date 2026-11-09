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
SPOT_WORDS = re.compile(r"주차장|자리|빈\s*칸|빈칸|여유|만차|남은|남았|비었|비어")
MONEY_WORDS = re.compile(r"요금|주차비|비용|금액|돈|\d\s*원")


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

    @property
    def single_lot(self):
        return self.lots[0] if len(self.lots) == 1 else None

    def hints(self) -> dict:
        """대화 AI에 넘기는 참고 정보(빈 값은 뺀다)."""
        out = {"rule": self.rule, "mentioned_lots": list(self.lots), "zone": self.zone, "car_number": self.car,
               "purpose_lot": self.purpose_lot, "wants": list(self.wants)}
        return {k: v for k, v in out.items() if v not in (None, [], "", {})}

    def summary(self) -> str:
        parts = [f"wants={'+'.join(self.wants) or '-'}"]
        if self.lots:
            parts.append("lots=" + "+".join(self.lots))
        if self.purpose_lot:
            parts.append(f"purpose={self.purpose_lot}")
        if self.zone:
            parts.append(f"zone={self.zone}")
        if self.car:
            parts.append(f"car={self.car}")
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
        ("rates", bool(re.search(r"요금\s*(?:기준|표|체계|정책)|기본\s*요금|시간당|최대\s*요금|싸|저렴|비싸", text))),
        ("policy", bool(_POLICY.search(text))),
        ("alert", bool(_ALERT.search(text))),
        ("compare", bool(_CHOICE.search(text))),
        ("future", bool(_FUTURE.search(text))),
    )
    return tuple(name for name, hit in checks if hit)


def make(text: str) -> Plan:
    """문장 하나의 계획표. 실패해도 빈 계획표를 돌려준다(답을 막지 않음)."""
    if not isinstance(text, str) or not text.strip():
        return Plan(text or "")
    from .info_gaps import hinted_lot
    from .parser import extract_lots, extract_vehicle, extract_zone, normalize_car_id, parse
    try:
        rule = parse(text, allow_llm=False)
        rule_reading = {"intent": rule.intent, **{k: v for k, v in rule.params.items()
                                                  if k in ("lot_id", "zone", "car_id", "rank", "purpose_hint")}}
    except Exception:
        rule_reading = None
    # 별칭(공항·신세계)은 주차 맥락이 있어야 인정되므로, 대화 해석(dialog.query)과 같게 맥락을 붙여 읽는다.
    lots = tuple(extract_lots(text + " 빈자리"))
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
