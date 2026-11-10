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


def identify(conn: sqlite3.Connection, *, session_id: str, car_id: str) -> ParkingSession:
    """Bind a confirmed identity to an active unidentified session exactly once."""
    if not isinstance(car_id, str) or re.fullmatch(r"[1-9][0-9]", car_id) is None:
        raise SessionError(422, "INVALID_CAR_ID", "차량 ID는 10~99 문자열이어야 합니다.")
    with _write(conn):
        existing = _find(conn, session_id)
        if existing is None or existing.status != 'active':
            raise SessionError(409, "SESSION_NOT_ACTIVE", "활성 세션만 차량 번호를 연결할 수 있습니다.")
        if existing.car_id == car_id:
            return existing
        if existing.car_id is not None:
            raise SessionError(409, "SESSION_IDENTITY_CONFLICT", "이미 확인된 차량 번호는 자동 변경하지 않습니다.")
        if conn.execute("SELECT 1 FROM parking_sessions WHERE car_id = ? AND status = 'active' AND session_id <> ?",
                        (car_id, session_id)).fetchone():
            raise SessionError(409, "ACTIVE_SESSION_EXISTS", "차량에 활성 세션이 이미 있습니다.")
        conn.execute("UPDATE parking_sessions SET car_id = ? WHERE session_id = ? AND car_id IS NULL AND status = 'active'",
                     (car_id, session_id))
        return _find(conn, session_id)


def init_session_moves(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS session_moves (
            move_id INTEGER PRIMARY KEY, session_id TEXT NOT NULL REFERENCES parking_sessions(session_id),
            lot_id TEXT NOT NULL, from_space_id TEXT NOT NULL, to_space_id TEXT NOT NULL,
            observed_at TEXT NOT NULL
        )
    """)


def move_sessions(conn, *, lot_id, moves, observed_at, save_snapshots):
    """Move active sessions as one transaction with their recovery checkpoints."""
    stamp = _timestamp(observed_at)
    origins = {source for _, source, _ in moves}
    targets = {target for _, _, target in moves}
    with _write(conn):
        if len(origins) != len(moves) or len(targets) != len(moves):
            raise SessionError(409, "SESSION_MOVE_CONFLICT", "주차칸 이동 근거가 중복됩니다.")
        for session_id, source, target in moves:
            session = _find(conn, session_id)
            if (session is None or session.status != "active" or session.car_id is None
                    or (session.lot_id, session.space_id) != (lot_id, source)
                    or datetime.fromisoformat(session.entry_at) > observed_at):
                raise SessionError(409, "SESSION_MOVE_CONFLICT", "활성 세션의 출발 칸이 변경됐습니다.")
            occupants = conn.execute(
                "SELECT session_id FROM parking_sessions WHERE lot_id=? AND space_id=? AND status='active'",
                (lot_id, target),
            ).fetchall()
            expected = {sid for sid, origin, _ in moves if origin == target}
            if {row[0] for row in occupants} != expected:
                raise SessionError(409, "SESSION_MOVE_CONFLICT", "이동 대상 칸의 활성 세션이 변경됐습니다.")
        conn.executemany("UPDATE parking_sessions SET space_id=NULL WHERE session_id=?",
                         [(session_id,) for session_id, _, _ in moves])
        for session_id, source, target in moves:
            conn.execute("UPDATE parking_sessions SET space_id=? WHERE session_id=?", (target, session_id))
            conn.execute("""
                INSERT INTO session_moves (session_id, lot_id, from_space_id, to_space_id, observed_at)
                VALUES (?, ?, ?, ?, ?)
            """, (session_id, lot_id, source, target, stamp))
        save_snapshots()


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
