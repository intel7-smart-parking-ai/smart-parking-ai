"""HTTP endpoints shared by the standalone and embedded FastAPI apps."""
from contextlib import asynccontextmanager
from functools import partial, wraps
from typing import Literal
import re
import time

import anyio
from anyio.lowlevel import RunVar
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field, StrictBool

from . import config, engine, incidents, monitoring
from .state import Conflict
from .response_locale import localize_response, question_to_korean, resolve_language, translate_timeout
from .adapters import DataUnavailable
from .contracts import alerts_source
from .http_api import change_report
from .access import require_admin, AccessDenied


_worker_limit = RunVar("parking_chatbot_worker_limit")
_chat_capacity = RunVar("parking_chatbot_chat_capacity")


def _worker(fn):
    """Keep embedded chatbot work out of the team's default AnyIO capacity."""
    @wraps(fn)
    async def run(*args, **kwargs):
        limiter = None
        if config.EMBEDDED:
            limiter = _worker_limit.get(None)
            if limiter is None:
                limiter = anyio.CapacityLimiter(config.EMBEDDED_CHAT_WORKERS)
                _worker_limit.set(limiter)
        return await anyio.to_thread.run_sync(partial(fn, *args, **kwargs), limiter=limiter)

    return run


def privileged(request: Request):
    try:
        require_admin(request.headers.get("authorization"), request.client.host if request.client else "", "/demo/" in request.url.path)
    except AccessDenied as exc:
        raise HTTPException(exc.status, str(exc)) from exc

@asynccontextmanager
async def _lifespan(app):
    config.log_startup()
    yield
    from . import monitoring
    monitoring.shutdown()


router = APIRouter(prefix="/api/v1/chat", tags=["chatbot"], lifespan=_lifespan)


Language = Literal["ko", "en", "zh-CN", "ja"]


QUESTION_TRANSLATE_SEC = 3.0   # 외국어 질문 → 한국어
FOREIGN_TURN_SEC = 6.0         # 번역된 질문의 대화 처리(대화 AI 포함)
ANSWER_DEADLINE_SEC = 17.0     # 시작부터 답 번역까지 끝낼 시각(화면 제한 20초)


def _guess_language(text):
    """language 없이 온 질문의 언어. 한글이 있거나 짧은 영문(OK, EV)이면 한국어로 둔다."""
    if not text or re.search(r"[가-힣]", text):
        return "ko"
    if re.search(r"[ぁ-んァ-ヶ]", text):
        return "ja"
    if re.search(r"[一-鿿]", text):
        return "zh-CN"
    return "en" if len(re.findall(r"[A-Za-z]{2,}", text)) >= 2 else "ko"


class ChatIn(BaseModel):
    language: Language | None = None
    session_id: str = Field(min_length=1, max_length=128)
    text: str | None = Field(default=None, max_length=200)
    chip_id: str | None = Field(default=None, max_length=128)
    event: str | None = Field(default=None, max_length=32)
    request_id: str | None = Field(default=None, min_length=1, max_length=128)
    confirmation_token: str | None = Field(default=None, max_length=128)
    runtime_id: str | None = Field(default=None, max_length=128)


class FeedbackIn(BaseModel):
    session_id: str = Field(min_length=1, max_length=128)
    rating: Literal["up", "down"]
    request_id: str | None = Field(default=None, min_length=1, max_length=128)
    comment: str | None = Field(default=None, max_length=300)


class AlertIn(BaseModel):
    level: int = Field(default=0, ge=0, le=2)
    lot_id: str | None = None
    spot_no: int | None = Field(default=None, ge=1)


class ReportIn(BaseModel):
    expected_revision: int = Field(ge=1)
    runtime_id: str


class CheckIn(ReportIn):
    index: int = Field(ge=0)
    checked: bool


class NotesIn(ReportIn):
    text: str = Field(default="", max_length=1000)


class ConfirmIn(ReportIn):
    notes: str | None = Field(default=None, max_length=1000)


class ExitIn(BaseModel):
    car_id: str


