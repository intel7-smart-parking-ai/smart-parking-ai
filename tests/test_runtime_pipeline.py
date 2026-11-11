from contextlib import closing
from datetime import datetime, timedelta, timezone
import sqlite3

import pytest
from fastapi.testclient import TestClient

from backend.app.billing.db import SCHEMA as BILLING_SCHEMA
from backend.app.database.parking_sessions import enter, exit_session, identify, move_sessions
from backend.app.runtime import create_runtime_app

TOKEN='pipeline-test-token'
HEADERS={'Authorization':'Bearer '+TOKEN}

def observation(at,index,lot='A',space='A-01',kind='vehicle_observed',car='10'):
    return dict(event_id='event-'+str(index),event_type=kind,source='simulation',lot_id=lot,camera_id=None,space_id=space,car_id=car if kind=='vehicle_observed' else None,confidence=.99,bbox_xyxy=None,observed_at=at.isoformat())

def test_receiver_sessions_led_payment_stale_and_restart(tmp_path):
    at=[datetime.now(timezone.utc)]
    database=tmp_path/'one.sqlite3'
    def app():return create_runtime_app(token=TOKEN,database=database,mode='real',clock=lambda:at[0])
    events=[]
    with TestClient(app()) as client:
        event=observation(at[0],0)
        assert client.post('/api/v1/internal/vision-events',json=event).status_code==403
        for i in range(3):
            event=observation(at[0],i);events.append(event)
            assert client.post('/api/v1/internal/vision-events',headers=HEADERS,json=event).status_code==202
            at[0]+=timedelta(seconds=1)
        assert client.post('/api/v1/internal/vision-events',headers=HEADERS,json=event).status_code==200
        assert client.post('/api/v1/internal/vision-events',headers=HEADERS,json=event|{'car_id':'11'}).status_code==409
        rows=client.get('/api/v1/parking-lots/A/spaces').json()['items']
        row=next(r for r in rows if r['space_id']=='A-01')
        assert row['status']=='occupied' and row['entry_at'] and row['is_mock']
        sid=client.get('/api/v1/vehicles/10/location').json()['session_id']
        assert client.get('/api/v1/parking-lots/B/spaces').json()['items'][0]['status']=='empty'
        d=client.get('/api/v1/parking-lots/D/spaces').json()['items']
        assert len(d)==64 and {r['space_id'][0] for r in d}==set('ABCD')
        fee=client.get(f'/api/v1/sessions/{sid}/fee').json()
        led=observation(at[0],50,kind='mock_fire_led_observed',space=None)
        assert client.post('/api/v1/internal/vision-events',headers=HEADERS,json=led).status_code==202
        alerts=client.get('/api/v1/alerts').json()['items']
        assert len(alerts)==1 and alerts[0]['level']==2
        req=dict(session_id=sid,quote_id=fee['quote_id'],idempotency_key='once',payment_method='MOCK_CARD')
        assert client.post('/api/v1/mock-payments',json=req).json()['error']['code']=='EVACUATION_ACTIVE'
        aid=alerts[0]['alert_id']
        assert client.post(f'/api/v1/alerts/{aid}/resolve').status_code==200
        payment=client.post('/api/v1/mock-payments',json=req)
        assert payment.status_code==200,payment.text
        pid=payment.json()['payment_id']
        assert client.get(f'/api/v1/sessions/{sid}/fee').json()['payment']['payment_id']==pid
        at[0]+=timedelta(seconds=11)
        rows=client.get('/api/v1/parking-lots/A/spaces').json()['items']
        assert rows[0]['status']=='uncertain'
        assert len(client.get('/api/v1/sessions?status=active').json()['items'])==1
    with TestClient(app()) as client:
        assert client.get(f'/api/v1/sessions/{sid}/fee').json()['payment']['payment_id']==pid
        assert client.post('/api/v1/internal/vision-events',headers=HEADERS,json=events[-1]).status_code==200
        assert client.post('/api/v1/internal/vision-events',headers=HEADERS,json=events[-1]|{'car_id':'11'}).status_code==409
        for i in range(4):
            event=observation(at[0],100+i,kind='space_empty_observed');at[0]+=timedelta(seconds=1)
            assert client.post('/api/v1/internal/vision-events',headers=HEADERS,json=event).status_code==202
        assert client.get('/api/v1/sessions?status=active').json()['items']==[]
        assert client.get('/api/v1/sessions?status=closed').json()['items'][0]['session_id']==sid

