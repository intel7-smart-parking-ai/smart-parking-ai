"""Exercise only the administrator C/D simulator. No payments or physical movements."""
import concurrent.futures
import http.cookiejar
import json
from pathlib import Path
import time
import urllib.error
import urllib.request
from urllib.parse import urlencode
ROOT=Path(__file__).resolve().parent.parent
# The workspace and cloned repo can each own their private credentials.
private=ROOT/'.local-access/credentials.json'
if not private.exists():private=ROOT.parent/'.local-access/credentials.json'
account=json.loads(private.read_text(encoding='utf-8'))
BASE='http://127.0.0.1:8080'
class NoRedirect(urllib.request.HTTPRedirectHandler):
 def redirect_request(self,*args,**kwargs):return None
jar=http.cookiejar.CookieJar()
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),urllib.request.HTTPCookieProcessor(jar),NoRedirect())
def call(path,body=None):
 headers={'Origin':BASE,'Content-Type':'application/json'}
 request=urllib.request.Request(BASE+path,data=json.dumps(body).encode() if body is not None else None,headers=headers)
 try:
  with opener.open(request,timeout=20) as r:return r.status,json.loads(r.read())
 except urllib.error.HTTPError as e:
  raw=e.read()
  try:data=json.loads(raw)
  except ValueError:data={}
  return e.code,data
prefix='/admin-api/demo/v1'
assert call(prefix+'/simulation/C')[0]==401
assert call('/demo-api/v1/simulation/C/entry',{})[0]==403
login=urllib.request.Request(BASE+'/auth/login',data=urlencode(account).encode(),headers={'Origin':BASE,'Content-Type':'application/x-www-form-urlencoded'})
try:opener.open(login)
except urllib.error.HTTPError as e:assert e.code==303
for lot in ('C','D'):assert call(prefix+'/simulation/'+lot+'/stop',{})[0]==200
physical=[l for l in call(prefix+'/parking-lots')[1]['items'] if l['lot_id'] in ('A','B')]
payments=call(prefix+'/mock-payments')[1]['items']
for lot in ('A','B'):
 for action,body in [('entry',{}),('exit',{}),('start',{'interval_seconds':3}),('stop',{})]:
  code,result=call(prefix+'/simulation/'+lot+'/'+action,body);assert code==403 and result['error']['code']=='SIMULATION_REAL_LOT_FORBIDDEN'
for lot in ('C','D'):
 code,event=call(prefix+'/simulation/'+lot+'/entry',{});assert code==200,event
 sid=event['session_id'];number=event['vehicle_number'];space=event['space_id']
 code,found=call(prefix+'/vehicle-search?vehicle_number='+number);assert code==200 and found['session_id']==sid
 assert call(prefix+'/simulation/'+lot+'/entry',{'vehicle_number':number})[0]==409
 code,exit_event=call(prefix+'/simulation/'+lot+'/exit',{'space_id':space});assert code==200 and exit_event['session_id']==sid
 assert call(prefix+'/vehicle-search?vehicle_number='+number)[0]==404
 assert call(prefix+'/simulation/'+lot+'/exit',{'space_id':space})[0]==409
 assert call(prefix+'/mock-payments',{'session_id':sid,'idempotency_key':'closed-session-'+sid,'payment_method':'MOCK_CARD'})[0]==409
 # Re-entry creates a fresh session; an old payment cannot be reused.
 code,reentry=call(prefix+'/simulation/'+lot+'/entry',{'vehicle_number':number,'space_id':space});assert code==200 and reentry['session_id']!=sid
 assert call(prefix+'/simulation/'+lot+'/exit',{'space_id':space})[0]==200
before=call(prefix+'/simulation/C')[1]['events'][0]['event_id']
assert call(prefix+'/simulation/C/start',{'interval_seconds':3})[0]==200
try:
 time.sleep(7)
 events=[e for e in call(prefix+'/simulation/C')[1]['events'] if e['event_id']>before]
 assert {e['action'] for e in events}=={'entry','exit'},events
finally:assert call(prefix+'/simulation/C/stop',{})[0]==200
last=call(prefix+'/simulation/C')[1]['events'][0]['event_id'];time.sleep(1.5)
assert call(prefix+'/simulation/C')[1]['events'][0]['event_id']==last
assert [l for l in call(prefix+'/parking-lots')[1]['items'] if l['lot_id'] in ('A','B')]==physical
assert call(prefix+'/mock-payments')[1]['items']==payments
print('PASS: administrator-only C/D entry/exit/re-entry, duplicate/closed-session guards, automatic entry+exit and pause; A/B and payments unchanged')