def _service(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except Conflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except (ValueError, TypeError) as exc:
        raise HTTPException(400, str(exc)) from exc
    except DataUnavailable as exc:
        raise HTTPException(503, "현재 정보를 가져올 수 없습니다.") from exc


def _team_chat_response(response):
    """Project the reply for the team's existing text-only ChatPanel."""
    text = response["text"]
    safety = response.get("safety")
    messages = []
    if safety and safety["guidance"] not in text:
        messages.append({"text": f"{safety['title']}\n{safety['guidance']}"})
    messages.append({"text": text})
    return {**response, "messages": messages}


@router.post("")
async def chat(body: ChatIn):
    # language가 없어도 한글 없이 영어 문장·가나·한자만 있으면 그 언어로 본다(봇 점검 11/10 🟠4)
    language = (resolve_language(body.language, body.text) if body.language
                else _guess_language(body.text))
    def work():
        started = time.monotonic()
        # 외국어 질문은 한국어로 바꿔 엔진에 넣고, 답은 아래 localize_response가 다시 그 언어로 옮긴다.
        # 화면의 챗봇 응답 제한은 20초(API_CONTRACT 8장, frontend api.js). 질문 번역 ≤3초 + 대화 처리 ≤6초 + 답 번역 ≤8초.
        # (11/10 이전에는 질문 번역 시간까지 4초에 넣어 대화 AI가 거의 항상 시간 초과 → 규칙 답이었다)
        q_token = translate_timeout.set(QUESTION_TRANSLATE_SEC) if language != "ko" else None
        try:
            text = question_to_korean(body.text, language) if body.text else body.text
        finally:
            if q_token is not None:
                translate_timeout.reset(q_token)
        cap_token = engine.budget_cap.set(time.monotonic() + FOREIGN_TURN_SEC) if language != "ko" else None
        try:
            response = _service(engine.handle, body.session_id, text, body.chip_id, body.event,
                                body.request_id, body.confirmation_token, body.runtime_id)
        finally:
            if cap_token is not None:
                engine.budget_cap.reset(cap_token)
        if not body.language and language == "ko":
            return response
        # 질문 번역·대화 처리에 쓴 시간을 빼고 남은 시간만 답 번역에 쓴다(화면 제한 20초 안, 여유 3초).
        token = translate_timeout.set(max(2.0, ANSWER_DEADLINE_SEC - (time.monotonic() - started)))
        try:
            localized = {**localize_response(response, language), "language": language}
        finally:
            translate_timeout.reset(token)
        if language != "ko" and isinstance(localized.get("meta"), dict):
            # 번역도 경로에 남긴다: 질문 번역(되었는지) > 엔진 경로 > 답 번역
            asked = "ok" if text != body.text else "kept_original"
            localized["meta"]["route"] = (f"translate_in:{asked} > {localized['meta'].get('route') or '-'}"
                                          f" > translate_out:{language}")
        return localized
    if not config.EMBEDDED:
        return await anyio.to_thread.run_sync(work)
    capacity = _chat_capacity.get(None)
    if capacity is None:
        limit = config.CHAT_MAX_CONCURRENT
        capacity = anyio.Semaphore(limit), anyio.CapacityLimiter(limit)
        _chat_capacity.set(capacity)
    admission, workers = capacity
    try:
        admission.acquire_nowait()
    except anyio.WouldBlock:
        return _team_chat_response(_service(engine.busy_response, body.runtime_id))
    try:
        return _team_chat_response(await anyio.to_thread.run_sync(work, limiter=workers))
    finally:
        admission.release()


@router.get("/config")
@_worker
def parking_config():
    engine.data._ensure_catalog()
    return config.catalog.public_snapshot()


@router.get("/suggest")
@_worker
def suggest(q: str = ""):
    return {"items": engine.suggest(q)}


@router.get("/updates")
@_worker
def updates(session_id: str = Query(min_length=1, max_length=128), since: int = Query(default=0, ge=0), cursor: int = Query(default=0, ge=0), runtime_id: str | None = None, language: Language | None = None):
    return localize_response(_service(engine.updates, session_id, since, cursor, runtime_id), language or "ko")


@router.post("/feedback")
def chat_feedback(body: FeedbackIn):
    """답 아래 👍/👎. 모니터링이 켜져 있으면 그 답의 Langfuse trace에 user_feedback 점수로 남긴다.
    답·대화 상태는 바꾸지 않는다. recorded=False는 모니터링 꺼짐 또는 해당 답 기록 없음."""
    return {"ok": True, "recorded": monitoring.feedback(body.session_id, body.rating, body.request_id, body.comment)}


@router.get("/admin/calls", dependencies=[Depends(privileged)])
@_worker
def admin_calls():
    return {"items": engine.admin_calls()}


@router.get("/incidents", dependencies=[Depends(privileged)])
@_worker
def incident_list():
    alert = engine.current_alert()
    return {"items": incidents.list_public(), "runtime_id": engine.RUNTIME_ID,
            "alerts_source": alerts_source(alert)}


@router.post("/incidents/{inc_id}/check", dependencies=[Depends(privileged)])
@_worker
def incident_check(inc_id: int, body: CheckIn):
    return _service(change_report, inc_id, "check", body.model_dump())


@router.post("/incidents/{inc_id}/notes", dependencies=[Depends(privileged)])
@_worker
def incident_notes(inc_id: int, body: NotesIn):
    return _service(change_report, inc_id, "notes", body.model_dump())


@router.post("/incidents/{inc_id}/confirm", dependencies=[Depends(privileged)])
@_worker
def incident_confirm(inc_id: int, body: ConfirmIn):
    return _service(change_report, inc_id, "confirm", body.model_dump())


@router.get("/stats", dependencies=[Depends(privileged)])
@_worker
def stats():
    return engine.stats()


@router.get("/demo/cars", dependencies=[Depends(privileged)])
@_worker
def demo_cars():
    return {"items": _service(engine.data.list_cars), **_demo(engine.data.demo_state)}


@router.post("/demo/alert", dependencies=[Depends(privileged)])
@_worker
def demo_alert(body: AlertIn):
    try:
        r = engine.data.set_alert(body.level, body.lot_id, body.spot_no)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    engine.current_alert()
    return r


@router.post("/demo/exit", dependencies=[Depends(privileged)])
@_worker
def demo_exit(body: ExitIn):
    try:
        return engine.data.exit_car(body.car_id.zfill(2))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


class EnterIn(BaseModel):
    lot_id: str
    car_id: str | None = None


class LotIn(BaseModel):
    lot_id: str | None = None


class RestrictionIn(BaseModel):
    lot_id: str
    restricted: StrictBool
    reason: str = ""


def _demo(fn, *args):
    try:
        return fn(*args)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/demo/enter", dependencies=[Depends(privileged)])
@_worker
def demo_enter(body: EnterIn):
    return _demo(engine.data.enter_car, body.lot_id, body.car_id)


@router.post("/demo/fill", dependencies=[Depends(privileged)])
@_worker
def demo_fill(body: LotIn):
    return _demo(engine.data.fill_lot, body.lot_id)


@router.post("/demo/clear", dependencies=[Depends(privileged)])
@_worker
def demo_clear(body: LotIn):
    return _demo(engine.data.clear_demo, body.lot_id)


@router.post("/demo/unrecognized", dependencies=[Depends(privileged)])
@_worker
def demo_unrecognized(body: EnterIn):
    return _demo(engine.data.enter_unrecognized, body.lot_id)


class UncertainIn(BaseModel):
    lot_id: str
    spot_no: int = Field(ge=1, strict=True)
    uncertain: StrictBool


@router.post("/demo/uncertain", dependencies=[Depends(privileged)])
@_worker
def demo_uncertain(body: UncertainIn):
    return _demo(engine.data.set_uncertain, body.lot_id, body.spot_no, body.uncertain)


@router.post("/demo/restriction", dependencies=[Depends(privileged)])
@_worker
def demo_restriction(body: RestrictionIn):
    return _demo(engine.data.set_restriction, body.lot_id, body.restricted, body.reason)


@router.post("/demo/reset", dependencies=[Depends(privileged)])
@_worker
def demo_reset():
    _service(engine.reset_all)
    return {"ok": True}