def test_virtual_observations_use_authenticated_receiver(tmp_path):
    app=create_runtime_app(token=TOKEN,database=tmp_path/'virtual.sqlite3',mode='demo')
    with TestClient(app) as c:
        entered=c.post('/api/v1/simulation/C/entry',json={'vehicle_number':'7654'})
        assert entered.status_code==200,(entered.text,c.get('/api/v1/parking-lots/C/spaces').text,c.get('/api/v1/simulation/C').text)
        result=c.get('/api/v1/vehicle-search?vehicle_number=7654').json()
        assert result['car_id'] is None and result['vehicle_number']=='7654'
        assert result['session_id']
        with closing(app.state.pipeline.connection()) as conn:
            assert conn.execute("SELECT count(*) FROM pipeline_events WHERE payload LIKE '%simulation%'").fetchone()[0]>=3
            assert conn.execute("SELECT car_id FROM parking_sessions WHERE session_id=?",(result['session_id'],)).fetchone()[0] is None
        left=c.post('/api/v1/simulation/C/exit',json={'space_id':result['space_id']})
        assert left.status_code==200,left.text
        assert c.get('/api/v1/vehicle-search?vehicle_number=7654').status_code==404


def test_journal_failure_retries_and_recovers(tmp_path,monkeypatch):
    import sqlite3
    import pytest
    from backend.app.api.vision_event_schema import VisionEvent
    from backend.app.api.vision_event_store import Receipt
    at=datetime.now(timezone.utc)
    path=tmp_path/'retry.sqlite3'
    app=create_runtime_app(token=TOKEN,database=path,mode='real',clock=lambda:at)
    with TestClient(app):
        store=app.state.pipeline
        original=store.connection
        failed=[False]
        class FailJournal(sqlite3.Connection):
            def execute(self,sql,parameters=()):
                if sql.startswith('INSERT INTO pipeline_events') and not failed[0]:
                    failed[0]=True
                    raise sqlite3.OperationalError('injected journal failure')
                return super().execute(sql,parameters)
        def connection():
            conn=sqlite3.connect(path,check_same_thread=False,factory=FailJournal)
            conn.row_factory=sqlite3.Row
            return conn
        event=VisionEvent(**observation(at,0))
        with monkeypatch.context() as patch:
            patch.setattr(store,'connection',connection)
            with pytest.raises(sqlite3.OperationalError):store.receive(event)
        assert store.receive(event) is Receipt.ACCEPTED
    with TestClient(create_runtime_app(token=TOKEN,database=path,mode='real',clock=lambda:at)) as client:
        assert client.post('/api/v1/internal/vision-events',headers=HEADERS,json=event.model_dump(mode='json')).status_code==200


def test_new_event_recovers_pending_exit_after_temporary_db_failure(tmp_path, monkeypatch):
    now = [datetime(2026, 11, 10, tzinfo=timezone.utc)]
    database = tmp_path / "pending-exit.sqlite3"
    app = create_runtime_app(token=TOKEN, database=database, mode="real", clock=lambda: now[0])

    with TestClient(app) as client:
        store = app.state.pipeline

        def send(event):
            return client.post("/api/v1/internal/vision-events", headers=HEADERS, json=event)

        def journal(event):
            with closing(store.connection()) as conn:
                return conn.execute(
                    "SELECT applied FROM pipeline_events WHERE event_id=?", (event["event_id"],)
                ).fetchone()

        start = now[0]
        for index in range(3):
            now[0] = start + timedelta(seconds=index)
            assert send(observation(now[0], index)).status_code == 202
        sid = client.get("/api/v1/vehicles/10/location").json()["session_id"]
        fee = client.get(f"/api/v1/sessions/{sid}/fee")
        assert fee.status_code == 200
        payment = dict(session_id=sid, quote_id=fee.json()["quote_id"],
                       idempotency_key="pending-exit-payment", payment_method="MOCK_CARD")
        paid = client.post("/api/v1/mock-payments", json=payment)
        assert paid.status_code == 200

        for index in range(3, 6):
            now[0] = start + timedelta(seconds=index)
            assert send(observation(now[0], index, kind="space_empty_observed")).status_code == 202

        def fail_exit(*args, **kwargs):
            raise sqlite3.OperationalError("synthetic exit write failure")

        now[0] = start + timedelta(seconds=6)
        pending = observation(now[0], 6, kind="space_empty_observed")
        with monkeypatch.context() as patch:
            patch.setattr(store, "session_exit", fail_exit)
            assert send(pending).status_code == 503
            now[0] += timedelta(seconds=1)
            next_event = observation(now[0], 7)
            assert send(next_event).status_code == 503
            assert journal(pending)["applied"] == 0
            assert journal(next_event) is None

        assert send(next_event).status_code == 202
        assert journal(pending)["applied"] == journal(next_event)["applied"] == 1
        assert send(pending).status_code == send(next_event).status_code == 200
        assert send(next_event | {"car_id": "11"}).status_code == 409
        for index in range(8, 10):
            now[0] = start + timedelta(seconds=index)
            assert send(observation(now[0], index)).status_code == 202

        repeated = client.post("/api/v1/mock-payments", json=payment)
        assert repeated.status_code == 200
        assert repeated.json()["payment_id"] == paid.json()["payment_id"]
        with closing(store.connection()) as conn:
            sessions = conn.execute(
                "SELECT session_id, status, exit_at FROM parking_sessions ORDER BY entry_at"
            ).fetchall()
            assert len(sessions) == 2
            assert tuple(sessions[0]) == (sid, "closed", pending["observed_at"])
            assert sessions[1]["session_id"] != sid
            assert sessions[1]["status"] == "active"
            assert conn.execute("SELECT count(*) FROM mock_payments").fetchone()[0] == 1
            assert conn.execute(
                "SELECT count(*) FROM pipeline_events WHERE applied=0"
            ).fetchone()[0] == 0


