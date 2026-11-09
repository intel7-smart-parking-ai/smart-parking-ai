"""Bounded LangChain model -> read-only tools -> grounded answer loop.

Imports are lazy so chips and deterministic business flows work without optional
LLM dependencies. Every invocation owns its messages and observations; neither
answers nor tool results are cached across sessions.
"""
from __future__ import annotations

import asyncio
from collections import Counter
from dataclasses import dataclass, field
import json
import threading
import time
from typing import Literal

import re

from .. import config, llm_limits
from ..adapters import request_timeout
from ..diagnostics import logger
from ..prompts import conversation_prompt
from .evidence import REPAIR_HINTS, action_claim, check_answer, no_tool_reply_ok


@dataclass
class AgentResult:
    text: str = ""
    status: str = "disabled"
    tool_results: list[dict] = field(default_factory=list)
    proposals: list[dict] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)


@dataclass(frozen=True)
class AgentOptions:
    # 도구 선택 2~3회 + 답 작성 1회 + 고쳐 쓰기 1회
    max_model_calls: int = 5
    max_tool_calls: int = 10
    max_output_tokens: int = 800
    max_total_tokens: int | None = None   # None이면 LLM_TURN_TOKEN_LIMIT
    max_history_messages: int = 12
    repair: bool | None = None            # None이면 LLM_REPAIR


class _Stopped(Exception):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


# 대화 조정부(conversation_turn)가 이번 턴에 넘기는 부가 정보: 빠진 조회 확인, 직전 답, 모니터링 콜백.
# 함수 인자 대신 문맥 변수로 넘겨 run_conversation의 기존 호출 형식(text, history, state, tools)을 유지한다.
from contextvars import ContextVar
turn_extras: ContextVar[dict | None] = ContextVar("conversation_turn_extras", default=None)
# 턴마다 새로 만든 HTTP 클라이언트. run_conversation은 턴마다 asyncio.run으로 새 이벤트 루프를 쓰므로,
# SDK 기본(전역 캐시) 비동기 클라이언트를 재사용하면 두 번째 턴부터 "Event loop is closed"가 난다.
_turn_clients: ContextVar[list | None] = ContextVar("conversation_turn_clients", default=None)

_slots_lock = threading.Lock()
_model_slots = None
_slot_size = 0
_stats_lock = threading.Lock()
_counts = Counter()
_active_models = 0
_log = logger(__name__)


def _slots():
    """서버 전체 동시 모델 호출 자리. 설정이 바뀌면(시험 등) 새 크기로 만든다."""
    global _model_slots, _slot_size
    with _slots_lock:
        if _model_slots is None or _slot_size != config.LLM_MAX_INFLIGHT:
            _model_slots, _slot_size = threading.BoundedSemaphore(config.LLM_MAX_INFLIGHT), config.LLM_MAX_INFLIGHT
        return _model_slots


async def _acquire_slot(slots, wait):
    """이벤트 루프를 막지 않고 최대 wait초 자리를 기다린다(요청마다 루프가 달라 threading 세마포어를 쓴다)."""
    end = time.monotonic() + max(0.0, wait)
    while True:
        if slots.acquire(blocking=False):
            return True
        if time.monotonic() >= end:
            return False
        await asyncio.sleep(0.05)


def stats() -> dict:
    with _stats_lock:
        return {"counts": dict(_counts), "inflight": _active_models, "max_inflight": config.LLM_MAX_INFLIGHT,
                "implementation": "langchain_create_agent"}


_NORMALIZE = re.compile(r"[\s.,!?~·'\"“”‘’()\-]+")


def _same_text(a, b):
    a, b = _NORMALIZE.sub("", a or ""), _NORMALIZE.sub("", b or "")
    if not a or not b:
        return False
    if a == b:
        return True
    from difflib import SequenceMatcher
    return min(len(a), len(b)) >= 20 and SequenceMatcher(None, a, b).ratio() >= 0.9


def _provider_model(options: AgentOptions):
    """Reuse the selected provider/model; this layer does not choose a new one."""
    provider = config.LLM_PROVIDER
    if provider == "none":
        raise _Stopped("disabled")
    if provider not in {"openrouter", "openai", "anthropic"}:
        raise _Stopped("unsupported_provider")
    key = config.api_key(provider)
    if not key or not config.LLM_MODEL:
        raise _Stopped("missing_key")
    timeout = request_timeout(config.LLM_TIMEOUT_SEC)
    kwargs = {"model": config.LLM_MODEL, "api_key": key, "temperature": 0,
              "max_tokens": options.max_output_tokens, "timeout": timeout, "max_retries": 0}
    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic
        return ChatAnthropic(**kwargs)
    from langchain_openai import ChatOpenAI
    if provider == "openrouter":
        kwargs["base_url"] = config.OPENROUTER_BASE_URL
    clients = _turn_clients.get()
    if clients is not None:
        import httpx
        client = httpx.AsyncClient(timeout=timeout)
        clients.append(client)
        kwargs["http_async_client"] = client
    return ChatOpenAI(**kwargs)


