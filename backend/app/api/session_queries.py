"""Read-only session list for the isolated demo app."""
import sqlite3
from datetime import datetime, timezone
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from backend.app.billing.db import demo_history_cutoff, get_db_conn

SessionStatus = Literal["active", "closed"]
router = APIRouter(prefix="/api/v1", tags=["주차 세션"])


class SessionItem(BaseModel):
    session_id: str
    lot_id: str
    space_id: str | None
    car_id: str | None
    plate_number: str | None = None
    entry_at: str
    exit_at: str | None
    status: SessionStatus


class SessionList(BaseModel):
    items: list[SessionItem]
    next_cursor: str | None = None
    as_of: str


@router.get("/sessions", response_model=SessionList, response_model_exclude_unset=True)
def list_sessions(
    conn: Annotated[sqlite3.Connection, Depends(get_db_conn)],
    lot_id: Literal["A", "B", "C", "D"] | None = None,
    car_id: Annotated[str | None, Query(pattern=r"^[1-9][0-9]$")] = None,
    status: SessionStatus | None = None,
    limit: Annotated[int | None, Query(ge=1, le=1000)] = None,
):
    rows = conn.execute(
        """
        WITH sessions AS (
            SELECT session_id, lot_id, space_id, car_id, entry_at, exit_at,
                   COALESCE(status, CASE WHEN exit_at IS NULL THEN 'active' ELSE 'closed' END) AS status
            FROM parking_sessions
        )
        SELECT sessions.*, plate.plate_number
        FROM sessions
        LEFT JOIN session_vehicle_plates AS plate ON plate.session_id = sessions.session_id
        WHERE (:lot_id IS NULL OR lot_id = :lot_id)
          AND (:car_id IS NULL OR car_id = :car_id)
          AND (:status IS NULL OR status = :status)
          AND (:cutoff IS NULL OR sessions.session_id NOT GLOB 'demo-*'
               OR julianday(COALESCE(exit_at, entry_at)) <= julianday(:cutoff))
        ORDER BY julianday(entry_at) DESC, sessions.session_id ASC
        LIMIT :limit
        """,
        {"lot_id": lot_id, "car_id": car_id, "status": status, "cutoff": demo_history_cutoff(conn), "limit": -1 if limit is None else limit},
    )
    items = []
    for row in rows:
        data = dict(row)
        if data["plate_number"] is None:
            del data["plate_number"]
        items.append(SessionItem(**data))
    return SessionList(
        items=items,
        next_cursor=None,
        as_of=datetime.now(timezone.utc).isoformat(),
    )
