"""D-20/D-28 application composition over the existing demo event pipeline.

Only confirmed occupancy callbacks write sessions. Simulators emit VisionEvent evidence
through the same authenticated receiver as cameras. Billing/alerts/chatbot stay owned modules.
"""

from __future__ import annotations
import asyncio
import json
import os
import httpx
import logging
import sqlite3
from contextlib import asynccontextmanager, closing
from copy import copy
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Literal
from uuid import uuid4
from fastapi import APIRouter, Depends, HTTPException, Header
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from backend.app.api.occupancy_query_demo import create_demo_app
from backend.app.api.occupancy_prototype import OccupancyExperiment
from backend.app.api.vision_event_schema import VisionEvent
from backend.app.api.vision_event_store import Receipt, canonical_payload, InMemoryEventStore
from backend.app.api.billing_router import billing_router, get_session_fee, create_mock_payment
from backend.app.api.session_queries import router as session_router
from backend.app.billing.db import get_db_conn, get_db_path, init_schema, seed_defaults
from backend.app.billing.schemas import PaymentCreateRequest
from backend.app.database.parking_sessions import enter, exit_session, SessionError
from backend.app.alerts.router import (
    router as alert_router,
    init_alerts_schema,
    ingest_led_fire_event,
    LedFireEvent,
    create_alert,
)
from backend.app.chatbot.router import router as chatbot_router
from backend.app.simulation.mock_data import SPACES


def utcnow():
    return datetime.now(timezone.utc)


SCHEMA = """
CREATE TABLE IF NOT EXISTS pipeline_events(sequence INTEGER PRIMARY KEY AUTOINCREMENT,event_id TEXT UNIQUE NOT NULL,payload TEXT NOT NULL,canonical BLOB NOT NULL,applied INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS simulation_targets(lot_id TEXT NOT NULL,space_id TEXT NOT NULL,occupied INTEGER NOT NULL DEFAULT 0,vehicle_number TEXT,plate_number TEXT,PRIMARY KEY(lot_id,space_id));
CREATE TABLE IF NOT EXISTS simulation_controls(lot_id TEXT PRIMARY KEY,running INTEGER NOT NULL DEFAULT 0,interval_seconds INTEGER NOT NULL DEFAULT 8,next_tick REAL NOT NULL DEFAULT 0,last_error TEXT);
CREATE TABLE IF NOT EXISTS simulation_movements(event_id TEXT PRIMARY KEY,lot_id TEXT NOT NULL,space_id TEXT NOT NULL,action TEXT NOT NULL,vehicle_number TEXT,session_id TEXT,occurred_at TEXT NOT NULL);
"""


