from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timedelta, timezone
from threading import Barrier
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.api.billing_router import billing_router
from backend.app.billing import db
from backend.app.database.parking_sessions import SessionError, enter, exit_session

ENTRY = datetime(2026, 11, 4, 9, tzinfo=timezone(timedelta(hours=9)))
EXIT = ENTRY + timedelta(minutes=45)


@pytest.fixture
def connect(tmp_path, monkeypatch):
    import sqlite3

    path = tmp_path / "shared-session.sqlite3"
    monkeypatch.setenv("SMART_PARKING_DB_PATH", str(path))

    def open_connection():
        return closing(sqlite3.connect(path, timeout=10))

    with open_connection() as conn:
        db.init_schema(conn)
        db.seed_defaults(conn)
        # This schema change is applied only to this freshly created fixture.
        columns = {row[1] for row in conn.execute("PRAGMA table_info(parking_sessions)")}
        if "status" not in columns:
            conn.execute(
                "ALTER TABLE parking_sessions ADD COLUMN status TEXT "
                "CHECK (status IN ('active', 'closed'))"
            )
    return open_connection


@pytest.mark.parametrize("sid", ["ses_legacy_01", str(uuid4())])
@pytest.mark.parametrize("car", ["10", None])
def test_lifecycle_and_billing_reads_same_row(connect, sid, car):
    with connect() as conn:
        active = enter(conn, session_id=sid, lot_id="A", space_id="A-01", car_id=car, entry_at=ENTRY)
        assert (active.status, active.exit_at, active.entry_at) == ("active", None, ENTRY.isoformat())
        closed = exit_session(conn, session_id=sid, exit_at=EXIT)
        assert (closed.status, closed.exit_at, closed.car_id) == ("closed", EXIT.isoformat(), car)
        assert exit_session(conn, session_id=sid, exit_at=EXIT) == closed

    app = FastAPI()
    app.include_router(billing_router)
    with TestClient(app) as client:
        response = client.get(f"/api/v1/sessions/{sid}/fee")
    assert response.status_code == 200, response.text
    fee = response.json()
    assert (fee["session_id"], fee["car_id"], fee["duration_minutes"]) == (sid, car, 45)


def test_replays_and_conflicts(connect):
    args = dict(session_id="ses_retry", lot_id="A", car_id="10", entry_at=ENTRY)
    with connect() as conn:
        active = enter(conn, **args)
        assert enter(conn, **args) == active
        for change, code in [
            ({"space_id": "A-02"}, "SESSION_ID_CONFLICT"),
            ({"session_id": "other", "lot_id": "B"}, "ACTIVE_SESSION_EXISTS"),
        ]:
            with pytest.raises(SessionError) as error:
                enter(conn, **(args | change))
            assert error.value.status_code == 409
            assert error.value.body["error"]["code"] == code
            assert error.value.body["error"]["details"] == []
            assert not conn.in_transaction
        closed = exit_session(conn, session_id=active.session_id, exit_at=EXIT)
        assert enter(conn, **args) == closed
        with pytest.raises(SessionError, match="이미 종료") as error:
            exit_session(conn, session_id=active.session_id, exit_at=EXIT + timedelta(seconds=1))
        assert error.value.status_code == 409
        assert enter(conn, **(args | {"session_id": "next", "entry_at": EXIT})).status == "active"


def test_invalid_exit_leaves_session_active(connect):
    with connect() as conn:
        with pytest.raises(SessionError) as missing:
            exit_session(conn, session_id="missing", exit_at=EXIT)
        assert missing.value.status_code == 404
        active = enter(conn, session_id="ses_open", lot_id="A", entry_at=ENTRY)
        for invalid in (ENTRY - timedelta(seconds=1), EXIT.replace(tzinfo=None)):
            with pytest.raises(SessionError) as error:
                exit_session(conn, session_id=active.session_id, exit_at=invalid)
            assert error.value.status_code == 422
        assert conn.execute("SELECT status, exit_at FROM parking_sessions").fetchone() == ("active", None)


@pytest.mark.parametrize("car_id", ["01", 10])
def test_invalid_car(connect, car_id):
    with connect() as conn, pytest.raises(SessionError) as error:
        enter(conn, session_id="ses_invalid", lot_id="A", car_id=car_id, entry_at=ENTRY)
    assert error.value.status_code == 422


@pytest.mark.parametrize("action", ["same_entry", "competing_entries", "same_exit", "competing_exits"])
def test_concurrent_requests(connect, action):
    if action.endswith("exits") or action == "same_exit":
        with connect() as conn:
            enter(conn, session_id="ses_parallel", lot_id="A", car_id="10", entry_at=ENTRY)

    barrier = Barrier(8)

    def attempt(index):
        with connect() as conn:
            barrier.wait(timeout=10)
            try:
                if action in ("same_exit", "competing_exits"):
                    observed = EXIT + timedelta(seconds=index if action == "competing_exits" else 0)
                    return exit_session(conn, session_id="ses_parallel", exit_at=observed)
                sid = f"ses_{index}" if action == "competing_entries" else "ses_parallel"
                return enter(conn, session_id=sid, lot_id="A", car_id="10", entry_at=ENTRY)
            except SessionError as error:
                return error.status_code

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(attempt, range(8)))
    conflicts = 7 if action.startswith("competing") else 0
    assert results.count(409) == conflicts
    successful = {result for result in results if result != 409}
    assert len(successful) == 1
    with connect() as conn:
        assert conn.execute("SELECT count(*) FROM parking_sessions").fetchone() == (1,)
        stored = conn.execute("SELECT status, exit_at FROM parking_sessions").fetchone()
    winner = successful.pop()
    assert stored == (winner.status, winner.exit_at)
