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
from datetime import datetime, timedelta, timezone
from pathlib import Path
from random import choice, randint, random
from threading import RLock
from typing import Literal
from uuid import uuid4
from fastapi import APIRouter, Depends, HTTPException, Header
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from backend.app.api.occupancy_query_demo import create_demo_app
from backend.app.api.occupancy_prototype import OccupancyExperiment, SIMULATION_MAX_GAP_SECONDS
from backend.app.api.vision_event_schema import VisionEvent
from backend.app.api.vision_event_store import Receipt, canonical_payload, InMemoryEventStore
from backend.app.api.billing_router import billing_router, get_session_fee, create_mock_payment
from backend.app.api.session_queries import router as session_router
from backend.app.billing.db import get_db_conn, get_db_path, init_schema, seed_defaults
from backend.app.billing.schemas import PaymentCreateRequest
from backend.app.database.parking_sessions import enter, exit_session, identify, SessionError
from backend.app.alerts.router import (
    router as alert_router,
    init_alerts_schema,
    ingest_led_fire_event,
    LedFireEvent,
    create_alert,
)
from backend.app.chatbot.router import router as chatbot_router
from backend.app.simulation.charging import charging_status, last_charge_end
from backend.app.simulation import demo_history
from backend.app.simulation.mock_data import SPACES
from backend.app.simulation.vehicle_assignment import (
    REGISTERED_VEHICLES, choose_entry_type, entry_vehicle_type,
)


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
            self._collect_identity_evidence = False
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
                    if not row["applied"]:
                        conn.execute(
                            "UPDATE pipeline_events SET applied=1 WHERE event_id=?", (event.event_id,)
                        )
                        conn.commit()
            finally:
                self.recovering = False
                self._collect_identity_evidence = True

    def _simulation_heartbeat(self, event):
        """C·D 시뮬레이터의 반복 관측(상태 변화 없음)이면 True.

        시뮬레이터는 1초마다 C·D 전 칸에 관측을 다시 보낸다. 이미 같은 상태로 확정된 칸의
        반복 관측까지 journal·접수함에 쌓으면 PARKING_MAX_EVENTS(기본 100만)에 도달해
        카메라 관측까지 503이 되고, 재시작 복구도 점점 느려진다. 상태를 바꾸는 관측
        (후보 시작·확정·간격 초과 리셋)은 그대로 기록한다. C·D는 재시작 때 새로 채운다(D-63).
        """
        if event.source != "simulation" or event.lot_id not in ("C", "D") or event.event_type not in (
            "vehicle_observed", "space_empty_observed"
        ):
            return False
        target = "occupied" if event.event_type == "vehicle_observed" else "empty"
        with self._state_lock:
            row = self._spaces.get((event.lot_id, event.space_id))
            if row is None or row["observed_at"] is None or row["status"] != target or row["source"] != "simulation":
                return False
            gap = (event.observed_at - row["observed_at"]).total_seconds()
            return (
                0 < gap <= SIMULATION_MAX_GAP_SECONDS
                and event.car_id is None
                and event.confidence >= self.policy.min_confidence
                and row["vehicle_type"] == (event.vehicle_type if target == "occupied" else None)
            )

    def _apply_unrecorded(self, event):
        # OccupancyExperiment.receive와 같은 상태 반영이지만 event_id를 접수함에 남기지 않는다.
        key = (event.lot_id, event.space_id)
        with self._state_lock:
            previous = self._spaces[key]
            row = previous.copy()
            self._apply_observation(row, event)
            if (self._on_confirmed is not None and row["version"] != previous["version"]
                    and row["status"] in ("occupied", "empty")):
                self._on_confirmed(row, event.observed_at)
            self._spaces[key] = row
        return Receipt.ACCEPTED

    def receive(self, event):
        with self.gate:
            if self._simulation_heartbeat(event):
                with closing(self.connection()) as conn:
                    pending = conn.execute(
                        "SELECT 1 FROM pipeline_events WHERE applied=0 LIMIT 1"
                    ).fetchone()
                if not pending:
                    return self._apply_unrecorded(event)
        with self.gate, closing(self.connection()) as conn:
            old = conn.execute(
                "SELECT canonical,applied,payload FROM pipeline_events WHERE event_id=?", (event.event_id,)
            ).fetchone()
            canonical = canonical_payload(event)
            if old:
                if old["canonical"] != canonical:
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
                if self._is_camera_observation(event):
                    now = self._identity_clock()
                    if event.observed_at > now + timedelta(seconds=2):
                        raise HTTPException(422, "관측 시각이 서버보다 2초 넘게 미래입니다.")
                    if event.observed_at > now:
                        event = event.model_copy(update={"observed_at": now})
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
                    (event.event_id, event.model_dump_json(), canonical),
                )
                conn.commit()
            else:
                # Replay and retry must use the same effective timestamp as the first receipt.
                event = VisionEvent.model_validate_json(old["payload"])
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
        # Applied observations can carry their existing link through replay.
        if self.recovering:
            if row["status"] == "empty":
                for key in ("session_id", "session_car_id", "entry_at"):
                    row.pop(key, None)
                return
            if row.get("session_id") and row.get("session_car_id") == row["car_id"]:
                return
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
                    if row.get("identity_observation"):
                        row["car_id"] = active["car_id"]
                    row.update(
                        session_id=active["session_id"],
                        session_car_id=active["car_id"],
                        entry_at=active["entry_at"],
                    )
                return
            active = conn.execute(
                "SELECT * FROM parking_sessions WHERE lot_id=? AND space_id=? AND status='active'",
                (row["lot_id"], row["space_id"]),
            ).fetchone()
            try:
                evidence = row.get("identity_match")
                if (
                    active and active["car_id"] is None and evidence
                    and evidence[1] >= datetime.fromisoformat(active["entry_at"])
                ):
                    linked = identify(conn, session_id=active["session_id"], car_id=evidence[0])
                    row.update(session_id=linked.session_id, session_car_id=linked.car_id,
                               entry_at=linked.entry_at)
                    if row["status"] == "occupied":
                        row["car_id"] = linked.car_id
                    row.pop("identity_evidence", None)
                    return
                if row["status"] == "occupied":
                    if active:
                        if active["car_id"] is None and row.get("identity_observation"):
                            row["car_id"] = None
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
            "SELECT vehicle_number,plate_number FROM simulation_targets WHERE lot_id=? AND space_id=?",
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
        if action == "entry" and target and target["plate_number"]:
            conn.execute("INSERT INTO session_vehicle_plates VALUES (?,?)", (session_id, target["plate_number"]))
        conn.commit()


