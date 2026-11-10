import threading, urllib.request as u, urllib.error, json, uuid,time,datetime,pathlib,sys
sys.stdout.reconfigure(encoding='utf-8')
base='http://127.0.0.1:18480/api/v1'
rows=[]
output_name=None
def req(p,b=None,token=None):
 h={'Content-Type':'application/json'}
 if token:h['Authorization']='Bearer '+token
 try:
  with u.urlopen(u.Request(base+p,None if b is None else json.dumps(b).encode(),h),timeout=45) as r:return r.status,json.load(r)
 except urllib.error.HTTPError as e:return e.code,json.load(e)
def log(n,q,v):
 rows.append(dict(n=n,question=q,result=v));print(n,q,json.dumps(v,ensure_ascii=False),flush=True)
 pathlib.Path(output_name).write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf-8')
def chat(n,q,s=None,lang='ko',**kw):
 t=time.monotonic()
 code,r=req('/chat',dict(session_id=s or uuid.uuid4().hex,text=q,language=lang,**kw))
 log(n,q,dict(status=code,seconds=round(time.monotonic()-t,2),**{k:r.get(k) for k in ('text','messages','card','confirmation_token','flow','safety','runtime_id')}))
 return r
def observe(vehicles):
 while True:
  for lot,space,car,kind in vehicles:
   req('/internal/vision-events',dict(event_id=uuid.uuid4().hex,event_type='vehicle_observed',source='simulation',lot_id=lot,space_id=space,car_id=car,camera_id=None,bbox_xyxy=None,vehicle_type=kind,confidence=.99,observed_at=datetime.datetime.now(datetime.timezone.utc).isoformat()),'parkon-qa-vision')
  time.sleep(1.05)

def start(filename, include_extra_car=False):
 global output_name
 output_name = filename
 vehicles = [('A','A-03','23','ev' if include_extra_car else 'general'),('B','B-02','27','general')]
 if include_extra_car:
  vehicles.append(('A','A-04','10','general'))
 threading.Thread(target=observe,args=(vehicles,),daemon=True).start()
 time.sleep(4)
