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
    "get_empty_spots": "확정 빈자리·수용 여유를 조회하고 주차장이나 전용 구역을 비교합니다(예: 승용차를 댈 여유 공간을 비교하고 싶습니다). 위치 선택이나 예약을 실행하지 않습니다.",
    "set_destination": "이동할 주차장을 목적지로 선택하려는 뜻입니다(예: 이번 방문의 도착 대상으로 개발원주차장을 선택합니다). 빈자리 조회와 구분하며 자리를 예약하지 않습니다.",
    "cancel_destination": "정해 둔 목적지나 이동 안내만 끝내려는 뜻입니다(예: 지정해 놓은 방문 대상 표식을 없애 주세요). 결제 확인 등 다른 미완료 요청 취소와 구분하며 결제를 환불하지 않습니다.",
    "find_my_car": "자동차를 세워 둔 곳이나 보관 위치를 찾는 조회입니다(예: 제 승용차가 세워진 구역을 조회하고 싶습니다). 없는 위치를 만들거나 차량 이동을 실행하지 않습니다.",
    "get_fee": "주차비·사용료·정산 금액·청구 내역과 결제 여부를 조회합니다(예: 대금이 어떻게 산출됐는지 자료를 보여 주세요). 사용자가 납부를 원하면 결제 요청으로 구분하며 조회만으로 결제하지 않습니다.",
    "fee_rule": "기본 요금·시간별 과금 방식·무료 시간·상한 같은 일반 규칙의 질문입니다(예: 시간대별 부과 방식과 상한 기준을 설명해 주세요). 특정 차량의 현재 청구액 조회나 결제는 아닙니다.",
    "pay_fee": "사용자가 청구액을 결제·계산·정산하거나 대금을 치르겠다는 명시적 요청입니다(예: 청구서를 확인했으니 모의 납부 절차를 시작하겠습니다). 조회만 원하면 요금 조회로 구분하며 차량·견적·금액 확인 없이 결제하지 않습니다.",
    "payment_help": "카드·현금·간편승인 등 사용할 수납 수단과 이용 방법의 질문입니다(예: 이용할 수 있는 수납 방식을 소개해 주세요). 승인 거절·오류·중복 청구는 결제 문제로 구분하고 실제 납부를 실행하지 않습니다.",
    "payment_issue": "승인 거절·결제 오류·중복 청구·납부 후 이상에 대한 문의입니다(예: 승인 과정에서 거부 통지가 나왔습니다). 수단 이용 가능 여부와 구분하며 새 결제나 자동 환불을 하지 않습니다.",
    "exit_help": "출구·차단기·통행 막대나 바가 자동차를 통과시키지 못하는 문제입니다(예: 차량 통행 봉이 올라오지 않으니 조치를 알려 주세요). 장치를 진단하거나 열지 않고 현장 지원 방법을 안내합니다.",
    "call_admin": "현장 인력이 오거나 상담을 연결해 달라는 명시적 요청입니다(예: 관리자가 지금 이쪽에 와 주기를 요청합니다). 인력 존재·방법·비용 질문은 관리자 문의로 구분하며 도구 선택만으로 접수하지 않습니다.",
    "call_status": "이미 남긴 문의·지원 요청의 접수와 처리 경과를 확인합니다(예: 예전에 등록한 현장 요청의 접수 번호를 확인합니다). 새 사람 호출이나 추가 접수가 아닙니다.",
    "admin_help": "직원·관리자·근무자가 있는지 또는 연락 방법·이용 조건·비용을 묻습니다(예: 상담 담당자에게 연락할 수단과 이용 조건이 궁금합니다). 실제 방문·연결 요청은 관리자 호출로 구분하며 문의만으로 사람을 부르지 않습니다.",
    "get_alerts": "현재 모의 화재 표시등과 경보 위치·확인·해제 상태를 조회합니다(예: 붉은 표시등에 따른 모의 상황을 조회합니다). 실제 화재를 판정하거나 경보를 만들고 해제하지 않습니다.",
    "get_violations": "전기차·장애인 전용 구역의 자격과 다른 자동차 점유를 조회합니다(예: 지정 이용 자격과 다른 자동차가 주차했는지 확인합니다). 서버가 제공한 위반 경보만 사용하며 화재 경보와 섞거나 위반을 추측하지 않습니다.",
    "evacuation_guide": "사람이 위험 범위에서 벗어날 통로와 대피 방법의 질문입니다(예: 사람이 건물 밖으로 벗어날 경로를 알려 주세요). 자동차를 회수하거나 옮기도록 지시하지 않습니다.",
    "alert_near_my_car": "선택한 자동차 주변의 모의 경보와 위험 범위를 조회합니다(예: 지정한 자동차 주변에 모의 상황이 있는지 살펴 주세요). 자동차 위치 찾기와 구분하며 안전을 단정하거나 이동을 권하지 않습니다.",
    "change_vehicle": "대화에 선택된 자동차 식별 번호를 다른 번호로 정정하려는 뜻입니다(예: 저장된 자동차 식별 대상을 42번으로 정정합니다). 사용자가 말하지 않은 번호를 만들지 않고 새 대상을 확인받습니다.",
    "cancel_action": "미완료 확인·요청을 중단하거나 실행하지 말라는 뜻입니다(예: 아직 끝나지 않은 부탁을 중단해 주세요). 이동 목적지 안내 종료는 별도 취소로 구분하며 완료된 결제를 환불하지 않습니다.",
    "help": "지원하는 기능과 이용 범위에 대한 안내 또는 실행을 원하지 않는 단순 문의입니다(예: 제공되는 주차 서비스의 범위를 소개해 주세요). 주차와 무관한 주제는 모름으로 구분하며 사람 호출이나 결제를 실행하지 않습니다.",
    "thanks": "정보를 받은 뒤의 감사나 고마움을 나타냅니다(예: 설명을 듣고 궁금증이 풀려서 고맙습니다). 새 업무 요청을 만들거나 접수하지 않습니다.",
    "unknown": "주차 서비스와 무관하거나 하나의 의도를 정하기 어려운 발화입니다(예: 우주선의 항법 원리를 설명해 주세요). 기능 이용 질문은 도움말로 구분하며 빈 인자를 만들어 다른 도구를 고르지 않습니다.",
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
        return ChatOpenAI(**kwargs, api_key=config.api_key("openai"))
    if provider == "openrouter":
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(**kwargs, base_url=config.OPENROUTER_BASE_URL, api_key=config.api_key("openrouter"))
    from langchain_anthropic import ChatAnthropic
    return ChatAnthropic(**kwargs, api_key=config.api_key("anthropic"))


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
    example_mode = config.LLM_FEWSHOT
    key = llm_limits.limits.key(text, ("langchain", settings, example_mode))
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
            messages = [("system", prompt)]
            if example_mode:
                from .llm_examples import messages as example_messages
                messages.extend(example_messages())
            messages.append(("human", text))
            message = model.bind_tools(tool_schemas()).invoke(messages)
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
    if _settings()[0] != settings or config.LLM_FEWSHOT != example_mode:
        return finish("invalid_params")
    reason, value = result[0]
    if reason == "ok":
        llm_limits.limits.save(key, value)
    return finish(reason, value)