def _history_messages(history, options):
    messages = []
    if not options.max_history_messages:
        return messages
    for row in list(history or [])[-options.max_history_messages:]:
        if isinstance(row, dict):
            role, text = row.get("role"), row.get("content")
        elif isinstance(row, (tuple, list)) and len(row) == 2:
            role, text = row
        else:
            continue
        role = {"bot": "assistant", "human": "user"}.get(role, role)
        # System/tool roles can only come from this invocation, never history.
        if role in {"user", "assistant"} and isinstance(text, str) and text.strip():
            messages.append({"role": role, "content": text[:2000]})
    return messages


def _message_text(message):
    content = getattr(message, "content", "")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        return "\n".join(block.get("text", "") for block in content
                         if isinstance(block, dict) and block.get("type") == "text").strip()
    return ""


def _decode_observation(message):
    artifact = getattr(message, "artifact", None)
    if isinstance(artifact, dict):
        return artifact
    content = getattr(message, "content", "")
    if isinstance(content, dict):
        return content
    if isinstance(content, str):
        try:
            value = json.loads(content)
        except (TypeError, ValueError):
            return {"text": content}
        return value if isinstance(value, dict) else {"data": value}
    return {"data": content}


def _proposals(observations):
    proposals = []
    for observation in observations:
        result = observation["result"]
        if observation["tool"] != "propose_action" or result.get("ok") is not True or result.get("executed") is not False:
            continue
        value = result.get("proposal")
        if isinstance(value, dict) and value.get("intent") in {"pay_fee", "call_admin", "set_destination"}:
            proposals.append(value)
    return proposals


async def arun_conversation(text, history, state, tools, model=None, *, options=None,
                            coverage=None, avoid=(), callbacks=None) -> AgentResult:
    """Run one isolated turn; HTTP clients created for this turn are closed in this loop."""
    clients = []
    token = _turn_clients.set(clients)
    try:
        return await _arun_conversation(text, history, state, tools, model, options=options,
                                        coverage=coverage, avoid=avoid, callbacks=callbacks)
    finally:
        _turn_clients.reset(token)
        for client in clients:
            try:
                await client.aclose()
            except Exception:
                pass


