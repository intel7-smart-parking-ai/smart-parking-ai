from client import chat, datetime, json, log, pathlib, req, start, sys, uuid

start('chatbot-1109-structural-A.json', include_extra_car=False)

import re
for lot,space,num in [('C','A-07','1724'),('D','A-01','7654')]:
 code,r=req(f'/simulation/{lot}/entry',dict(space_id=space,vehicle_number=num))
 assert code==200,r
fixtures={car:req(f'/vehicles/{car}/location')[1] for car in ('23','27')}
def fee(car):
 return req('/sessions/'+fixtures[car]['session_id']+'/fee')[1]
log('baseline','server',dict(lots=req('/parking-lots')[1],spaces={l:[{k:s[k] for k in ('space_id','status','space_type','vehicle_number')} for s in req(f'/parking-lots/{l}/spaces')[1]['items']] for l in 'ABCD'},fees={c:fee(c) for c in fixtures},policies=req('/rate-policies')[1]))
doc=pathlib.Path(sys.argv[1])
qs={int(n):q.strip() for n,q in re.findall(r'^\| (\d+) \| ([^|]+) \|',doc.read_text(encoding='utf-8-sig'),re.M)}
override={9:'23번 차 어디 있어?',11:'1724 차량 위치',16:'23번 차 주차 요금 얼마야?',19:'23번 차 결제해줘',25:'27번 차 결제할게',41:'요금이랑 위치 같이 알려줘 27번',45:'How much for car 27?',57:'27번 차 너무 오래 세워둔 것 같은데 많이 나왔나'}
sessions={}
aid=None
token=None
for n in range(1,64):
 if n==24:
  aid=req('/demo/alerts',{'lot_id':'B'})[1]['alert_id']
  log('alert','activated',req(f'/alerts/{aid}'))
 if n==27:
  log('alert','resolved',req(f'/alerts/{aid}/resolve',{}))
 s=sessions[n]=sessions.get({18:17,20:19,21:19}.get(n),uuid.uuid4().hex)
 q=override.get(n,qs[n])
 q=re.sub(r'🚫\s*|\[연속\]\s*| \((?:오타|말하듯|띄어쓰기|첫 질문|다른 사람 차 정보)\)','',q)
 for part in q.split(' → '):
  try:
   r=chat(n,part,s,{42:'en',43:'zh-CN',44:'ja',45:'en'}.get(n,'ko'))
   if n in (9,16,19,25,41,57) and r.get('flow')=='confirm_vehicle':
    r=chat(n,'차량 확인',s,chip_id='confirm_yes',confirmation_token=r['confirmation_token'])
   if n==19:
    token=r.get('confirmation_token')
    log('19-check','before',fee('23').get('payment'))
   if n==20:
    log('20-check','bare yes',fee('23').get('payment'))
    if token:
     chat(n,'모의 결제 확인',s,chip_id='confirm_yes',confirmation_token=token)
    log('20-check','after button',fee('23').get('payment'))
   if n==21:log('21-check','duplicate',fee('23').get('payment'))
   if n==25:
    quote=fee('27')
    log('25-check','server block',req('/mock-payments',dict(session_id=quote['session_id'],quote_id=quote['quote_id'],idempotency_key=uuid.uuid4().hex,payment_method='MOCK_CARD')))
  except Exception as exc:
   log(n,part,{'error':str(exc)})
log('baseline','final',dict(lots=req('/parking-lots')[1],fees={c:fee(c) for c in fixtures}))
