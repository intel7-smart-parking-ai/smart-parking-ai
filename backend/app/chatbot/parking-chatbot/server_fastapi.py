"""팀 백엔드(FastAPI)용 진입점. server_dev.py 와 같은 엔진·같은 경로.

    pip install fastapi uvicorn
    uvicorn server_fastapi:app --reload --port 8080

팀 FastAPI 앱에 합칠 때는 아래 router 만 가져가서 app.include_router(router) 하면 된다.
"""
from fastapi import APIRouter, FastAPI, HTTPException, Query, Request, Depends
from fastapi.staticfiles import StaticFiles
from pathlib import Path
from pydantic import BaseModel, Field, StrictBool

from chatbot import engine, incidents
from chatbot.state import Conflict
from chatbot.adapters import DataUnavailable
from chatbot.http_api import change_report
from chatbot.access import require_admin, AccessDenied


def privileged(request: Request):
    try:
        require_admin(request.headers.get("authorization"), request.client.host if request.client else "", "/demo/" in request.url.path)
    except AccessDenied as exc:
        raise HTTPException(exc.status, str(exc)) from exc

router = APIRouter(prefix="/api/v1/chat", tags=["chatbot"])


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


@router.post("")
def chat(body: ChatIn):
    return _service(engine.handle, body.session_id, body.text, body.chip_id, body.event, body.request_id, body.confirmation_token, body.runtime_id)


@router.get("/config")
def parking_config():
    engine.data._ensure_catalog()
    return engine.config.catalog.public()


@router.get("/suggest")
def suggest(q: str = ""):
    return {"items": engine.suggest(q)}


@router.get("/updates")
def updates(session_id: str = Query(min_length=1, max_length=128), since: int = Query(default=0, ge=0), cursor: int = Query(default=0, ge=0), runtime_id: str | None = None):
    return _service(engine.updates, session_id, since, cursor, runtime_id)


@router.get("/admin/calls", dependencies=[Depends(privileged)])
def admin_calls():
    return {"items": engine.admin_calls()}






@router.get("/incidents", dependencies=[Depends(privileged)])
def incident_list():
    engine.current_alert()
    return {"items": incidents.list_public(), "runtime_id": engine.RUNTIME_ID}


def _ok(r):
    if not r or "error" in r:
        raise HTTPException(400, (r or {}).get("error", "없는 보고서"))
    return r


@router.post("/incidents/{inc_id}/check", dependencies=[Depends(privileged)])
def incident_check(inc_id: int, body: CheckIn):
    return _service(change_report, inc_id, "check", body.model_dump())


@router.post("/incidents/{inc_id}/notes", dependencies=[Depends(privileged)])
def incident_notes(inc_id: int, body: NotesIn):
    return _service(change_report, inc_id, "notes", body.model_dump())


@router.post("/incidents/{inc_id}/confirm", dependencies=[Depends(privileged)])
def incident_confirm(inc_id: int, body: ConfirmIn):
    return _service(change_report, inc_id, "confirm", body.model_dump())


@router.get("/stats", dependencies=[Depends(privileged)])
def stats():
    return engine.stats()


@router.get("/demo/cars", dependencies=[Depends(privileged)])
def demo_cars():
    return {"items": _service(engine.data.list_cars), **_demo(engine.data.demo_state)}


@router.post("/demo/alert", dependencies=[Depends(privileged)])
def demo_alert(body: AlertIn):
    try:
        r = engine.data.set_alert(body.level, body.lot_id, body.spot_no)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    engine.current_alert()
    return r


@router.post("/demo/exit", dependencies=[Depends(privileged)])
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
def demo_enter(body: EnterIn):
    return _demo(engine.data.enter_car, body.lot_id, body.car_id)


@router.post("/demo/fill", dependencies=[Depends(privileged)])
def demo_fill(body: LotIn):
    return _demo(engine.data.fill_lot, body.lot_id)


@router.post("/demo/clear", dependencies=[Depends(privileged)])
def demo_clear(body: LotIn):
    return _demo(engine.data.clear_demo, body.lot_id)


@router.post("/demo/unrecognized", dependencies=[Depends(privileged)])
def demo_unrecognized(body: EnterIn):
    return _demo(engine.data.enter_unrecognized, body.lot_id)


class UncertainIn(BaseModel):
    lot_id: str
    spot_no: int = Field(ge=1, strict=True)
    uncertain: StrictBool


@router.post("/demo/uncertain", dependencies=[Depends(privileged)])
def demo_uncertain(body: UncertainIn):
    return _demo(engine.data.set_uncertain, body.lot_id, body.spot_no, body.uncertain)


@router.post("/demo/restriction", dependencies=[Depends(privileged)])
def demo_restriction(body: RestrictionIn):
    return _demo(engine.data.set_restriction, body.lot_id, body.restricted, body.reason)


@router.post("/demo/reset", dependencies=[Depends(privileged)])
def demo_reset():
    _service(engine.reset_all)
    return {"ok": True}


app = FastAPI(title="주차장 안내 챗봇 (EPIC 06 테스트 모델)")
app.include_router(router)
app.mount("/", StaticFiles(directory=str(Path(__file__).parent / "static"), html=True), name="static")
