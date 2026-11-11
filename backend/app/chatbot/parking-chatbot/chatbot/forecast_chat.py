"""혼잡도 예측 질문 (C+안, 11/09).

김채린 브랜치 forecast_chat.py·report_lab/forecast_query.py의 규칙 기반 날짜·시각 해석만 가져왔다.
LLM·report_lab은 쓰지 않고, 예측 숫자는 서버 조회(`GET /api/v1/congestion/forecast`) 결과만 쓴다.
"""
import re
import time
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))
WANTS = re.compile(r"혼잡|붐[비빌벼빈볐]|북적|한산|사람\s*많|복잡할|예측")
WHEN = re.compile(r"오늘|내일|모레|요일|주말|\d+\s*시|\d+\s*월")
AVAIL = re.compile(r"빈자리|만차|자리.{0,8}(?:있|없|남)")


FUTURE = re.compile(r"내일|모레|요일|주말|\d+\s*월\s*\d+\s*일|(?:오전|오후|아침|저녁|밤|새벽)\s*\d+\s*시|\d+\s*시")
NOT_FORECAST = re.compile(r"지금|현재|\d+\s*시간\s*(?:전|후|뒤)|추천")
# '안 붐비는 시간대는?', '언제 한산해?', '몇 시에 가야 덜 붐벼?' — 하루 중 여유로운 시각을 묻는 질문
BEST_TIME = re.compile(r"시간대|언제|몇\s*시(?!간)")
# 되물은 뒤의 짧은 답: '오후', '내일 오후 3시요', '토요일'
FOLLOW_UP = re.compile(r"^\s*(?:(?:오늘|내일|모레|다음\s*주\s*)?[월화수목금토일]?요?일?\s*)?(?:오늘|내일|모레)?\s*(?:오전|오후|아침|저녁|밤|새벽)?\s*"
                       r"(?:\d{1,2}\s*시(?:\s*\d{1,2}\s*분|\s*반)?)?\s*(?:요|이요|에요|이에요|쯤|정도)?\s*[.?!~]*\s*$")
ASK_TTL_SEC = 180
# 시간대만 말했을 때의 대표 시각
PART_HOURS = {"새벽": 6, "아침": 9, "오전": 10, "점심": 12, "낮": 13, "오후": 15, "저녁": 18, "밤": 21}


def wants(text):
    """'내일 오후 3시 공항 붐벼?'처럼 **특정 미래 시점**의 혼잡을 묻는 문장만.

    '지금 붐벼?', '덜 붐비는 곳 추천해줘'(현재 빈자리 비교), '출발 3시간 전에 도착하면'(상대 시각)은
    기존 처리(현재 빈자리 안내)로 넘긴다.
    """
    if NOT_FORECAST.search(text):
        return False
    if WANTS.search(text) and BEST_TIME.search(text) and not re.search(r"\d+\s*시", text):
        return True
    if not FUTURE.search(text):
        return False
    return bool(WANTS.search(text) or (re.search(r"내일|모레|요일|주말", text) and AVAIL.search(text)))


def _follow_up(s, text):
    """앞 턴에서 시각을 되물었고 이번 말이 그 답(짧은 시각 표현)이면 앞 질문과 이어 붙인 문장."""
    ask = getattr(s, "forecast_ask", None)
    s.forecast_ask = None
    stripped = (text or "").strip()
    if (ask and time.monotonic() < ask["expires"] and stripped and len(stripped) <= 15
            and FOLLOW_UP.match(stripped) and re.search(r"오늘|내일|모레|요일|오전|오후|아침|저녁|밤|새벽|\d", stripped)):
        return ask["text"] + " " + stripped, ask.get("lots") or []
    return None


