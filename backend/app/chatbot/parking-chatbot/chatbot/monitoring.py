"""턴 단위 모니터링(선택). Langfuse 또는 로컬 JSON 한 줄 기록으로 '모델 답이 나갔는지, 왜 대체됐는지'를 남긴다.

원칙
- 기본은 꺼짐(MONITOR_BACKEND=off). 켜져 있어도 답·흐름·시간 예산에 영향을 주지 않는다.
  모든 기록 호출은 예외를 삼키고, 실패가 반복되면 스스로 꺼진다.
- Langfuse는 관측(모니터링) 용도로만 쓴다. 프롬프트를 Langfuse에서 내려받지 않으며, 답 생성 경로를 바꾸지 않는다.
- 비밀 키·토큰은 기록하지 않는다. 세션 ID는 해시로만 남긴다. MONITOR_CAPTURE_TEXT=0이면 원문 대신 길이만 남긴다.

구조
    engine.handle ─ with monitoring.turn(...) as mon ─┬─ conversation_turn: mon.callbacks() → LangChain 콜백(모델·도구 호출 구간)
                                                      │                     mon.note(agent=...) → 도구 목록·검사 결과
                                                      └─ mon.record(response) → 최종 출처(llm/rule)·상태·지연
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
import atexit
import hashlib
import json
import os
from pathlib import Path
import random
import threading
import time

from . import config
from .diagnostics import logger

_log = logger(__name__)
_current: ContextVar["Turn | None"] = ContextVar("monitor_turn", default=None)
_lock = threading.Lock()
_state = {"client": None, "flavor": None, "disabled_reason": None, "errors": 0, "turns": 0,
          "last_error": None, "local_bytes": 0}
_MAX_ERRORS = 20
_LOCAL_MAX_BYTES = 5 * 1024 * 1024


def _backend():
    return "off" if _state["disabled_reason"] else config.MONITOR_BACKEND


def enabled() -> bool:
    return _backend() != "off"


def current():
    return _current.get()


def _error(where, exc):
    with _lock:
        _state["errors"] += 1
        _state["last_error"] = f"{where}:{type(exc).__name__}"
        if _state["errors"] >= _MAX_ERRORS and not _state["disabled_reason"]:
            _state["disabled_reason"] = "too_many_errors"
            _log.warning("Monitoring disabled after repeated failures (%s)", _state["last_error"])


def _session_hash(session_id):
    return hashlib.sha256(str(session_id or "").encode("utf-8")).hexdigest()[:16]


def _text(value):
    if value is None:
        return None
    if config.MONITOR_CAPTURE_TEXT:
        return value[:2000]
    return {"chars": len(value)}


def _langfuse():
    """Langfuse 클라이언트를 한 번만 만든다. 미설치·키 없음이면 경고 1회 후 모니터링을 끈다."""
    with _lock:
        if _state["client"] is not None or _state["disabled_reason"]:
            return _state["client"]
        if not (config.LANGFUSE_PUBLIC_KEY and config.LANGFUSE_SECRET_KEY):
            _state["disabled_reason"] = "missing_langfuse_keys"
            _log.warning("MONITOR_BACKEND=langfuse but LANGFUSE_PUBLIC_KEY/LANGFUSE_SECRET_KEY are not set; monitoring off")
            return None
        try:
            from langfuse import Langfuse
        except ImportError:
            _state["disabled_reason"] = "langfuse_not_installed"
            _log.warning("MONITOR_BACKEND=langfuse but the langfuse package is not installed; monitoring off")
            return None
        try:
            client = Langfuse(public_key=config.LANGFUSE_PUBLIC_KEY, secret_key=config.LANGFUSE_SECRET_KEY,
                              host=config.LANGFUSE_HOST)
        except Exception as exc:  # 잘못된 주소 등. 답 경로에는 영향 없음.
            _state["disabled_reason"] = "langfuse_init_failed"
            _log.warning("Langfuse client init failed (%s); monitoring off", type(exc).__name__)
            return None
        _state["client"] = client
        _state["flavor"] = "v3" if hasattr(client, "start_as_current_span") else "v2" if hasattr(client, "trace") else None
        if _state["flavor"] is None:
            _state["client"], _state["disabled_reason"] = None, "langfuse_unsupported_version"
            _log.warning("Unsupported langfuse SDK version; monitoring off")
            return None
        return client


class Turn:
    """한 번의 /api/v1/chat 처리. 모든 메서드는 실패해도 조용히 넘어간다."""

    def __init__(self, session_id, text, chip_id, event):
        self.started = time.monotonic()
        self.session = _session_hash(session_id)
        self.input = {"text": _text(text), "chip_id": chip_id, "event": event}
        self.notes = {}
        self.output = None
        self.error = None
        self._span_cm = None
        self._span = None
        self._trace = None
        self._handler = None

    # ---------------------------------------------------------------- 시작·종료
    def start(self):
        if _backend() != "langfuse":
            return
        client = _langfuse()
        if client is None:
            return
        try:
            tags = ["parking-chatbot", "chip" if self.input["chip_id"] else "text"]
            if _state["flavor"] == "v3":
                self._span_cm = client.start_as_current_span(name="chat-turn", input=self.input)
                self._span = self._span_cm.__enter__()
                self._span.update_trace(name="parking-chat", session_id=self.session, tags=tags, input=self.input,
                                        metadata={"data_source": config.DATA_SOURCE, "model": config.LLM_MODEL or None})
            else:
                self._trace = client.trace(name="parking-chat", session_id=self.session, tags=tags, input=self.input,
                                           metadata={"data_source": config.DATA_SOURCE, "model": config.LLM_MODEL or None})
        except Exception as exc:
            _error("start", exc)
            self._span_cm = self._span = self._trace = None

    def callbacks(self):
        """대화 에이전트 실행에 붙일 LangChain 콜백. 모델·도구 호출 구간과 토큰이 같은 trace 아래에 남는다."""
        if _backend() != "langfuse" or (self._span is None and self._trace is None):
            return None
        if self._handler is None:
            try:
                if _state["flavor"] == "v3":
                    from langfuse.langchain import CallbackHandler
                    self._handler = CallbackHandler(public_key=config.LANGFUSE_PUBLIC_KEY)
                else:
                    self._handler = self._trace.get_langchain_handler(update_parent=False)
            except Exception as exc:
                _error("callbacks", exc)
                return None
        return [self._handler]

    def note(self, **values):
        """턴 중간 정보(사용한 도구, 근거 검사 결과 등)."""
        try:
            for key, value in values.items():
                self.notes[key] = value
        except Exception as exc:
            _error("note", exc)

    def record(self, response):
        try:
            meta = response.get("meta") or {}
            card = response.get("card") or {}
            self.output = {
                "text": _text(response.get("text")),
                "intent": meta.get("intent"), "source": meta.get("source"),
                "agent_status": meta.get("agent_status"), "model_calls": meta.get("model_calls"),
                "grounding_error": meta.get("grounding_error"), "repair_reason": meta.get("repair_reason"),
                "repaired": bool(meta.get("repaired")), "repeated": bool(meta.get("repeated")),
                "smalltalk": bool(meta.get("smalltalk")), "flow": response.get("flow"),
                "card": card.get("type") if isinstance(card, dict) else None,
                "alert_level": (response.get("banner") or {}).get("level") if isinstance(response.get("banner"), dict) else None,
            }
        except Exception as exc:
            _error("record", exc)

    def _summary(self):
        latency = round((time.monotonic() - self.started) * 1000, 1)
        agent = self.notes.get("agent") or {}
        return {"at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "session": self.session, "input": self.input,
                "output": self.output, "error": self.error, "latency_ms": latency,
                "tools": self.notes.get("tools"), "agent": agent}

    def finish(self):
        summary = self._summary()
        with _lock:
            _state["turns"] += 1
        backend = _backend()
        if backend == "local":
            _write_local(summary)
            return
        if backend != "langfuse" or (self._span is None and self._trace is None):
            return
        try:
            out = summary["output"] or {}
            metadata = {k: summary[k] for k in ("latency_ms", "tools", "agent", "error")}
            scores = []
            if out.get("agent_status") is not None:
                scores.append(("model_answer_used", 1.0 if out.get("source") == "llm" else 0.0))
                scores.append(("repaired", 1.0 if out.get("repaired") else 0.0))
            if self._span is not None:
                self._span.update(output=out, metadata=metadata)
                self._span.update_trace(output=out)
                for name, value in scores:
                    self._span.score_trace(name=name, value=value)
            else:
                self._trace.update(output=out, metadata=metadata)
                client = _state["client"]
                for name, value in scores:
                    client.score(trace_id=self._trace.id, name=name, value=value)
        except Exception as exc:
            _error("finish", exc)
        finally:
            if self._span_cm is not None:
                try:
                    self._span_cm.__exit__(None, None, None)
                except Exception as exc:
                    _error("close", exc)


def _write_local(summary):
    try:
        path = Path(config.MONITOR_LOCAL_PATH)
        line = json.dumps(summary, ensure_ascii=False, default=str) + "\n"
        with _lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists() and path.stat().st_size + len(line) > _LOCAL_MAX_BYTES:
                backup = path.with_suffix(path.suffix + ".1")
                if backup.exists():
                    backup.unlink()
                os.replace(path, backup)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(line)
    except Exception as exc:
        _error("local", exc)


@contextmanager
def turn(session_id, text=None, chip_id=None, event=None):
    """요청 하나를 감싼다. 꺼져 있으면 None을 돌려주고 아무것도 하지 않는다."""
    if not enabled() or (config.MONITOR_SAMPLE_RATE < 1 and random.random() >= config.MONITOR_SAMPLE_RATE):
        yield None
        return
    try:
        item = Turn(session_id, text, chip_id, event)
        item.start()
    except Exception as exc:
        _error("turn", exc)
        yield None
        return
    token = _current.set(item)
    try:
        yield item
    except BaseException as exc:
        item.error = type(exc).__name__
        raise
    finally:
        _current.reset(token)
        try:
            item.finish()
        except Exception as exc:
            _error("finish", exc)


def stats() -> dict:
    with _lock:
        return {"backend": config.MONITOR_BACKEND, "active": _backend(), "disabled_reason": _state["disabled_reason"],
                "turns": _state["turns"], "errors": _state["errors"], "last_error": _state["last_error"],
                "langfuse_sdk": _state["flavor"], "capture_text": config.MONITOR_CAPTURE_TEXT}


def shutdown(timeout_sec: float = 3.0):
    """보낼 기록을 비운다(서버 종료 시). 실패해도 종료를 막지 않는다."""
    client = _state["client"]
    if client is None:
        return
    done = threading.Event()

    def run():
        try:
            client.flush()
        except Exception as exc:
            _error("flush", exc)
        finally:
            done.set()
    threading.Thread(target=run, name="monitoring-flush", daemon=True).start()
    done.wait(timeout_sec)


def reset_for_tests():
    with _lock:
        _state.update(client=None, flavor=None, disabled_reason=None, errors=0, turns=0, last_error=None)


atexit.register(shutdown)
