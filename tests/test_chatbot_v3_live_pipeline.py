"""Real runtime + temporary SQLite + preview HTTP. Model decisions remain fixtures."""
from contextlib import closing, contextmanager
from datetime import datetime, timedelta, timezone
import re
from fastapi.testclient import TestClient
from backend.app.runtime import create_runtime_app
from backend.app.chatbot_v3.conversation import ConversationService
from backend.app.chatbot_v3.fixture import FixtureModel
from backend.app.chatbot_v3.knowledge import KnowledgeStore
from backend.app.chatbot_v3.tools import ParkingTools
from backend.app.chatbot_v3.preview import PreviewGateway, create_preview_app

TOKEN='chatbot-live-test-token'

@contextmanager
def preview_for(backend):
    def factory():
        return ConversationService(FixtureModel(),ParkingTools(backend),KnowledgeStore()),lambda:None
    with TestClient(create_preview_app(gateway=PreviewGateway(factory))) as client:
        config=client.get('/api/v1/chat/config').json()
        count=[0]
        def ask(text,rid=None):
            count[0]+=1
            response=client.post('/api/v1/chat',json=dict(session_id=config['session_id'],runtime_id=config['runtime_id'],request_id=rid or str(count[0]),text=text,language='ko'))
            assert response.status_code==200,response.text
            data=response.json()
            assert data['text']==data['messages'][0]['text']
            return data
        yield ask


def command(c,action,**fields):
    r=c.post('/api/v1/simulation/D/'+action,json=fields)
    assert r.status_code==200,r.text
    return r.json()


def empty_count(c):
    return sum(s['status']=='empty' for s in c.get('/api/v1/parking-lots/D/spaces').json()['items'])


def test_virtual_entry_location_quote_replay_exit_through_chat_http(tmp_path):
    app=create_runtime_app(token=TOKEN,database=tmp_path/'live.db',mode='demo')
    with TestClient(app) as backend, preview_for(backend) as ask:
        initial=empty_count(backend)
        assert f'{initial}칸' in ask('신세계 빈자리 있어?')['text']
        entry=command(backend,'entry',space_id='A-04',vehicle_number='0765',vehicle_type='general')
        assert empty_count(backend)==initial-1
        assert f'{initial-1}칸' in ask('신세계 빈자리 있어?')['text']
        location=ask('0765 차량 어디 있어?')
        assert location['meta']['status']=='ok' and 'A-04' in location['text']
        assert '모의' in location['text']
        actual=backend.get('/api/v1/vehicle-search?vehicle_number=0765').json()
        sid=actual['session_id']
        fee=ask('0765 차량 요금 알려줘','same-fee-request')
        assert fee['meta']['status']=='ok',fee
        with closing(app.state.pipeline.connection()) as conn:
            rows=conn.execute('SELECT amount_krw FROM fee_quotes WHERE session_id=?',(sid,)).fetchall()
        assert len(rows)==1 and f"{rows[0]['amount_krw']:,}원" in fee['text']
        assert ask('0765 차량 요금 알려줘','same-fee-request')==fee
        with closing(app.state.pipeline.connection()) as conn:
            assert conn.execute('SELECT count(*) FROM fee_quotes WHERE session_id=?',(sid,)).fetchone()[0]==1
        command(backend,'exit',space_id=entry['space_id'])
        assert empty_count(backend)==initial
        assert f'{initial}칸' in ask('신세계 빈자리 있어?')['text']
        missing=ask('0765 차량 어디 있어?')
        assert missing['meta']['status']=='partial'
        assert '단일 차량 위치' in missing['text'] and '미등록' not in missing['text']
        after_exit=ask('0765 차량 요금 알려줘')
        assert after_exit['meta']['status']=='partial'
        with closing(app.state.pipeline.connection()) as conn:
            assert conn.execute('SELECT count(*) FROM fee_quotes WHERE session_id=?',(sid,)).fetchone()[0]==1


def test_recommendation_occupied_stale_and_acknowledged_alarm_on_runtime(tmp_path,monkeypatch):
    import backend.app.chatbot_v3.recommendation as module
    now=[datetime.now(timezone.utc)]
    class Clock(datetime):
        @classmethod
        def now(cls,tz=None):return now[0]
    monkeypatch.setattr(module,'datetime',Clock)
    import backend.app.alerts.router as alert_module
    monkeypatch.setattr(alert_module,'datetime',Clock)
    app=create_runtime_app(token=TOKEN,database=tmp_path/'recommend.db',mode='real',clock=lambda:now[0])
    serial=[0]
    with TestClient(app) as backend, preview_for(backend) as ask:
        def observation(space,kind='space_empty_observed',car=None):
            serial[0]+=1
            event=dict(event_id=f'chat-recommend-{serial[0]}',event_type=kind,source='simulation',lot_id='D',camera_id=None,space_id=space,car_id=car,confidence=.99,bbox_xyxy=None,observed_at=now[0].isoformat())
            r=backend.post('/api/v1/internal/vision-events',headers={'Authorization':'Bearer '+TOKEN},json=event)
            assert r.status_code==202,r.text
        for _ in range(4):
            for sid in ['A-04','A-03','A-02']:observation(sid)
            now[0]+=timedelta(seconds=1)
        normal=ask('영화관 가까운 빈자리 추천해줘')
        assert normal['meta']['status']=='ok',normal
        assert 'A-04:' in normal['text'] and '가상' in normal['text']
        for _ in range(3):
            observation('A-04','vehicle_observed','23')
            observation('A-03');observation('A-02')
            now[0]+=timedelta(seconds=1)
        occupied=ask('영화관 가까운 빈자리 추천해줘')
        assert 'A-04:' not in occupied['text'] and 'A-03:' in occupied['text']
        observation(None,'mock_fire_led_observed')
        alarm=backend.get('/api/v1/alerts?lot_id=D').json()['items'][0]
        active=ask('영화관 가까운 빈자리 추천해줘')
        assert active['meta']['status']=='partial' and '모의 경보' in active['text']
        assert backend.post(f"/api/v1/alerts/{alarm['alert_id']}/acknowledge").status_code==200
        acknowledged=ask('영화관 가까운 빈자리 추천해줘')
        assert acknowledged['meta']['status']=='partial' and '추천을 중단' in acknowledged['text']
        assert backend.post(f"/api/v1/alerts/{alarm['alert_id']}/resolve").status_code==200
        assert 'A-03:' in ask('영화관 가까운 빈자리 추천해줘')['text']
        now[0]+=timedelta(seconds=11)
        stale=ask('영화관 가까운 빈자리 추천해줘')
        assert '추천할 수 있는 빈자리를 확인하지 못했습니다' in stale['text']
        assert not re.search(r'A-0[234]:',stale['text'])