def _best_time(engine, s, text, lot, now, meta):
    """하루 중 여유로운 시각(서버 예측의 시간대별 값만 사용)."""
    day = now.date() + timedelta(days=2 if "모레" in text else 1 if "내일" in text else 0)
    weekday = re.search(r"([월화수목금토일])요일", text)
    if weekday:
        day = now.date() + timedelta(days=("월화수목금토일".index(weekday[1]) - now.weekday()) % 7)
    start = datetime(day.year, day.month, day.day, tzinfo=KST)
    target = max(start + timedelta(hours=12), (now + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)) \
        if day == now.date() else start + timedelta(hours=12)
    if target.date() != day:
        return engine._respond(s, "오늘 남은 시간대가 없어요. 내일 중 언제 방문하실지 알려주시면 예측해 드릴게요.", meta=meta)
    try:
        result = engine.data.congestion_forecast(lot, target)
        hours = [h for h in result.get("hours") or []
                 if isinstance(h, dict) and isinstance(h.get("at"), str) and h.get("level") in ("여유", "보통", "혼잡")
                 and type(h.get("empty")) is int and type(h.get("occupancy_percent")) is int]
        if result.get("lot_id") != lot or result.get("is_mock") is not True or not hours:
            raise ValueError("invalid forecast")
    except Exception:
        return engine._respond(s, "시연 혼잡 예측을 조회하지 못했어요. 잠시 후 다시 시도해 주세요.",
                               meta={**meta, "unavailable": True})
    engine.DC.remember(s, lot=lot)

    def clock(item):
        at = datetime.fromisoformat(item["at"]).astimezone(KST)
        return f"{'오전' if at.hour < 12 else '오후'} {at.hour % 12 or 12}시"
    calm = sorted(hours, key=lambda h: (h["occupancy_percent"], h["at"]))[:3]
    busy = max(hours, key=lambda h: (h["occupancy_percent"], h["at"]))
    label = "오늘" if day == now.date() else "내일" if day == now.date() + timedelta(days=1) else f"{day.month}월 {day.day}일"
    calm_text = "·".join(clock(h) for h in sorted(calm, key=lambda h: h["at"]))
    reply = (f"{label} {engine.config.catalog.name(lot)}은 {calm_text}가 가장 여유로울 것으로 예상돼요 "
             f"(예상 빈자리 약 {calm[0]['empty']}자리).\n가장 붐빌 것으로 보이는 시간은 {clock(busy)}(점유 약 {busy['occupancy_percent']}%)예요.\n"
             "※ 가상 시연 이력 기준 예측이에요. 실제 자리를 보장하지 않아요.")
    return engine._respond(s, reply, card={"type": "congestion_forecast", "data": result},
                           meta={**meta, "forecast_source": result.get("source"), "forecast_mode": "best_time"})


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
        # '내일 아침에 가면?'처럼 시간대만 말했으면 대표 시각으로 본다(답에 기준 시각을 밝힘)
        part = re.search(r"새벽|아침|오전|점심|낮|오후|저녁|밤", text)
        if not part:
            return None, "시각"
        hour = PART_HOURS[part[0]]
        target = datetime(day.year, day.month, day.day, hour, tzinfo=KST)
        if weekday and target <= now and not re.search(r"오늘|이번\s*주", text):
            target += timedelta(days=7)
        if target <= now and not weekday and not absolute and not re.search(r"오늘|내일|모레|주말", text):
            target += timedelta(days=1)
        return target, None
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
    # 날짜 없이 '3시에 붐벼?'라고 했는데 오늘 그 시각이 지났으면 내일 같은 시각으로 본다
    if (target <= now and not weekday and not absolute
            and not re.search(r"오늘|내일|모레|주말", text)):
        target += timedelta(days=1)
    return target, None


def answer(engine, s, text, lots):
    """engine._respond 결과 또는 None(예측 질문이 아님)."""
    joined = _follow_up(s, text)
    if joined:
        text, lots = joined[0], (lots or joined[1])
    if not wants(text):
        return None
    if not joined and not engine.route_check.keep(text, "congestion_forecast",
                                                  urgent=engine.current_alert().get("level", 0) >= 2):
        return None   # [실험] AI 확인이 혼잡 예측 질문이 아니라고 함 → 대화 AI로
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
    if BEST_TIME.search(text) and not re.search(r"\d+\s*시", text):
        return _best_time(engine, s, text, lot, now, meta)
    target, missing = parse_when(text, now)
    if target is None:
        ask = {"오전오후": "오전인지 오후인지 함께 알려주세요. 예: “내일 오후 3시”",
               "날짜": "날짜를 다시 알려주세요. 예: “11월 12일 오후 2시”"}.get(missing, "언제 방문하시나요? 예: “내일 오후 3시”")
        # 다음 말('오후', '내일 3시요')을 이 질문과 이어 붙인다
        s.forecast_ask = {"text": text, "lots": [lot], "expires": time.monotonic() + ASK_TTL_SEC}
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
    if not re.search(r"\d+\s*시", text):
        part = re.search(r"새벽|아침|오전|점심|낮|오후|저녁|밤", text)
        clock = f"{part[0]}({clock} 기준)" if part else clock
    mae = result.get("mae_spaces")
    reply = (f"{day} {clock} {engine.config.catalog.name(lot)}은 {phrase} 것으로 예상돼요.\n"
             f"예상 빈자리: 약 {selected['empty']}자리 (점유 약 {selected.get('occupancy_percent', '—')}%"
             + (f", 오차 약 ±{mae}자리" if mae is not None else "") + ")\n※ 가상 시연 이력 기준 예측이에요. 실제 자리를 보장하지 않아요.")
    return engine._respond(s, reply, card={"type": "congestion_forecast", "data": result},
                           meta={**meta, "forecast_source": result.get("source")})
