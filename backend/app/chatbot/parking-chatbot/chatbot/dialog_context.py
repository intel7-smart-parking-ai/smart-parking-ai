"""대화 맥락 이해: 이전 턴의 주차장을 기억해 지시어·생략·선택형 후속 질문을 완성한다.

방법론
- 대화 상태 추적(DST)의 '슬롯 + 유효 기간': 마지막으로 다룬 주차장(focus)과 방금 제시한 선택지(offered)를
  세션에 두고 QUERY_CONTEXT_TTL_SEC가 지나면 버린다(Dialogflow context lifespan, Rasa slot과 같은 역할).
- 불완전 발화 재작성(incomplete utterance rewriting): '거기로 안내해줘' → '김해공항 주차장로 안내해줘'처럼
  파서 앞에서 문장을 완성한다. 규칙 파서와 LLM 분류기 모두 완성된 문장을 받는다(모델 무관).
- 선택지 채우기: '어느 주차장?' 뒤의 '공항', '세 번째'를 제시한 목록에서 고른다.

원칙: 사용자가 주차장을 직접 말하면 그 값이 항상 우선한다. 맥락이 둘 이상을 가리키면 고르지 않고 되묻는다.
기록(history)에는 사용자가 쓴 원문을 그대로 남긴다.
"""
from __future__ import annotations

import re
import time

from . import config
from .parser import ParseResult, extract_lot

ANAPHOR = re.compile(r"(?<![가-힣])(?:그\s*주차장|이\s*주차장|해당\s*주차장|거기|그곳|그쪽|여기|이곳)")
ORDINAL = re.compile(r"(?<![가-힣0-9])(?:(첫|두|세|네)\s*(?:번째|번\s*째)|([1-4])\s*번째|(마지막))")
_ORDINAL_INDEX = {"첫": 0, "두": 1, "세": 2, "네": 3, "1": 0, "2": 1, "3": 2, "4": 3}
SECTION_ONLY = re.compile(r"^\s*([A-Za-z])\s*구역")
# 주차 영역 밖의 대화(차량·결제·호출)는 주차장 맥락을 바꾸지 않는다.
LOT_INTENTS = {"get_empty_spots", "set_destination", "fee_rule", "help"}


def _fresh(at: float) -> bool:
    return bool(at) and time.monotonic() - at <= config.QUERY_CONTEXT_TTL_SEC


def resolve_facility(s, intent, params, text, source):
    """주차장만 고른 후속 입력에 직전 시설 질문을 연결한다."""
    pending = s.facility_question
    fresh = _fresh(s.facility_question_at)
    s.facility_question = {}
    s.facility_question_at = 0
    lot = params.get("lot_id")
    if not pending or not fresh or intent != "get_empty_spots" or lot not in config.catalog.ids():
        return intent, params
    row = config.catalog.lot(lot)
    aliases = [row["name"], lot, lot + " 주차장"] + list(row.get("aliases", []))
    bare_lot = any(re.fullmatch(re.escape(alias) + r"\s*(?:이요|이야|요|로|으로|은|는)?[?.!]*",
                              (text or "").strip(), re.I) for alias in aliases)
    if source == "chip" or bare_lot:
        return "help", {**pending, "lot_id": lot}
    return intent, params


def focus(s):
    return s.focus_lot if s.focus_lot in config.catalog.ids() and _fresh(s.focus_at) else None


def offered(s):
    ids = config.catalog.ids()
    return [k for k in s.offered_lots if k in ids] if _fresh(s.offered_at) else []


def remember(s, lot=None, offered_lots=None):
    now = time.monotonic()
    if lot and lot in config.catalog.ids():
        s.focus_lot, s.focus_at = lot, now
    if offered_lots is not None:
        s.offered_lots, s.offered_at = tuple(offered_lots), (now if offered_lots else 0)


def _name(lot):
    return config.catalog.name(lot)


def _aliases(lot):
    row = config.catalog.lot(lot) or {}
    words = {row.get("name", "")} | set(row.get("aliases", [])) | set(row.get("loose_aliases", []))
    # 한 글자 별칭(비·씨·디)은 맥락이 있어도 오인이 잦아 제외한다.
    return sorted((w for w in words if len(w.replace(" ", "")) >= 2 and not w.isascii()), key=len, reverse=True)


def _licensed_alias(text):
    """맥락이 있을 때만 '신세계는?'·'공항'처럼 주차장 단어 하나를 주차장으로 인정한다."""
    hits = {}
    for lot in config.catalog.ids():
        for word in _aliases(lot):
            m = re.search(r"(?<![가-힣])" + re.escape(word), text)
            if m:
                hits.setdefault(lot, m)
                break
    if len(hits) != 1:
        return text
    lot, m = next(iter(hits.items()))
    return text[:m.start()] + _name(lot) + text[m.end():]


def rewrite(s, text: str) -> str:
    """맥락으로 문장을 완성한다. 맥락이 없거나 이미 주차장이 분명하면 그대로 돌려준다."""
    if not text:
        return text
    lot_now, choices = focus(s), offered(s)
    if not lot_now and not choices:
        return text
    if choices:
        m = ORDINAL.search(text)
        if m:
            index = len(choices) - 1 if m[3] else _ORDINAL_INDEX[m[1] or m[2]]
            if index < len(choices):
                text = text[:m.start()] + _name(choices[index]) + text[m.end():]
    if extract_lot(text) is None:
        text = _licensed_alias(text)
    section = SECTION_ONLY.match(text)
    if section and lot_now and _has_section(lot_now, section[1]) and extract_lot(text) in (None, section[1].upper()):
        return f"{_name(lot_now)} {text.strip()}"
    if extract_lot(text) is None and ANAPHOR.search(text):
        if lot_now:
            text = ANAPHOR.sub(_name(lot_now), text, count=1)
        elif len(choices) > 1:
            # 동률 등으로 하나를 정할 수 없으면 여러 주차장을 넣어 '어느 곳인가요?'로 되묻게 한다.
            text = ANAPHOR.sub(" ".join(_name(k) for k in choices), text, count=1)
    return text


def _has_section(lot, letter):
    prefix = letter.upper() + "-"
    return any(label.startswith(prefix) for label in config.catalog.spaces(lot))


def fill(s, result: ParseResult, text: str) -> ParseResult:
    """주차장이 빠진 '데이터 없는 질문'(시설·혼잡)에 맥락 주차장을 채운다."""
    lot_now = focus(s)
    if not lot_now or result.params.get("lot_id") or result.params.get("car_id") or result.vehicle_only:
        return result
    from . import info_gaps
    if result.params.get("info_gap") in ("facility", "nearest", "forecast"):
        return ParseResult(result.intent, {**result.params, "lot_id": lot_now}, result.source)
    if result.intent == "unknown":
        found = info_gaps.classify(text)
        if found and found[0] == "crowd_now":
            return ParseResult("get_empty_spots", {"lot_id": lot_now}, result.source)
    return result
