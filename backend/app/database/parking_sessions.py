"""Persist confirmed entry and exit commands on the shared billing table."""

import re
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime


class SessionError(Exception):
    """Service error for a future API adapter."""

    def __init__(self, status_code: int, code: str, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.body = {"error": {"code": code, "message": message, "details": []}}


@dataclass(frozen=True)
class ParkingSession:
    session_id: str
    lot_id: str
    space_id: str | None
    car_id: str | None
    entry_at: str
    exit_at: str | None
    status: str


@contextmanager
def _write(conn: sqlite3.Connection):
    # Reserve the writer before checking for an active session.
    try:
        conn.execute("BEGIN IMMEDIATE")
    except sqlite3.OperationalError as error:
        if getattr(error, "sqlite_errorcode", None) in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
            raise SessionError(503, "SESSION_WRITE_BUSY", "잠시 후 세션 요청을 다시 시도하세요.") from error
        raise
    try:
        yield
        conn.commit()
    except BaseException:
        conn.rollback()
        raise


def _timestamp(value: datetime) -> str:
    if value.utcoffset() is None:
        raise SessionError(422, "INVALID_SESSION_TIME", "시간대를 포함해야 합니다.")
    return value.isoformat()


def _find(conn: sqlite3.Connection, session_id: str) -> ParkingSession | None:
    row = conn.execute(
        "SELECT session_id, lot_id, space_id, car_id, entry_at, exit_at, status "
        "FROM parking_sessions WHERE session_id = ?", (session_id,),
    ).fetchone()
    return ParkingSession(*row) if row is not None else None


def enter(
    conn: sqlite3.Connection, *, session_id: str, lot_id: str,
    entry_at: datetime, space_id: str | None = None, car_id: str | None = None,
) -> ParkingSession:
    """Use a dedicated connection and a stable caller-assigned session ID."""
    if not isinstance(session_id, str) or not session_id.strip():
        raise SessionError(422, "INVALID_SESSION_ID", "세션 ID가 필요합니다.")
    if lot_id not in ("A", "B", "C", "D"):
        raise SessionError(422, "INVALID_LOT_ID", "지원하는 주차장 ID가 필요합니다.")
    if car_id is not None and (
        not isinstance(car_id, str) or re.fullmatch(r"[1-9][0-9]", car_id) is None
    ):
        raise SessionError(422, "INVALID_CAR_ID", "차량 ID는 10~99 문자열 또는 null이어야 합니다.")

    timestamp = _timestamp(entry_at)
    with _write(conn):
        existing = _find(conn, session_id)
        if existing is not None:
            if (
                (existing.lot_id, existing.space_id, existing.car_id) == (lot_id, space_id, car_id)
                and datetime.fromisoformat(existing.entry_at) == entry_at
            ):
                return existing
            raise SessionError(409, "SESSION_ID_CONFLICT", "이미 다른 입차에 사용된 세션 ID입니다.")
        if car_id is not None and conn.execute(
            "SELECT 1 FROM parking_sessions WHERE car_id = ? AND status = 'active'", (car_id,),
        ).fetchone():
            raise SessionError(409, "ACTIVE_SESSION_EXISTS", "차량에 활성 세션이 이미 있습니다.")
        conn.execute(
            "INSERT INTO parking_sessions (session_id, lot_id, space_id, car_id, entry_at, status) "
            "VALUES (?, ?, ?, ?, ?, 'active')",
            (session_id, lot_id, space_id, car_id, timestamp),
        )
        return ParkingSession(session_id, lot_id, space_id, car_id, timestamp, None, "active")


def exit_session(conn: sqlite3.Connection, *, session_id: str, exit_at: datetime) -> ParkingSession:
    """Close a confirmed session by ID, including unidentified vehicles."""
    timestamp = _timestamp(exit_at)
    with _write(conn):
        existing = _find(conn, session_id)
        if existing is None:
            raise SessionError(404, "SESSION_NOT_FOUND", "주차 세션을 찾을 수 없습니다.")
        if existing.status == "closed":
            if existing.exit_at and datetime.fromisoformat(existing.exit_at) == exit_at:
                return existing
            raise SessionError(409, "SESSION_ALREADY_CLOSED", "이미 종료된 세션입니다.")
        if existing.status != "active":
            raise SessionError(503, "SESSION_STATE_UNAVAILABLE", "세션 상태를 확인할 수 없습니다.")
        if exit_at < datetime.fromisoformat(existing.entry_at):
            raise SessionError(422, "INVALID_SESSION_TIME", "출차 시각이 입차보다 빠릅니다.")
        conn.execute(
            "UPDATE parking_sessions SET exit_at = ?, status = 'closed' WHERE session_id = ?",
            (timestamp, session_id),
        )
        return ParkingSession(
            session_id, existing.lot_id, existing.space_id, existing.car_id,
            existing.entry_at, timestamp, "closed",
        )
