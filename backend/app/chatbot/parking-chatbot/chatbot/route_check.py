"""가로채기 확인(실험, 기본 꺼짐: LLM_ROUTE_CHECK=1일 때만).

규칙은 자기가 틀린 줄 모른다. "주차요금 안내해줘"를 목적지 설정으로, "장애인 차량이야. 어디 세우는 게 나아?"를
내 차 찾기로 '확신하고' 가로챈 사례(11/10)가 있었다. 규칙이 대화 AI를 거치지 않고 직접 맡으려는 순간에만
AI에게 "이 문장이 정말 그 처리인가?"를 짧게 묻는다.

- 맞다(true) → 규칙대로. 아니다(false) → 가로채지 않고 대화 AI로.
- AI 확인이 꺼져 있거나, 키가 없거나, 늦거나, 실패하면 → 규칙대로(지금과 같은 동작).
- 결제 승인·연기 신고·대피 안내처럼 빠르고 항상 같아야 하는 처리는 대상이 아니다. Lv2 경보 중에도 확인하지 않는다.

확인이 응답을 늦추지 않게 하는 두 가지(11/10 밤, 현업 구조에서 가져옴 — docs/exp_route_check_1110.md §구조 비교)
- 빠른 길(LLM_ROUTE_CHECK_FAST, 기본 켬): 그 처리의 말로만 된 문장("B로 갈게")은 확인을 생략한다. sure() 참고.
- 동시 실행(LLM_ROUTE_CHECK_PARALLEL, 기본 끔): 확인을 기다리지 않고 대화 AI를 함께 시작한다. 확인이 맞다면
  대화 AI를 취소하고 규칙으로, 아니라면 이미 달리던 대화 AI 답을 쓴다. keep() → _defer() → settle(), 엔진 _handle_checked.
"""
from __future__ import annotations

import json
import re
import threading
import time
import urllib.request
from collections import OrderedDict
from concurrent.futures import Future
from contextvars import ContextVar

from . import config, prompts, route
from .adapters import request_timeout
from .diagnostics import logger
from .transport import load_json

# 확인 대상: 규칙이 직접 맡는 처리 중 '뜻을 잘못 읽으면 답이 엉뚱해지는' 것만
HANDLERS = {
    "set_destination": ("목적지 설정", "사용자가 말한 주차장을 지금 '목적지로 정해 달라' 또는 '그 주차장으로 길 안내해 달라'고 요청한다."),
    "find_my_car": ("내 차 찾기", "사용자가 이미 주차해 둔 자기 차의 위치(어느 칸)나 그 차에 나온 요금을 묻는다. 차량 번호를 되물어야 답할 수 있는 질문이다."),
    "congestion_forecast": ("혼잡 예측", "사용자가 앞으로의 특정 날짜·시각(또는 하루 중 시간대)에 주차장이 붐빌지 예측을 묻는다."),
    "charging_status": ("충전 현황", "사용자가 전기차 충전기의 현재 상태(충전 가능·충전 중·완료, 남은 시간) 화면을 보여 달라고 한다."),
}

_log = logger(__name__)
_lock = threading.Lock()
_cache: OrderedDict = OrderedDict()
_CACHE_SIZE = 256
_counts = {"keep": 0, "veto": 0, "skip": 0, "sure": 0, "defer": 0}

