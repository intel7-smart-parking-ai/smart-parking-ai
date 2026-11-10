"""C·D 시뮬레이터 반복 관측이 journal·접수함에 쌓이지 않는지 (11/10 uncertain 깜빡임·100만 한도 문제).

- 같은 상태로 확정된 칸의 반복 관측은 상태만 갱신하고 pipeline_events·접수함에 남기지 않는다
- 상태를 바꾸는 관측(후보 시작·확정)은 그대로 기록한다
- 재시작 때 예전 버전이 쌓은 시뮬레이션 관측은 칸마다 마지막 10초만 남기고, 카메라 관측은 그대로 둔다
"""
from contextlib import closing
from datetime import datetime, timedelta, timezone
from time import monotonic, sleep
from uuid import uuid4

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
