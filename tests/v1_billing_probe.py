"""최신 main을 cwd로 별도 실행하는 실제 billing 라우터 시험. 원본 수정 없음."""
from concurrent.futures import ThreadPoolExecutor
import sqlite3
import threading
import time
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from backend.app.billing.db import init_schema
from backend.app.api.billing_router import billing_router
from v1_support import PROFILES, allocate, save, summarize


@pytest.mark.parametrize('profile', PROFILES)
def test_twenty_payments(tmp_path, monkeypatch, profile):
    path, connect, config = allocate(tmp_path, profile)
    with connect() as conn:
        init_schema(conn)
        conn.execute("INSERT INTO parking_lots VALUES ('A','V1 합성 예시','real',1)")
        conn.execute("INSERT INTO rate_policies VALUES ('A',30,1000,10,500,10000)")
        conn.execute("INSERT INTO parking_sessions VALUES ('v1-session','A','A-01','01','2026-11-03T09:00:00+09:00','2026-11-03T09:55:00+09:00')")
    monkeypatch.setenv('SMART_PARKING_DB_PATH', str(path))
    monkeypatch.setattr(sqlite3, 'connect', connect)
    app = FastAPI()
    app.include_router(billing_router)
    barrier = threading.Barrier(20)
    with TestClient(app, raise_server_exceptions=False) as client:
        def pay(index):
            barrier.wait(timeout=15)
            start = time.perf_counter()
            response = client.post('/api/v1/mock-payments', json={'session_id':'v1-session','idempotency_key':f'v1-{index}'})
            try:
                body = response.json()
            except ValueError:
                body = {'unparsed': response.text}
            return {'index':index,'status':response.status_code,'body':body,'ms':(time.perf_counter()-start)*1000}
        with ThreadPoolExecutor(20) as executor:
            rows = list(executor.map(pay, range(20)))
        successes = [r for r in rows if r['status'] == 200]
        replay = None
        if successes:
            winner = successes[0]
            replay = client.post('/api/v1/mock-payments', json={'session_id':'v1-session','idempotency_key':f"v1-{winner['index']}"})
            before = client.get('/api/v1/reports/revenue').json()['summary']
            cancel = client.post('/api/v1/mock-payments/'+winner['body']['payment_id']+'/cancel')
            after = client.get('/api/v1/reports/revenue').json()['summary']
            recancel = client.post('/api/v1/mock-payments/'+winner['body']['payment_id']+'/cancel')
        with connect() as conn:
            counts = conn.execute("SELECT count(*),sum(status='completed') FROM mock_payments").fetchone()
        save('billing-'+profile, {'profile':profile,'config':config,'requests':rows,
            'latency':summarize([r['ms'] for r in rows]),'automatic_retries':0,
            'replay_status': replay.status_code if replay is not None else None,
            'payment_rows_after_cancel':counts[0], 'completed_after_cancel':counts[1]})
        assert len(successes) == 1
        assert all(r['status'] == 409 and r['body']['error']['code'] == 'ALREADY_PAID' for r in rows if r['status'] != 200)
        assert replay.status_code == 200 and replay.json()['idempotent_replayed'] is True
        assert replay.json()['payment_id'] == winner['body']['payment_id']
        assert cancel.status_code == 200 and recancel.status_code == 409
        assert before['net_revenue_krw'] == 2500 and after['net_revenue_krw'] == 0
        assert counts == (1,0)


def test_sixty_second_mixed_load(tmp_path, monkeypatch):
    """실제 billing 요청2/s + 합성 관측 INSERT10/s. 비전/상태엔진 통합은 아니다."""
    path, connect, config = allocate(tmp_path, 'wal_timeout')
    with connect() as conn:
        init_schema(conn)
        conn.execute("INSERT INTO parking_lots VALUES ('A','V1 부하 예시','real',1)")
        conn.execute("INSERT INTO rate_policies VALUES ('A',30,1000,10,500,10000)")
        conn.executemany("INSERT INTO parking_sessions VALUES (?, 'A','A-01','01','2026-11-03T09:00:00+09:00','2026-11-03T09:55:00+09:00')", [(f'load-{i}',) for i in range(120)])
        conn.execute('CREATE TABLE synthetic_observations (event_id INTEGER PRIMARY KEY)')
    monkeypatch.setenv('SMART_PARKING_DB_PATH', str(path))
    monkeypatch.setattr(sqlite3, 'connect', connect)
    app = FastAPI()
    app.include_router(billing_router)
    started = time.perf_counter()
    with TestClient(app, raise_server_exceptions=False) as client:
        def task(tick, observed):
            start = time.perf_counter()
            try:
                if observed:
                    with connect() as conn:
                        conn.execute('INSERT INTO synthetic_observations VALUES (?)', (tick,))
                    status = 200
                else:
                    status = client.post('/api/v1/mock-payments', json={'session_id':f'load-{tick}', 'idempotency_key':f'load-key-{tick}'}).status_code
                return {'kind':'synthetic_observation' if observed else 'billing', 'status':status,'ms':(time.perf_counter()-start)*1000}
            except sqlite3.Error as exc:
                return {'kind':'sqlite_error','error':str(exc),'status':500,'ms':(time.perf_counter()-start)*1000}
        futures = []
        with ThreadPoolExecutor(20) as executor:
            for tick in range(600):
                delay = started + tick / 10 - time.perf_counter()
                if delay > 0:
                    time.sleep(delay)
                futures.append(executor.submit(task,tick,True))
                if tick % 5 == 0:
                    futures.append(executor.submit(task,tick//5,False))
            rows = [f.result() for f in futures]
        remaining = started + 60 - time.perf_counter()
        if remaining > 0:
            time.sleep(remaining)
    with connect() as conn:
        counts = {'observations':conn.execute('SELECT count(*) FROM synthetic_observations').fetchone()[0],
                  'payments':conn.execute('SELECT count(*) FROM mock_payments').fetchone()[0]}
    save('load-wal_timeout',{'config':config,'elapsed_s':time.perf_counter()-started,'counts':counts,
        'latency':summarize([r['ms'] for r in rows]),'rows':rows,'automatic_retries':0})
    assert counts == {'observations':600,'payments':120}
    assert all(r['status'] == 200 for r in rows)