# ---- 빠른 길: 문장이 그 처리의 말로만 이루어져 있으면 확인을 생략한다(현업의 '확신 높으면 빠른 길, 낮으면 AI').
# 모르는 낱말이 하나라도 있으면 확신하지 않는다 → AI 확인. 그래서 이 목록이 모자라도 틀린 답이 아니라 확인 한 번이 늘 뿐이다.
_TAIL = r"(?:으로|로|을|를|은|는|이|가|에|도|요|좀)*[?.!~]*"
_SURE_WORDS = {
    "set_destination": r"§|[A-Da-d]|주차장|목적지|길|거기|그쪽|저기|좀|안내|이동|시작|설정|지정|"
                       r"가자|가줘|가\s*줘|가요|갈게|갈게요|갈래|갈래요|해|해줘|해\s*줘|해주세요|해요|할게|할게요|하자|줘|주세요|부탁|부탁해|부탁해요",
    "find_my_car": r"내|제|우리|저희|차|차량|자동차|어디|어디에|어딨어|어딨지|위치|어느|몇|번|칸|지금|현재|좀|"
                   r"있어|있어요|있지|있나요|있는지|세웠지|세웠더라|세워뒀지|세워뒀더라|세워놨지|주차했지|주차했더라|주차했는지|"
                   r"찾아줘|찾아|알려줘|알려|모르겠어|모르겠어요|기억이|안\s*나|요금|주차비|얼마|나왔어|나왔어요|나왔지",
    "congestion_forecast": r"§|주차장|내일|모레|주말|평일|이번|다음|주|[월화수목금토일]요일|아침|오전|오후|저녁|밤|낮|점심때?|새벽|"
                           r"\d{1,2}\s*시|\d{1,2}\s*분|반|쯤|에|때|가면|"
                           r"붐벼|붐빌까|붐비나|붐비니|혼잡해|혼잡할까|혼잡도|혼잡|한산해|한산할까|많아|많을까|많이|"
                           r"자리|있어|있을까|어때|어떨까|예측|시간대|시간|언제|덜|안|가장|제일",
    "charging_status": r"§|주차장|전기차|충전|충전기|충전소|현황|상태|상황|화면|지금|현재|좀|보여줘|보여|알려줘|알려|열어줘|열어|줘|어때",
}
_FILLER = "그럼|그러면|그리고|혹시"   # 이어 말할 때 붙는 말(뜻을 바꾸지 않는다)
_SURE = {intent: re.compile(rf"^(?:{_FILLER}|{words})+{_TAIL}$") for intent, words in _SURE_WORDS.items()}


def sure(text: str, intent: str) -> bool:
    """문장의 모든 낱말이 그 처리의 말(주차장 이름·요청 동사·조사)뿐인가. 하나라도 벗어나면 False(→ AI 확인)."""
    pattern = _SURE.get(intent)
    if pattern is None or not isinstance(text, str):
        return False
    from .parser import mask_spaces
    masked = mask_spaces(text.strip())
    for _key, aliases, contextual, names in config.catalog.lot_patterns(config.DATA_SOURCE == "api"):
        for name in sorted(names, key=len, reverse=True):
            masked = masked.replace(name, "§")
        for lot_pattern in (*aliases, *contextual):
            masked = lot_pattern.sub("§", masked)
    words = masked.split()
    return bool(words) and len(words) <= 8 and all(pattern.match(word) for word in words)


# ---- 동시 실행: 확인을 기다리지 않고 대화 AI를 함께 시작한다(현업의 '입력 가드레일 병렬 실행').
# 확인이 '맞다'면 대화 AI를 취소하고 규칙으로, '아니다'면 이미 달리고 있던 대화 AI 답을 그대로 쓴다.
class RuleKeeps(Exception):
    """뒤로 미룬 확인이 '규칙이 맞다'로 끝났다 → 이번 턴을 규칙 처리로 다시 한다."""

    def __init__(self, intent):
        super().__init__(intent)
        self.intent = intent


class _Pending:
    __slots__ = ("intent", "key", "future", "timeout", "started", "ended")

    def __init__(self, intent, key, future, timeout):
        self.intent, self.key, self.future, self.timeout = intent, key, future, timeout
        self.started, self.ended = time.monotonic(), None


_turn: ContextVar = ContextVar("route_check_turn", default=None)


def begin_turn():
    return _turn.set({"settled": {}, "pending": None})


def end_turn(token):
    try:
        _turn.reset(token)
    except (ValueError, LookupError):
        pass


def pending():
    state = _turn.get()
    return state["pending"] if state else None


