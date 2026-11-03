"""Integration tests against the running Docker stack; uses test car 22.
Only the payment created by this test is canceled. DB history is retained.
"""
import json
import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError

BASE = os.environ.get('PARKON_TEST_URL','http://127.0.0.1:8002')
def request(path,method='GET',body=None):
    req=Request(BASE+'/api/v1'+path,method=method,data=json.dumps(body).encode() if body is not None else None,headers={'Content-Type':'application/json'})
    try:
        with urlopen(req,timeout=15) as response: return response.status,(lambda result:result['items'] if isinstance(result,dict) and 'items' in result else result)(json.load(response))
    except HTTPError as error: return error.code,json.load(error)

def check(condition,message):
    if not condition: raise AssertionError(message)
    print('PASS:',message)

status,health=request('/health')
check(status==200 and health['database']=='postgresql','Nginx → FastAPI → PostgreSQL health')
_,lots=request('/parking-lots')
check([(l['lot_id'],len(l['spaces'])) for l in lots]==[('A',12),('B',16),('C',12),('D',10)],'50 spaces and stable lot identifiers')
_,active_sessions=request('/sessions')
for lot,amount in [('A',5000),('B',6000),('C',2600),('D',3200)]:
    session=next(s for s in active_sessions if s['lot_id']==lot)
    _,fee=request('/sessions/'+session['session_id']+'/fee')
    check(fee['amount_krw']==amount and fee['authority']=='server_fixture','Server fixture quote for lot '+lot)
_,before=request('/reports/revenue?month=2026-11')
_,existing=request('/mock-payments')
completed={p['session_id'] for p in existing if p['status']=='completed'}
_,sessions=request('/sessions')
sid=next((s['session_id'] for s in sessions if s['session_id'] not in completed),None)
check(sid is not None,'An unpaid test session is available; existing payments are untouched')
_,test_fee=request('/sessions/'+sid+'/fee')
expected=test_fee['amount_krw']
token='integration-'+uuid.uuid4().hex
payload={'session_id':sid,'idempotency_key':token,'payment_method':'MOCK_CARD'}
status,_=request('/mock-payments','POST',{**payload,'simulate_failure':True})
check(status==422,'Unsupported payment fields are rejected')
with ThreadPoolExecutor(max_workers=3) as pool:
    responses=list(pool.map(lambda _:request('/mock-payments','POST',payload),range(3)))
check(all(code==200 for code,_ in responses) and len({p['payment_id'] for _,p in responses})==1,'Concurrent retries create exactly one payment')
payment=responses[0][1]
try:
    check(payment['amount_krw']==expected,'Server amount is authoritative')
    status,_=request('/mock-payments','POST',{**payload,'idempotency_key':token+'-other'})
    check(status==409,'Second completed payment for the session is rejected')
    _,after=request('/reports/revenue?month=2026-11')
    check(after['summary']['net_revenue_krw']==before['summary']['net_revenue_krw']+expected,'Server revenue reflects committed payment')
finally:
    request('/mock-payments/'+payment['payment_id']+'/cancel','POST')
_,again=request('/mock-payments/'+payment['payment_id']+'/cancel','POST')
check(again['status']=='canceled','Cancellation is idempotent')
_,report=request('/reports/revenue?month=2026-11')
check(report['summary']['net_revenue_krw']==before['summary']['net_revenue_krw'],'Cancellation restores server net revenue')
_,empty=request('/reports/revenue?month=2026-12')
check(empty['summary']['net_revenue_krw']==0,'Empty reporting period returns zero')
_,alert=request('/demo/alerts','POST',{'lot_id':'D'})
check(alert['space_id'] is None and alert['type']=='mock_fire_led_observed','Unknown alert location remains null')
check(request('/alerts/'+alert['alert_id']+'/resolve','POST')[0]==409,'Alert cannot resolve before acknowledgement')
request('/alerts/'+alert['alert_id']+'/acknowledge','POST')
_,resolved=request('/alerts/'+alert['alert_id']+'/resolve','POST')
check(resolved['status']=='resolved','Alert lifecycle is stored in DB')
Path(__file__).with_name('verification-record.json').write_text(json.dumps({'payment_id':payment['payment_id'],'alert_id':alert['alert_id']},indent=2))
print('All integration checks passed. Test records remain for persistence verification.')
