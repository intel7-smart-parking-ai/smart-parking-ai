"""경보(Alert) API 최소 구현 — LED 모의 화재 이벤트 → 경보 목록·확인·해제.

담당: 3팀 윤명수 · 서정범 (DECISIONS D-29)

- 실제 화재가 아니라 '모의 화재(LED 점등)'로만 표기한다 (CLAUDE.md 규칙 6).
- 경보 생성은 공개 API가 아니다.
  - vision-event 수신기(토큰 필요)가 `mock_fire_led_observed`를 받으면 `ingest_led_fire_event()`를 호출하거나,
  - 내부 API `POST /api/v1/internal/alerts`(Bearer `ALERTS_INTERNAL_TOKEN`)로만 만든다.
- DB는 billing과 같은 SQLite 파일을 요청마다 새 연결로 사용한다 (`billing.db.get_db_conn`).
  시연 서버(`demo_main`)가 `get_db_conn`을 바꾸면 경보도 같은 파일을 쓴다.

공개 API
- GET   /api/v1/alerts                      목록 (팀 목록 형식 + 상태별 건수)
- GET   /api/v1/alerts/{alert_id}           상세
- POST·PATCH /api/v1/alerts/{alert_id}/acknowledge   확인 (active → acknowledged)
- POST·PATCH /api/v1/alerts/{alert_id}/resolve       해제 (active/acknowledged → resolved)
- PATCH /api/v1/alerts/{alert_id}           상태 직접 변경 (status=acknowledged|resolved)
"""

from __future__ import annotations

import os
import sqlite3
import uuid
from datetime import datetime, timezone
from secrets import compare_digest
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, Header, Query, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator

from backend.app.billing.db import get_db_conn

AlertStatus = Literal["active", "acknowledged", "resolved"]
LotId = Literal["A", "B", "C", "D"]
Source = Literal["vision", "simulation", "manual"]

ALERT_TYPE_MOCK_FIRE = "mock_fire_led"
VISION_FIRE_EVENT_TYPE = "mock_fire_led_observed"
# 챗봇 판단 레벨: 1 = 주의, 2 = 대피 안내·결제 중단. LED 모의 화재는 2 (서정범과 확인할 값)
MOCK_FIRE_LEVEL = int(os.getenv("ALERT_MOCK_FIRE_LEVEL", "2"))

