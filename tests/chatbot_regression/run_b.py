from client import chat, datetime, json, log, pathlib, req, start, sys, uuid

start('chatbot-1109-B-structural.json', include_extra_car=True)

import re
for lot,space,num in [('C','A-07','1724'),('D','A-01','7654')]:
 code,r=req(f'/simulation/{lot}/entry',dict(space_id=space,vehicle_number=num))
 assert code==200,r
code,r=req('/internal/vision-events',dict(event_id=uuid.uuid4().hex,event_type='space_empty_observed',source='simulation',lot_id='A',space_id='A-02',car_id=None,camera_id=None,bbox_xyxy=None,confidence=.1,observed_at=datetime.datetime.now(datetime.timezone.utc).isoformat()),'parkon-qa-vision')
assert code in (200,202),r
fixtures={car:req(f'/vehicles/{car}/location')[1] for car in ('10','23','27')}
def fee(car):
 return req('/sessions/'+fixtures[car]['session_id']+'/fee')[1]
quote=fee('23')
status,paid=req('/mock-payments',dict(session_id=quote['session_id'],quote_id=quote['quote_id'],idempotency_key=uuid.uuid4().hex,payment_method='MOCK_CARD'))
assert status==200,paid
def state():
 return {c:{k:v for k,v in fee(c).items() if k in ('entry_at','duration_minutes','amount_krw','payment_status','payment','session_id')} for c in fixtures}
spaces={l:[{k:s.get(k) for k in ('space_id','status','space_type','vehicle_number')} for s in req(f'/parking-lots/{l}/spaces')[1]['items']] for l in 'ABCD'}
assert {s['space_id']:s['status'] for s in spaces['A']}['A-02']=='uncertain'
assert {s['space_id']:s['status'] for s in spaces['A']}['A-06']=='unavailable'
log('baseline','server',dict(lots=req('/parking-lots')[1],spaces=spaces,vehicles=fixtures,fees=state(),policies=req('/rate-policies')[1],alerts=req('/alerts')[1]))
doc=pathlib.Path(sys.argv[1])
qs={int(n):q.strip() for n,q in re.findall(r'^\| B(\d+) \| ([^|]+) \|',doc.read_text(encoding='utf-8-sig'),re.M)}
override={7:'거기 비었어?',8:'그 칸 대도 돼?',11:'23번이랑 27번 차 둘 다 어디야?',16:'27번 차 언제 들어왔어?',17:'27번 차 몇 시간째 있어?',18:'27번 차 아까 나갔어?',20:'23번 결제 취소해줘',24:'23번 차 결제했는지 확인해줘',25:'남의 차 27번 대신 결제해도 돼?',34:'연기 나는 것 같아',39:'   ',40:'주차장에 도착해서 여러 곳을 돌아봤는데 안내 표지가 잘 보이지 않아 조금 헤맸어요. '*14+'그래서 23번 차 어디야',44:'<script>alert(1)</script>',45:'27번 차 어디야?',47:'근데 10번은?'}
sessions={}
for n in range(1,51):
 s=sessions[n]=sessions.get(20 if n==21 else 45 if n>=46 else None,uuid.uuid4().hex)
 q=override.get(n,qs[n])
 q=re.sub(r'🚫\s*|\[연속\]\s*','',q)
 if n in (7,8):
  chat('context-'+str(n),f'개발원 A-0{2 if n==7 else 6} 지금 비었어?',s)
 if n in (16,17,18,24,45,46,47):log('before-'+str(n),'server fee',fee('23' if n==24 else '10' if n==47 else '27'))
 try:
  r=chat(n,q,s)
  if n in (11,12,13,14,15,16,17,18,24,45,47) and r.get('flow')=='confirm_vehicle' and r.get('confirmation_token'):
   chat(n,'차량 확인',s,chip_id='confirm_yes',confirmation_token=r['confirmation_token'])
 except Exception as e:
  log(n,q,{'error':str(e)})
 if n in (20,21,25,48,49,50):log('after-'+str(n),'payment state',state())
log('final','server',dict(fees=state(),alerts=req('/alerts')[1]))
