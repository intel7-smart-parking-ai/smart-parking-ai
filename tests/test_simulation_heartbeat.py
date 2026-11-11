"""C·D 시뮬레이터 반복 관측이 journal·접수함에 쌓이지 않는지 (11/10 uncertain 깜빡임·100만 한도 문제).

- 같은 상태로 확정된 칸의 반복 관측은 상태만 갱신하고 pipeline_events·접수함에 남기지 않는다
- 상태를 바꾸는 관측(후보 시작·확정)은 그대로 기록한다
- 재시작 때 예전 버전이 쌓은 시뮬레이션 관측은 칸마다 마지막 10초만 남기고, 카메라 관측은 그대로 둔다
"""
from contextlib import closing
from datetime import datetime, timedelta, timezone
from time import monotonic, sleep
from uuid import uuid4

import pytest

from fastapi.testclient import TestClient

from backend.app.api.vision_event_schema import VisionEvent
from backend.app.api.vision_event_store import canonical_payload
from backend.app.runtime import create_runtime_app

TOKEN = "simulation-heartbeat-token"
BAYS = ("A-07", "A-08", "B-13")


def _app(database):
    return create_runtime_app(token=TOKEN, database=database, mode="demo")


def _status(client, lot="C"):
    return {s["space_id"]: s["status"] for s in client.get(f"/api/v1/parking-lots/{lot}/spaces").json()["items"]}


def _sim_rows(conn, space_id):
    return conn.execute(
        "SELECT count(*) FROM pipeline_events WHERE json_extract(payload,'$.source')='simulation' "
        "AND json_extract(payload,'$.lot_id')='C' AND json_extract(payload,'$.space_id')=?",
        (space_id,),
    ).fetchone()[0]


def test_steady_bays_are_not_journaled_every_second(tmp_path):
    app = _app(tmp_path / "heartbeat.db")
    with TestClient(app) as client, closing(app.state.pipeline.connection()) as conn:
        conn.executemany("UPDATE simulation_targets SET occupied=1 WHERE lot_id='C' AND space_id=?",
                         [(b,) for b in BAYS])
        conn.commit()
        deadline = monotonic() + 15
        while monotonic() < deadline and any(_status(client)[b] != "occupied" for b in BAYS):
            sleep(.2)
        assert all(_status(client)[b] == "occupied" for b in BAYS)
        recorded = {b: _sim_rows(conn, b) for b in BAYS}
        accepted = len(app.state.pipeline)
        sleep(4)  # 약 4회 더 반복 관측
        status = _status(client)
        assert all(status[b] == "occupied" for b in BAYS), status
        assert {b: _sim_rows(conn, b) for b in BAYS} == recorded
        assert len(app.state.pipeline) == accepted
        # 상태를 만든 관측(후보 시작·확정)은 남아 있다
        assert all(1 <= n <= 4 for n in recorded.values()), recorded


def test_restart_prunes_old_simulation_journal_but_keeps_camera_events(tmp_path):
    database = tmp_path / "prune.db"
    app = _app(database)
    with TestClient(app), closing(app.state.pipeline.connection()) as conn:
        now = datetime.now(timezone.utc)
        rows = []
        for second in range(60, -1, -1):
            event = VisionEvent(event_id=uuid4().hex, event_type="vehicle_observed", source="simulation",
                                lot_id="C", camera_id=None, space_id="A-09", car_id=None, vehicle_type="general",
                                confidence=.99, bbox_xyxy=None, observed_at=now - timedelta(seconds=second))
            rows.append((event.event_id, event.model_dump_json(), canonical_payload(event)))
        camera = VisionEvent(event_id="camera-keep-1", event_type="vehicle_observed", source="vision",
                             lot_id="A", camera_id="camera_a", space_id="A-01", car_id=None, vehicle_type=None,
                             confidence=.95, bbox_xyxy=[1, 1, 5, 5], observed_at=now - timedelta(minutes=5))
        rows.append((camera.event_id, camera.model_dump_json(), canonical_payload(camera)))
        conn.executemany("INSERT INTO pipeline_events(event_id,payload,canonical,applied) VALUES(?,?,?,1)", rows)
        conn.commit()

    restarted = _app(database)
    with TestClient(restarted), closing(restarted.state.pipeline.connection()) as conn:
        kept = _sim_rows(conn, "A-09")
        assert 1 <= kept <= 12, kept  # 마지막 10초 구간만
        assert conn.execute("SELECT count(*) FROM pipeline_events WHERE event_id='camera-keep-1'").fetchone()[0] == 1


