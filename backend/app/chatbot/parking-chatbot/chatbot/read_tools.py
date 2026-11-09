"""Read-only chat operations backed by the current space and session APIs."""
from datetime import datetime
import re
from urllib.parse import quote
from zoneinfo import ZoneInfo

from .parser import extract_lots
from .turn_policy import vehicle_mentions


SPACE = re.compile(r"(?<![A-Z0-9])([A-D]-\d{2})(?!\d)", re.I)
SPACE_QUESTION = re.compile(r"비었|빈\s*칸|상태|대도|주차해도|사용.{0,4}(?:가능|돼)|확인\s*중")
SESSION_QUESTION = re.compile(r"언제.{0,8}(?:입차|들어|주차)|입차\s*(?:시각|시간)|(?:몇\s*(?:분|시간)|얼마나).{0,8}(?:째|있|지났|됐)|(?:아까\s*)?(?:나갔|출차했)|출차\s*여부")
LOCATION = re.compile(r"어디|위치|찾")
LOT_SHORT = {"개발원": "A", "부산역": "B", "김해공항": "C", "신세계": "D"}


def handle(engine, session, text, mentions=None):
    """Route explicit lookup requests and context references to validated reads."""
    if re.search(r"다른\s*사람|남의\s*차|모든\s*차|전체\s*차|무슨\s*차", text):
        return None
    numbers = vehicle_mentions(text) if mentions is None else mentions
    slot = SPACE.search(text)
    if SPACE_QUESTION.search(text) and (slot or session.last_space and re.search(r"거기|그\s*칸|그\s*자리", text)):
        if slot:
            lots = extract_lots(text)
            if not lots:
                lots = [key for alias, key in LOT_SHORT.items() if alias in text]
            if len(lots) != 1:
                return None
            lot, space = lots[0], slot[1].upper()
        else:
            lot, space = session.last_space
        session.last_space = (lot, space)
        return engine._run(session, "", "get_empty_spots", {"lot_id": lot, "space_id": space})

    if engine.current_alert().get("level", 0) >= 2:
        return None
    if 1 < len(numbers) <= 4 and LOCATION.search(text):
        answers = []
        for number in numbers:
            vehicle = engine._find_vehicle(number)
            if vehicle:
                answers.append(engine.T.car_location(vehicle) if vehicle.get("status") != engine.LOCATION_PENDING
                               else engine.T.car_pending(vehicle))
            else:
                answers.append(f"{number}번 차량의 현재 입차 기록을 확인하지 못했어요.")
        return engine._respond(session, "\n".join(answers))

    if len(numbers) != 1 or not SESSION_QUESTION.search(text):
        return None
    number = numbers[0]
    if engine.config.getenv("REPORT_CHAT_ENABLED", "0") != "1":
        return None
    from backend.app.chatbot.report_lab.source import read

    try:
        payload = read(f"/api/v1/sessions?car_id={number}", {"items": []})
        rows = payload["items"]
        if (not isinstance(rows, list) or payload.get("next_cursor")
                or any(row.get("car_id") != number or row.get("status") not in ("active", "closed") for row in rows)):
            raise ValueError("Incomplete session result")
        active = [row for row in rows if row["status"] == "active"]
        if len(active) > 1:
            return engine._respond(session, f"{number}번 차량의 세션을 하나로 확인하지 못했어요.")
        row = active[0] if active else next(iter(rows), None)
        if row is None:
            return engine._respond(session, f"{number}번 차량의 입·출차 기록을 확인하지 못했어요.")
        if re.search(r"나갔|출차", text):
            status = "현재 주차 중이에요." if row["status"] == "active" else "서버 기록상 출차했어요."
            return engine._respond(session, f"{number}번 차량은 {status}")
        if re.search(r"언제|입차", text):
            entered = datetime.fromisoformat(row["entry_at"]).astimezone(ZoneInfo("Asia/Seoul"))
            return engine._respond(session, f"{number}번 차량은 {entered:%m월 %d일 %H:%M}에 입차했어요.")
        fee = read("/api/v1/sessions/" + quote(row["session_id"], safe="") + "/fee", {})
        minutes = fee["duration_minutes"]
        if fee["session_id"] != row["session_id"] or type(minutes) is not int or minutes < 0:
            raise ValueError("Invalid session duration")
        return engine._respond(session, f"{number}번 차량의 서버 기록상 주차 시간은 {minutes:,}분이에요.")
    except (OSError, ValueError, KeyError, TypeError):
        return engine._respond(session, f"{number}번 차량의 입·출차 정보를 확인할 수 없어요.")