class EventPipeline(OccupancyExperiment):
    """Durable event journal; recovery reuses engine transitions without replaying effects."""

    def __init__(self, *, database, session_enter=enter, session_exit=exit_session, **kwargs):
        super().__init__(**kwargs)
        self.database = Path(database)
        self.session_enter = session_enter
        self.session_exit = session_exit
        self.recovering = False
        self.gate = RLock()

    def connection(self):
        conn = sqlite3.connect(self.database, timeout=15, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def recover(self):
        with self.gate, closing(self.connection()) as conn:
            self.recovering = True
            try:
                for row in conn.execute(
                    "SELECT payload,applied FROM pipeline_events ORDER BY sequence"
                ).fetchall():
                    self.recovering = bool(row["applied"])
                    event = VisionEvent.model_validate_json(row["payload"])
                    if event.event_type == "mock_fire_led_observed":
                        InMemoryEventStore.receive(self, event)
                        if not row["applied"]:
                            ingest_led_fire_event(conn, LedFireEvent(**event.model_dump()))
                    else:
                        super().receive(event)
                    conn.execute(
                        "UPDATE pipeline_events SET applied=1 WHERE event_id=?", (event.event_id,)
                    )
                    conn.commit()
            finally:
                self.recovering = False

    def receive(self, event):
        with self.gate, closing(self.connection()) as conn:
            old = conn.execute(
                "SELECT canonical,applied FROM pipeline_events WHERE event_id=?", (event.event_id,)
            ).fetchone()
            if old:
                if old["canonical"] != canonical_payload(event):
                    return Receipt.CONFLICT
                if old["applied"]:
                    return Receipt.DUPLICATE
            pending = conn.execute(
                "SELECT event_id FROM pipeline_events WHERE applied=0 ORDER BY sequence LIMIT 1"
            ).fetchone()
            if pending and pending["event_id"] != event.event_id:
                raise HTTPException(503, "미완료 이벤트를 같은 event_id로 먼저 재시도하세요.")
            # Journal evidence before session/alert side effects. Pending records are
            # retried idempotently or recovered after process interruption.
            if not old:
                if (
                    event.event_type == "mock_fire_led_observed"
                    and event.space_id is not None
                    and not any(
                        s.lot_id == event.lot_id and s.space_id == event.space_id for s in SPACES
                    )
                ):
                    raise HTTPException(422, "도면에 없는 주차칸입니다.")
                if (
                    event.event_type != "mock_fire_led_observed"
                    and (event.lot_id, event.space_id) not in self._spaces
                ):
                    raise HTTPException(422, "도면에 없는 주차칸입니다.")
                conn.execute(
                    "INSERT INTO pipeline_events(event_id,payload,canonical) VALUES(?,?,?)",
                    (event.event_id, event.model_dump_json(), canonical_payload(event)),
                )
                conn.commit()
            try:
                if event.event_type == "mock_fire_led_observed":
                    result = InMemoryEventStore.receive(self, event)
                else:
                    result = super().receive(event)
            except HTTPException as exc:
                # Permanent semantic rejections must not poison startup recovery.
                # Transient writes remain pending and block overtaking observations.
                if exc.status_code < 500:
                    conn.execute("DELETE FROM pipeline_events WHERE event_id=?", (event.event_id,))
                    conn.commit()
                raise
            if result in (Receipt.ACCEPTED, Receipt.DUPLICATE):
                if event.event_type == "mock_fire_led_observed":
                    ingest_led_fire_event(conn, LedFireEvent(**event.model_dump()))
                conn.execute(
                    "UPDATE pipeline_events SET applied=1 WHERE event_id=?", (event.event_id,)
                )
                conn.commit()
            elif not old:
                conn.execute("DELETE FROM pipeline_events WHERE event_id=?", (event.event_id,))
                conn.commit()
            return result

    def sync_session(self, row, observed_at):
        # During recovery, reconnect historical session IDs; never create past sessions again.
        with closing(self.connection()) as conn:
            stamp = observed_at.isoformat()
            if self.recovering:
                active = conn.execute(
                    (
                        'SELECT * FROM parking_sessions WHERE lot_id=? AND space_id=? AND julianday(entry_at)<'
                        '=julianday(?) AND (exit_at IS NULL OR julianday(exit_at)>=julianday(?)) ORDER BY juli'
                        'anday(entry_at) DESC LIMIT 1'
                    ),
                    (row["lot_id"], row["space_id"], stamp, stamp),
                ).fetchone()
                if row["status"] == "occupied" and active:
                    row.update(
                        session_id=active["session_id"],
                        session_car_id=active["car_id"],
                        entry_at=active["entry_at"],
                    )
                elif row["status"] == "empty":
                    for key in ("session_id", "session_car_id", "entry_at"):
                        row.pop(key, None)
                return
            active = conn.execute(
                "SELECT * FROM parking_sessions WHERE lot_id=? AND space_id=? AND status='active'",
                (row["lot_id"], row["space_id"]),
            ).fetchone()
            try:
                if row["status"] == "occupied":
                    if active:
                        if active["car_id"] != row["car_id"]:
                            raise HTTPException(
                                409, "활성 세션의 차량 식별 변경은 확인이 필요합니다."
                            )
                        row.update(
                            session_id=active["session_id"],
                            session_car_id=active["car_id"],
                            entry_at=active["entry_at"],
                        )
                    else:
                        session = self.session_enter(
                            conn,
                            session_id=uuid4().hex,
                            lot_id=row["lot_id"],
                            space_id=row["space_id"],
                            car_id=row["car_id"],
                            entry_at=observed_at,
                        )
                        row.update(
                            session_id=session.session_id,
                            session_car_id=session.car_id,
                            entry_at=session.entry_at,
                        )
                        self.record_movement(conn, row, "entry", session.session_id, stamp)
                elif row["status"] == "empty":
                    if active:
                        self.session_exit(
                            conn, session_id=active["session_id"], exit_at=observed_at
                        )
                        self.record_movement(conn, row, "exit", active["session_id"], stamp)
                    for key in ("session_id", "session_car_id", "entry_at"):
                        row.pop(key, None)
            except SessionError as exc:
                raise HTTPException(exc.status_code, exc.body) from exc
            except sqlite3.Error as exc:
                raise HTTPException(503, "세션 저장 실패. 동일 이벤트를 재시도하세요.") from exc

    def record_movement(self, conn, row, action, session_id, stamp):
        if row["lot_id"] not in ("C", "D") or row.get("source") != "simulation":
            return
        target = conn.execute(
            "SELECT vehicle_number FROM simulation_targets WHERE lot_id=? AND space_id=?",
            (row["lot_id"], row["space_id"]),
        ).fetchone()
        conn.execute(
            "INSERT INTO simulation_movements VALUES(?,?,?,?,?,?,?)",
            (
                uuid4().hex,
                row["lot_id"],
                row["space_id"],
                action,
                target["vehicle_number"] if target else None,
                session_id,
                stamp,
            ),
        )
        conn.commit()


class SimulationCommand(BaseModel):
    space_id: str | None = None
    vehicle_number: str | None = Field(None, pattern=r"^[0-9]{4}$")
    interval_seconds: int = Field(8, ge=3, le=60)


class MockAlertCommand(BaseModel):
    lot_id: Literal["A", "B", "C", "D"]


def create_runtime_app(
    *,
    token: str,
    database: Path | str | None = None,
    mode="demo",
    clock=utcnow,
    session_enter=enter,
    session_exit=exit_session,
):
    database = Path(database) if database is not None else get_db_path()
    store_ref = {}

    def store_factory(**kwargs):
        # Use the exact demo engine and callback; journal adds recovery, not a second state path.
        kwargs["on_confirmed"] = lambda row, at: store_ref["store"].sync_session(row, at)
        store = EventPipeline(
            database=database, session_enter=session_enter, session_exit=session_exit, **kwargs
        )
        store_ref["store"] = store
        return store

    def enrich(row):
        row = dict(row, zone=row["space_id"].split("-")[0])
        if row.get("session_id") and row["status"] == "occupied":
            with closing(store_ref["store"].connection()) as conn:
                target = conn.execute(
                    "SELECT vehicle_number,plate_number FROM simulation_targets WHERE lot_id=? AND space_id=?",
                    (row["lot_id"], row["space_id"]),
                ).fetchone()
            if target:
                row.update(dict(target))
        # Uncertain observations do not close the session or erase its entry time.
        if not row.get("entry_at"):
            with closing(store_ref["store"].connection()) as conn:
                active = conn.execute(
                    "SELECT entry_at FROM parking_sessions WHERE lot_id=? AND space_id=? AND status='active'",
                    (row["lot_id"], row["space_id"]),
                ).fetchone()
            row["entry_at"] = active["entry_at"] if active else None
        return row

    app = create_demo_app(
        token=token,
        clock=clock,
        public_reads=True,
        max_events=int(os.getenv("PARKING_MAX_EVENTS", "1000000")),
        store_factory=store_factory,
        enrich_space=enrich,
    )
    store = store_ref["store"]
    app.title = "PARK:ON 공통 이벤트 통합 API"
    app.state.pipeline = store
    app.state.billing_path = database
    app.state.simulation_enabled = mode == "demo"

    def db_connection():
        with closing(store.connection()) as conn:
            yield conn

    app.dependency_overrides[get_db_conn] = db_connection

    def active_alert(conn, session_id):
        return (
            conn.execute(
                (
                    'SELECT 1 FROM alerts a JOIN parking_sessions s ON s.lot_id=a.lot_id WHERE s.session_i'
                    "d=? AND a.level>=2 AND a.status!='resolved' LIMIT 1"
                ),
                (session_id,),
            ).fetchone()
            is not None
        )

    # Register adapters before existing routers; owned module implementations remain unchanged.
    route = APIRouter(prefix="/api/v1")

    @route.get("/health")
    def health():
        with closing(store.connection()) as conn:
            conn.execute("SELECT 1")
        return dict(
            status="ok",
            database="sqlite",
            mode=mode,
            capabilities=dict(simulation=mode == "demo", vision_events=True),
            timestamp=clock().isoformat(),
        )

    @route.get("/sessions/{session_id}/fee")
    def fee(session_id: str, conn=Depends(get_db_conn)):
        response = get_session_fee(session_id=session_id, now=None, conn=conn)
        data = json.loads(response.body)
        if response.status_code == 200:
            receipt = conn.execute(
                (
                    "SELECT * FROM mock_payments WHERE session_id=? AND status='completed' ORDER BY create"
                    'd_at DESC LIMIT 1'
                ),
                (session_id,),
            ).fetchone()
            data["payment"] = dict(receipt) if receipt else None
            if receipt:
                data["amount_krw"] = receipt["amount_krw"]
            data["payment_blocked"] = active_alert(conn, session_id)
        return JSONResponse(status_code=response.status_code, content=data)

    @route.post("/mock-payments")
    def pay(req: PaymentCreateRequest, conn=Depends(get_db_conn)):
        # Serialize alarm check and payment entry with receiver alarm updates.
        with store.gate:
            if active_alert(conn, req.session_id):
                return JSONResponse(
                    status_code=409,
                    content={
                        "error": dict(
                            code="EVACUATION_ACTIVE",
                            message="대피 단계의 모의 화재 경보 중에는 결제할 수 없습니다.",
                            details=[],
                        )
                    },
                )
            return create_mock_payment(req=req, conn=conn)

    @route.post("/internal/alerts")
    def internal_alert(
        event: LedFireEvent, authorization: str = Header(default=""), conn=Depends(get_db_conn)
    ):
        with store.gate:
            return create_alert(event=event, authorization=authorization, conn=conn)

    @route.get("/vehicle-search")
    def vehicle_search(vehicle_number: str):
        if len(vehicle_number) != 4 or not vehicle_number.isascii() or not vehicle_number.isdigit():
            raise HTTPException(422, "차량번호 뒤 4자리를 입력해주세요.")
        matches = [
            enrich(row)
            for lot in ("C", "D")
            for row in store.spaces(lot, as_of=clock())
            if row["status"] == "occupied" and enrich(row).get("vehicle_number") == vehicle_number
        ]
        if len(matches) != 1:
            raise HTTPException(404, "단일 차량 위치를 확인할 수 없습니다.")
        row = matches[0]
        row["observed_at"] = row["observed_at"].isoformat() if row["observed_at"] else None
        return dict(row, lookup_type="plate")

    async def send_event(event):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://pipeline"
        ) as client:
            response = await client.post(
                "/api/v1/internal/vision-events",
                json=event.model_dump(mode="json"),
                headers={"Authorization": "Bearer " + token},
            )
            response.raise_for_status()
            return response

    @route.post("/demo/alerts")
    async def mock_alert(body: MockAlertCommand):
        if mode != "demo":
            raise HTTPException(403, "모의 경보 버튼은 demo 모드에서만 사용할 수 있습니다.")
        event = VisionEvent(
            event_id=uuid4().hex,
            event_type="mock_fire_led_observed",
            source="manual",
            lot_id=body.lot_id,
            camera_id=None,
            space_id=None,
            car_id=None,
            confidence=0.99,
            bbox_xyxy=None,
            observed_at=clock(),
        )
        await send_event(event)
        with closing(store.connection()) as conn:
            return dict(
                conn.execute(
                    "SELECT a.* FROM alerts a JOIN alert_events e USING(alert_id) WHERE e.event_id=?",
                    (event.event_id,),
                ).fetchone()
            ) | {"is_mock": True}

    def simulation_state(lot):
        if lot not in ("C", "D") or mode != "demo":
            raise HTTPException(403, "가상 시뮬레이터를 사용할 수 없습니다.")
        with closing(store.connection()) as conn:
            control = dict(
                conn.execute("SELECT * FROM simulation_controls WHERE lot_id=?", (lot,)).fetchone()
            )
            events = [
                dict(r)
                for r in conn.execute(
                    "SELECT * FROM simulation_movements WHERE lot_id=? ORDER BY occurred_at DESC LIMIT 30",
                    (lot,),
                )
            ]
        return dict(control, running=bool(control["running"]), events=events)

    @route.get("/simulation/{lot}")
    def state(lot: str):
        return simulation_state(lot)

    def set_target(lot, action, body):
        simulation_state(lot)
        with store.gate, closing(store.connection()) as conn:
            rows = store.spaces(lot, as_of=clock())
            available = []
            for row in rows:
                target = conn.execute(
                    "SELECT * FROM simulation_targets WHERE lot_id=? AND space_id=?",
                    (lot, row["space_id"]),
                ).fetchone()
                if bool(target["occupied"]) != (action == "exit"):
                    continue
                if row["status"] != ("occupied" if action == "exit" else "empty"):
                    continue
                if action == "entry" and row["space_type"] != "general":
                    continue
                if body.space_id and row["space_id"] != body.space_id:
                    continue
                available.append((row, target))
            if not available:
                raise HTTPException(409, "입출차할 주차칸이 없습니다.")
            row, target = available[0]
            number = target["vehicle_number"]
            if action == "entry":
                number = body.vehicle_number
                if not number:
                    number = next(
                        (
                            f"{i:04}"
                            for i in range(2000, 10000)
                            if not conn.execute(
                                "SELECT 1 FROM simulation_targets WHERE vehicle_number=? AND occupied=1",
                                (f"{i:04}",),
                            ).fetchone()
                        ),
                        None,
                    )
                if conn.execute(
                    "SELECT 1 FROM simulation_targets WHERE vehicle_number=? AND occupied=1",
                    (number,),
                ).fetchone():
                    raise HTTPException(409, "이미 주차 중인 차량번호입니다.")
            conn.execute(
                (
                    'UPDATE simulation_targets SET occupied=?,vehicle_number=?,plate_number=? WHERE lot_id'
                    '=? AND space_id=?'
                ),
                (
                    action == "entry",
                    number,
                    "88가" + number if number else None,
                    lot,
                    row["space_id"],
                ),
            )
            conn.commit()
            return row["space_id"], number

    @route.post("/simulation/{lot}/{action}")
    async def command(
        lot: str,
        action: Literal["entry", "exit", "start", "stop"],
        body: SimulationCommand = SimulationCommand(),
    ):
        simulation_state(lot)
        if action in ("start", "stop"):
            with closing(store.connection()) as conn:
                conn.execute(
                    "UPDATE simulation_controls SET running=?,interval_seconds=?,next_tick=? WHERE lot_id=?",
                    (
                        action == "start",
                        body.interval_seconds,
                        clock().timestamp() + body.interval_seconds,
                        lot,
                    ),
                )
                conn.commit()
            return simulation_state(lot)
        space_id, number = set_target(lot, action, body)
        # Wait for ordinary live observations to stabilize; never send future-dated evidence.
        deadline = asyncio.get_running_loop().time() + 7
        while asyncio.get_running_loop().time() < deadline:
            row = next(r for r in store.spaces(lot, as_of=clock()) if r["space_id"] == space_id)
            if row["status"] == ("occupied" if action == "entry" else "empty"):
                with closing(store.connection()) as conn:
                    event = conn.execute(
                        (
                            'SELECT * FROM simulation_movements WHERE lot_id=? AND space_id=? AND action=? ORDER B'
                            'Y occurred_at DESC LIMIT 1'
                        ),
                        (lot, space_id, action),
                    ).fetchone()
                return (
                    dict(event)
                    if event
                    else dict(
                        lot_id=lot,
                        space_id=space_id,
                        action=action,
                        vehicle_number=number,
                        session_id=row.get("session_id"),
                        occurred_at=clock().isoformat(),
                    )
                )
            await asyncio.sleep(0.15)
        raise HTTPException(503, "관측 안정화를 기다리고 있습니다. 현황을 다시 확인해주세요.")

    async def observations():
        while True:
            with closing(store.connection()) as conn:
                targets = [dict(r) for r in conn.execute("SELECT * FROM simulation_targets")]
            observed = {
                (r["lot_id"], r["space_id"])
                for lot in ("C", "D")
                for r in store.spaces(lot, as_of=clock())
                if r["observed_at"] is not None and r["source"] == "simulation"
            }
            for target in targets:
                if (
                    not target["occupied"]
                    and (target["lot_id"], target["space_id"]) not in observed
                ):
                    continue
                event = VisionEvent(
                    event_id=uuid4().hex,
                    event_type="vehicle_observed" if target["occupied"] else "space_empty_observed",
                    source="simulation",
                    lot_id=target["lot_id"],
                    camera_id=None,
                    space_id=target["space_id"],
                    car_id=None,
                    vehicle_type="general" if target["occupied"] else None,
                    confidence=0.99,
                    bbox_xyxy=None,
                    observed_at=clock(),
                )
                try:
                    await send_event(event)
                except httpx.HTTPError:
                    logging.exception(
                        "Simulation observation rejected for %s/%s", event.lot_id, event.space_id
                    )
            with closing(store.connection()) as conn:
                controls = [
                    dict(r)
                    for r in conn.execute(
                        "SELECT * FROM simulation_controls WHERE running=1 AND next_tick<=?",
                        (clock().timestamp(),),
                    )
                ]
            for control in controls:
                try:
                    with closing(store.connection()) as conn:
                        count = conn.execute(
                            "SELECT count(*) FROM simulation_targets WHERE lot_id=? AND occupied=1",
                            (control["lot_id"],),
                        ).fetchone()[0]
                    action = (
                        "exit"
                        if count > len([s for s in SPACES if s.lot_id == control["lot_id"]]) // 2
                        else "entry"
                    )
                    space_id, number = set_target(control["lot_id"], action, SimulationCommand())
                    error = None
                except Exception as exc:
                    error = str(exc)
                with closing(store.connection()) as conn:
                    conn.execute(
                        "UPDATE simulation_controls SET next_tick=?,last_error=? WHERE lot_id=?",
                        (
                            clock().timestamp() + control["interval_seconds"],
                            error,
                            control["lot_id"],
                        ),
                    )
                    conn.commit()
            await asyncio.sleep(1)

    @asynccontextmanager
    async def lifespan(application):
        database.parent.mkdir(parents=True, exist_ok=True)
        with closing(store.connection()) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            init_schema(conn)
            seed_defaults(conn)
            init_alerts_schema(conn)
            conn.executescript(SCHEMA)
            if "applied" not in {
                r["name"] for r in conn.execute("PRAGMA table_info(pipeline_events)")
            }:
                conn.execute(
                    "ALTER TABLE pipeline_events ADD COLUMN applied INTEGER NOT NULL DEFAULT 1"
                )
            for lot in ("C", "D"):
                conn.execute("INSERT OR IGNORE INTO simulation_controls(lot_id) VALUES(?)", (lot,))
                for space in [s for s in SPACES if s.lot_id == lot]:
                    conn.execute(
                        "INSERT OR IGNORE INTO simulation_targets(lot_id,space_id) VALUES(?,?)",
                        (lot, space.space_id),
                    )
            conn.commit()
        store.recover()
        task = asyncio.create_task(observations()) if mode == "demo" else None
        try:
            yield
        finally:
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

    app.router.lifespan_context = lifespan

    # Earlier query router is preserved. It supplies summary-shaped lots; add capabilities via middleware.
    @app.middleware("http")
    async def lot_capabilities(request, call_next):
        response = await call_next(request)
        if (
            request.method == "GET"
            and request.url.path == "/api/v1/parking-lots"
            and response.status_code == 200
        ):
            raw = b"".join([chunk async for chunk in response.body_iterator])
            data = json.loads(raw)
            for lot in data["items"]:
                rows = store.spaces(lot["lot_id"], as_of=clock())
                times = [r["observed_at"] for r in rows if r["observed_at"]]
                lot.update(
                    layout_type=dict(A="basic", B="angled", C="loop", D="mall")[lot["lot_id"]],
                    simulation_enabled=mode == "demo" and lot["lot_id"] in ("C", "D"),
                    last_observed_at=max(times).isoformat() if times else None,
                    camera_status=(
                        "not_connected"
                        if not times
                        else "stale" if (clock() - max(times)).total_seconds() > 10 else "observing"
                    ),
                )
            return JSONResponse(
                content=data,
                headers={
                    k: v
                    for k, v in response.headers.items()
                    if k.lower() not in ("content-length", "content-type")
                },
            )
        return response

    app.include_router(route)
    owned = copy(billing_router)
    owned.routes = [
        r
        for r in billing_router.routes
        if r.path not in ("/api/v1/sessions/{session_id}/fee", "/api/v1/mock-payments")
        or "GET" in getattr(r, "methods", set())
        and r.path == "/api/v1/mock-payments"
    ]
    app.include_router(owned)
    app.include_router(session_router)
    app.include_router(alert_router)
    app.include_router(chatbot_router)
    return app
