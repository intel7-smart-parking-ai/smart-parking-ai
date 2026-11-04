"""선택 LangChain 단일 단계 분류. 도구는 스키마일 뿐 실행하지 않는다."""
from __future__ import annotations

from collections import Counter
from contextvars import copy_context
import hashlib
import threading
import time

from . import config
from .diagnostics import logger
from .adapters import request_timeout
from . import llm_limits

# 고정 개수의 실행 슬롯. 시간 초과된 작업도 실제 종료 전에는 슬롯을 반납하지 않는다.
_MAX_INFLIGHT = 2
_slots = threading.BoundedSemaphore(_MAX_INFLIGHT)
_model_lock = threading.Lock()
_stats_lock = threading.Lock()
_model = None
_model_key = None
_initializing = False
_failure = None
_retry_at = 0.0
_counts = Counter({k: 0 for k in (
    "disabled", "not_installed", "missing_key", "timeout", "api_error", "rate_limited", "cache_hit",
    "no_tool_call", "invalid_intent", "invalid_params", "ok", "preparing", "busy")})
_warned = set()
_last_ms = 0.0
_last_reason = None
_inflight = 0
_log = logger(__name__)

# 설명에는 실제 동작과 구분용 예문만 넣는다.
_DESCRIPTIONS = {
    "get_empty_spots": "확정 빈자리 조회와 전체 비교·추천. 예: 주차할 공간을 찾아줘.",
    "set_destination": "목적지 선택, 예약은 아님. 예: B로 갈게.",
    "cancel_destination": "목적지 안내 취소. 예: 목적지를 지워줘.",
    "find_my_car": "차량의 주차 위치 조회. 예: 내 자동차 어디 뒀더라.",
    "get_fee": "차량 요금·결제 여부 조회, 결제 실행 아님. 예: 지금 얼마를 내야 해?",
    "fee_rule": "일반 요금 규칙 질문. 예: 10분에 얼마야?",
    "pay_fee": "사용자가 명시한 모의 결제 요청, 엔진의 금액 확인 단계로 연결. 예: 미리 결제할게.",
    "payment_help": "결제 수단 질문, 실행 아님. 예: 현금도 돼?",
    "payment_issue": "결제 오류 문의, 새 결제 아님. 예: 결제 실패했어.",
    "exit_help": "출구·차단기 문제 문의. 진단하지 않고 현장 관리자 연결을 안내. 예: 차단기가 안 열려.",
    "call_admin": "명시적인 관리자 호출 요청, 즉시 접수됨. 예: 직원 보내줘.",
    "call_status": "기존 문의 접수 확인, 새 호출 아님. 예: 내 문의 들어갔어?",
    "admin_help": "관리자 이용방법·비용 질문, 호출 아님. 예: 직원 부르면 비용이 드나요?",
    "get_alerts": "현재 모의 화재 경보 조회. 예: 경보 상황이 어때?",
    "get_violations": "전기차·장애인 전용 칸 위반(일반차 주차) 문의. 서버 위반 경보로만 답함. 예: 불법 주차한 차 있어?",
    "evacuation_guide": "사람의 대피 안내. 예: 어디로 피해야 해?",
    "alert_near_my_car": "차량 근처 경보 조회, 차량 이동 지시 아님. 예: 내 차 근처야?",
    "change_vehicle": "차량 번호 변경 확인. 예: 19번 차로 바꿔.",
    "cancel_action": "미완료 요청 취소·부정, 환불 아님. 예: 결제하지 마.",
    "help": "지원하는 기능 안내. 예: 뭘 할 수 있어?",
    "thanks": "감사 인사. 예: 고마워.",
    "unknown": "주차장과 무관하거나 의도가 불명확함. 예: 오늘 날씨 어때?",
}
_VEHICLE = {"find_my_car", "get_fee", "alert_near_my_car", "change_vehicle"}
_LOT = {"get_empty_spots", "set_destination", "get_alerts"}
_ZONE = {"get_empty_spots", "set_destination"}


def tool_schemas():
    """명부 변경을 즉시 반영하며 실행 함수를 모델에 노출하지 않는다."""
    lots = config.catalog.ids()
    zones = [z["key"] for z in config.catalog.zones()]
    vehicles = [str(n) for n in range(max(10, config.VEHICLE_MIN), config.VEHICLE_MAX + 1)]  # 두 자리 '10'~'99'만
    tools = []
    for name, description in _DESCRIPTIONS.items():
        properties = {}
        for key, allowed, values in (("lot_id", _LOT, lots), ("zone", _ZONE, zones), ("car_id", _VEHICLE, vehicles)):
            if name in allowed:
                properties[key] = {"type": "string", "enum": values}
        required = ["lot_id"] if name == "set_destination" else ["car_id"] if name == "change_vehicle" else []
        tools.append({"type": "function", "function": {"name": name, "description": description,
            "parameters": {"type": "object", "properties": properties, "required": required, "additionalProperties": False}}})
    return tools


def _settings():
    provider = config.LLM_PROVIDER
    if provider == "none":
        return None, "disabled"
    key = config.api_key(provider)
    if not key or not config.LLM_MODEL:
        return None, "missing_key"
    # 비밀키 자체는 캐시 키나 통계에 보관하지 않는다.
    return (provider, config.LLM_MODEL, min(3.0, config.LLM_TIMEOUT_SEC), hashlib.sha256(key.encode()).hexdigest()), None


