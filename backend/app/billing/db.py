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
-- 요금 견적: 요금 화면에 보인 금액 그대로 결제하기 위한 기록 (DECISIONS D-09)
CREATE TABLE IF NOT EXISTS fee_quotes (
    quote_id      TEXT PRIMARY KEY,
    session_id    TEXT NOT NULL,
    amount_krw    INTEGER NOT NULL,
    calculated_at TEXT NOT NULL,              -- 요금 산출 기준 시각
    created_at    TEXT NOT NULL,
    expires_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_fee_quotes_session ON fee_quotes(session_id);
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


# 주차장 4곳 기본 데이터 (2026-11-03, 요금표는 윤명수 작성·PM 확인)
# (lot_id, 이름, 종류, 칸 수) — 이름·칸 수는 docs/DECISIONS.md D-14·D-15. D 이름은 김채린 지정 전 임시
DEFAULT_LOTS = [
    ("A", "개발원주차장", "real", 12),
    ("B", "부산역주차장", "real", 16),
    ("C", "북항주차장", "virtual", 12),
    ("D", "라 주차장", "virtual", 64),
]
# (lot_id, 기본 시간(분), 기본 요금, 추가 단위(분), 추가 요금, 일 최대)
DEFAULT_RATE_POLICIES = [
    ("A", 30, 1000, 10, 500, 10000),
    ("B", 30, 2000, 10, 700, 20000),
    ("C", 30, 3000, 10, 800, 40000),
    ("D", 30, 4000, 10, 1000, 50000),
]


def seed_defaults(conn: sqlite3.Connection) -> None:
    """빈 DB일 때만 주차장·요금표 기본 데이터를 넣는다 (이미 데이터가 있으면 건드리지 않음).

    DB 통합(박해준) 후에는 공용 시드로 옮긴다.
    """
    if conn.execute("SELECT 1 FROM parking_lots LIMIT 1").fetchone():
        return
    # 첫 요청이 동시에 들어와도 충돌하지 않도록 OR IGNORE
    conn.executemany(
        "INSERT OR IGNORE INTO parking_lots (lot_id, name, type, total_spaces) VALUES (?, ?, ?, ?)", DEFAULT_LOTS
    )
    conn.executemany(
        "INSERT OR IGNORE INTO rate_policies (lot_id, base_minutes, base_fee, unit_minutes, extra_fee, daily_cap)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        DEFAULT_RATE_POLICIES,
    )
    conn.commit()


def get_db_path() -> Path:
    return Path(os.getenv("SMART_PARKING_DB_PATH", str(DEFAULT_DB_PATH)))


def get_db_conn() -> Iterator[sqlite3.Connection]:
    """FastAPI 의존성. 요청마다 연결을 열고 닫는다."""
    path = get_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    # FastAPI는 sync 의존성과 sync 엔드포인트를 서로 다른 스레드에서 실행할 수 있다.
    # 연결은 요청 하나에서만 순차적으로 쓰이므로 스레드 검사를 끄고, 동시 쓰기 대기 시간을 둔다.
    conn = sqlite3.connect(path, check_same_thread=False, timeout=10)
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    seed_defaults(conn)
    try:
        yield conn
    finally:
        conn.close()
