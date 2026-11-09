from .source import count, read


def fetch(day, query=None):
    path = "/api/v1/reports/revenue?month=" + day[:7]
    if query and len(query.lots) == 1:
        path += "&lot_id=" + query.lots[0]
    elif query and len(query.lots) > 1:
        from .query import Clarification
        raise Clarification("매출을 확인할 주차장 하나를 지정하거나 전체 매출을 요청해 주세요.")
    data = read(path, {
        "daily_revenue": [{"date": day, "net_krw": 14500, "completed_count": 5, "canceled_count": 1}]
    })
    rows = data["daily_revenue"]
    if not isinstance(rows, list):
        raise ValueError("Invalid revenue rows")
    row = next((row for row in rows if row["date"] == day), None)
    if row is None:
        return {"순매출(원)": 0, "완료": 0, "취소": 0}
    return {"순매출(원)": count(row["net_krw"]), "완료": count(row["completed_count"]),
            "취소": count(row["canceled_count"])}