# 주차장 이름 (DECISIONS D-15·D-16)
LOT_NAMES: dict[str, str] = {
    "A": "개발원주차장",
    "B": "부산역주차장",
    "C": "김해공항 주차장",
    "D": "신세계백화점주차장",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def format_mock_fire_message(lot_id: str, space_id: Optional[str]) -> str:
    """표시 예: "[모의 화재] 개발원주차장 A-03 빨간 LED 점등" (D-04: 주차장 이름 + 칸 번호)."""
    place = LOT_NAMES.get(lot_id, f"{lot_id} 주차장") + (f" {space_id}" if space_id else "")
    return f"[모의 화재] {place} 빨간 LED 점등 (실제 화재 아님)"


# ---------------------------------------------------------------------------
# DB
# ---------------------------------------------------------------------------
ALERTS_DDL = """
CREATE TABLE IF NOT EXISTS alerts (
    alert_id        TEXT PRIMARY KEY,
    lot_id          TEXT NOT NULL,
    camera_id       TEXT,
    space_id        TEXT,
    type            TEXT NOT NULL DEFAULT 'mock_fire_led',
    level           INTEGER NOT NULL DEFAULT 2 CHECK (level IN (1, 2)),
    status          TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'acknowledged', 'resolved')),
    source          TEXT NOT NULL CHECK (source IN ('vision', 'simulation', 'manual')),
    message         TEXT NOT NULL,
    confidence      REAL,
    frame_id        TEXT,
    detected_at     TEXT NOT NULL,
    acknowledged_at TEXT,
    resolved_at     TEXT,
    actor           TEXT
);
CREATE INDEX IF NOT EXISTS ix_alerts_lot_status ON alerts (lot_id, status);
-- 경보를 만든·갱신한 이벤트 ID를 모두 기록한다 (같은 event_id 재전송 → 같은 경보)
CREATE TABLE IF NOT EXISTS alert_events (
    event_id   TEXT PRIMARY KEY,
    alert_id   TEXT NOT NULL REFERENCES alerts(alert_id),
    received_at TEXT NOT NULL
);
"""


def init_alerts_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(ALERTS_DDL)


def get_alert_db_conn(conn: sqlite3.Connection = Depends(get_db_conn)) -> sqlite3.Connection:
    """billing과 같은 SQLite 파일, 요청마다 새 연결. 시연 서버의 get_db_conn 교체도 그대로 따른다."""
    conn.row_factory = sqlite3.Row
    init_alerts_schema(conn)
    return conn


# ---------------------------------------------------------------------------
# 입출력 모델
# ---------------------------------------------------------------------------
class LedFireEvent(BaseModel):
    """vision-event(`mock_fire_led_observed`)와 같은 필드."""

    event_id: str = Field(..., min_length=1)
    event_type: Literal["mock_fire_led_observed"] = VISION_FIRE_EVENT_TYPE
    source: Source = "vision"
    lot_id: LotId
    camera_id: Optional[str] = None
    space_id: Optional[str] = None
    car_id: Optional[str] = Field(default=None, pattern=r"^[1-9][0-9]$")  # D-06: "10"~"99" 또는 null
    confidence: float = Field(default=0.95, ge=0.0, le=1.0)
    bbox_xyxy: Optional[tuple[float, float, float, float]] = None
    observed_at: Optional[datetime] = None  # 없으면 서버 현재 시각
    frame_id: Optional[str] = None

    @field_validator("event_id")
    @classmethod
    def _event_id_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("event_id는 빈 문자열일 수 없습니다.")
        return value.strip()

    @field_validator("observed_at")
    @classmethod
    def _timezone_required(cls, value: Optional[datetime]) -> Optional[datetime]:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("observed_at에는 시간대가 필요합니다 (예: 2026-11-04T14:00:00+09:00).")
        return value


class AlertActionRequest(BaseModel):
    actor: str = "admin"
    note: Optional[str] = None


class AlertStatusPatchRequest(BaseModel):
    status: Literal["acknowledged", "resolved"]
    actor: str = "admin"


class AlertItem(BaseModel):
    alert_id: str
    lot_id: str
    camera_id: Optional[str] = None
    space_id: Optional[str] = None
    type: str
    level: int
    status: AlertStatus
    source: Source
    is_mock: bool  # source가 simulation·manual이면 true (D-11 "모의 이벤트" 표시)
    message: str
    confidence: Optional[float] = None
    frame_id: Optional[str] = None
    detected_at: str
    acknowledged_at: Optional[str] = None
    resolved_at: Optional[str] = None
    actor: Optional[str] = None


class AlertListResponse(BaseModel):
    """팀 공통 목록 형식 {items, next_cursor, as_of} + 상태별 건수."""

    items: list[AlertItem]
    next_cursor: Optional[str] = None
    as_of: str
    total: int
    active_count: int
    acknowledged_count: int
    resolved_count: int


def _item(row: sqlite3.Row) -> AlertItem:
    data = dict(row)
    return AlertItem(**data, is_mock=data["source"] in ("simulation", "manual"))


def _get(conn: sqlite3.Connection, alert_id: str) -> Optional[sqlite3.Row]:
    return conn.execute("SELECT * FROM alerts WHERE alert_id = ?", (alert_id,)).fetchone()


# ---------------------------------------------------------------------------
# 서비스 함수 (수신기·내부 API·테스트에서 직접 호출)
# ---------------------------------------------------------------------------
def ingest_led_fire_event(conn: sqlite3.Connection, event: LedFireEvent) -> tuple[AlertItem, bool]:
    """LED 모의 화재 이벤트 → 경보 생성 또는 같은 칸의 미해제 경보 갱신.

    반환: (경보, 새로 만들었는지)
    - 같은 event_id 재전송 → 처음 처리한 경보 그대로 (False)
    - 같은 (lot_id, space_id)에 active/acknowledged 경보가 있으면 → 신뢰도·프레임만 갱신 (False)
    - 그 외 → 새 경보 (True)
    """
    if event.lot_id in {"C", "D"} and event.camera_id is not None:
        raise ValueError("가상 주차장(C, D)의 camera_id는 null이어야 합니다.")

    received = now_iso()
    seen = conn.execute("SELECT alert_id FROM alert_events WHERE event_id = ?", (event.event_id,)).fetchone()
    if seen is not None:
        return _item(_get(conn, seen["alert_id"])), False

    observed = (event.observed_at or datetime.now(timezone.utc)).isoformat()
    open_row = conn.execute(
        """
        SELECT * FROM alerts
        WHERE lot_id = ? AND space_id IS ? AND type = ? AND status IN ('active', 'acknowledged')
        ORDER BY detected_at DESC LIMIT 1
        """,
        (event.lot_id, event.space_id, ALERT_TYPE_MOCK_FIRE),
    ).fetchone()

    if open_row is not None:
        conn.execute(
            "UPDATE alerts SET confidence = MAX(COALESCE(confidence, 0), ?), frame_id = COALESCE(?, frame_id)"
            " WHERE alert_id = ?",
            (event.confidence, event.frame_id, open_row["alert_id"]),
        )
        conn.execute("INSERT INTO alert_events VALUES (?, ?, ?)", (event.event_id, open_row["alert_id"], received))
        conn.commit()
        return _item(_get(conn, open_row["alert_id"])), False

    alert_id = f"alt_{uuid.uuid4().hex[:12]}"
    conn.execute(
        """
        INSERT INTO alerts (alert_id, lot_id, camera_id, space_id, type, level, status, source,
                            message, confidence, frame_id, detected_at)
        VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, ?, ?)
        """,
        (alert_id, event.lot_id, event.camera_id, event.space_id, ALERT_TYPE_MOCK_FIRE, MOCK_FIRE_LEVEL,
         event.source, format_mock_fire_message(event.lot_id, event.space_id), event.confidence,
         event.frame_id, observed),
    )
    conn.execute("INSERT INTO alert_events VALUES (?, ?, ?)", (event.event_id, alert_id, received))
    conn.commit()
    return _item(_get(conn, alert_id)), True


def list_alerts(conn: sqlite3.Connection, *, lot_id: Optional[str] = None, status_filter: Optional[str] = None,
                alert_type: Optional[str] = None, limit: int = 100) -> AlertListResponse:
    clauses, params = [], []
    for column, value in (("lot_id", lot_id), ("status", status_filter), ("type", alert_type)):
        if value:
            clauses.append(f"{column} = ?")
            params.append(value)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = conn.execute(
        f"""SELECT * FROM alerts {where}
            ORDER BY CASE status WHEN 'active' THEN 0 WHEN 'acknowledged' THEN 1 ELSE 2 END, detected_at DESC
            LIMIT ?""",
        (*params, limit),
    ).fetchall()
    count_where, count_params = ("WHERE lot_id = ?", [lot_id]) if lot_id else ("", [])
    counts = {r["status"]: r["cnt"] for r in conn.execute(
        f"SELECT status, COUNT(*) AS cnt FROM alerts {count_where} GROUP BY status", count_params)}
    items = [_item(r) for r in rows]
    return AlertListResponse(
        items=items, as_of=now_iso(), total=len(items),
        active_count=counts.get("active", 0), acknowledged_count=counts.get("acknowledged", 0),
        resolved_count=counts.get("resolved", 0),
    )


def acknowledge_alert(conn: sqlite3.Connection, alert_id: str, *, actor: str = "admin") -> AlertItem:
    """active → acknowledged. 이미 acknowledged면 그대로, resolved면 ValueError."""
    row = _get(conn, alert_id)
    if row is None:
        raise KeyError(alert_id)
    if row["status"] == "resolved":
        raise ValueError("이미 해제(resolved)된 경보는 확인 상태로 바꿀 수 없습니다.")
    if row["status"] == "active":
        conn.execute("UPDATE alerts SET status = 'acknowledged', acknowledged_at = ?, actor = ? WHERE alert_id = ?",
                     (now_iso(), actor, alert_id))
        conn.commit()
    return _item(_get(conn, alert_id))


def resolve_alert(conn: sqlite3.Connection, alert_id: str, *, actor: str = "admin") -> AlertItem:
    """active/acknowledged → resolved. 이미 resolved면 그대로."""
    row = _get(conn, alert_id)
    if row is None:
        raise KeyError(alert_id)
    if row["status"] != "resolved":
        at = now_iso()
        conn.execute(
            "UPDATE alerts SET status = 'resolved', acknowledged_at = COALESCE(acknowledged_at, ?),"
            " resolved_at = ?, actor = ? WHERE alert_id = ?",
            (at, at, actor, alert_id),
        )
        conn.commit()
    return _item(_get(conn, alert_id))


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------
def _error(status_code: int, code: str, message: str) -> JSONResponse:
    """팀 공통 에러 형식 {"error": {"code", "message", "details"}}."""
    return JSONResponse(status_code=status_code, content={"error": {"code": code, "message": message, "details": []}})


def _not_found(alert_id: str) -> JSONResponse:
    return _error(404, "ALERT_NOT_FOUND", f"경보({alert_id})를 찾을 수 없습니다.")


router = APIRouter(prefix="/api/v1", tags=["경보 (3팀 윤명수·서정범)"])


@router.get("/alerts", response_model=AlertListResponse, summary="경보 목록")
def get_alerts(
    lot_id: Optional[LotId] = Query(default=None),
    status_: Optional[AlertStatus] = Query(default=None, alias="status"),
    alert_type: Optional[str] = Query(default=None, alias="type"),
    limit: int = Query(default=100, ge=1, le=500),
    conn: sqlite3.Connection = Depends(get_alert_db_conn),
):
    return list_alerts(conn, lot_id=lot_id, status_filter=status_, alert_type=alert_type, limit=limit)


@router.get("/alerts/{alert_id}", response_model=AlertItem, summary="경보 상세")
def get_alert(alert_id: str, conn: sqlite3.Connection = Depends(get_alert_db_conn)):
    row = _get(conn, alert_id)
    return _item(row) if row is not None else _not_found(alert_id)


def _transition(fn, conn, alert_id, actor):
    try:
        return fn(conn, alert_id, actor=actor)
    except KeyError:
        return _not_found(alert_id)
    except ValueError as exc:
        return _error(409, "INVALID_ALERT_TRANSITION", str(exc))


@router.post("/alerts/{alert_id}/acknowledge", response_model=AlertItem, summary="경보 확인")
@router.patch("/alerts/{alert_id}/acknowledge", response_model=AlertItem, summary="경보 확인 (PATCH)")
def acknowledge(alert_id: str, payload: Optional[AlertActionRequest] = None,
                conn: sqlite3.Connection = Depends(get_alert_db_conn)):
    return _transition(acknowledge_alert, conn, alert_id, payload.actor if payload else "admin")


@router.post("/alerts/{alert_id}/resolve", response_model=AlertItem, summary="경보 해제")
@router.patch("/alerts/{alert_id}/resolve", response_model=AlertItem, summary="경보 해제 (PATCH)")
def resolve(alert_id: str, payload: Optional[AlertActionRequest] = None,
            conn: sqlite3.Connection = Depends(get_alert_db_conn)):
    return _transition(resolve_alert, conn, alert_id, payload.actor if payload else "admin")


@router.patch("/alerts/{alert_id}", response_model=AlertItem, summary="경보 상태 변경")
def patch_status(alert_id: str, payload: AlertStatusPatchRequest,
                 conn: sqlite3.Connection = Depends(get_alert_db_conn)):
    fn = acknowledge_alert if payload.status == "acknowledged" else resolve_alert
    return _transition(fn, conn, alert_id, payload.actor)


# 내부 생성 API — 토큰이 설정된 경우에만 동작 (공개 생성 금지)
internal_router = APIRouter(prefix="/api/v1/internal", tags=["경보 내부 생성"])


def _require_internal_token(authorization: str = Header(default="")) -> None:
    token = os.getenv("ALERTS_INTERNAL_TOKEN", "")
    if len(token) < 16 or not compare_digest(authorization.encode(), f"Bearer {token}".encode()):
        raise PermissionError


@internal_router.post("/alerts", response_model=AlertItem, status_code=status.HTTP_201_CREATED,
                      summary="LED 모의 화재 이벤트로 경보 생성 (내부·토큰 필요)")
def create_alert(event: LedFireEvent, authorization: str = Header(default=""),
                 conn: sqlite3.Connection = Depends(get_alert_db_conn)):
    try:
        _require_internal_token(authorization)
    except PermissionError:
        return _error(403, "FORBIDDEN", "내부 경보 생성 토큰이 필요합니다.")
    try:
        item, created = ingest_led_fire_event(conn, event)
    except ValueError as exc:
        return _error(422, "INVALID_ALERT_EVENT", str(exc))
    return JSONResponse(status_code=201 if created else 200, content=item.model_dump())


__all__ = ["router", "internal_router", "ingest_led_fire_event", "LedFireEvent", "get_alert_db_conn"]
