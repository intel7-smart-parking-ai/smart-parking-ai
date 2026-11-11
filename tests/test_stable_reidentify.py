"""11/11 PM: a car replaced without the 3 s empty gap stays 409 forever (bay 'uncertain').

A different number read on >= 8 distinct, confident camera frames is a real swap:
close the old session at the first new read and open the new car. Shorter misreads
keep the old session (no wrong exit, fee unchanged).
"""
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from backend.app.runtime import create_runtime_app

TOKEN = "t" * 24
HEADERS = {"Authorization": "Bearer " + TOKEN}
START = datetime(2026, 11, 11, 1, 0, tzinfo=timezone.utc)


@pytest.fixture
def bay(tmp_path):
    now = [START]
    seq = [0]
    db = tmp_path / "reidentify.db"
    app = create_runtime_app(token=TOKEN, database=db, clock=lambda: now[0])
    with TestClient(app) as client:
        def frames(car, count, event_type="vehicle_observed", confidence=0.95):
            codes = []
            for _ in range(count):
                now[0] += timedelta(seconds=0.6)
                seq[0] += 1
                stamp = now[0].isoformat()
                event = {
                    "event_id": f"e{seq[0]}", "event_type": event_type, "source": "vision",
                    "lot_id": "B", "camera_id": "camera_b", "space_id": "A-01", "car_id": car,
                    "vehicle_type": "general" if event_type == "vehicle_observed" else None,
                    "confidence": confidence,
                    "bbox_xyxy": [1, 1, 9, 9] if event_type == "vehicle_observed" else None,
                    "observed_at": stamp, "frame_id": f"camera_b:1:{seq[0]}:{stamp}",
                }
                codes.append(client.post("/api/v1/internal/vision-events", json=event,
                                         headers=HEADERS).status_code)
            return codes

        def sessions():
            with sqlite3.connect(db) as conn:
                return conn.execute(
                    "SELECT car_id, status, entry_at, exit_at FROM parking_sessions "
                    "WHERE lot_id='B' AND space_id='A-01' ORDER BY entry_at, rowid").fetchall()

        def status():
            items = client.get("/api/v1/parking-lots/B/spaces").json()["items"]
            return next(s for s in items if s["space_id"] == "A-01")

        yield frames, sessions, status


def test_swap_without_empty_gap_reidentifies(bay):
    frames, sessions, status = bay
    frames("33", 10)
    frames("24", 25)
    rows = sessions()
    assert [(car, state) for car, state, *_ in rows] == [("33", "closed"), ("24", "active")]
    assert rows[0][3] == rows[1][2]          # old exit == new entry (no gap, no overlap)
    assert status()["car_id"] == "24" and status()["status"] == "occupied"


def test_short_misread_keeps_the_session(bay):
    frames, sessions, status = bay
    frames("33", 10)
    frames("38", 5)                          # brief misread (< 8 qualifying frames)
    frames("33", 10)
    assert [(car, state) for car, state, *_ in sessions()] == [("33", "active")]


def test_low_confidence_reads_never_reidentify(bay):
    frames, sessions, status = bay
    frames("33", 10)
    frames("24", 25, confidence=0.6)
    assert [(car, state) for car, state, *_ in sessions()] == [("33", "active")]


def test_empty_gap_flow_is_unchanged(bay):
    frames, sessions, status = bay
    frames("33", 10)
    frames(None, 10, event_type="space_empty_observed")
    frames("24", 10)
    assert [(car, state) for car, state, *_ in sessions()] == [("33", "closed"), ("24", "active")]


def test_failed_new_entry_rolls_back_the_old_exit(bay, tmp_path):
    """PR #132 review: exit and entry are one transaction (no orphan closed session)."""
    frames, sessions, status = bay
    frames("33", 10)
    with sqlite3.connect(tmp_path / "reidentify.db") as conn:
        conn.execute("CREATE TRIGGER fail_24 BEFORE INSERT ON parking_sessions "
                     "WHEN NEW.car_id='24' BEGIN SELECT RAISE(ABORT, 'injected'); END")
    codes = frames("24", 25)
    assert 503 in codes
    assert [(car, state) for car, state, *_ in sessions()] == [("33", "active")]


def test_new_number_active_elsewhere_is_not_a_swap(bay, tmp_path):
    frames, sessions, status = bay
    frames("33", 10)
    with sqlite3.connect(tmp_path / "reidentify.db") as conn:
        conn.execute("INSERT INTO parking_sessions (session_id, lot_id, space_id, car_id, entry_at, status) "
                     "VALUES ('elsewhere', 'C', 'A-01', '24', ?, 'active')", (START.isoformat(),))
    frames("24", 25)
    assert [(car, state) for car, state, *_ in sessions()] == [("33", "active")]


def test_old_entry_kept_and_new_entry_equals_old_exit(bay, tmp_path):
    frames, sessions, status = bay
    frames("33", 10)
    before = sessions()[0]
    frames("24", 25)
    old, new = sessions()
    assert old[2] == before[2] and old[1] == "closed" and new[2] == old[3]