@pytest.mark.parametrize(("code", "status"), [
    ("ACTIVE_SESSION_EXISTS", 409),
    ("SESSION_MOVE_CONFLICT", 409),
    ("같은 칸에 활성 세션이 중복되어 이동할 수 없습니다.", 409),
    ("SESSION_NOT_ACTIVE", 409),
    ("SESSION_NOT_FOUND", 404),
], ids=["active-car", "move", "duplicate-bay", "inactive", "missing"])
def test_permanent_replay_conflict_is_quarantined_without_blocking_new_events(
    tmp_path, monkeypatch, code, status,
):
    now = [datetime(2026, 11, 10, tzinfo=timezone.utc)]
    start = now[0]
    database = tmp_path / "pending-conflict.sqlite3"

    def app():
        return create_runtime_app(token=TOKEN, database=database, mode="real", clock=lambda: now[0])

    def send(client, event):
        return client.post("/api/v1/internal/vision-events", headers=HEADERS, json=event)

    with TestClient(app()) as client:
        store = client.app.state.pipeline
        for index in range(5):
            now[0] = start + timedelta(seconds=index)
            space = "A-01" if index < 3 else "A-02"
            assert send(client, observation(now[0], index, space=space)).status_code == 202
        sid = client.get("/api/v1/vehicles/10/location").json()["session_id"]

        fee = client.get(f"/api/v1/sessions/{sid}/fee")
        assert fee.status_code == 200
        assert client.post("/api/v1/mock-payments", json={
            "session_id": sid, "quote_id": fee.json()["quote_id"],
            "idempotency_key": "quarantine-payment", "payment_method": "MOCK_CARD",
        }).status_code == 200

        def records():
            with closing(store.connection()) as conn:
                return [
                    [tuple(row) for row in conn.execute(f"SELECT * FROM {table} ORDER BY 1")]
                    for table in ("parking_sessions", "fee_quotes", "mock_payments", "session_moves")
                ]

        if code.startswith("같은 칸"):
            with closing(store.connection()) as conn:
                enter(conn, session_id="duplicate-bay-session", lot_id="A", space_id="A-01",
                      car_id="12", entry_at=start - timedelta(seconds=1))
        before = records()

        def fail_entry(*args, **kwargs):
            raise sqlite3.OperationalError("synthetic temporary entry failure")

        def replay_entry(conn, **kwargs):
            if code == "SESSION_MOVE_CONFLICT":
                return move_sessions(
                    conn, lot_id="A", moves=[(sid, "A-03", "A-02")], observed_at=now[0],
                    save_snapshots=lambda: pytest.fail("Conflicting move reached snapshot write"),
                )
            if code == "SESSION_NOT_ACTIVE":
                return identify(conn, session_id="missing-session", car_id="10")
            if code == "SESSION_NOT_FOUND":
                return exit_session(conn, session_id="missing-session", exit_at=now[0])
            if code.startswith("같은 칸"):
                store._apply_camera_move(conn, {
                    "lot_id": "A", "space_id": "A-02", "source": "vision",
                    "move_evidence": ("10", (now[0],)),
                }, None)
                pytest.fail("Expected duplicate bay conflict")
            return enter(conn, **kwargs)

        now[0] = start + timedelta(seconds=5)
        pending = observation(now[0], 5, space="A-02")
        with monkeypatch.context() as patch:
            patch.setattr(store, "session_enter", fail_entry)
            assert send(client, pending).status_code == 503

        # Retrying the pending entry now conflicts with the existing session in A-01.
        now[0] = start + timedelta(seconds=6)
        new_event = observation(now[0], 6, space="A-02", kind="space_empty_observed")
        with monkeypatch.context() as patch:
            patch.setattr(store, "session_enter", replay_entry)
            assert send(client, new_event).status_code == 202
        assert records() == before
        with closing(store.connection()) as conn:
            assert conn.execute("SELECT count(*) FROM pipeline_events WHERE applied=0").fetchone()[0] == 0
            assert conn.execute(
                "SELECT applied FROM pipeline_events WHERE event_id=?", (pending["event_id"],)
            ).fetchone()[0] == -1
            rejection = conn.execute(
                "SELECT status_code, detail FROM pipeline_event_rejections WHERE journal_key=?",
                (pending["event_id"],),
            ).fetchone()
            assert rejection["status_code"] == status
            assert code in rejection["detail"]

    with TestClient(app()) as client:
        assert client.get("/api/v1/vehicles/10/location").json()["session_id"] == sid
        assert records() == before
        assert send(client, pending).status_code == 200
        assert send(client, pending | {"car_id": "11"}).status_code == 409
        now[0] = start + timedelta(seconds=606)
        assert send(client, pending).status_code == 202
        assert records() == before