def settle(item) -> bool:
    """미뤄 둔 확인의 결과를 받는다(끝나지 않았으면 제한 시간까지 기다린다). True=규칙이 맞다(실패·시간 초과 포함)."""
    verdict, why = None, None
    try:
        # 제한 시간까지만 기다린다. 조회 스레드가 더 걸려도(연결·응답을 따로 재는 탓) 턴은 기다리지 않는다.
        verdict = item.future.result(timeout=max(0.0, item.started + item.timeout - time.monotonic()))
        if verdict is None:
            why = "형식 오류"
    except Exception as exc:
        why = type(exc).__name__
    _record(item.key, verdict)
    state = _turn.get()
    if state is not None:
        state["settled"][item.key] = verdict is not False
        if state["pending"] is item:
            state["pending"] = None
    route.mark("check", "skip" if verdict is None else "keep" if verdict else "veto", intent=item.intent,
               ms=round(((item.ended or time.monotonic()) - item.started) * 1000), why=why, parallel=True)
    return verdict is not False


def _time_left(limit):
    """확인에 쓸 수 있는 시간. 서버 조회 마감(턴 시작 + 2초)이 아니라 턴 전체 마감을 기준으로 한다."""
    try:
        from . import engine
        deadline = engine._turn_deadline.get()
    except Exception:
        deadline = None
    if deadline is None:
        return request_timeout(limit)
    return min(limit, deadline - time.monotonic())


def _record(key, verdict):
    with _lock:
        _counts["skip" if verdict is None else "keep" if verdict else "veto"] += 1
        if verdict is not None:
            _cache[key] = verdict
            while len(_cache) > _CACHE_SIZE:
                _cache.popitem(last=False)


def _defer(text, intent, key, timeout):
    """확인을 뒤에서 시작해 두고 바로 돌아온다. 한 번의 짧은 조회(제한 시간 있음)만 하는 스레드다."""
    future: Future = Future()
    item = _Pending(intent, key, future, timeout)

    def work():
        try:
            verdict, failure = _ask(text, intent, timeout), None
        except Exception as exc:   # 시간 초과·네트워크·키 오류 → settle이 '규칙대로'로 처리
            verdict, failure = None, exc
        finally:
            slots.release()
        item.ended = time.monotonic()   # 확인 자체에 걸린 시간(대화 AI를 기다린 시간은 빼고 기록)
        try:
            future.set_exception(failure) if failure else future.set_result(verdict)
        except Exception:   # 기다리던 쪽이 먼저 끝나 결과를 받을 곳이 없다 → 버린다
            pass

    slots = _slots()
    try:
        threading.Thread(target=work, name="route-check", daemon=True).start()
    except Exception:
        slots.release()
        raise
    return item


def _slots():
    """동시에 도는 확인 호출 수 상한. 넘으면 확인하지 않고 규칙대로(사용자가 몰려도 확인이 모델 호출을 불리지 않게)."""
    global _check_slots, _check_slot_size
    with _lock:
        if _check_slots is None or _check_slot_size != config.LLM_ROUTE_CHECK_MAX_INFLIGHT:
            _check_slots = threading.BoundedSemaphore(config.LLM_ROUTE_CHECK_MAX_INFLIGHT)
            _check_slot_size = config.LLM_ROUTE_CHECK_MAX_INFLIGHT
        return _check_slots


_check_slots = None
_check_slot_size = 0


def enabled() -> bool:
    return (config.LLM_ROUTE_CHECK and config.LLM_PROVIDER == "openrouter" and config.LLM_ENGINE == "langchain"
            and bool(config.api_key("openrouter")) and bool(config.LLM_MODEL))


def stats() -> dict:
    with _lock:
        return dict(_counts)


def reset_for_tests():
    with _lock:
        _cache.clear()
        for key in _counts:
            _counts[key] = 0


def _lot_names() -> str:
    """'A 개발원주차장, B 부산역주차장, …' — 사용자는 기호(A~D)만으로도 주차장을 부른다."""
    return ", ".join(f"{key} {row.get('name', key)}" for key, row in config.catalog.lots().items())


