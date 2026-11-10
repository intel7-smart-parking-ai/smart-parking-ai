"""D-70 camera movement scenarios on the shared runtime SQLite pipeline."""
import sqlite3
from datetime import timedelta

import pytest

from test_identity_observation_evidence import AT, d68_runtime, frame  # noqa: F401


def observe(client, send, start, bays, *, seconds=3, lot="A"):
    replies = []
    for tick in range(seconds):
        for offset, (space, car) in enumerate(bays.items()):
            event = frame((start + tick) * 10 + offset, car).model_copy(update={
                "lot_id": lot, "space_id": space, "camera_id": f"camera_{lot.lower()}",
                "event_type": "vehicle_observed" if car else "space_empty_observed",
                "observed_at": AT + timedelta(seconds=start + tick),
            })
            replies.append(send(client, event).status_code)
    return replies


def sessions(read):
    return {car: (sid, bay, entered, status) for sid, bay, entered, car, status in read(
        "SELECT session_id,space_id,entry_at,car_id,status FROM parking_sessions"
    )}


@pytest.mark.parametrize(("original", "moved"), [
    ({"A-01": "10", "A-02": "11"}, {"A-01": "11", "A-02": "10"}),
    ({"A-01": "10"}, {"A-01": None, "A-02": "10"}),
    ({"A-01": "10", "A-02": "11", "A-03": "12"},
     {"A-01": "12", "A-02": "10", "A-03": "11"}),
    ({"A-01": "10", "A-02": "11"}, {"A-01": None, "A-02": "10", "A-03": "11"}),
], ids=["swap", "empty-bay", "three-bay-cycle", "three-bay-chain"])
def test_d70_moves_keep_sessions(d68_runtime, original, moved):
    clients, send, read = d68_runtime
    with clients() as client:
        assert set(observe(client, send, 0, original)) == {202}
        before = sessions(read)
        assert len(before) == len(original)
        assert set(observe(client, send, 3, moved, seconds=2)) == {202}
        assert sessions(read) == before
        assert set(observe(client, send, 5, moved, seconds=2)) == {202}
        for bay, car in moved.items():
            if car:
                assert sessions(read)[car] == (before[car][0], bay, before[car][2], "active")
        assert read("SELECT count(*) FROM session_moves") == [(len(original),)]


def test_d70_unknown_replacement_does_not_overwrite_identity(d68_runtime):
    clients, send, read = d68_runtime
    with clients() as client:
        observe(client, send, 0, {"A-01": "10"})
        before = sessions(read)
        assert observe(client, send, 3, {"A-01": "11"}) == [202, 202, 409]
        assert sessions(read) == before


def test_d70_cross_lot_move_is_conflict(d68_runtime):
    clients, send, read = d68_runtime
    with clients() as client:
        observe(client, send, 0, {"A-01": "10"})
        assert observe(client, send, 3, {"A-01": "10"}, lot="B") == [202, 202, 409]
        assert sessions(read)["10"][1] == "A-01"


def test_d70_preserves_quote_payment_and_restarts_with_swapped_locations(d68_runtime):
    clients, send, read = d68_runtime
    with clients() as client:
        observe(client, send, 0, {"A-01": "10", "A-02": "11"})
        sid = sessions(read)["10"][0]
        fee = client.get(f"/api/v1/sessions/{sid}/fee")
        assert fee.status_code == 200
        assert client.post("/api/v1/mock-payments", json={
            "session_id": sid, "quote_id": fee.json()["quote_id"],
            "idempotency_key": "move-payment", "payment_method": "MOCK_CARD",
        }).status_code == 200
        history = read("SELECT * FROM fee_quotes"), read("SELECT * FROM mock_payments")
        assert set(observe(client, send, 3, {"A-01": "11", "A-02": "10"})) == {202}
    with clients() as client:
        location = client.get("/api/v1/vehicles/10/location").json()
        assert (location["session_id"], location["space_id"]) == (sid, "A-02")
        assert (read("SELECT * FROM fee_quotes"), read("SELECT * FROM mock_payments")) == history


def test_d70_duplicate_and_late_observation_cannot_reverse_swap(d68_runtime):
    clients, send, read = d68_runtime
    with clients() as client:
        observe(client, send, 0, {"A-01": "10", "A-02": "11"})
        assert set(observe(client, send, 3, {"A-01": "11", "A-02": "10"})) == {202}
        before = sessions(read)
        late = frame(21, "11").model_copy(update={
            "space_id": "A-02", "observed_at": AT + timedelta(seconds=2),
        })
        assert send(client, late).status_code == 200
        another_id = late.model_copy(update={"event_id": "d70-late-retry", "frame_id": "d70-late-frame"})
        assert send(client, another_id).status_code == 202
        assert sessions(read) == before
        assert read("SELECT count(*) FROM session_moves") == [(2,)]


def test_d70_move_write_failure_rolls_back_entire_swap(d68_runtime, monkeypatch):
    clients, send, read = d68_runtime
    with clients() as client:
        observe(client, send, 0, {"A-01": "10", "A-02": "11"})
        before = sessions(read)
        observe(client, send, 3, {"A-01": "11", "A-02": "10"}, seconds=2)
        store = client.app.state.pipeline
        def fail(*args):
            raise sqlite3.OperationalError("synthetic snapshot failure")
        with monkeypatch.context() as patch:
            patch.setattr(store, "_save_camera_snapshot", fail)
            assert observe(client, send, 5, {"A-01": "11", "A-02": "10"}, seconds=1) == [202, 503]
        assert sessions(read) == before
        assert read("SELECT count(*) FROM session_moves") == [(0,)]
        # Same pending trigger remains retriable after the failure.
        assert send(client, frame(51).model_copy(update={
            "space_id": "A-02", "observed_at": AT + timedelta(seconds=5),
        })).status_code == 202
        assert sessions(read)["10"][1] == "A-02"



def test_d70_null_number_breaks_move_streak(d68_runtime):
    clients, send, read = d68_runtime
    with clients() as client:
        observe(client, send, 0, {"A-01": "10"})
        before = sessions(read)
        observe(client, send, 3, {"A-01": None, "A-02": "10"}, seconds=1)
        observe(client, send, 4, {"A-01": None}, seconds=1)
        assert send(client, frame(41, None).model_copy(update={
            "space_id": "A-02", "observed_at": AT + timedelta(seconds=4),
        })).status_code == 202
        assert set(observe(client, send, 5, {"A-01": None, "A-02": "10"}, seconds=2)) == {202}
        assert sessions(read) == before
        assert set(observe(client, send, 7, {"A-01": None, "A-02": "10"}, seconds=1)) == {202}
        assert sessions(read)["10"][1] == "A-02"