def test_confirming_event_recovers_after_side_effect_before_applied_flag(tmp_path,monkeypatch):
    import sqlite3
    import pytest
    from backend.app.api.vision_event_schema import VisionEvent
    at=[datetime.now(timezone.utc)]
    path=tmp_path/'confirm.sqlite3'
    app=create_runtime_app(token=TOKEN,database=path,mode='real',clock=lambda:at[0])
    with TestClient(app) as c:
        for i in range(2):
            assert c.post('/api/v1/internal/vision-events',headers=HEADERS,json=observation(at[0],i)).status_code==202
            at[0]+=timedelta(seconds=1)
        class FailApplied(sqlite3.Connection):
            def execute(self,sql,parameters=()):
                if sql.startswith('UPDATE pipeline_events SET applied=1'):
                    raise sqlite3.OperationalError('interrupted after confirmed session commit')
                return super().execute(sql,parameters)
        def connection():
            conn=sqlite3.connect(path,check_same_thread=False,factory=FailApplied)
            conn.row_factory=sqlite3.Row
            return conn
        with monkeypatch.context() as patch:
            patch.setattr(app.state.pipeline,'connection',connection)
            with pytest.raises(sqlite3.OperationalError):
                app.state.pipeline.receive(VisionEvent(**observation(at[0],2)))
        sid=c.get('/api/v1/sessions?status=active').json()['items'][0]['session_id']
    with TestClient(create_runtime_app(token=TOKEN,database=path,mode='real',clock=lambda:at[0])) as c:
        assert c.get('/api/v1/vehicles/10/location').json()['session_id']==sid
        assert len(c.get('/api/v1/sessions?status=active').json()['items'])==1
        at[0]+=timedelta(seconds=11)
        space=c.get('/api/v1/parking-lots/A/spaces').json()['items'][0]
        assert space['status']=='uncertain' and space['entry_at']


def test_simulator_rejects_dedicated_bay_and_real_mode_button(tmp_path):
    app=create_runtime_app(token=TOKEN,database=tmp_path/'dedicated.db',mode='demo')
    with TestClient(app) as c:
        assert c.post('/api/v1/simulation/D/entry',json={'space_id':'D-05'}).status_code==409
    app=create_runtime_app(token=TOKEN,database=tmp_path/'real.db',mode='real')
    with TestClient(app) as c:
        assert c.post('/api/v1/demo/alerts',json={'lot_id':'A'}).status_code==403