def _create_model(settings):
    provider, model, timeout, _ = settings
    kwargs = dict(model=model, temperature=0, max_tokens=200, timeout=timeout, max_retries=0)
    if provider == "openai":
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(**kwargs)
    if provider == "openrouter":
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(**kwargs, base_url=config.OPENROUTER_BASE_URL, api_key=config.api_key("openrouter"))
    from langchain_anthropic import ChatAnthropic
    return ChatAnthropic(**kwargs)


def warmup():
    """최초 import·모델 생성도 백그라운드에서 수행하며 호출자는 기다리지 않는다."""
    global _initializing, _model_key, _model, _failure
    settings, reason = _settings()
    if reason:
        return
    with _model_lock:
        if _initializing or (_model_key == settings and (_model is not None or time.monotonic() < _retry_at)):
            return
        _initializing = True
        _model_key, _model, _failure = settings, None, None
    def prepare():
        global _model, _failure, _retry_at, _initializing
        try:
            model, failure = _create_model(settings), None
        except ImportError:
            model, failure = None, "not_installed"
        except Exception as exc:
            _log.error("LLM prepare failed (%s)", type(exc).__name__)
            model, failure = None, "api_error"
        with _model_lock:
            _model, _failure = model, failure
            _retry_at = time.monotonic() + 30 if failure else 0
            _initializing = False
    threading.Thread(target=prepare, name="parking-llm-prepare", daemon=True).start()


def _record(reason, started):
    global _last_ms, _last_reason
    with _stats_lock:
        _counts[reason] += 1
        _last_ms = round((time.monotonic() - started) * 1000, 2)
        _last_reason = reason
        warn = reason not in ("ok", "disabled", "cache_hit", "rate_limited") and reason not in _warned
        _warned.add(reason)
    if warn:
        _log.warning("LLM router fallback: %s", reason)  # 원문·키·SDK 오류 본문은 기록하지 않는다.


def stats():
    with _stats_lock:
        snapshot = {"counts": dict(_counts), "last_latency_ms": _last_ms,
                    "last_reason": _last_reason, "inflight": _inflight, "max_inflight": _MAX_INFLIGHT}
    with _model_lock:
        snapshot.update(ready=_model is not None, preparing=_initializing)
    return snapshot


def _decode(message):
    calls = getattr(message, "tool_calls", None)
    if not isinstance(calls, list) or not calls:
        return "no_tool_call", None
    call = calls[0]  # 한 번의 분류, 첫 도구 하나만 선택. 실행/에이전트 루프 없음.
    if not isinstance(call, dict) or not isinstance(call.get("name"), str) or call["name"] not in _DESCRIPTIONS:
        return "invalid_intent", None
    name, args = call["name"], call.get("args")
    if not isinstance(args, dict):
        return "invalid_params", None
    args = dict(args)
    if name in ("pay_fee", "call_admin"):
        args.pop("car_id", None)
    schema = next(t["function"]["parameters"] for t in tool_schemas() if t["function"]["name"] == name)
    if set(args) - set(schema["properties"]) or any(k not in args for k in schema["required"]):
        return "invalid_params", None
    for key, value in args.items():
        if not isinstance(value, str) or value not in schema["properties"][key]["enum"]:
            return "invalid_params", None
    return "ok", {"intent": name, "params": args}


def route(text):
    global _inflight
    started = time.monotonic()
    def finish(reason, value=None):
        _record(reason, started)
        return value
    if not isinstance(text, str) or len(text) > 200:
        return finish("invalid_params")
    settings, reason = _settings()
    if reason:
        return finish(reason)
    warmup()
    with _model_lock:
        model = _model if settings == _model_key else None
        failure = _failure if settings == _model_key else None
    if model is None:
        return finish(failure or "preparing")
    key = llm_limits.limits.key(text, ("langchain", settings))
    cached = llm_limits.limits.lookup(key)
    if cached is not None:
        return finish("cache_hit", cached)
    try:
        deadline = started + request_timeout(min(3.0, config.LLM_TIMEOUT_SEC))
    except TimeoutError:
        return finish("timeout")
    if not _slots.acquire(blocking=False):
        return finish("busy")
    if not llm_limits.limits.admit(llm_limits.session_id.get()):
        _slots.release()
        return finish("rate_limited")
    done = threading.Event()
    result = []
    with _stats_lock:
        _inflight += 1
    def invoke():
        global _inflight
        try:
            from .llm import system_prompt
            prompt = system_prompt().split("출력 예:", 1)[0].replace(
                "JSON 한 줄만 출력한다.", "허용된 도구 하나만 선택한다.")
            prompt += "\nJSON 본문 대신 도구 하나만 선택한다. 확인 승인은 선택할 수 없다."
            # bind_tools와 호출도 제한 시간에 포함. 모델에 업무 처리 함수를 넘기지 않는다.
            message = model.bind_tools(tool_schemas()).invoke([("system", prompt), ("human", text)])
            result.append(_decode(message))
        except Exception as exc:
            _log.error("LLM invoke failed (%s)", type(exc).__name__)
            result.append(("timeout" if "timeout" in type(exc).__name__.lower() else "api_error", None))
        finally:
            with _stats_lock:
                _inflight -= 1
            _slots.release()
            done.set()
    threading.Thread(target=copy_context().run, args=(invoke,), name="parking-llm-route", daemon=True).start()
    if not done.wait(max(0, deadline - time.monotonic())) or time.monotonic() > deadline:
        return finish("timeout")
    # 작업 중 설정이 바뀐 결과는 새 설정으로 오인하지 않는다.
    if _settings()[0] != settings:
        return finish("invalid_params")
    reason, value = result[0]
    if reason == "ok":
        llm_limits.limits.save(key, value)
    return finish(reason, value)