async def _arun_conversation(text, history, state, tools, model=None, *, options=None,
                             coverage=None, avoid=(), callbacks=None) -> AgentResult:
    """Run one isolated turn. ``tools`` must expose async, read-only callables.

    The caller owns the session lock, current alert policy and business flows.
    A failed model never erases observations already completed in this turn.
    ``coverage(observations)`` may return a short Korean note naming a requested goal
    that was not read; ``avoid`` holds recent bot answers that should not be repeated.
    ``callbacks`` are LangChain callback handlers (monitoring); they never affect the answer.
    """
    global _active_models
    started = time.monotonic()
    options = options or AgentOptions()
    observations = []
    metadata = {"implementation": "langchain_create_agent", "model_calls": 0,
                "tool_calls": 0, "input_tokens": 0, "output_tokens": 0}

    def finish(status, answer=""):
        metadata["latency_ms"] = round((time.monotonic() - started) * 1000, 1)
        with _stats_lock:
            _counts[status] += 1
        proposals = _proposals(observations) if status == "ok" else []
        return AgentResult(answer, status, observations, proposals, metadata)

    if not isinstance(text, str) or not text.strip() or len(text) > 2000:
        return finish("invalid_input")
    extras = turn_extras.get() or {}
    coverage = coverage if coverage is not None else extras.get("coverage")
    avoid = avoid or extras.get("avoid") or ()
    callbacks = callbacks if callbacks is not None else extras.get("callbacks")
    token_limit = options.max_total_tokens if options.max_total_tokens is not None else config.LLM_TURN_TOKEN_LIMIT
    repair_enabled = config.LLM_REPAIR if options.repair is None else options.repair
    if (min(options.max_model_calls, options.max_tool_calls, options.max_output_tokens,
            token_limit) <= 0 or options.max_history_messages < 0):
        return finish("invalid_options")
    # 호출 상한은 질문(턴) 단위다. 한 턴 안의 도구 선택·답 작성·고쳐 쓰기는 따로 세지 않는다.
    if not llm_limits.limits.admit_turn(llm_limits.session_id.get()):
        return finish("rate_limited")
    try:
        remaining = request_timeout(config.REQUEST_BUDGET_SEC)
        deadline = time.monotonic() + remaining
        if model is None:
            model = _provider_model(options)
        from langchain.agents import create_agent
        from langchain.agents.middleware import AgentMiddleware
        from langchain_core.messages import ToolMessage
        from langchain_core.tools import StructuredTool
    except _Stopped as exc:
        return finish(exc.reason)
    except ImportError:
        return finish("not_installed")
    except TimeoutError:
        return finish("timeout")
    except Exception as exc:
        _log.warning("Conversation model preparation failed (%s)", type(exc).__name__)
        return finish("api_error")

    questions = {"vehicle": "어느 차량인지 번호를 알려주시겠어요?",
                 "lot": "어느 주차장을 확인해 드릴까요?",
                 "goal": "차량 위치, 빈자리, 요금 중 무엇을 확인해 드릴까요?"}

    async def ask_clarification(topic: Literal["vehicle", "lot", "goal"], question: str | None = None) -> dict:
        """Ask for one missing vehicle ID, parking lot, or user goal. question: one short Korean question
        written for this situation (no numbers, no promises). topic decides the screen flow."""
        text = questions[topic]
        if (isinstance(question, str) and 4 <= len(question.strip()) <= 120 and question.strip().endswith("?")
                and not re.search(r"\d", question) and not action_claim(question)):
            text = question.strip()
        return {"ok": True, "data": {"topic": topic, "question": text}}

    tools = list(tools or [])
    tools.append(StructuredTool.from_function(coroutine=ask_clarification))
    # Async-only avoids ToolNode offloading synchronous reads to orphan threads.
    if any(not getattr(tool, "coroutine", None) for tool in tools):
        return finish("invalid_tools")
    tool_names = {tool.name for tool in tools}
    if len(tool_names) != len(tools):
        return finish("invalid_tools")
    allowed = {"parking_status", "vehicle_info", "fee_policies", "compare_lots", "estimate_fee",
               "alert_summary", "violations", "call_status", "search_knowledge",
               "propose_action", "ask_clarification"}
    if not tool_names <= allowed:
        return finish("unsafe_tools")

    def time_left():
        remaining = min(deadline - time.monotonic(), request_timeout(config.REQUEST_BUDGET_SEC))
        if remaining <= 0:
            raise _Stopped("timeout")
        return remaining

    # Serialize this turn's reads: adapters may stage catalog evidence in context.
    tool_lock = asyncio.Lock()

    class TurnLimits(AgentMiddleware):
        async def awrap_model_call(self, request, handler):
            global _active_models
            if metadata["model_calls"] >= options.max_model_calls:
                raise _Stopped("model_limit")
            if metadata["input_tokens"] + metadata["output_tokens"] >= token_limit:
                raise _Stopped("token_limit")
            # 조회 결과를 읽고 답을 쓰는 호출은 조금 더 길게 기다린다.
            limit = config.LLM_ANSWER_TIMEOUT_SEC if observations else config.LLM_TIMEOUT_SEC
            slots = _slots()
            if not await _acquire_slot(slots, min(config.LLM_SLOT_WAIT_SEC, max(0.0, time_left() - 0.5))):
                raise _Stopped("busy")
            try:
                timeout = min(time_left(), limit)
                metadata["model_calls"] += 1
                with _stats_lock:
                    _active_models += 1
                try:
                    response = await asyncio.wait_for(handler(request), timeout)
                finally:
                    with _stats_lock:
                        _active_models -= 1
                for message in getattr(response, "result", []):
                    usage = getattr(message, "usage_metadata", None) or {}
                    for key in ("input_tokens", "output_tokens"):
                        value = usage.get(key, 0)
                        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
                            metadata[key] += value
                return response
            finally:
                slots.release()

        async def awrap_tool_call(self, request, handler):
            async with tool_lock:
                time_left()
                if metadata["tool_calls"] >= options.max_tool_calls:
                    raise _Stopped("tool_limit")
                name = request.tool_call.get("name")
                if name not in tool_names:
                    raise _Stopped("invalid_tool")
                metadata["tool_calls"] += 1
                try:
                    message = await asyncio.wait_for(handler(request), time_left())
                except (TimeoutError, asyncio.TimeoutError):
                    raise _Stopped("timeout")
                if not isinstance(message, ToolMessage):
                    raise _Stopped("invalid_tool_result")
                observations.append({"tool": name, "call_id": message.tool_call_id,
                                     "result": _decode_observation(message)})
                return message

    def clarification_of():
        return next((row["result"].get("data", {}).get("question")
                     for row in reversed(observations)
                     if row["tool"] == "ask_clarification" and row["result"].get("ok") is True), None)

    def problem_of(answer):
        if not answer:
            return "empty_answer"
        if not observations:
            return None if no_tool_reply_ok(text, answer) else "no_evidence"
        problem = check_answer(answer, text, observations)
        if problem:
            return problem
        if coverage is not None:
            missing = coverage(observations)
            if missing:
                metadata["missing_goal"] = missing
                return "missing_goal"
        if any(_same_text(answer, previous) for previous in avoid or ()):
            return "repeated"
        return None

    run_config = {"recursion_limit": 2 * options.max_model_calls + 2, "max_concurrency": 1}
    if callbacks:
        run_config["callbacks"] = list(callbacks)
    try:
        context = json.dumps(state or {}, ensure_ascii=False, default=str)
        prompt = conversation_prompt() + "\n\n대화 상태(현재 관측값이나 실행 승인이 아님):\n" + context[:8000]
        agent = create_agent(model=model, tools=tools, system_prompt=prompt, middleware=[TurnLimits()])
        messages = _history_messages(history, options)
        # Engine history often already includes the current input; avoid duplication.
        if not messages or messages[-1] != {"role": "user", "content": text}:
            messages.append({"role": "user", "content": text})
        output = await asyncio.wait_for(agent.ainvoke({"messages": messages}, run_config), time_left())
        answer = _message_text(output["messages"][-1])
        if metadata["input_tokens"] + metadata["output_tokens"] > token_limit:
            return finish("token_limit")
        clarification = clarification_of()
        if clarification and all(row["tool"] == "ask_clarification" or row["result"].get("ok") is not True
                                 for row in observations):
            return finish("clarify", clarification)
        problem = problem_of(answer)
        if problem == "no_evidence" and not observations:
            # 조회 없이 운영 사실을 말한 답은 고쳐 쓰지 않고 바로 확인된 경로로 넘긴다.
            return finish("ungrounded")
        if problem and repair_enabled and problem != "empty_answer" \
                and metadata["model_calls"] < options.max_model_calls:
            # 이유를 알려 주고 한 번만 다시 쓰게 한다. 실패하면 처음 판정으로 대체 경로를 탄다.
            first_problem = problem
            metadata["repair_reason"] = first_problem
            hint = REPAIR_HINTS.get(first_problem, "조회 결과와 맞지 않는 내용이 있습니다.")
            if first_problem == "missing_goal":
                hint += " 빠진 것: " + metadata.get("missing_goal", "") + ". 필요한 도구를 호출해 함께 답하세요."
            note = ("[답변 검사 메모 · 사용자 발화 아님] 방금 답은 화면에 나가지 않았습니다. " + hint +
                    " 필요한 조회를 한 뒤, 사용자에게 보일 최종 답만 다시 쓰세요.")
            try:
                if time_left() < 1.0:
                    raise _Stopped("timeout")
                retry = await asyncio.wait_for(agent.ainvoke(
                    {"messages": list(output["messages"]) + [{"role": "user", "content": note}]}, run_config), time_left())
                repaired = _message_text(retry["messages"][-1])
                problem = problem_of(repaired)
                if problem == "repeated":
                    problem = None   # 같은 사실을 다시 말하는 것은 안전 문제가 아니다.
                    metadata["repeated"] = True
                if problem is None:
                    metadata["repaired"] = True
                    answer = repaired
                else:
                    metadata["repair_failed"] = problem
                    problem = first_problem
            except _Stopped as exc:
                metadata["repair_failed"] = exc.reason
                problem = first_problem
            except Exception as exc:
                metadata["repair_failed"] = type(exc).__name__
                problem = first_problem
        if problem == "repeated":
            metadata["repeated"] = True
            problem = None
        if problem:
            metadata["grounding_error"] = problem
            return finish("ungrounded")
        if not observations:
            metadata["smalltalk"] = True
        if metadata["input_tokens"] + metadata["output_tokens"] > token_limit:
            return finish("token_limit")
        return finish("ok", answer)
    except _Stopped as exc:
        return finish(exc.reason)
    except (TimeoutError, asyncio.TimeoutError):
        return finish("timeout")
    except Exception as exc:
        # Log exception class only: provider messages may contain user text/keys.
        _log.warning("Conversation agent stopped (%s)", type(exc).__name__)
        return finish("api_error")


def run_conversation(text, history, state, tools, model=None, *, options=None,
                     coverage=None, avoid=(), callbacks=None) -> AgentResult:
    """Synchronous entry point for the existing worker-thread HTTP contract.

    Async consumers must await ``arun_conversation``; no background loop/thread is
    created, so returning from a timed-out turn cannot leave a tool executing.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(arun_conversation(text, history, state, tools, model, options=options,
                                             coverage=coverage, avoid=avoid, callbacks=callbacks))
    return AgentResult(status="async_required", metadata={"implementation": "langchain_create_agent"})
