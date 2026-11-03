"""Verify optional quote_id against the live demo without leaving paid sessions."""
import http.cookiejar,json,uuid,urllib.request
from pathlib import Path
from urllib.parse import urlencode
root=Path(__file__).resolve().parent.parent
private=root/'.local-access/credentials.json'
if not private.exists():private=root.parent/'.local-access/credentials.json'
account=json.loads(private.read_text(encoding='utf-8'));base='http://127.0.0.1:8080'
client=urllib.request.build_opener(urllib.request.ProxyHandler({}),urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
client.open(urllib.request.Request(base+'/auth/login',data=urlencode(account).encode(),headers={'Origin':base,'Content-Type':'application/x-www-form-urlencoded'})).close()
def call(path,body=None):
 req=urllib.request.Request(base+'/admin-api/demo/v1'+path,data=json.dumps(body).encode() if body is not None else None,headers={'Origin':base,'Content-Type':'application/json'})
 with client.open(req,timeout=20) as r:return json.load(r)
before=call('/reports/revenue?month=2026-11')['summary']['net_revenue_krw']
for include_quote in (True,False):
 event=call('/simulation/C/entry',{});payment=None
 try:
  sid=event['session_id'];fee=call('/sessions/'+sid+'/fee')
  body={'session_id':sid,'idempotency_key':uuid.uuid4().hex,'payment_method':'MOCK_CARD'}
  if include_quote:body['quote_id']=fee['quote_id']
  payment=call('/mock-payments',body)
  assert payment['status']=='completed' and payment['amount_krw']==fee['amount_krw']
  assert call('/mock-payments',body)['payment_id']==payment['payment_id']
 finally:
  if payment:assert call('/mock-payments/'+payment['payment_id']+'/cancel',{})['status']=='canceled'
  call('/simulation/C/exit',{'space_id':event['space_id']})
assert call('/reports/revenue?month=2026-11')['summary']['net_revenue_krw']==before
print('PASS: live demo accepts correct quote_id and absent quote_id, retains idempotency; test payments canceled and sessions exited')
