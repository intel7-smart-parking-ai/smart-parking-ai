"""질문 하나가 거친 단계(경로) 기록.

엔진(총괄)이 질문을 어느 가지로 보냈는지, 각 단계에서 왜 넘겼는지를 순서대로 남긴다.
응답 meta.route(한 줄 요약)와 모니터링 기록에 들어가서, 이상한 답의 원인을 바로 찾게 한다.

    시작·버튼 → 안전 확인(guard) → 정해진 답(boundary) → 업무 흐름(workflow)
      → 대화 AI(agent) → 규칙 답(fallback)

단계 이름(stage)과 결과(outcome)는 짧은 영문 식별자, 이유(why)는 사람이 읽는 값이다.
기록 실패가 답을 막지 않도록 모든 함수는 조용히 넘어간다.
"""
from __future__ import annotations

from contextvars import ContextVar

_trace: ContextVar[list | None] = ContextVar("route_trace", default=None)

# 단계 이름 → 화면·문서용 한국어 이름
STAGES = {
    "start": "대화 시작", "chip": "버튼", "guard": "안전 확인", "boundary": "정해진 답", "workflow": "업무 흐름",
    "plan": "계획표", "agent": "대화 AI", "fallback": "대체 규칙 답(AI 실패 후)", "rule": "규칙 처리",
    "replay": "같은 요청 재전송",
}


def begin():
    return _trace.set([])


def end(token):
    try:
        _trace.reset(token)
    except (ValueError, LookupError):
        pass


def mark(stage: str, outcome: str, **why):
    steps = _trace.get()
    if steps is None:
        return
    step = {"stage": stage, "outcome": str(outcome)}
    step.update({k: v for k, v in why.items() if v not in (None, "", [], {}, ())})
    steps.append(step)


def steps() -> list:
    return [dict(step) for step in (_trace.get() or [])]


def summary(items) -> str:
    """'guard:pass > boundary:pass > agent:timeout > fallback:get_empty_spots' 형식."""
    return " > ".join(f"{s['stage']}:{s['outcome']}" for s in items or [])


def last(items, stage):
    return next((s for s in reversed(items or []) if s.get("stage") == stage), None)
