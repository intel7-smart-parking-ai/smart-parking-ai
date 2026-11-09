from .source import count, read
from .query import Clarification, core


def format_values(values, query):
    from importlib import import_module
    fmt = import_module(core()[0].__package__ + ".answer_format")
    rows = []
    for key, value in values.items():
        if not key.endswith(" 빈자리"):
            continue
        lot, _, scope = key.removesuffix(" 빈자리").partition(" ")
        # The heading already names the space type; retain only the section.
        section = scope.split(" ")[0] if query.section else ""
        label = fmt.lot_name(lot, lot) + (" " + section if section else "")
        rows.append(f"{label} {value:,}자리")
    summary = fmt.vacancy(rows, query.space_type)
    if query.charging_status:
        summary += "\n충전 상태(가상 주차장은 시연 데이터)\n" + fmt.facts({k: v for k, v in values.items() if "충전 상태" in k})
    return summary


def fetch(_day, query=None):
    data = read("/api/v1/parking-lots", {"items": [
        {"lot_id": "A", "empty_spaces": 5, "total_spaces": 12},
        {"lot_id": "B", "empty_spaces": 0, "total_spaces": 16},
    ]})
    if not isinstance(data["items"], list) or not data["items"]:
        raise ValueError("Missing parking lot rows")
    values = {}
    seen = set()
    requested = query.lots if query else []
    if query and query.section and len(requested) != 1:
        raise Clarification("어느 주차장 안의 구역인가요? 주차장 이름과 구역을 함께 알려주세요.")
    for row in data["items"]:
        lot = row["lot_id"]
        if lot not in ("A", "B", "C", "D") or lot in seen:
            raise ValueError("Invalid parking lot")
        seen.add(lot)
        empty, total = count(row["empty_spaces"]), count(row["total_spaces"])
        if empty > total:
            raise ValueError("Invalid vacancy count")
        if requested and lot not in requested:
            continue
        label = lot
        if query and (query.space_type or query.section):
            if (query.section and not query.airport_section
                    and not any(space.startswith(query.section + "-") for space in core()[0].catalog.spaces(lot))):
                raise Clarification("해당 주차장의 구역을 확인하지 못했어요. 구역 이름을 다시 알려주세요.")
            detail = read(f"/api/v1/parking-lots/{lot}/spaces", {"items": [
                {"space_id": "A-01", "space_type": "general", "status": "empty", "zone": "A"},
                {"space_id": "A-02", "space_type": "ev", "status": "occupied", "zone": "A"},
                {"space_id": "B-01", "space_type": "ev", "status": "empty", "zone": "B"},
            ]})
            rows = detail["items"]
            if not isinstance(rows, list) or detail.get("next_cursor"):
                raise ValueError("Incomplete parking spaces")
            if len({row["space_id"] for row in rows}) != len(rows):
                raise ValueError("Duplicate parking spaces")
            filtered = []
            for space in rows:
                if space["status"] not in ("empty", "occupied", "uncertain", "unavailable"):
                    raise ValueError("Unknown parking status")
                if space["space_type"] not in ("general", "ev", "accessible"):
                    raise ValueError("Unknown space type")
                section = space["space_id"].split("-")[0]
                if query.airport_section and section == "B" and int(space["space_id"].split("-")[1]) > 24:
                    section = "C"
                if query.section and section != query.section:
                    continue
                if query.space_type and space["space_type"] != query.space_type:
                    continue
                filtered.append(space)
            empty = sum(space["status"] == "empty" for space in filtered)
            total = len(filtered)
            if query.charging_status:
                labels = {"available": "대기", "connected": "연결 대기", "charging": "충전 중", "completed": "충전 완료", "not_connected": "미연결", "unknown": "확인 불가"}
                for space in filtered:
                    charging = space.get("charging") or {}
                    state = labels.get(charging.get("status"), "확인 불가")
                    power = charging.get("power_kw")
                    values[f"{lot} {space['space_id']} 충전 상태"] = state + (f" · {power:g}kW" if isinstance(power, (int, float)) else "")
            if query.section:
                label += " " + ({"A": "P1", "B": "P2", "C": "P3"}[query.section]
                                if query.airport_section else query.section + "구역")
            if query.space_type:
                label += " " + {"general": "일반", "ev": "전기차", "accessible": "장애인"}[query.space_type]
        values[f"{label} 빈자리"] = empty
        values[f"{label} 전체 칸"] = total
    if not values:
        raise Clarification("지정한 주차장의 현재 빈자리를 확인하지 못했어요.")
    return values
