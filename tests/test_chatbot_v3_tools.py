"""Recovery regression tests: real runtime contracts and conservative failures."""
from datetime import datetime, timedelta, timezone
from contextlib import closing
import json
from pathlib import Path
import httpx
import pytest
from fastapi.testclient import TestClient
from backend.app.runtime import create_runtime_app
from backend.app.chatbot_v3.tools import ParkingTools
from backend.app.chatbot_v3.knowledge import KnowledgeStore
from backend.app.chatbot_v3.recommendation import recommend_spaces


def test_runtime_location_fee_and_receipt(tmp_path):
    now=[datetime.now(timezone.utc)]
    app=create_runtime_app(token='v3-test-token-123456', database=tmp_path/'runtime.db', mode='real', clock=lambda:now[0])
    with TestClient(app) as c:
        tools=ParkingTools(c)
        for i in range(3):
            event=dict(event_id=f'v3-{i}',event_type='vehicle_observed',source='simulation',lot_id='A',camera_id=None,space_id='A-01',car_id='10',confidence=.99,bbox_xyxy=None,observed_at=now[0].isoformat())
            assert c.post('/api/v1/internal/vision-events',headers={'Authorization':'Bearer v3-test-token-123456'},json=event).status_code==202
            now[0]+=timedelta(seconds=1)
        assert sum(r['total_spaces'] for r in tools.list_lots().data['items'])==156
        assert tools.list_spaces('A').status=='ok'
        assert tools.list_rate_policies().status=='ok'
        assert tools.list_alerts('A').data['no_active_alerts_confirmed']
        location=tools.find_vehicle('sticker','10')
        assert location.status=='ok'
        sid=location.data['session_id']
        fee=tools.get_fee(sid,'r1')
        assert fee.status=='ok',fee
        assert tools.get_fee(sid,'r1').data['quote_id']==fee.data['quote_id']
        assert tools.get_fee('different','r1').status=='conflict'
        with closing(app.state.pipeline.connection()) as conn:
            assert conn.execute('SELECT count(*) FROM fee_quotes WHERE session_id=?',(sid,)).fetchone()[0]==1
        paid=c.post('/api/v1/mock-payments',json=dict(session_id=sid,quote_id=fee.data['quote_id'],idempotency_key='v3pay',payment_method='MOCK_CARD'))
        assert paid.status_code==200,paid.text
        receipt=tools.get_fee(sid,'r2')
        assert receipt.status=='ok',receipt
        assert receipt.data['amount_krw']==receipt.data['payment']['amount_krw']


def test_runtime_virtual_lookup(tmp_path):
    with TestClient(create_runtime_app(token='v3-test-token-123456', database=tmp_path/'virtual.db', mode='demo')) as c:
        assert c.post('/api/v1/simulation/C/entry',json={'vehicle_number':'0765'}).status_code==200
        result=ParkingTools(c).find_vehicle('plate_suffix','0765')
        assert result.status=='ok',result
        assert result.data['vehicle_number']=='0765'
        assert result.data['lot_id']=='C'


@pytest.mark.parametrize('code,expected',[(401,'unavailable'),(403,'unavailable'),(500,'unavailable'),(422,'invalid_input'),(404,'ambiguous')])
def test_errors_are_sanitized(code,expected):
    with httpx.Client(base_url='http://test',transport=httpx.MockTransport(lambda r:httpx.Response(code,json={'detail':'private secret'}))) as c:
        result=ParkingTools(c).find_vehicle('sticker','10')
        assert result.status==expected
        assert 'private secret' not in result.model_dump_json()


def test_invalid_identifiers_and_timeout_not_retried():
    calls=[]
    def fail(request):
        calls.append(request)
        raise httpx.ReadTimeout('private secret',request=request)
    with httpx.Client(base_url='http://test',transport=httpx.MockTransport(fail)) as c:
        tools=ParkingTools(c)
        for kind,value in [('sticker','01'),('sticker',10),('plate_suffix','１２３４')]:
            assert tools.find_vehicle(kind,value).status=='invalid_input'
        assert not calls
        assert tools.get_fee('s','r').status=='unavailable'
        assert tools.get_fee('s','r').status=='unavailable'
        assert len(calls)==1