def _parse(content) -> bool | None:
    if not isinstance(content, str):
        return None
    start, end = content.find("{"), content.rfind("}")
    if start >= 0 and end > start:
        try:
            value = json.loads(content[start:end + 1]).get("match")
            if isinstance(value, bool):
                return value
        except (ValueError, AttributeError):
            pass
    lowered = content.strip().lower()
    if lowered in ("true", "yes", "맞음", "예"):
        return True
    if lowered in ("false", "no", "아님", "아니오"):
        return False
    return None


def _ask(text: str, intent: str, timeout: float) -> bool | None:
    """모델에 한 번 묻는다. True=그 처리가 맞다, False=아니다, None=판단 못 함(실패·형식 오류)."""
    name, handler = HANDLERS[intent]
    body = {
        "model": config.LLM_ROUTE_CHECK_MODEL or config.LLM_MODEL,
        "temperature": 0,
        "max_tokens": 30,
        "reasoning": {"enabled": False},
        "messages": [
            {"role": "system", "content": prompts.render("route_check", intent=name, handler=handler, lots=_lot_names())},
            {"role": "user", "content": text[:200]},
        ],
    }
    request = urllib.request.Request(
        config.OPENROUTER_BASE_URL + "/chat/completions", data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + config.api_key("openrouter")})
    result = load_json(request, timeout)
    return _parse(result["choices"][0]["message"]["content"])


def keep(text: str, intent: str, *, urgent: bool = False) -> bool:
    """규칙이 이 문장을 intent로 직접 맡아도 되는가. False일 때만 가로채지 않고 대화 AI로 넘긴다."""
    if intent not in HANDLERS or not isinstance(text, str) or not text.strip() or urgent or not enabled():
        return True
    key = (intent, text.strip())
    state = _turn.get()
    if state is not None and key in state["settled"]:
        return state["settled"][key]   # 이번 턴에 이미 받은 결과(규칙 처리로 다시 할 때)
    if config.LLM_ROUTE_CHECK_FAST and sure(text, intent):
        with _lock:
            _counts["sure"] += 1
        route.mark("check", "sure", intent=intent, why="그 처리의 말로만 된 문장 → 확인 생략")
        return True
    with _lock:
        if key in _cache:
            _cache.move_to_end(key)
            verdict = _cache[key]
            route.mark("check", "keep" if verdict else "veto", intent=intent, cached=True)
            return verdict
    started = time.monotonic()
    verdict, why = None, None
    try:
        # 턴 예산에서 최대 LLM_ROUTE_CHECK_TIMEOUT_SEC만 쓴다. 남은 시간이 모자라면 확인하지 않는다.
        # 동시 실행은 대화 AI가 도는 동안 기다리는 것이라 조금 더 길게 본다(늦은 확인을 버려 엉뚱한 규칙 답이 나가는 일을 줄인다).
        parallel = config.LLM_ROUTE_CHECK_PARALLEL and state is not None and state["pending"] is None
        limit = config.LLM_ROUTE_CHECK_PARALLEL_TIMEOUT_SEC if parallel else config.LLM_ROUTE_CHECK_TIMEOUT_SEC
        timeout = _time_left(limit)
        if timeout < min(0.8, limit):
            why = "남은 시간 부족"
        elif not _slots().acquire(blocking=False):
            why = "확인 자리 없음"
        elif parallel:
            # 기다리지 않는다: 확인을 뒤에서 시작하고 일단 대화 AI 쪽으로 보낸다. 결과는 대화 AI와 함께 기다린다.
            state["pending"] = _defer(text, intent, key, timeout)
            with _lock:
                _counts["defer"] += 1
            route.mark("check", "defer", intent=intent, why="확인과 대화 AI를 함께 시작")
            return False
        else:
            try:
                verdict = _ask(text, intent, timeout)
            finally:
                _slots().release()
            if verdict is None:
                why = "형식 오류"
    except Exception as exc:   # 시간 초과·네트워크·키 오류 → 규칙대로
        why = type(exc).__name__
        _log.info("route check skipped (%s)", why)
    ms = round((time.monotonic() - started) * 1000)
    _record(key, verdict)
    route.mark("check", "skip" if verdict is None else "keep" if verdict else "veto", intent=intent, ms=ms, why=why)
    return verdict is not False
