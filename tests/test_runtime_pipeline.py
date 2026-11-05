from contextlib import closing
from datetime import datetime, timedelta, timezone
from fastapi.testclient import TestClient
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
        assert c.post('/api/v1/simulation/D/entry',json={'space_id':'D-01'}).status_code==409
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
