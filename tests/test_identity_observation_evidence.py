"""Test-only examples for two unapproved identity evidence policies."""

from collections import deque
from datetime import datetime, timedelta, timezone

import pytest

from backend.app.api.vision_event_schema import VisionEvent

AT = datetime(2026, 11, 10, tzinfo=timezone.utc)
GAP_LIMIT = timedelta(seconds=2)
# Experiment assumption only. This is not a main API freshness requirement.
AGE_LIMIT = timedelta(seconds=2)


def frame(index, car_id="10", *, frame_id=None, event_type="vehicle_observed",
          confidence=0.9, camera_id="camera_a"):
    return VisionEvent(
        event_id=f"event-{index}", event_type=event_type, source="vision",
        lot_id="A", camera_id=camera_id, space_id="A-01", car_id=car_id,
        confidence=confidence, bbox_xyxy=None,
        observed_at=AT + timedelta(seconds=index),
        frame_id=frame_id if frame_id is not None else f"frame-{index}",
    )


def decisions(deliveries, policy):
    """Evaluate synthetic evidence only. None is an explicit loss of visibility."""
    recent = deque(maxlen=5)
    consecutive = 0
    candidate = None
    last_observed = None
    seen = set()
    for event, received_at in deliveries:
        if event is None:
            recent.clear()
            consecutive, candidate = 0, None
            yield False
            continue
        if event.camera_id != "camera_a" or event.frame_id is None:
            yield False
            continue
        if (event.frame_id in seen or received_at - event.observed_at > AGE_LIMIT
                or (last_observed is not None and event.observed_at <= last_observed)):
            yield False
            continue
        if last_observed is not None and event.observed_at - last_observed > GAP_LIMIT:
            recent.clear()
            consecutive, candidate = 0, None
        seen.add(event.frame_id)
        last_observed = event.observed_at
        if event.event_type != "vehicle_observed" or event.confidence < 0.8:
            recent.clear()
            consecutive, candidate = 0, None
            yield False
            continue
        if event.car_id is not None and event.car_id != candidate:
            recent.clear()
            consecutive, candidate = 0, event.car_id
        recent.append(event.car_id)
        if event.car_id is None:
            consecutive = 0
            yield False
            continue
        consecutive += 1
        yield consecutive >= 3 if policy == "consecutive" else recent.count(candidate) >= 3


def assess(policy, *events):
    deliveries = [
        (event, event.observed_at) if event is not None else (None, None)
        for event in events
    ]
    return list(decisions(deliveries, policy))


@pytest.mark.parametrize("policy", ["consecutive", "three_of_five"])
def test_identity_requires_distinct_frames(policy):
    repeated = frame(1, frame_id="frame-0")
    assert assess(policy, frame(0), repeated, frame(2), frame(3)) == [
        False, False, False, True,
    ]


@pytest.mark.parametrize("policy", ["consecutive", "three_of_five"])
def test_different_number_resets_previous_evidence(policy):
    assert assess(policy, frame(0), frame(1), frame(2, "11"),
                  frame(3), frame(4), frame(5)) == [
        False, False, False, False, False, True,
    ]


@pytest.mark.parametrize("policy", ["consecutive", "three_of_five"])
@pytest.mark.parametrize("interruption", [
    None,
    frame(2, None, event_type="space_empty_observed"),
    frame(2, None, confidence=0.79),
])
def test_occlusion_empty_and_low_confidence_break_continuity(policy, interruption):
    assert assess(policy, frame(0), frame(1), interruption,
                  frame(3), frame(4), frame(5)) == [
        False, False, False, False, False, True,
    ]


@pytest.mark.parametrize("policy", ["consecutive", "three_of_five"])
def test_frame_gap_breaks_continuity(policy):
    assert assess(policy, frame(0), frame(1), frame(4), frame(5), frame(6)) == [
        False, False, False, False, True,
    ]


@pytest.mark.parametrize("policy", ["consecutive", "three_of_five"])
def test_stale_and_out_of_order_frames_do_not_count(policy):
    events = [frame(0), frame(2), frame(1), frame(3), frame(4)]
    arrivals = [0, 2, 2, 6, 6]
    deliveries = [(event, AT + timedelta(seconds=arrival))
                  for event, arrival in zip(events, arrivals)]
    assert list(decisions(deliveries, policy)) == [False, False, False, False, True]


def test_consecutive_requires_three_uninterrupted_number_reads():
    assert assess("consecutive", frame(0), frame(1, None), frame(2),
                  frame(3, None), frame(4), frame(5), frame(6)) == [
        False, False, False, False, False, False, True,
    ]


def test_three_of_five_allows_visible_vehicle_with_unreadable_number():
    assert assess("three_of_five", frame(0), frame(1, None), frame(2),
                  frame(3, None), frame(4)) == [False, False, False, False, True]


