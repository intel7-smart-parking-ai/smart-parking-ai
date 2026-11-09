"""Answer facilities and operating rules using the validated shared catalog."""
from .query import Clarification, core, name


class PolicyValues(dict):
    """Keep missing catalog entries distinct from confirmed business facts."""
    missing = False


def fetch(_day, query):
    if query.route_requested:
        from .routing import fetch as route
        return PolicyValues(route(_day, query))
    lots = query.lots or ([query.resolved_vehicle["lot_id"]] if query.resolved_vehicle else [])
    if len(lots) != 1:
        raise Clarification("어느 주차장의 이용 정책·시설을 확인할까요? 개발원, 부산역, 김해공항, 신세계 중 하나를 알려주세요.")
    if not query.topics:
        raise Clarification("운영 시간, 할인, 예약 정책, 시설 위치 중 어떤 정보가 필요한가요?")
    lot = lots[0]
    guide = core()[0].catalog.lot(lot).get("guide") or {}
    values = PolicyValues()
    for key, label in query.topics:
        value = guide.get(key)
        if query.policy_overview and (not isinstance(value, str) or not value.strip()):
            continue
        if not isinstance(value, str) or not value.strip():
            values.missing = True
        values["" if lot == "C" and key == "reservation" else name(lot) + " " + label] = (
            value.strip() if isinstance(value, str) and value.strip()
            else "안내가 아직 등록되지 않아 확인할 수 없어요. 현장 안내판이나 관리자에게 확인해 주세요.")
    if not values:
        values[name(lot) + " 이용 정책"] = "등록된 이용 정책이 아직 없어요. 현장 안내판이나 관리자에게 확인해 주세요."
        values.missing = True
    return values