def test_knowledge_matches_upstream():
    knowledge=KnowledgeStore()
    upstream=json.loads(Path('backend/app/chatbot/parking-chatbot/chatbot/lots.json').read_text(encoding='utf-8'))
    assert sum(len(l['spaces']) for l in knowledge.lots.values())==156
    assert sum(len(l['facilities']) for l in knowledge.lots.values())==28
    for row in upstream['lots']:
        assert set(row['spaces'])=={s['space_id'] for s in knowledge.lots[row['lot_id']]['spaces']}
    assert knowledge.get_space_context('D','A-03').is_mock is True
    assert knowledge.get_destination_candidates('D','missing').status=='unsupported'


@pytest.mark.parametrize('case', ['ok','stale','alarm','truncated','paged','unobserved'])
def test_recommendation_rejects_unconfirmed_data(case):
    now=datetime.now(timezone.utc)
    stamp=now.isoformat()
    alerts={'items':[],'total':0,'as_of':stamp}
    spaces={'items':[dict(space_id=s,status='empty',space_type='general',version=1,observed_at=stamp,source='vision',is_mock=False) for s in ('A-04','A-03')], 'as_of':stamp}
    if case=='stale': spaces['as_of']=(now-timedelta(seconds=11)).isoformat()
    if case=='unobserved':
        for s in spaces['items']:s['observed_at']=None
    if case=='paged': spaces['next_cursor']='more'
    if case=='truncated': alerts['total']=1
    if case=='alarm':
        alerts.update(items=[dict(alert_id='a',lot_id='D',level=2,status='acknowledged',type='mock_fire',source='simulation',is_mock=True,message='모의 경보',detected_at=stamp)],total=1)
    def handler(r):return httpx.Response(200,json=alerts if r.url.path.endswith('/alerts') else spaces)
    with httpx.Client(base_url='http://test',transport=httpx.MockTransport(handler)) as c:
        result=recommend_spaces(ParkingTools(c),KnowledgeStore(),'D','D_cinema',preference='easy',now=now)
        if case=='ok':
            assert result.status=='ok'
            assert result.data['items'][0]['space_id']=='A-03'
            assert result.data['knowledge_is_scenario'] is True
            assert result.data['space_sources'][0]['is_mock'] is False
            assert result.is_mock is None
        elif case=='unobserved':assert result.status=='ok' and result.data['items']==[]
        else: assert result.status=='unavailable'


def test_recommendation_clock_is_checked_after_network(monkeypatch):
    import backend.app.chatbot_v3.recommendation as module
    start=datetime.now(timezone.utc)
    current=[start]
    class Clock:
        @staticmethod
        def now(tz):return current[0]
        fromisoformat=datetime.fromisoformat
    monkeypatch.setattr(module,'datetime',Clock)
    def handler(r):
        current[0]+=timedelta(seconds=1)
        stamp=current[0].isoformat()
        if r.url.path.endswith('/alerts'):
            return httpx.Response(200,json={'items':[],'total':0,'as_of':stamp})
        return httpx.Response(200,json={'items':[dict(space_id='A-04',status='empty',space_type='general',version=1,observed_at=stamp)],'as_of':stamp})
    with httpx.Client(base_url='http://test',transport=httpx.MockTransport(handler)) as c:
        result=module.recommend_spaces(ParkingTools(c),KnowledgeStore(),'D','D_cinema')
        assert result.status=='ok',result
        assert result.data['items'][0]['space_id']=='A-04'


def test_recommendation_rechecks_alert_age_after_spaces(monkeypatch):
    import backend.app.chatbot_v3.recommendation as module
    current=[datetime.now(timezone.utc)]
    class Clock:
        @staticmethod
        def now(tz):return current[0]
        fromisoformat=datetime.fromisoformat
    monkeypatch.setattr(module,'datetime',Clock)
    def handler(r):
        if r.url.path.endswith('/alerts'):
            return httpx.Response(200,json={'items':[],'total':0,'as_of':current[0].isoformat()})
        current[0]+=timedelta(seconds=11)
        return httpx.Response(200,json={'items':[],'as_of':current[0].isoformat()})
    with httpx.Client(base_url='http://test',transport=httpx.MockTransport(handler)) as c:
        result=module.recommend_spaces(ParkingTools(c),KnowledgeStore(),'D','D_cinema')
        assert result.status=='unavailable'
        assert result.error.code=='ALERTS_UNCONFIRMED'
