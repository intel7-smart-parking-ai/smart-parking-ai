"""기능을 유지하는 최적화 비교. 로컬 mock·통제 지연이며 실제 서버 성능 측정은 아니다."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sys
import threading
import time
from unittest.mock import patch
p=argparse.ArgumentParser();p.add_argument('--project',default=str(Path(__file__).resolve().parent.parent));args=p.parse_args()
sys.path.insert(0,str(Path(args.project).resolve()))
from chatbot import engine as e
from chatbot.adapters import CachedAdapter,MockAdapter

def fresh():
 e.data=CachedAdapter(MockAdapter());e.reset_all()
results={}
fresh();e.get_session('near').vehicle_no='07';e.data.set_alert(2,'A',6);e.current_alert()
original=e.data.inner.find_car;reads=0
def delayed(v):
 global reads
 reads+=1;time.sleep(.015);return original(v)
start=time.perf_counter()
with patch.object(e.data.inner,'find_car',side_effect=delayed):
 for _ in range(8):e.handle('near',chip_id='alert_near')
results['near_vehicle']={'requests':8,'backend_reads':reads,'elapsed_ms':round((time.perf_counter()-start)*1000,2),'injected_delay_ms':15}

for kind in ('location','occupancy'):
 fresh();barrier=threading.Barrier(12);lock=threading.Lock();reads=0
 for i in range(12):
  s=e.get_session('read-'+str(i))
  if kind=='location':s.vehicle_no='07'
 name='find_car' if kind=='location' else 'get_empty_spots';original=getattr(e.data.inner,name)
 def delayed(*a):
  global reads
  with lock:reads+=1
  time.sleep(.04);return original(*a)
 def request(i):
  barrier.wait(5);start=time.perf_counter()
  r=e.handle('read-'+str(i),chip_id='help' if kind=='location' else 'empty_all')
  return round((time.perf_counter()-start)*1000,2)
 with patch.object(e.data.inner,name,side_effect=delayed):
  with ThreadPoolExecutor(max_workers=12)as pool:latencies=list(pool.map(request,range(12)))
 results['concurrent_'+kind]={'clients':12,'backend_reads':reads,'max_latency_ms':max(latencies),'injected_delay_ms':40}
print(json.dumps({'scope':'controlled local mock, single process, LLM disabled','results':results},ensure_ascii=False,indent=2))
