from .source import count, read


def fetch(_day, query=None):
    path = "/api/v1/alerts?limit=1"
    if query and len(query.lots) == 1:
        path += "&lot_id=" + query.lots[0]
    elif query and len(query.lots) > 1:
        from .query import Clarification
        raise Clarification("경보를 확인할 주차장 하나를 지정하거나 전체 경보를 요청해 주세요.")
    data = read(path, {
        "active_count": 1, "acknowledged_count": 2, "resolved_count": 3
    })
    return {"활성": count(data["active_count"]), "확인": count(data["acknowledged_count"]),
            "해제": count(data["resolved_count"])}
