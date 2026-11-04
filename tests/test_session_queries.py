import sqlite3
from contextlib import closing
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from backend.app.demo_main import create_app

SESSIONS = "/api/v1/sessions"


@pytest.fixture
def client():
    app = create_app(token="synthetic-test-token")
    with TestClient(app) as client:
        with closing(sqlite3.connect(app.state.billing_path)) as conn, conn:
            conn.executemany(
                "INSERT INTO parking_sessions "
                "(session_id, lot_id, space_id, car_id, entry_at, exit_at, status) VALUES (?,?,?,?,?,?,?)",
                [
                    ("unidentified", "B", None, None, "2026-11-06T08:45:00+00:00", None, "active"),
                    ("parked", "A", "A-02", "21", "2026-11-06T17:30:00+09:00", None, "active"),
                    ("legacy", "A", "A-03", "22", "2026-11-06T16:00:00+09:00",
                     "2026-11-06T16:30:00+09:00", None),
                ],
            )
        yield client


def test_session_list_is_newest_first_and_keeps_unidentified_cars(client):
    response = client.get(SESSIONS)
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"items", "next_cursor", "as_of"}
    assert body["next_cursor"] is None
    assert datetime.fromisoformat(body["as_of"]).utcoffset() is not None
    assert [item["session_id"] for item in body["items"]] == [
        "unidentified", "parked", "legacy", "ses_demo",
    ]
    assert body["items"][0] == {
        "session_id": "unidentified", "lot_id": "B", "space_id": None,
        "car_id": None, "entry_at": "2026-11-06T08:45:00+00:00",
        "exit_at": None, "status": "active",
    }
    assert body["items"][2]["status"] == "closed"


@pytest.mark.parametrize(("params", "expected"), [
    ({"lot_id": "B"}, ["unidentified"]),
    ({"car_id": "21"}, ["parked"]),
    ({"status": "closed"}, ["legacy", "ses_demo"]),
    ({"lot_id": "A", "car_id": "21", "status": "active"}, ["parked"]),
    ({"lot_id": "D"}, []),
])
def test_session_filters(client, params, expected):
    response = client.get(SESSIONS, params=params)
    assert response.status_code == 200
    assert [item["session_id"] for item in response.json()["items"]] == expected


@pytest.mark.parametrize("params", [{"lot_id": "X"}, {"car_id": "01"}, {"status": "parked"}])
def test_invalid_session_filter(client, params):
    response = client.get(SESSIONS, params=params)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
