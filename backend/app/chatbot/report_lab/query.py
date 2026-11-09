"""Validated query scope, shared with the existing chatbot catalog and parser."""
from dataclasses import dataclass, field
from functools import lru_cache
from importlib import import_module
import re


class Clarification(ValueError):
    pass


@lru_cache
def core():
    import_module("backend.app.chatbot.router")
    root = "backend.app.chatbot._parking_runtime."
    return (import_module(root + "config"), import_module(root + "parser"),
            import_module(root + "info_gaps"), import_module(root + "contracts"))


@dataclass
class Query:
    lots: list[str] = field(default_factory=list)
    section: str | None = None
    airport_section: bool = False
    space_type: str | None = None
    charging_status: bool = False
    policy_overview: bool = False
    route_requested: bool = False
    route_destination: str | None = None
    route_plan: dict | None = None
    route_space: str | None = None
    vehicle_id: str | None = None
    full_plate: str | None = None
    vehicle_kind: str | None = None
    vehicle_problem: bool = False
    vehicle_candidates: list[str] = field(default_factory=list)
    individual_fee: bool = False
    topics: list[tuple[str, str]] = field(default_factory=list)
    resolved_vehicle: dict | None = None
    scope_problem: str | None = None


def lot_choice(text):
    config, _, _, _ = core()
    matches = []
    for lot in config.catalog.ids():
        row = config.catalog.lot(lot)
        aliases = [lot, row["name"]] + list(row.get("aliases", [])) + list(row.get("loose_aliases", []))
        if any(re.fullmatch(re.escape(alias) + r"\s*(?:이요|요|으로|로|은|는)?[?.!]*",
                            text.strip(), re.I) for alias in aliases):
            matches.append(lot)
    return matches[0] if len(matches) == 1 else None


def parse(text, *, selected_lot=None, confirmed_car=None):
    config, parser, gaps, _ = core()
    query = Query()
    query.lots = parser.extract_lots(text)
    chosen = lot_choice(text)
    if not query.lots and chosen:
        query.lots = [chosen]
    if selected_lot in config.catalog.ids():
        query.lots = [selected_lot]
    section = parser.extract_section(text)
    raw_section = re.search(r"(?<![A-Za-z0-9])([A-Za-z])\s*구역", text)
    airport = re.search(r"(?<![A-Za-z0-9])P([123])(?!\d)", text, re.I)
    if section:
        query.lots, query.section = [section[0]], section[1]
    elif raw_section:
        query.section = raw_section[1].upper()
        if not selected_lot:
            query.lots = []
    if airport:
        query.section = {"1": "A", "2": "B", "3": "C"}[airport[1]]
        query.airport_section = True
        if not query.lots:
            query.lots = ["C"]
        if query.lots != ["C"]:
            query.scope_problem = "P1·P2·P3는 김해공항 구역이에요. 어느 주차장과 구역을 확인할까요?"
    query.space_type = parser.extract_zone(text)
    query.charging_status = bool(re.search(r"충전\s*(?:상태|현황|중|여부|완료)|충전하고", text))
    if query.charging_status:
        query.space_type = "ev"
    virtual = parser.extract_virtual(text)
    car, _ = parser.extract_vehicle(text)
    candidates = list(dict.fromkeys(re.findall(r"(?<!\d)(\d{4})(?!\d)(?!\s*(?:원|년))", text)))
    candidates += [number for number in re.findall(r"(?<!\d)([1-9]\d)\s*번\s*(?:차량|차)", text) if number not in candidates]
    if not virtual and len(candidates) == 1 and len(candidates[0]) == 4 and re.search(PAYMENT_STATUS_PATTERN, text):
        virtual = candidates[0]
    if len(candidates) > 1:
        query.vehicle_problem = True
        query.vehicle_candidates = candidates
    elif virtual and not re.search(re.escape(virtual) + r"\s*(?:원|년)", text):
        query.vehicle_id = virtual[-4:]
        query.vehicle_kind = "plate"
        if parser.RE_PLATE.fullmatch(virtual):
            query.full_plate = virtual
    elif car in ("ambiguous", "invalid", "short", "missing"):
        query.vehicle_problem = True
        if car == "ambiguous":
            query.vehicle_candidates = list(dict.fromkeys(re.findall(r"(?<!\d)([1-9]\d)(?!\d)", text)))
    elif car:
        query.vehicle_id, query.vehicle_kind = car, "sticker"
    elif confirmed_car and re.search(r"내\s*차|제\s*차|그\s*차", text):
        query.vehicle_id, query.vehicle_kind = confirmed_car, "sticker"
    query.topics = [(key, label) for key, label, pattern in gaps.TOPICS if pattern.search(text)]
    query.policy_overview = bool(gaps.OVERVIEW.search(text))
    if query.policy_overview:
        query.topics = [(key, label) for key, label, _ in gaps.TOPICS]
    from .routing import request as route_request
    destination, route_lot = route_request(text)
    if destination:
        query.route_requested = True
        query.route_destination = destination
        if route_lot and not query.lots:
            query.lots = [route_lot]
        elif route_lot and query.lots != [route_lot]:
            query.scope_problem = '해당 목적지는 다른 주차장 도면에 있어요. 주차장과 목적지를 다시 확인해 주세요.'
        if not any(key == 'walking' for key, _ in query.topics):
            query.topics.append(('walking','시설 이동 안내'))
        spot=re.search(r'(?<![A-Za-z0-9])([A-Da-d])\s*-?\s*(\d{1,2})(?!\d|층)',text)
        if spot:
            query.route_space=f'{spot[1].upper()}-{int(spot[2]):02}'
    query.individual_fee = bool(re.search(r"요금|주차비|" + PAYMENT_STATUS_PATTERN, text)
                                and (query.vehicle_id or query.vehicle_problem
                                     or re.search(r"내\s*차|제\s*차|차량|그\s*차", text))
                                and not re.search(r"요금\s*(?:정책|표)|기본\s*요금", text))
    return query


PAYMENT_STATUS_PATTERN = r"(?:결제|결재)\s*(?:상태|여부|(?:완료\s*)?(?:됐|됬|됫|되었|됏|됀)\s*는지)|정산\s*상태"


def name(lot):
    return core()[0].catalog.name(lot)
