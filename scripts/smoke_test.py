"""Integration tests against the running Docker stack; uses test car 22.
Only the payment created by this test is cancelled. DB history is retained.
"""
import json
import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError

BASE = os.environ.get('PARKON_TEST_URL','http://127.0.0.1:8080')
def request(path,method='GET',body=None):
    req=Request(BASE+'/demo-api/v1'+path,method=method,data=json.dumps(body).encode() if body is not None else None,headers={'Content-Type':'application/json'})
    try:
        with urlopen(req,timeout=15) as response: return response.status,json.load(response)
    except HTTPError as error: return error.code,json.load(error)

def check(condition,message):
    if not condition: raise AssertionError(message)
    print('PASS:',message)

status,health=request('/health')
check(status==200 and health['database']=='postgresql','Nginx → FastAPI → PostgreSQL health')
_,lots=request('/parking-lots')
check([(l['lot_id'],len(l['spaces'])) for l in lots]==[('A',12),('B',16),('C',12),('D',10)],'50 spaces and stable lot identifiers')
for car,amount in [('07',5000),('12',6000),('15',2600),('19',3200)]:
    _,fee=request('/sessions/s-'+car+'/fee')
    check(fee['amount_krw']==amount and fee['authority']=='server_fixture','Server fixture quote for car '+car)
_,before=request('/reports/revenue?month=2026-11')
token='integration-'+uuid.uuid4().hex
payload={'session_id':'s-22','quote_id':'quote-s-22','idempotency_key':token,'amount_krw':1}
status,_=request('/mock-payments','POST',{**payload,'simulate_failure':True})
check(status==400,'Declined payment is not stored')
with ThreadPoolExecutor(max_workers=3) as pool:
    responses=list(pool.map(lambda _:request('/mock-payments','POST',payload),range(3)))
check(all(code==200 for code,_ in responses) and len({p['payment_id'] for _,p in responses})==1,'Concurrent retries create exactly one payment')
payment=responses[0][1]
try:
    check(payment['amount_krw']==3200,'Client-supplied amount is ignored')
    status,_=request('/mock-payments','POST',{**payload,'idempotency_key':token+'-other'})
    check(status==409,'Second completed payment for the session is rejected')
    _,after=request('/reports/revenue?month=2026-11')
    check(after['summary']['amount_krw']==before['summary']['amount_krw']+3200,'Server revenue reflects committed payment')
finally:
    request('/mock-payments/'+payment['payment_id']+'/cancel','POST')
_,again=request('/mock-payments/'+payment['payment_id']+'/cancel','POST')
check(again['status']=='cancelled','Cancellation is idempotent')
_,report=request('/reports/revenue?month=2026-11')
check(report['summary']['amount_krw']==before['summary']['amount_krw'],'Cancellation restores server net revenue')
_,empty=request('/reports/revenue?month=2026-12')
check(empty['summary']['amount_krw']==0,'Empty reporting period returns zero')
_,alert=request('/demo/alerts','POST',{'lot_id':'D'})
check(request('/alerts/'+alert['alert_id']+'/resolve','POST')[0]==409,'Alert cannot resolve before acknowledgement')
request('/alerts/'+alert['alert_id']+'/acknowledge','POST')
_,resolved=request('/alerts/'+alert['alert_id']+'/resolve','POST')
check(resolved['status']=='resolved','Alert lifecycle is stored in DB')
Path(__file__).with_name('verification-record.json').write_text(json.dumps({'payment_id':payment['payment_id'],'alert_id':alert['alert_id']},indent=2))
print('All integration checks passed. Test records remain for persistence verification.')
