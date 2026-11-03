"""혼합 질의·대기·호출·경보 전환의 동시 반복. 로컬 mock 전용 불변 조건 검사."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import random
import sys
import threading
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from chatbot import engine as e,incidents
from chatbot.adapters import CachedAdapter,MockAdapter

e.data=CachedAdapter(MockAdapter());e.reset_all()
users=30;iterations=80;barrier=threading.Barrier(users+1)
chips=['empty_all','find_car','fee','alert_near','call_admin','call_status','help']
for i in range(users):e.get_session('soak-'+str(i)).vehicle_no='07' if i%2==0 else '08'

def user(i):
 sid='soak-'+str(i);rng=random.Random(i);cursor=0
 barrier.wait(5)
 for step in range(iterations):
  if step%4==0:
   r=e.updates(sid,cursor=cursor,runtime_id=e.RUNTIME_ID)
   ids=[m['event_id']for m in r['messages']]
   assert ids==sorted(set(ids));assert all(n>cursor for n in ids)
   cursor=r['cursor']
  else:
   r=e.handle(sid,chip_id=rng.choice(chips),request_id=f'{i}-{step}')
   if r.get('card',{} )and r['card']['type']in('car_location','fee'):
    assert r['card']['data']['vehicle_no']==('07'if i%2==0 else'08')
  assert r['runtime_id']==e.RUNTIME_ID
  assert r['vehicle_context']['vehicle_no']==('07'if i%2==0 else'08')
  assert r['state_version']>0
 return iterations

def alarms():
 barrier.wait(5)
 for i in range(40):
  e.data.set_alert((1,2,0,2)[i%4],'A'if i%2==0 else'B',6 if i%2==0 else 3)
  e.current_alert()
  threading.Event().wait(.002)
 return 40

with ThreadPoolExecutor(max_workers=users+1)as pool:
 futures=[pool.submit(user,i)for i in range(users)]+[pool.submit(alarms)]
 counts=[f.result(30)for f in futures]
reports={r['id']:r for r in incidents.list_public()}
for c in e.admin_calls():
 if c['incident_id']is not None:
  inc=reports[c['incident_id']]
  assert c['id']in inc['admin_calls']
  assert f"보고서 #{inc['id']}"in c['summary']
  assert inc['lot_id']+' 'in c['summary']
assert not e.data.inflight
assert not e.data.get_fee('07')['paid']
assert not e.data.get_fee('08')['paid']
print(json.dumps({'scope':'local mock, LLM disabled','users':users,'user_requests':sum(counts[:-1]),
 'alert_changes':counts[-1],'assertions':'all passed','open_read_flights':len(e.data.inflight),
 'sessions':len(e.SESSIONS),'reports':len(reports),'admin_calls':len(e.admin_calls())},ensure_ascii=False,indent=2))