def test_three_of_five_expires_evidence_outside_the_window():
    assert assess("three_of_five", frame(0), frame(1, None), frame(2),
                  frame(3, None), frame(4, None), frame(5)) == [False] * 6


@pytest.fixture
def d68_runtime(tmp_path):
    from contextlib import closing
    import sqlite3

    from fastapi.testclient import TestClient

    from backend.app.runtime import create_runtime_app

    database = tmp_path / "d68.sqlite3"
    at = [AT]
    token = "d68-synthetic-test-token"
    headers = {"Authorization": f"Bearer {token}"}

    def with_clients():
        return TestClient(
            create_runtime_app(token=token, mode="real", database=database, clock=lambda: at[0])
        )

    def send(client, event, *, arrival=None):
        at[0] = max(at[0], arrival or event.observed_at)
        return client.post("/api/v1/internal/vision-events", headers=headers,
                           json=event.model_dump(mode="json"))

    def read(sql):
        with closing(sqlite3.connect(database)) as conn:
            return conn.execute(sql).fetchall()

    return with_clients, send, read


def start_unknown(client, send):
    for index in range(3):
        assert send(client, frame(index, None)).status_code == 202


def test_d68_third_distinct_frame_links_only_existing_session(d68_runtime):
    clients, send, read = d68_runtime
    with clients() as client:
        start_unknown(client, send)
        original = read("SELECT session_id,entry_at,car_id FROM parking_sessions")[0]
        fee_url = f"/api/v1/sessions/{original[0]}/fee"
        quote = client.get(fee_url)
        assert quote.status_code == 200
        payment = client.post("/api/v1/mock-payments", json={
            "session_id": original[0], "quote_id": quote.json()["quote_id"],
            "idempotency_key": "d68-payment", "payment_method": "MOCK_CARD",
        })
        assert payment.status_code == 200
        history = read("SELECT * FROM fee_quotes"), read("SELECT * FROM mock_payments")
        first = frame(3)
        assert send(client, first).status_code == 202
        assert send(client, first).status_code == 200
        assert send(client, first.model_copy(update={"car_id": "11"})).status_code == 409
        for item in (frame(4, frame_id="frame-3"), frame(5, None), frame(6)):
            assert send(client, item).status_code == 202
            assert read("SELECT session_id,entry_at,car_id FROM parking_sessions") == [original]
        assert send(client, frame(7, confidence=0.8)).status_code == 202
        assert read("SELECT session_id,entry_at,car_id FROM parking_sessions") == [
            (original[0], original[1], "10")
        ]
        assert (read("SELECT * FROM fee_quotes"), read("SELECT * FROM mock_payments")) == history
        # Binding identity does not bypass the existing occupancy stability interval.
        assert client.get("/api/v1/vehicles/10/location").status_code == 404
        assert send(client, frame(8)).status_code == 202
        assert client.get("/api/v1/vehicles/10/location").json()["session_id"] == original[0]


@pytest.mark.parametrize(("middle", "last_two", "third"), [
    ([frame(5, "11")], [frame(6), frame(7)], frame(8)),
    ([frame(5, None, event_type="space_empty_observed")], [frame(6), frame(7)], frame(8)),
    ([frame(5, None, confidence=0.79)], [frame(6), frame(7)], frame(8)),
    ([], [frame(7), frame(8)], frame(9)),
    ([frame(i, None) for i in range(5, 14)], [frame(14), frame(15)], frame(16)),
])
def test_d68_resets_number_evidence(d68_runtime, middle, last_two, third):
    clients, send, read = d68_runtime
    with clients() as client:
        start_unknown(client, send)
        for item in [frame(3), frame(4), *middle, *last_two]:
            assert send(client, item).status_code == 202
            assert read("SELECT car_id FROM parking_sessions") == [(None,)]
        assert send(client, third).status_code == 202
        assert read("SELECT car_id FROM parking_sessions") == [("10",)]


def test_d68_stale_reverse_and_other_camera_do_not_vote(d68_runtime):
    clients, send, read = d68_runtime
    with clients() as client:
        start_unknown(client, send)
        assert send(client, frame(3), arrival=AT + timedelta(seconds=14)).status_code == 202
        for item in (frame(14), frame(13), frame(15, camera_id="camera_b"), frame(16), frame(17)):
            assert send(client, item).status_code == 202
            assert read("SELECT car_id FROM parking_sessions") == [(None,)]
        assert send(client, frame(18)).status_code == 202
        assert read("SELECT car_id FROM parking_sessions") == [("10",)]


