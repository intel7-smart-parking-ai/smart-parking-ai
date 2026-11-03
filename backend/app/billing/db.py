"""billing 임시 DB 연결·스키마.

database 모듈(박해준)이 확정되면 get_db_conn/SCHEMA를 그쪽 것으로 교체한다.
DB 파일 위치는 환경변수 SMART_PARKING_DB_PATH로 바꿀 수 있고,
기본값은 저장소 루트의 data/smart_parking.db (*.db는 .gitignore 대상)이다.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Iterator

DEFAULT_DB_PATH = Path(__file__).resolve().parents[3] / "data" / "smart_parking.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS parking_lots (
    lot_id        TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    type          TEXT NOT NULL,              -- real / virtual
    total_spaces  INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS rate_policies (
    lot_id        TEXT PRIMARY KEY REFERENCES parking_lots(lot_id),
    base_minutes  INTEGER NOT NULL,
    base_fee      INTEGER NOT NULL,
    unit_minutes  INTEGER NOT NULL,
    extra_fee     INTEGER NOT NULL,
    daily_cap     INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS parking_sessions (
    session_id    TEXT PRIMARY KEY,
    lot_id        TEXT NOT NULL,
    space_id      TEXT,
    car_id        TEXT,                       -- 미인식이면 NULL
    entry_at      TEXT NOT NULL,
    exit_at       TEXT
);
CREATE TABLE IF NOT EXISTS mock_payments (
    payment_id      TEXT PRIMARY KEY,
    session_id      TEXT NOT NULL,
    lot_id          TEXT NOT NULL,
    car_id          TEXT,
    amount_krw      INTEGER NOT NULL,
    payment_method  TEXT NOT NULL,
    status          TEXT NOT NULL,            -- completed / canceled
    idempotency_key TEXT NOT NULL UNIQUE,
    created_at      TEXT NOT NULL,
    canceled_at     TEXT,
    refund_reason   TEXT
);
-- 한 세션에 완료 결제는 1건만 (동시 요청 이중 결제 방지)
CREATE UNIQUE INDEX IF NOT EXISTS ux_mock_payments_session_completed
    ON mock_payments(session_id) WHERE status = 'completed';
CREATE TABLE IF NOT EXISTS audit_logs (
    log_id      TEXT PRIMARY KEY,
    actor       TEXT NOT NULL,
    action      TEXT NOT NULL,
    target      TEXT NOT NULL,
    created_at  TEXT NOT NULL
);
"""


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)


def get_db_path() -> Path:
    return Path(os.getenv("SMART_PARKING_DB_PATH", str(DEFAULT_DB_PATH)))


def get_db_conn() -> Iterator[sqlite3.Connection]:
    """FastAPI 의존성. 요청마다 연결을 열고 닫는다."""
    path = get_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    try:
        yield conn
    finally:
        conn.close()
