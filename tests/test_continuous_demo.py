from contextlib import closing
from datetime import datetime, timedelta
import re
from time import monotonic

from fastapi.testclient import TestClient

from backend.app.runtime import create_runtime_app
from backend.app.simulation import demo_history


def test_continuous_demo_seeds_settles_and_recovers(monkeypatch, tmp_path):
    monkeypatch.setenv("PARKON_CONTINUOUS_DEMO", "1")
    start = datetime(2026, 11, 7, tzinfo=demo_history.KST)
    monkeypatch.setattr(demo_history, "START", start)
    now = [start + timedelta(days=1, hours=10)]
    database = tmp_path / "continuous.sqlite3"

    def app(*, ticking=False):
        started = monotonic()
        return create_runtime_app(token="synthetic-demo-token", database=database,
                                  clock=lambda: now[0] + timedelta(seconds=monotonic() - started)
                                  if ticking else now[0])

    first = app(ticking=True)
    with TestClient(first) as client, closing(first.state.pipeline.connection()) as conn:
        for lot in "CD":
            control = client.get(f"/api/v1/simulation/{lot}").json()
            assert control["running"] and control["interval_seconds"] == 30
        seed = conn.execute("SELECT count(*) FROM demo_session_details").fetchone()[0]
        assert seed > 0
        assert conn.execute("SELECT count(DISTINCT lot_id) FROM parking_sessions").fetchone()[0] == 4
        plates = [r[0] for r in conn.execute("SELECT plate_number FROM session_vehicle_plates")]
        assert {len(p) for p in plates} == {7, 8}
        assert all(re.fullmatch(r"\d{2,3}[가-힣]\d{4}", p) for p in plates)
        assert conn.execute("SELECT max(exit_at) FROM parking_sessions").fetchone()[0] < now[0].isoformat()
        assert client.post("/api/v1/simulation/C/entry", json={"vehicle_number": "7654"}).status_code == 200
        session = client.get("/api/v1/vehicle-search?vehicle_number=7654").json()
        now[0] += timedelta(minutes=10)
        assert client.post("/api/v1/simulation/C/exit", json={"space_id": session["space_id"]}).status_code == 200
        demo_history.checkpoint(conn, now[0] + timedelta(seconds=10))
        demo_history.checkpoint(conn, now[0] + timedelta(seconds=10))
        payment = conn.execute("SELECT * FROM mock_payments WHERE session_id=?", (session["session_id"],)).fetchall()
        assert len(payment) == 1 and payment[0]["status"] == "completed"
        assert client.post("/api/v1/simulation/D/stop", json={}).status_code == 200
        report = client.get("/api/v1/reports/revenue?month=2026-11&lot_id=C").json()
        assert report["has_demo_history"]
        assert report["usage"]["entries"] == conn.execute(
            "SELECT count(*) FROM parking_sessions WHERE lot_id='C'"
        ).fetchone()[0]

    now[0] += timedelta(days=1)
    restarted = app()
    with TestClient(restarted) as client, closing(restarted.state.pipeline.connection()) as conn:
        assert client.get("/api/v1/simulation/C").json()["running"]
        assert not client.get("/api/v1/simulation/D").json()["running"]
        gaps = dict(conn.execute(
            "SELECT lot_id,count(*) FROM parking_sessions WHERE session_id GLOB 'gap-*' GROUP BY lot_id"
        ))
        assert set(gaps) == {"C"} and gaps["C"] > 0
        assert conn.execute("SELECT count(*) FROM demo_outages").fetchone()[0] == 1

    repeated = app()
    with TestClient(repeated), closing(repeated.state.pipeline.connection()) as conn:
        assert conn.execute("SELECT count(*) FROM demo_outages").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM parking_sessions WHERE session_id GLOB 'gap-*'").fetchone()[0] == gaps["C"]
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