def test_d68_restart_discards_votes_and_preserves_database(d68_runtime):
    clients, send, read = d68_runtime
    with clients() as client:
        start_unknown(client, send)
        for item in (frame(3), frame(4)):
            assert send(client, item).status_code == 202
        stored = read("SELECT * FROM parking_sessions")
        journal = read("SELECT event_id,applied FROM pipeline_events")
    with clients() as client:
        assert read("SELECT * FROM parking_sessions") == stored
        assert read("SELECT event_id,applied FROM pipeline_events") == journal
        assert send(client, frame(4)).status_code == 200
        for item in (frame(5, frame_id="frame-4"), frame(6), frame(7)):
            assert send(client, item).status_code == 202
            assert read("SELECT * FROM parking_sessions") == stored
        assert send(client, frame(8)).status_code == 202
        bound = read("SELECT session_id,entry_at,car_id FROM parking_sessions")
    with clients() as client:
        assert read("SELECT session_id,entry_at,car_id FROM parking_sessions") == bound
        assert bound[0][2] == "10"


def test_d68_duplicate_number_and_known_identity_conflicts_remain_409(d68_runtime):
    clients, send, read = d68_runtime
    with clients() as client:
        start_unknown(client, send)
        for index in range(3, 6):
            item = frame(index, "10").model_copy(update={"space_id": "A-02"})
            assert send(client, item).status_code == 202
        before = read("SELECT * FROM parking_sessions ORDER BY space_id")
        for index in (6, 7):
            assert send(client, frame(index)).status_code == 202
        assert send(client, frame(8)).status_code == 409
        for index in (9, 10):
            item = frame(index, "11").model_copy(update={"space_id": "A-02"})
            assert send(client, item).status_code == 202
        item = frame(11, "11").model_copy(update={"space_id": "A-02"})
        assert send(client, item).status_code == 409
        assert read("SELECT * FROM parking_sessions ORDER BY space_id") == before



@pytest.mark.parametrize("event_type", ["vehicle_observed", "space_empty_observed"])
def test_d68_future_frame_does_not_block_normal_observations(d68_runtime, event_type):
    clients, send, read = d68_runtime
    with clients() as client:
        start_unknown(client, send)
        assert send(client, frame(3)).status_code == 202
        original = read("SELECT session_id,entry_at,car_id FROM parking_sessions")
        spaces_url = "/api/v1/parking-lots/A/spaces"

        def bay():
            return next(row for row in client.get(spaces_url).json()["items"]
                        if row["space_id"] == "A-01")

        before = bay()
        future = frame(6, "11" if event_type == "vehicle_observed" else None,
                       event_type=event_type)
        assert send(client, future, arrival=AT + timedelta(seconds=3)).status_code == 422
        assert bay() == before
        assert read("SELECT event_id FROM pipeline_events WHERE event_id = 'event-6'") == []

        assert send(client, frame(4)).status_code == 202
        assert read("SELECT session_id,entry_at,car_id FROM parking_sessions") == original
        assert send(client, frame(5)).status_code == 202
        assert read("SELECT session_id,entry_at,car_id FROM parking_sessions") == [
            (original[0][0], original[0][1], "10")
        ]


def test_d68_tolerates_small_clock_skew_without_blocking_normal_frames(d68_runtime):
    clients, send, read = d68_runtime
    with clients() as client:
        start_unknown(client, send)
        future = frame(4).model_copy(update={"observed_at": AT + timedelta(seconds=4.5)})
        arrival = AT + timedelta(seconds=3)
        assert send(client, future, arrival=arrival).status_code == 202
        assert send(client, future, arrival=arrival).status_code == 200
        assert send(client, future.model_copy(update={"car_id": "11"}),
                    arrival=arrival).status_code == 409
        stored_time = read("SELECT json_extract(payload, '$.observed_at') FROM pipeline_events "
                           "WHERE event_id = 'event-4'")[0][0]
        assert datetime.fromisoformat(stored_time) == arrival

        normal = frame(5).model_copy(update={"observed_at": AT + timedelta(seconds=4)})
        assert send(client, normal).status_code == 202
        assert read("SELECT car_id FROM parking_sessions") == [(None,)]
        assert send(client, frame(6).model_copy(
            update={"observed_at": AT + timedelta(seconds=5)}
        )).status_code == 202
        bound = read("SELECT session_id, entry_at, car_id FROM parking_sessions")
        assert len(bound) == 1 and bound[0][2] == "10"
        assert client.get("/api/v1/vehicles/10/location").json()["session_id"] == bound[0][0]
    with clients() as client:
        assert send(client, future).status_code == 200
        assert read("SELECT session_id, entry_at, car_id FROM parking_sessions") == bound
        assert client.get("/api/v1/vehicles/10/location").json()["session_id"] == bound[0][0]


def test_d68_accepts_exact_two_second_clock_skew(d68_runtime):
    clients, send, read = d68_runtime
    with clients() as client:
        start_unknown(client, send)
        event = frame(5)
        assert send(client, event, arrival=AT + timedelta(seconds=3)).status_code == 202
        assert read("SELECT count(*) FROM pipeline_events WHERE event_id = 'event-5'") == [(1,)]
