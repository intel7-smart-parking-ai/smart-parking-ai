import json
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app.api.occupancy_query_demo import create_demo_app
from backend.app import demo_main
from backend.app.demo_main import create_app

POST = "/api/v1/internal/vision-events"
LOTS = "/api/v1/parking-lots"
SPACES = f"{LOTS}/A/spaces"
LOCATION = "/api/v1/vehicles/10/location"


@pytest.mark.parametrize("single_server", [False, True])
def test_vision_to_kan16_demo(single_server):
    fixture = Path(__file__).parent / "fixtures" / "vision_event_sequences.json"
    sample = json.loads(fixture.read_text("utf-8"))["normal"][0]
    start = datetime.fromisoformat(sample["observed_at"])
    token = "synthetic-test-token"
    clock = [start]

    factory = create_app if single_server else create_demo_app
    app = factory(token=token, clock=lambda: clock[0])
    with TestClient(app) as client:
        assert client.post(POST, json=sample).status_code == 403
        for path in (LOTS, SPACES, LOCATION):
            expected = (404 if path == LOCATION else 200) if single_server else 403
            assert client.get(path).status_code == expected
        auth = {"Authorization": f"Bearer {token}"}
        if not single_server:
            client.headers.update(auth)

        def verify(status, empty_count):
            response = client.get(SPACES)
            assert response.headers["X-ParkFlow-Data-Source"] == "mock"
            spaces = response.json()
            assert set(spaces) == {"items", "as_of", "next_cursor"}
            row = next(item for item in spaces["items"] if item["space_id"] == "A-01")
            assert row["status"] == status
            lots = client.get(LOTS).json()
            assert [lot["total_spaces"] for lot in lots["items"]] == [12, 16, 12, 64]
            assert lots["items"][0]["empty_spaces"] == empty_count
            assert sum(item["status"] == "empty" for item in spaces["items"]) == empty_count
            return row

        verify("empty", 12)
        for tick in range(7):
            clock[0] = start + timedelta(seconds=tick)
            event = sample | {"event_id": f"query-demo-{tick}", "source": "simulation",
                              "observed_at": clock[0].isoformat()}
            if tick >= 3:
                event |= {"event_type": "space_empty_observed", "car_id": None, "bbox_xyxy": None}
            assert client.post(POST, json=event, headers=auth).status_code == 202
            if tick == 2:
                occupied = verify("occupied", 11)
                assert occupied["car_id"] == "10"
                assert client.post(POST, json=event, headers=auth).status_code == 200
                assert verify("occupied", 11) == occupied
                location = client.get(LOCATION).json()
                assert location["space_id"] == "A-01"
                assert (location["session_id"] is not None) == single_server
            if tick == 3:
                verify("uncertain", 11)
        verify("empty", 12)
        assert client.get(LOCATION).json()["error"]["code"] == "VEHICLE_NOT_FOUND"


def test_single_demo_billing_and_schema():
    app = create_app(token="synthetic-test-token")
    with TestClient(app) as client:
        assert client.get("/docs").status_code == 200
        paths = client.get("/openapi.json").json()["paths"]
        assert {POST, LOTS, SPACES.replace("/A/", "/{lot_id}/"),
                "/api/v1/vehicles/{car_id}/location",
                "/api/v1/sessions/{session_id}/fee", "/api/v1/mock-payments"} <= paths.keys()
        path = app.state.billing_path
        fee = client.get("/api/v1/sessions/ses_demo/fee")
        assert fee.status_code == 200
        payment = client.post("/api/v1/mock-payments", json={
            "session_id": "ses_demo", "quote_id": fee.json()["quote_id"],
            "idempotency_key": "single-demo-payment",
        })
        assert payment.status_code == 200
        assert payment.json()["amount_krw"] == fee.json()["amount_krw"]
    assert not path.exists()


@pytest.mark.parametrize("car_id", ["10", None])
def test_single_demo_session_lifecycle_and_retry(monkeypatch, car_id):
    fixture = Path(__file__).parent / "fixtures" / "vision_event_sequences.json"
    sample = json.loads(fixture.read_text("utf-8"))["normal"][0]
    start = datetime.fromisoformat(sample["observed_at"])
    now = [start]
    token = "synthetic-test-token"
    app = create_app(token=token, clock=lambda: now[0])

    def fail_write(*args, **kwargs):
        raise sqlite3.OperationalError("synthetic write failure")

    with TestClient(app) as client:
        def send(tick, *, empty=False, expected=202):
            now[0] = start + timedelta(seconds=tick)
            payload = sample | {"event_id": f"session-{tick}", "source": "simulation",
                                "car_id": car_id, "observed_at": now[0].isoformat()}
            if empty:
                payload |= {"event_type": "space_empty_observed", "car_id": None, "bbox_xyxy": None}
            response = client.post(POST, json=payload, headers={"Authorization": f"Bearer {token}"})
            assert response.status_code == expected, response.text

        def sessions():
            with closing(sqlite3.connect(app.state.billing_path)) as conn:
                return conn.execute(
                    "SELECT session_id, car_id, status, entry_at, exit_at FROM parking_sessions "
                    "WHERE session_id <> 'ses_demo'"
                ).fetchall()

        def status():
            return next(row["status"] for row in client.get(SPACES).json()["items"]
                        if row["space_id"] == "A-01")

        send(0)
        send(1)
        with monkeypatch.context() as patch:
            patch.setattr(demo_main, "enter", fail_write)
            send(2, expected=503)
            assert sessions() == [] and status() == "uncertain"
        send(2)
        send(2, expected=200)
        assert status() == "occupied"
        [(sid, stored_car, state, entry, exit_at)] = sessions()
        assert (stored_car, state, entry, exit_at) == (
            car_id, "active", (start + timedelta(seconds=2)).isoformat(), None,
        )
        location = client.get(LOCATION)
        if car_id is None:
            assert location.status_code == 404
        else:
            assert location.json()["session_id"] == sid
        assert client.get(f"/api/v1/sessions/{sid}/fee", params={"now": entry}).status_code == 200
        now[0] = start + timedelta(seconds=13)
        assert status() == "uncertain" and sessions()[0][2] == "active"
        for tick in (14, 15, 16):
            send(tick, empty=True)
        assert status() == "uncertain" and sessions()[0][2] == "active"
        with monkeypatch.context() as patch:
            patch.setattr(demo_main, "exit_session", fail_write)
            send(17, empty=True, expected=503)
            assert status() == "uncertain" and sessions()[0][2] == "active"
        send(17, empty=True)
        assert status() == "empty"
        assert sessions() == [(sid, car_id, "closed", entry, now[0].isoformat())]
        assert client.get(f"/api/v1/sessions/{sid}/fee").status_code == 200