class SimulationCommand(BaseModel):
    space_id: str | None = None
    vehicle_number: str | None = Field(None, pattern=r"^[0-9]{4}$")
    interval_seconds: int = Field(8, ge=3, le=60)
    vehicle_type: Literal["general", "ev", "accessible"] | None = None


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
    continuous_demo = mode == "demo" and os.getenv("PARKON_CONTINUOUS_DEMO") == "1"
    store_ref = {}

    def store_factory(**kwargs):
        # Use the exact demo engine and callback; journal adds recovery, not a second state path.
        kwargs["on_confirmed"] = lambda row, at: store_ref["store"].sync_session(row, at)
        store = EventPipeline(
            database=database, session_enter=session_enter, session_exit=session_exit,
            identity_clock=clock, **kwargs
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
        if mode == "demo" and row["lot_id"] in ("C", "D") and row.get("space_type") == "ev":
            last_used = None
            if row["status"] == "empty":
                with closing(store_ref["store"].connection()) as conn:
                    exits = conn.execute(
                        """SELECT s.entry_at, s.exit_at FROM simulation_movements m
                           JOIN parking_sessions s ON s.session_id=m.session_id
                           WHERE m.lot_id=? AND m.space_id=? AND m.action='exit' AND s.exit_at IS NOT NULL
                           ORDER BY m.occurred_at DESC LIMIT 1""",
                        (row["lot_id"], row["space_id"]),
                    ).fetchone()
                if exits:
                    last_used = last_charge_end(exits["entry_at"], exits["exit_at"])
            row["charging"] = charging_status(row, clock(), last_used)
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

    @route.get("/congestion/forecast")
    def congestion_forecast(lot_id: Literal["C", "D"], at: datetime):
        """가상 시연 이력(demo_hourly_usage) 기반 혼잡 예측. 조회 전용, 상태를 바꾸지 않는다."""
        if at.tzinfo is None:
            raise HTTPException(422, "시간대가 포함된 시각이 필요합니다.")
        with closing(store.connection()) as conn:
            try:
                return demo_history.forecast(conn, lot_id, at, clock())
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from exc

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

    pipeline_client = {}

    async def send_event(event):
        # 시뮬레이터는 1초마다 C·D 전 칸을 보내므로 클라이언트를 매번 만들지 않는다.
        client = pipeline_client.get("client")
        if client is None:
            client = pipeline_client["client"] = httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://pipeline"
            )
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

    clearing_lots = set()
    automatic_goals: dict[str, int] = {}

    def set_target(lot, action, body, *, automatic=False):
        simulation_state(lot)
        if lot in clearing_lots:
            raise HTTPException(409, "전체 출차가 진행 중입니다.")
        with store.gate, closing(store.connection()) as conn:
            rows = store.spaces(lot, as_of=clock())
            targets = {r["space_id"]: dict(r) for r in conn.execute(
                "SELECT * FROM simulation_targets WHERE lot_id=?", (lot,)
            )}
            available = [row for row in rows
                         if bool(targets[row["space_id"]]["occupied"]) == (action == "exit")
                         and (action == "exit" or row["status"] == "empty")
                         and (not body.space_id or row["space_id"] == body.space_id)]
            kind = None
            if action == "entry":
                try:
                    kind = (choose_entry_type(rows, targets, available) if automatic and available
                            else entry_vehicle_type(body.vehicle_number, body.vehicle_type))
                except ValueError as exc:
                    raise HTTPException(409, str(exc)) from exc
                available = [row for row in available if row["space_type"] == kind]
            if not available:
                raise HTTPException(409, "차량 종류에 맞는 빈자리 또는 출차할 차량이 없습니다.")
            row = choice(available) if automatic and action == "exit" else available[0]
            target = targets[row["space_id"]]
            number = target["vehicle_number"]
            plate = target["plate_number"]
            if action == "entry":
                number = body.vehicle_number
                if not number:
                    used = {r[0] for r in conn.execute(
                        "SELECT vehicle_number FROM simulation_targets WHERE occupied=1"
                    )}
                    available_numbers = [
                        f"{value:04d}" for value in range(101, 10000)
                        if f"{value:04d}" not in used and f"{value:04d}" not in REGISTERED_VEHICLES
                    ]
                    if not available_numbers:
                        raise HTTPException(409, "생성할 수 있는 차량번호가 없습니다.")
                    number = choice(available_numbers)
                if conn.execute(
                    "SELECT 1 FROM simulation_targets WHERE vehicle_number=? AND occupied=1",
                    (number,),
                ).fetchone():
                    raise HTTPException(409, "이미 주차 중인 차량번호입니다.")
                usage = choice('가나다라마거너더러머버서어저고노도로모보소오조구누두루무부수우주')
                prefix_min, prefix_max = choice(((1, 69), (100, 699)))
                prefix = f"{randint(prefix_min, prefix_max):02d}"
                plate = f"{prefix}{usage}{number}"
            conn.execute(
                "UPDATE simulation_targets SET occupied=?,vehicle_number=?,plate_number=?,"
                "vehicle_type=? WHERE lot_id=? AND space_id=?",
                (action == "entry", number, plate,
                 kind if action == "entry" else target["vehicle_type"], lot, row["space_id"]),
            )
            conn.commit()
            return row["space_id"], number

    @route.post("/simulation/{lot}/{action}")
    async def command(
        lot: str,
        action: Literal["entry", "exit", "start", "stop", "clear"],
        body: SimulationCommand = SimulationCommand(),
    ):
        simulation_state(lot)
        if lot in clearing_lots:
            raise HTTPException(409, "전체 출차가 진행 중입니다.")
        if action == "clear":
            with store.gate, closing(store.connection()) as conn:
                control = conn.execute("SELECT running FROM simulation_controls WHERE lot_id=?", (lot,)).fetchone()
                if control["running"]:
                    raise HTTPException(409, "자동 실행을 정지한 뒤 전체 출차해주세요.")
                conn.execute("UPDATE simulation_targets SET occupied=0 WHERE lot_id=?", (lot,))
                conn.commit()
                clearing_lots.add(lot)
            try:
                # Empty evidence closes sessions normally; payment/history rows are retained.
                deadline = asyncio.get_running_loop().time() + 15
                while asyncio.get_running_loop().time() < deadline:
                    rows = store.spaces(lot, as_of=clock())
                    with closing(store.connection()) as conn:
                        active = conn.execute(
                            "SELECT 1 FROM parking_sessions WHERE lot_id=? AND status='active' LIMIT 1", (lot,)
                        ).fetchone()
                    if all(row["status"] == "empty" for row in rows) and not active:
                        with closing(store.connection()) as conn:
                            conn.execute("UPDATE simulation_controls SET last_error=NULL WHERE lot_id=?", (lot,))
                            conn.commit()
                        return simulation_state(lot)
                    await asyncio.sleep(0.15)
                raise HTTPException(503, "전체 출차 관측을 기다리고 있습니다. 현황 확인 후 다시 시도해주세요.")
            finally:
                clearing_lots.discard(lot)
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
                    vehicle_type=target["vehicle_type"] if target["occupied"] else None,
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
                        "SELECT * FROM simulation_controls WHERE running=1 AND next_tick<=? ORDER BY next_tick, lot_id LIMIT 1",
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
                    lot = control["lot_id"]
                    goal = automatic_goals.get(lot)
                    if goal is None or count == goal:
                        goal = choice([size for size in range(5, 61) if size != count])
                        automatic_goals[lot] = goal
                    action = "exit" if count > goal else "entry"
                    set_target(lot, action, SimulationCommand(), automatic=True)
                    error = None
                except Exception as exc:
                    error = str(exc)
                interval = control["interval_seconds"]
                next_interval = randint(5, 10) if error is None and interval == 30 and random() < 0.1 else interval
                with closing(store.connection()) as conn:
                    conn.execute(
                        "UPDATE simulation_controls SET next_tick=?,last_error=? WHERE lot_id=?",
                        (
                            max(
                                control["next_tick"] + next_interval,
                                clock().timestamp() + next_interval / 2,
                            ),
                            error,
                            control["lot_id"],
                        ),
                    )
                    conn.commit()
            await asyncio.sleep(1)

    async def history_checkpoints():
        while True:
            await asyncio.sleep(demo_history.HEARTBEAT.total_seconds())
            try:
                with closing(store.connection()) as conn:
                    demo_history.checkpoint(conn, clock())
            except sqlite3.Error:
                logging.exception("Demo history checkpoint failed")

    @asynccontextmanager
    async def lifespan(application):
        database.parent.mkdir(parents=True, exist_ok=True)
        with closing(store.connection()) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            init_schema(conn)
            # Legacy databases acquire and backfill status in init_schema.
            conn.execute(
                "CREATE INDEX IF NOT EXISTS ix_parking_sessions_active_space "
                "ON parking_sessions(lot_id, space_id) WHERE status='active'"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS ix_parking_sessions_active_car "
                "ON parking_sessions(car_id) WHERE status='active'"
            )
            seed_defaults(conn)
            conn.execute(
                "UPDATE parking_lots SET name = ?, total_spaces = ? "
                "WHERE lot_id = 'C' AND type = 'virtual' AND name = ? AND total_spaces = 12",
                ("김해공항 주차장", 64, "북항주차장"),
            )
            init_alerts_schema(conn)
            conn.executescript(SCHEMA)
            if "applied" not in {
                r["name"] for r in conn.execute("PRAGMA table_info(pipeline_events)")
            }:
                conn.execute(
                    "ALTER TABLE pipeline_events ADD COLUMN applied INTEGER NOT NULL DEFAULT 1"
                )
            conn.execute("CREATE INDEX IF NOT EXISTS ix_pipeline_pending ON pipeline_events(sequence) WHERE applied=0")
            conn.execute("CREATE INDEX IF NOT EXISTS ix_simulation_bay_movements ON simulation_movements(lot_id,space_id,action,occurred_at DESC)")
            if "vehicle_type" not in {
                r["name"] for r in conn.execute("PRAGMA table_info(simulation_targets)")
            }:
                # Old vehicles remain general until explicitly exited, never relabel to fit a bay.
                conn.execute("ALTER TABLE simulation_targets ADD COLUMN vehicle_type TEXT NOT NULL DEFAULT 'general'")
            for lot in ("C", "D"):
                conn.execute("INSERT OR IGNORE INTO simulation_controls(lot_id) VALUES(?)", (lot,))
                for space in [s for s in SPACES if s.lot_id == lot]:
                    conn.execute(
                        "INSERT OR IGNORE INTO simulation_targets(lot_id,space_id) VALUES(?,?)",
                        (lot, space.space_id),
                    )
            # 예전 버전이 쌓아 둔 시뮬레이션 반복 관측 정리: 칸마다 마지막 관측 10초 구간만 남긴다
            # (복구 때 확정 상태를 다시 만들 수 있을 만큼). 카메라·수동 관측은 건드리지 않는다.
            conn.execute(
                """DELETE FROM pipeline_events
                   WHERE applied=1 AND json_extract(payload,'$.source')='simulation'
                     AND julianday(json_extract(payload,'$.observed_at')) < (
                       SELECT julianday(max(json_extract(p.payload,'$.observed_at'))) - 10.0/86400
                       FROM pipeline_events p
                       WHERE p.applied=1 AND json_extract(p.payload,'$.source')='simulation'
                         AND json_extract(p.payload,'$.lot_id')=json_extract(pipeline_events.payload,'$.lot_id')
                         AND json_extract(p.payload,'$.space_id')=json_extract(pipeline_events.payload,'$.space_id'))"""
            )
            conn.commit()
        store.recover()
        if continuous_demo:
            with closing(store.connection()) as conn:
                demo_history.prepare(conn, clock())
                demo_history.checkpoint(conn, clock())
        task = asyncio.create_task(observations()) if mode == "demo" else None
        history_task = asyncio.create_task(history_checkpoints()) if continuous_demo else None
        try:
            yield
        finally:
            for background in (task, history_task):
                if background is None:
                    continue
                background.cancel()
                try:
                    await background
                except asyncio.CancelledError:
                    pass
            client = pipeline_client.pop("client", None)
            if client is not None:
                await client.aclose()
            if continuous_demo:
                with closing(store.connection()) as conn:
                    demo_history.checkpoint(conn, clock())

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
