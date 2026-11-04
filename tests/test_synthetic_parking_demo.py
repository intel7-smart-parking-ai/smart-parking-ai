import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from backend.app import demo_main
from backend.app.database.parking_sessions import SessionError
from backend.app.demo_main import create_app
from backend.app.simulation.run_parking_demo import A_SPACES, DemoError, local_base_url, run_demo


@pytest.mark.parametrize(("short", "empty_during_entry"), [(False, 0), (True, 9)])
def test_a_spaces_enter_and_exit(short, empty_during_entry):
    time = [datetime(2026, 11, 6, tzinfo=timezone.utc)]
    token = "synthetic-test-token"
    output = []

    def advance(seconds):
        time[0] += timedelta(seconds=seconds)

    assert len({space.space_id for space in A_SPACES}) == 12
    with TestClient(create_app(token=token, clock=lambda: time[0])) as client:
        with pytest.raises(DemoError, match="인증 실패"):
            run_demo(client, "invalid-test-token", output=lambda _: None)
        run_demo(client, token, short=short, wait=advance, clock=lambda: time[0], output=output.append)
        body = client.get("/api/v1/parking-lots/A/spaces").json()
        assert set(body) == {"items", "next_cursor", "as_of"}
        assert len(body["items"]) == 12
        assert all(row["status"] == "empty" for row in body["items"])
        updated = {row["space_id"] for row in body["items"] if row["version"] == 7}
        assert updated == ({"A-01", "A-02", "A-03"} if short else {space.space_id for space in A_SPACES})
    assert [line.rsplit(" | ", 1)[-1] for line in output] == [
        "빈자리 12/12", f"빈자리 {empty_during_entry}/12", "빈자리 12/12",
    ]


@pytest.mark.parametrize(("short", "duration"), [(False, 305), (True, 15)])
def test_hold_refreshes_occupancy_past_stale_window(short, duration):
    start = datetime(2026, 11, 6, tzinfo=timezone.utc)
    now = start
    token = "synthetic-test-token"

    def advance(seconds):
        nonlocal now
        if (now - start).total_seconds() >= duration:
            raise KeyboardInterrupt
        now += timedelta(seconds=seconds)

    with TestClient(create_app(token=token, clock=lambda: now)) as client:
        with pytest.raises(KeyboardInterrupt):
            run_demo(client, token, short=short, hold=True, wait=advance,
                     clock=lambda: now, output=lambda _: None)
        rows = client.get("/api/v1/parking-lots/A/spaces").json()["items"]
        occupied = {row["space_id"]: row for row in rows if row["status"] == "occupied"}
        expected = ["A-01", "A-02", "A-03"] if short else [space.space_id for space in A_SPACES]
        assert set(occupied) == set(expected)
        lot = next(row for row in client.get("/api/v1/parking-lots").json()["items"] if row["lot_id"] == "A")
        assert lot["empty_spaces"] == 12 - len(expected)
        for number, space_id in enumerate(expected, start=10):
            row = occupied[space_id]
            assert row["car_id"] == str(number) and row["source"] == "simulation"
            assert datetime.fromisoformat(row["observed_at"]) == now
            assert row["version"] > (10 if short else 1000 // 12)


def test_base_url_only_allows_local_http_with_port():
    assert local_base_url("http://localhost:8012/") == "http://127.0.0.1:8012"
    with pytest.raises(ValueError):
        local_base_url("http://192.0.2.1:8002")


@pytest.mark.parametrize("short", [False, True])
def test_depart_then_repeat_without_losing_billing_history(short, monkeypatch):
    now = datetime.now(timezone.utc)
    stop_at = now
    token = "synthetic-test-token"
    count = 3 if short else 12
    app = create_app(token=token, clock=lambda: now)

    def advance(seconds):
        nonlocal now
        now += timedelta(seconds=seconds)

    def hold_until_interrupt(seconds):
        if now >= stop_at:
            raise KeyboardInterrupt
        advance(seconds)

    def sessions():
        with closing(sqlite3.connect(app.state.billing_path)) as conn:
            return conn.execute(
                "SELECT session_id, status, exit_at FROM parking_sessions "
                "WHERE session_id <> 'ses_demo' ORDER BY session_id"
            ).fetchall()

    with TestClient(app) as client:
        def run(**options):
            run_demo(client, token, short=short, clock=lambda: now, output=lambda _: None, **options)

        history = {}
        for cycle in range(2):
            advance(1.1)
            stop_at = now + timedelta(seconds=5)
            with pytest.raises(KeyboardInterrupt):
                run(hold=True, wait=hold_until_interrupt)
            active = {sid for sid, state, _ in sessions() if state == "active"}
            assert len(active) == count and not active.intersection(history)

            if cycle == 0:
                sid = client.get("/api/v1/vehicles/10/location").json()["session_id"]
                assert sid in active
                fee = client.get(f"/api/v1/sessions/{sid}/fee", params={"now": now.isoformat()})
                assert fee.status_code == 200
                payment = client.post("/api/v1/mock-payments", json={
                    "session_id": sid, "quote_id": fee.json()["quote_id"],
                    "idempotency_key": "repeat-demo-payment",
                })
                assert payment.status_code == 200
                advance(11)
                rows = client.get("/api/v1/parking-lots/A/spaces").json()["items"]
                assert sum(row["status"] == "uncertain" for row in rows) == count
                assert {sid for sid, state, _ in sessions() if state == "active"} == active

                def conflict(*args, **kwargs):
                    raise SessionError(409, "TEST_CONFLICT", "합성 충돌")

                def unavailable(*args, **kwargs):
                    raise sqlite3.OperationalError("synthetic write failure")

                for failure, code in ((conflict, 409), (unavailable, 503)):
                    with monkeypatch.context() as patch:
                        patch.setattr(demo_main, "exit_session", failure)
                        with pytest.raises(DemoError, match=rf"HTTP {code}.*--depart"):
                            run(depart=True, wait=advance)
                    assert {sid for sid, state, _ in sessions() if state == "active"} == active

            if cycle == 0:
                departure_started = now
                stop_at = now + timedelta(seconds=21)
                with pytest.raises(KeyboardInterrupt):
                    run(depart=True, keep_empty=True, wait=hold_until_interrupt)
                rows = client.get("/api/v1/parking-lots/A/spaces").json()["items"]
                refreshed = [row for row in rows if row["observed_at"] is not None
                             and datetime.fromisoformat(row["observed_at"]) == now]
                assert len(refreshed) == count
                assert all(row["status"] == "empty" for row in rows)
                assert (now - departure_started).total_seconds() >= 19.4
            else:
                run(depart=True, wait=advance)
            closed = sessions()
            assert len(closed) == count * (cycle + 1)
            assert all(state == "closed" and exited for _, state, exited in closed)
            if cycle == 0:
                latest_exit = max(datetime.fromisoformat(exited) for _, _, exited in closed)
                assert now - latest_exit >= timedelta(seconds=15)
            assert {sid: exited for sid, _, exited in closed if sid in history} == history
            history = {sid: exited for sid, _, exited in closed}
            run(depart=True, wait=advance)
            assert sessions() == closed
            lots = client.get("/api/v1/parking-lots").json()["items"]
            assert next(lot for lot in lots if lot["lot_id"] == "A")["empty_spaces"] == 12

        with closing(sqlite3.connect(app.state.billing_path)) as conn:
            assert conn.execute("SELECT payment_id FROM mock_payments").fetchall() == [
                (payment.json()["payment_id"],),
            ]
            assert conn.execute("SELECT quote_id FROM fee_quotes").fetchall() == [(fee.json()["quote_id"],)]