def test_batched_heartbeats_preserve_receipts_and_roll_back_together(tmp_path, monkeypatch):
    import sqlite3

    import pytest

    at = [datetime.now(timezone.utc)]
    app = create_runtime_app(token=TOKEN, database=tmp_path / "batch.db",
                             mode="real", clock=lambda: at[0])
    store = app.state.pipeline

    def round_events():
        return [VisionEvent(event_id=uuid4().hex, event_type="space_empty_observed",
                            source="simulation", lot_id="C", camera_id=None, space_id=bay,
                            car_id=None, confidence=.99, bbox_xyxy=None, observed_at=at[0])
                for bay in BAYS]

    with TestClient(app) as client, closing(store.connection()) as conn:
        for _ in range(4):
            for event in round_events():
                assert store.receive(event).value == "accepted"
            at[0] += timedelta(seconds=1)
        previous = store.spaces("C", as_of=at[0])
        journal = conn.execute("SELECT count(*) FROM pipeline_events").fetchone()[0]
        receipts = conn.execute("SELECT count(*) FROM vision_event_receipts").fetchone()[0]
        batch = round_events()
        save = store._save_receipt

        def fail_second(connection, event, *args):
            if event.event_id == batch[1].event_id:
                raise sqlite3.OperationalError("synthetic batch failure")
            save(connection, event, *args)

        with monkeypatch.context() as patch:
            patch.setattr(store, "_save_receipt", fail_second)
            with pytest.raises(sqlite3.OperationalError, match="synthetic batch failure"):
                store.receive_simulation_heartbeats(batch)
        assert store.spaces("C", as_of=at[0]) == previous
        assert conn.execute("SELECT count(*) FROM vision_event_receipts").fetchone()[0] == receipts

        assert store.receive_simulation_heartbeats(batch)
        assert conn.execute("SELECT count(*) FROM pipeline_events").fetchone()[0] == journal
        assert conn.execute("SELECT count(*) FROM vision_event_receipts").fetchone()[0] == receipts + len(batch)
        assert all(row["observed_at"] == at[0] for row in store.spaces("C", as_of=at[0])
                   if row["space_id"] in BAYS)

        url = "/api/v1/internal/vision-events"
        headers = {"Authorization": f"Bearer {TOKEN}"}
        for event in batch:
            assert client.post(url, headers=headers, json=event.model_dump(mode="json")).status_code == 200
            conflicting = event.model_copy(update={"confidence": .98})
            assert client.post(url, headers=headers,
                               json=conflicting.model_dump(mode="json")).status_code == 409

        at[0] += timedelta(seconds=1)
        fresh = round_events()
        with conn:
            pending = fresh[0]
            conn.execute("INSERT INTO pipeline_events(event_id,payload,canonical) VALUES(?,?,?)",
                         (pending.event_id, pending.model_dump_json(), canonical_payload(pending)))
        assert not store.receive_simulation_heartbeats(fresh)
        assert conn.execute("SELECT count(*) FROM vision_event_receipts").fetchone()[0] == receipts + len(batch)


@pytest.mark.parametrize("failures", [1, 3])
def test_observation_task_recovers_and_cancels(tmp_path, monkeypatch, caplog, failures):
    app = _app(tmp_path / "observation-errors.db")
    store = app.state.pipeline
    original_spaces = store.spaces
    attempts = 0

    def flaky_spaces(lot, **kwargs):
        nonlocal attempts
        if lot == "C" and attempts < failures:
            attempts += 1
            raise RuntimeError(f"synthetic observation failure {attempts}")
        return original_spaces(lot, **kwargs)

    monkeypatch.setattr(store, "spaces", flaky_spaces)
    with TestClient(app), closing(store.connection()) as conn:
        conn.execute("UPDATE simulation_targets SET occupied=1 WHERE lot_id IN ('C', 'D') AND space_id='A-01'")
        conn.commit()
        deadline = monotonic() + failures + 5
        while monotonic() < deadline:
            lots = {r[0] for r in conn.execute(
                "SELECT DISTINCT json_extract(payload,'$.lot_id') FROM pipeline_events"
            )}
            if {"C", "D"} <= lots:
                break
            sleep(0.05)
        assert {"C", "D"} <= lots
        task = app.state.simulation_observation_task
        assert not task.done()
        assert app.state.simulation_observation_last_error == f"synthetic observation failure {failures}"
        assert caplog.text.count("Simulation observation cycle failed") == failures

    assert task.done() and task.cancelled()
