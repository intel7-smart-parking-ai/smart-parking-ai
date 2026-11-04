"""HTTP endpoints shared by the standalone and embedded FastAPI apps."""
from contextlib import asynccontextmanager
from functools import partial, wraps

import anyio
from anyio.lowlevel import RunVar
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field, StrictBool

from . import config, engine, incidents
from .state import Conflict
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


router = APIRouter(prefix="/api/v1/chat", tags=["chatbot"], lifespan=_lifespan)


class ChatIn(BaseModel):
    session_id: str = Field(min_length=1, max_length=128)
    text: str | None = Field(default=None, max_length=200)
    chip_id: str | None = Field(default=None, max_length=128)
    event: str | None = Field(default=None, max_length=32)
    request_id: str | None = Field(default=None, min_length=1, max_length=128)
    confirmation_token: str | None = Field(default=None, max_length=128)
    runtime_id: str | None = Field(default=None, max_length=128)


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
    work = partial(_service, engine.handle, body.session_id, body.text, body.chip_id, body.event,
                   body.request_id, body.confirmation_token, body.runtime_id)
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
def updates(session_id: str = Query(min_length=1, max_length=128), since: int = Query(default=0, ge=0), cursor: int = Query(default=0, ge=0), runtime_id: str | None = None):
    return _service(engine.updates, session_id, since, cursor, runtime_id)


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
