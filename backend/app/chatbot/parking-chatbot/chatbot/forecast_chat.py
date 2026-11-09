"""혼잡도 예측 질문 (C+안, 11/09).

김채린 브랜치 forecast_chat.py·report_lab/forecast_query.py의 규칙 기반 날짜·시각 해석만 가져왔다.
LLM·report_lab은 쓰지 않고, 예측 숫자는 서버 조회(`GET /api/v1/congestion/forecast`) 결과만 쓴다.
"""
import re
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))
WANTS = re.compile(r"혼잡|붐[비빌벼빈볐]|북적|한산|사람\s*많|복잡할|예측")
WHEN = re.compile(r"오늘|내일|모레|요일|주말|\d+\s*시|\d+\s*월")
AVAIL = re.compile(r"빈자리|만차|자리.{0,8}(?:있|없|남)")


FUTURE = re.compile(r"내일|모레|요일|주말|\d+\s*월\s*\d+\s*일|(?:오전|오후|아침|저녁|밤|새벽)\s*\d+\s*시|\d+\s*시")
NOT_FORECAST = re.compile(r"지금|현재|\d+\s*시간\s*(?:전|후|뒤)|추천")


def wants(text):
    """'내일 오후 3시 공항 붐벼?'처럼 **특정 미래 시점**의 혼잡을 묻는 문장만.

    '지금 붐벼?', '덜 붐비는 곳 추천해줘'(현재 빈자리 비교), '출발 3시간 전에 도착하면'(상대 시각)은
    기존 처리(현재 빈자리 안내)로 넘긴다.
    """
    if NOT_FORECAST.search(text) or not FUTURE.search(text):
        return False
    return bool(WANTS.search(text) or (re.search(r"내일|모레|요일|주말", text) and AVAIL.search(text)))


def parse_when(text, now):
    """(target datetime | None, 이유). 오전·오후가 없으면 1~12시는 되묻는다(추측 금지)."""
    day = now.date() + timedelta(days=2 if "모레" in text else 1 if "내일" in text else 0)
    weekday = re.search(r"([월화수목금토일])요일", text)
    if weekday:
        index = "월화수목금토일".index(weekday[1])
        offset = (index - now.weekday()) % 7
        if re.search(r"다음\s*주", text):
            offset = 7 - now.weekday() + index
        day = now.date() + timedelta(days=offset)
    absolute = re.search(r"(\d{1,2})\s*월\s*(\d{1,2})\s*일", text)
    if absolute:
        try:
            day = datetime(now.year, int(absolute[1]), int(absolute[2])).date()
        except ValueError:
            return None, "날짜"
    matches = list(re.finditer(r"(\d{1,2})\s*시(?:\s*(\d{1,2})\s*분)?", text))
    if not matches:
        return None, "시각"
    m = matches[-1]
    hour, minute = int(m[1]), int(m[2] or 0)
    if "반" in text[m.end():m.end() + 2]:
        minute = 30
    if re.search(r"오후|저녁|밤", text) and 1 <= hour <= 12:
        hour = hour % 12 + 12
    elif re.search(r"오전|새벽|아침", text) and 1 <= hour <= 12:
        hour = hour % 12
    elif not (13 <= hour <= 23 or hour == 0):
        return None, "오전오후"
    target = datetime(day.year, day.month, day.day, hour, minute, tzinfo=KST)
    if weekday and target <= now and not re.search(r"오늘|이번\s*주", text):
        target += timedelta(days=7)
    return target, None


def answer(engine, s, text, lots):
    """engine._respond 결과 또는 None(예측 질문이 아님)."""
    if not wants(text):
        return None
    meta = {"intent": "congestion_forecast", "source": "rule"}
    if engine.current_alert().get("level", 0) >= 2:
        return engine._respond(s, "경보 중에는 현장 안전 안내를 먼저 따라 주세요.", chip_set="alert")
    if len(lots) > 1:
        return engine._respond(s, "한 번에 한 주차장을 선택해 주세요. 김해공항과 신세계 중 어디가 궁금하세요?", meta=meta)
    lot = lots[0] if lots else engine.DC.focus(s)
    if lot not in ("C", "D"):
        return engine._respond(s, "혼잡 예측은 김해공항·신세계(가상 시연 주차장)에서 제공해요. 어느 주차장이 궁금하세요?",
                               chip_set=["lot_C", "lot_D"], meta=meta)
    now = datetime.now(KST)
    target, missing = parse_when(text, now)
    if target is None:
        ask = {"오전오후": "오전인지 오후인지 함께 알려주세요. 예: “내일 오후 3시”",
               "날짜": "날짜를 다시 알려주세요. 예: “11월 12일 오후 2시”"}.get(missing, "언제 방문하시나요? 예: “내일 오후 3시”")
        return engine._respond(s, ask, meta=meta)
    if target <= now:
        return engine._respond(s, "말씀하신 시각은 이미 지났어요. 방문할 날짜와 시간을 다시 알려주세요.", meta=meta)
    if target > now + timedelta(days=7):
        return engine._respond(s, "예측은 앞으로 일주일까지만 제공해요. 그 안의 방문 날짜를 알려주세요.", meta=meta)
    try:
        result = engine.data.congestion_forecast(lot, target)
        selected = result["selected"]
        if (result.get("lot_id") != lot or result.get("is_mock") is not True
                or selected["level"] not in ("여유", "보통", "혼잡")
                or not 0 <= selected["empty"] <= selected["capacity"]):
            raise ValueError("invalid forecast")
    except Exception:
        return engine._respond(s, "시연 혼잡 예측을 조회하지 못했어요. 잠시 후 다시 시도해 주세요.",
                               meta={**meta, "unavailable": True})
    engine.DC.remember(s, lot=lot)
    phrase = {"여유": "여유로울", "보통": "보통 수준일", "혼잡": "혼잡할"}[selected["level"]]
    day = "오늘" if target.date() == now.date() else "내일" if target.date() == now.date() + timedelta(days=1) \
        else f"{target.month}월 {target.day}일"
    clock = f"{'오전' if target.hour < 12 else '오후'} {target.hour % 12 or 12}시" + (f" {target.minute}분" if target.minute else "")
    mae = result.get("mae_spaces")
    reply = (f"{day} {clock} {engine.config.catalog.name(lot)}은 {phrase} 것으로 예상돼요.\n"
             f"예상 빈자리: 약 {selected['empty']}자리 (점유 약 {selected.get('occupancy_percent', '—')}%"
             + (f", 오차 약 ±{mae}자리" if mae is not None else "") + ")\n※ 가상 시연 이력 기준 예측이에요. 실제 자리를 보장하지 않아요.")
    return engine._respond(s, reply, card={"type": "congestion_forecast", "data": result},
                           meta={**meta, "forecast_source": result.get("source")})
