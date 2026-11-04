import sqlite3
from fastapi.testclient import TestClient
from backend.app.integrated import create_app

def test_full_flow_and_restart(tmp_path,monkeypatch):
    path=tmp_path/'parking.db'
    monkeypatch.setenv('SMART_PARKING_DB_PATH',str(path))
    with TestClient(create_app()) as c:
        lots=c.get('/api/v1/parking-lots').json()['items']
        assert [len(x['spaces']) for x in lots]==[12,16,12,64]
        assert c.get('/api/v1/vehicles/10/location').json()['source']=='simulation'
        event=c.post('/api/v1/simulation/C/entry',json={'vehicle_number':'7654'}).json()
        assert 'session_id' in event,event
        sid=event['session_id']
        assert c.get('/api/v1/vehicle-search?vehicle_number=7654').json()['session_id']==sid
        bay=next(x for x in c.get('/api/v1/parking-lots/C/spaces').json()['items'] if x['space_id']==event['space_id'])
        assert bay['entry_at'] and bay['is_mock']
        fee=c.get(f'/api/v1/sessions/{sid}/fee').json()
        assert fee['amount_krw']==3000,fee
        req=dict(session_id=sid,quote_id=fee['quote_id'],idempotency_key='test-payment',payment_method='MOCK_CARD')
        pay=c.post('/api/v1/mock-payments',json=req)
        assert pay.status_code==200,pay.text
        payment=pay.json()
        assert c.post('/api/v1/mock-payments',json=req).json()['payment_id']==payment['payment_id']
        assert c.get(f'/api/v1/sessions/{sid}/fee').json()['payment']['payment_id']==payment['payment_id']
        report=c.get('/api/v1/reports/revenue?month=ALL').json()
        assert report['summary']['net_revenue_krw']==3000
        assert c.post('/api/v1/mock-payments/'+payment['payment_id']+'/cancel',json={}).status_code==200
        assert c.get('/api/v1/reports/revenue?month=ALL').json()['summary']['net_revenue_krw']==0
        alert=c.post('/api/v1/demo/alerts',json={'lot_id':'C'}).json()
        assert alert['is_mock'] is True,alert
        aid=alert['alert_id']
        assert c.post(f'/api/v1/alerts/{aid}/acknowledge').status_code==200
        assert c.post(f'/api/v1/alerts/{aid}/resolve').status_code==200
        assert c.post('/api/v1/simulation/C/exit',json={'space_id':event['space_id']}).status_code==200
        assert all(x['status']=='active' for x in c.get('/api/v1/sessions?status=active').json()['items'])
        assert any(x['session_id']==sid for x in c.get('/api/v1/sessions?status=closed').json()['items'])
        assert c.post('/api/v1/simulation/A/entry',json={}).status_code==403
        assert c.post('/api/v1/simulation/C/entry',json={'vehicle_number':'x'}).status_code==422
    with TestClient(create_app()) as c:
        assert c.get(f'/api/v1/sessions/{sid}/fee').json()['payment_status']=='canceled'
        assert any(x['alert_id']==aid for x in c.get('/api/v1/alerts').json()['items'])
        assert c.get('/api/v1/vehicle-search?vehicle_number=7654').status_code==404
