"""Goals preserved by the offline path for multi-part parking questions.

The model gets the original utterance. These deterministic hints constrain known
identifiers and provide a limited answer when the model is unavailable.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import re

from .. import config, dialog_context, info_gaps, templates as T
from ..parser import extract_lots, extract_vehicle, extract_virtual, extract_zone, normalize_car_id


@dataclass(frozen=True)
class Query:
    original: str
    candidates: tuple[str, ...] = ()
    excluded: tuple[str, ...] = ()
    zone: str | None = None
    car_id: str | None = None
    wants_location: bool = False
    wants_fee: bool = False
    wants_payment: bool = False
    wants_parking: bool = False
    wants_rates: bool = False
    compare: bool = False
    future: bool = False
    policy_query: str | None = None
    prefer_low_rate: bool = False
    clarification: str | None = None

    @property
    def needs_vehicle(self) -> bool:
        return not self.car_id and (self.wants_location or self.wants_fee or self.wants_payment)

    def with_car(self, car_id: str):
        if not normalize_car_id(car_id):
            raise ValueError("Expected an unambiguous two-digit car ID")
        return replace(self, car_id=car_id, clarification=None)


# Each pattern identifies a user-facing question in the offline fallback.
# Leave invitation, evacuation, confirmation and payment execution to workflows.
_LOCATION = re.compile(r"위치|어디|찾|주차(?:돼|되어|했)|입차(?:했|해)|주차\s*(?:중|여부)")
_FEE = re.compile(r"요금|주차비|금액|얼마|비용")
_CHEAP = re.compile(r"저렴|싸[요지거나]?|싼|먼저\s*싼")
_PAYMENT = re.compile(r"(?:결제|정산)(?:도|까지|를|해)?\s*(?:해\s*줘|해주세요|해\s*주|부탁|하고\s*싶|할게|할래|진행|시작)")
_STOP_PAYMENT = re.compile(r"(?:결제|정산)(?:은|는|를|는요)?\s*(?:는\s*)?(?:하지\s*(?:말|마)|말고|안\s*(?:해|할)|취소|필요\s*없)")
_PARKING = re.compile(r"주차|자리|빈칸|혼잡|붐[벼빌비]|널널|한산|가득|댈\s*수|세울\s*수")
_FUTURE = re.compile(r"(?:분|시간)\s*(?:뒤|후|뒤에|후에)|도착(?:할|하면|해|예정)|내일|나중에|이따")
_CHOICE = re.compile(r"어디가|어느\s*(?:곳|쪽)|비교|추천|더\s*(?:나|낫|좋)|장단점|골라|둘\s*중|두\s*곳")
_FOLLOW = re.compile(r"그럼|그러면|거기|그\s*곳|그쪽|둘\s*중|두\s*곳|대신|바꿔|(?:자리|구역)(?:는|은)[?？]?$")
_POLICY = re.compile(r"이용\s*(?:자격|조건|규칙)|규정|일반차.{0,20}(?:충전|전용|장애인)|(?:전기차|장애인).{0,18}(?:아닌데|아니어도)|(?:확인\s*중|미확인).{0,12}(?:뜻|의미|없다는)")
_EXCLUDE = re.compile(r"말고|빼고|제외(?:하고)?|빼줘")
_ALERT = re.compile(r"대피|피난|경보|화재|불났|연기")
_CAR = re.compile(r"내\s*차|차량|차(?:를|가)?\s*(?:못\s*)?찾|차가\s*어디|차\s*위치")
_SPACE_WORDS = re.compile(r"자리|빈\s*칸|빈칸|비어|비었|남은|남았|만차|여유|댈\s*수|세울\s*수")
_NEGATE_READ = re.compile(r"(?:위치|요금|자리)(?:는|은|를)?\s*(?:말고|빼고|필요\s*없|알려\s*주지\s*마)")


def _lots(text: str):
    # Enable catalog contextual aliases, e.g. '공항' inside a comparison.
    return tuple(extract_lots(text + " 빈자리"))


def _filters(text: str):
    parts = _EXCLUDE.split(text)
    # A zone immediately before '말고' excludes the zone, not its venue.
    # "공항에서 일반 자리 말고 충전" still scopes the read to the airport.
    venue_scope = []
    excluded_lots = []
    for part in parts[:-1]:
        if extract_zone(part) and re.search(r"(?:일반|충전|장애인|전기차|전용)(?:\s*(?:자리|칸|구역))?\s*$", part):
            venue_scope.extend(_lots(part))
        else:
            excluded_lots.extend(_lots(part))
    excluded = tuple(dict.fromkeys(excluded_lots))
    selected = tuple(k for k in dict.fromkeys([*venue_scope, *_lots(parts[-1])]) if k not in excluded)
    if excluded and not selected:
        selected = tuple(k for k in config.catalog.ids() if k not in excluded)
    # Zone after “일반 자리 말고 충전 자리” overrides the earlier zone.
    zone = extract_zone(parts[-1])
    return selected, excluded, zone


def _car_query(text, s, raw_car, car_only):
    if car_only:
        previous = getattr(s, "dialog_query", None)
        pending = getattr(s, "pending", None)
        if (isinstance(previous, Query) and previous.needs_vehicle
                and pending is not None and s.flow.value == "ask_vehicle"
                and normalize_car_id(raw_car)):
            return previous.with_car(raw_car)
        return None

    fee = bool(_FEE.search(text))
    explicit = raw_car is not None
    car_words = explicit or bool(_CAR.search(text))
    location = car_words and bool(_LOCATION.search(text))
    forbidden = bool(_STOP_PAYMENT.search(text))
    payment = bool(_PAYMENT.search(text)) and not forbidden
    wants_fee = fee and (car_words or forbidden or ("요금만" in text and not _lots(text)))
    if "기본요금" in text or "요금표" in text:
        wants_fee = False
    # Do not steal help/education, exit, or cancelled-only commands.
    if not (location or wants_fee) or re.search(r"무슨\s*질문|질문할\s*수|번호대|번호\s*목록|차단기|출구", text):
        if raw_car != "ambiguous":
            return None
    for denied in _NEGATE_READ.finditer(text):
        if denied[0].startswith("위치"):
            location = False
        elif denied[0].startswith("요금"):
            wants_fee = False
    if not (location or wants_fee) and raw_car != "ambiguous":
        return None
    issues = {"ambiguous": "차량 번호가 여러 개예요. 어느 차를 조회할까요?",
              "missing": T.ASK_DIFFERENT_VEHICLE, "short": T.SHORT_VEHICLE, "invalid": T.INVALID_VEHICLE}
    car = normalize_car_id(raw_car) if explicit else normalize_car_id(getattr(s, "car_id", None))
    return Query(text, car_id=car, wants_location=location, wants_fee=wants_fee,
                 wants_payment=payment, clarification=issues.get(raw_car))


def interpret(text: str, session) -> Query | None:
    """Return a supported compound read or None for the existing control/lookup path."""
    if not isinstance(text, str) or not text.strip() or _ALERT.search(text):
        return None
    text = text.strip()
    # Virtual plates and range queries use their own identifier/selection contract.
    if extract_virtual(text) or re.search(r"\d+\s*번\s*(?:부터|에서|~|-)|\d+\s*~\s*\d+", text):
        return None
    raw_car, car_only = extract_vehicle(text)
    vehicle = _car_query(text, session, raw_car, car_only)
    if vehicle:
        return vehicle
    if car_only or raw_car:
        return None

    selected, excluded, zone = _filters(text)
    previous = getattr(session, "dialog_query", None)
    previous = previous if isinstance(previous, Query) else None
    followup = bool(_FOLLOW.search(text))
    previous_lots = tuple(dialog_context.offered(session)) or (
        (dialog_context.focus(session),) if dialog_context.focus(session) else ())
    if previous and followup and not previous_lots:
        previous_lots = previous.candidates
    if not selected and followup:
        selected = previous_lots
        if previous and not zone and not _EXCLUDE.search(text):
            zone = previous.zone
    policy = text if _POLICY.search(text) else None
    if policy and not _PARKING.search(text) and not selected:
        return Query(text, policy_query=policy)
    # A policy question about an EV space is not a request for general vacancies.
    if policy and not re.search(r"빈\s*자리|몇\s*(?:자리|칸)|비교|저렴|싼", text):
        return Query(text, candidates=selected, policy_query=policy)
    future = bool(_FUTURE.search(text)) and bool(_PARKING.search(text) or zone)
    prefer_low = bool(_CHEAP.search(text))
    compare = bool(len(selected) > 1 and (_CHOICE.search(text) or prefer_low or _FEE.search(text)))
    if not selected and not excluded and prefer_low and _SPACE_WORDS.search(text):
        # '제일 싼 데 어디야, 근데 자리도 있어야 돼' → 전 주차장의 빈자리·기본요금을 함께 비교(요금만 보는 답 금지)
        selected, compare = tuple(config.catalog.ids()), True
    follow_zone = bool(followup and zone and selected)
    corrected_zone = bool(_EXCLUDE.search(text) and zone and _PARKING.search(text))
    if not (future or compare or follow_zone or corrected_zone or (excluded and _PARKING.search(text)) or policy):
        if _CHOICE.search(text) and re.search(r"둘\s*중|두\s*곳", text) and not selected:
            return Query(text, clarification="비교하고 싶은 두 주차장이 어디인가요?")
        return None
    if not selected and excluded:
        return Query(text, excluded=excluded, clarification="남은 후보가 없어요. 어느 주차장을 조회할까요?")
    if not selected and not excluded and future:
        hinted = info_gaps.hinted_lot(text)
        selected = (hinted,) if hinted else previous_lots
    # Only inherit candidate names, never old counts or rates.
    wants_rates = compare or bool(prefer_low and (selected or excluded))
    return Query(text, candidates=selected, excluded=excluded, zone=zone, wants_parking=True,
                 wants_rates=wants_rates, compare=compare, future=future, policy_query=policy,
                 prefer_low_rate=prefer_low)