def test_rehearsal_led_and_entry_at_use_same_demo_database():
    from backend.app.demo_main import create_app
    at=[datetime.now(timezone.utc)]
    with TestClient(create_app(token=TOKEN,clock=lambda:at[0])) as c:
        for i in range(3):
            assert c.post('/api/v1/internal/vision-events',headers=HEADERS,json=observation(at[0],i)).status_code==202
            at[0]+=timedelta(seconds=1)
        assert c.get('/api/v1/parking-lots/A/spaces').json()['items'][0]['entry_at']
        led=observation(at[0],10,kind='mock_fire_led_observed',space=None)
        assert c.post('/api/v1/internal/vision-events',headers=HEADERS,json=led).status_code==202
        assert c.get('/api/v1/alerts').json()['items'][0]['level']==2


def test_rejected_identity_change_does_not_poison_restart(tmp_path):
    at=[datetime.now(timezone.utc)]
    path=tmp_path/'rejected.db'
    def app():return create_runtime_app(token=TOKEN,database=path,mode='real',clock=lambda:at[0])
    with TestClient(app()) as c:
        for i in range(3):
            assert c.post('/api/v1/internal/vision-events',headers=HEADERS,json=observation(at[0],i)).status_code==202
            at[0]+=timedelta(seconds=1)
        for i in range(3):
            response=c.post('/api/v1/internal/vision-events',headers=HEADERS,json=observation(at[0],10+i,car='11'))
            at[0]+=timedelta(seconds=1)
        assert response.status_code==409
    with TestClient(app()) as c:
        assert c.get('/api/v1/health').status_code==200
        assert len(c.get('/api/v1/sessions?status=active').json()['items'])==1


@pytest.mark.parametrize("initial_schema", ["new", "legacy", "current"])
def test_startup_indexes_active_sessions_without_changing_records(tmp_path, initial_schema):
    database = tmp_path / "session-index.sqlite3"
    active = ("active-1", "A", "A-01", "10", "2026-11-10T01:00:00+00:00", None)
    closed = ("closed-1", "A", "A-01", "10", "2026-11-09T01:00:00+00:00",
              "2026-11-09T02:00:00+00:00")
    payment = ("payment-1", "closed-1", 1500, "completed")
    if initial_schema != "new":
        with closing(sqlite3.connect(database)) as conn, conn:
            if initial_schema == "legacy":
                conn.execute("""
                    CREATE TABLE parking_sessions (
                        session_id TEXT PRIMARY KEY, lot_id TEXT NOT NULL, space_id TEXT,
                        car_id TEXT, entry_at TEXT NOT NULL, exit_at TEXT
                    )
                """)
            conn.executescript(BILLING_SCHEMA)
            conn.executemany("""
                INSERT INTO parking_sessions (session_id, lot_id, space_id, car_id, entry_at, exit_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (active, closed))
            if initial_schema == "current":
                conn.execute("UPDATE parking_sessions SET status = CASE WHEN exit_at IS NULL "
                             "THEN 'active' ELSE 'closed' END")
            conn.execute("""
                INSERT INTO mock_payments (payment_id, session_id, lot_id, car_id, amount_krw,
                                           payment_method, status, idempotency_key, created_at)
                VALUES ('payment-1', 'closed-1', 'A', '10', 1500,
                        'MOCK_CARD', 'completed', 'payment-key', '2026-11-09T02:01:00+00:00')
            """)

    queries = [
        ("SELECT * FROM parking_sessions WHERE lot_id=? AND space_id=? AND status='active'",
         ("A", "A-01"), "ix_parking_sessions_active_space"),
        ("SELECT 1 FROM parking_sessions WHERE car_id=? AND status='active'",
         ("10",), "ix_parking_sessions_active_car"),
    ]
    for _ in range(2):
        app = create_runtime_app(token=TOKEN, database=database, mode="real")
        with TestClient(app), closing(sqlite3.connect(database)) as conn:
            indexes = conn.execute("PRAGMA index_list(parking_sessions)").fetchall()
            for query, params, index_name in queries:
                assert [row[2:] for row in indexes if row[1] == index_name] == [(0, "c", 1)]
                plan = conn.execute("EXPLAIN QUERY PLAN " + query, params).fetchall()
                assert any(index_name in step[-1] for step in plan), plan

            sessions = conn.execute("SELECT * FROM parking_sessions ORDER BY session_id").fetchall()
            payments = conn.execute(
                "SELECT payment_id, session_id, amount_krw, status FROM mock_payments"
            ).fetchall()
            if initial_schema == "new":
                assert sessions == payments == []
            else:
                assert sessions == [active + ("active",), closed + ("closed",)]
                assert payments == [payment]
