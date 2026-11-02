"""모의 경보 조회 40ms 지연, 12세션 동시 요청. 팀 실서버 성능 측정이 아니다."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sys
import threading
import time
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from chatbot import engine as e
from chatbot.adapters import CachedAdapter,MockAdapter

with patch.object(e,'data',CachedAdapter(MockAdapter())):
    e.reset_all()
    barrier=threading.Barrier(12)
    original=e.data.inner.get_alert
    def delayed():
        time.sleep(.04)
        return original()
    def call(i):
        barrier.wait(5);started=time.perf_counter()
        r=e.handle('load-'+str(i),chip_id='help')
        return {'wall_ms':round((time.perf_counter()-started)*1000,2),'reported_ms':r['meta']['latency_ms']}
    with patch.object(e.data.inner,'get_alert',side_effect=delayed) as fetched:
        with ThreadPoolExecutor(max_workers=12) as pool: samples=list(pool.map(call,range(12)))
    result={'environment':'local mock, single process','injected_delay_ms':40,'clients':12,
            'backend_alert_fetches':fetched.call_count,'max_wall_ms':max(x['wall_ms']for x in samples),
            'samples':samples}
    print(json.dumps(result,ensure_ascii=False,indent=2))
