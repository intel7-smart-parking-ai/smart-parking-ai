"""칸 배정/입차 검증용 테이블 실험. 실제 업무 모델·공식 상태 계약이 아니다."""
from concurrent.futures import ThreadPoolExecutor
import sqlite3
import threading
import time
import pytest
from v1_support import PROFILES, allocate, save, summarize


@pytest.mark.parametrize('profile', PROFILES)
@pytest.mark.parametrize('kind', ['slot', 'visit'])
def test_ten_competing_inserts(tmp_path, profile, kind):
    path, connect, config = allocate(tmp_path, profile)
    # 슬롯 PK는 칸당 하나, 방문 PK는 차량당 하나의 현재 실험 행만 허용한다.
    # 실제 입차·종료 정책/이동/미식별 차량/부분 UNIQUE를 구현한 것이 아니다.
    with connect() as conn:
        conn.execute('CREATE TABLE experiment (resource TEXT PRIMARY KEY, car TEXT NOT NULL, request_id TEXT UNIQUE NOT NULL)')
    barrier = threading.Barrier(10)

    def attempt(index, synchronize=True):
        conn = connect()
        if synchronize:
            barrier.wait(timeout=15)
        start = time.perf_counter()
        try:
            resource = 'A-01' if kind == 'slot' else '01'
            conn.execute('INSERT INTO experiment VALUES (?, ?, ?)', (resource, f'{index:02}', f'req-{index}'))
            conn.commit()
            outcome = 'inserted'
        except sqlite3.IntegrityError:
            conn.rollback()
            row = conn.execute('SELECT request_id FROM experiment WHERE resource=?', (resource,)).fetchone()
            outcome = 'replayed' if row and row[0] == f'req-{index}' else 'conflict'
        except sqlite3.OperationalError as exc:
            conn.rollback()
            outcome = 'sqlite_error:' + str(exc)
        finally:
            conn.close()
        return {'index': index, 'outcome': outcome, 'ms': (time.perf_counter()-start)*1000}

    with ThreadPoolExecutor(10) as executor:
        rows = list(executor.map(attempt, range(10)))
    winner = next((row['index'] for row in rows if row['outcome'] == 'inserted'), None)
    replay = attempt(winner, False) if winner is not None else None
    with connect() as conn:
        count = conn.execute('SELECT COUNT(*) FROM experiment').fetchone()[0]
    result = {'kind': kind, 'profile': profile, 'config': config, 'results': rows,
              'latency': summarize([r['ms'] for r in rows]), 'row_count': count,
              'replay': replay, 'automatic_retries': 0}
    save(f'{kind}-{profile}', result)
    assert [r['outcome'] for r in rows].count('inserted') == 1
    assert [r['outcome'] for r in rows].count('conflict') == 9
    assert count == 1 and replay['outcome'] == 'replayed'


def test_transactions_without_unique_allow_duplicate_resources(tmp_path):
    _, connect, _ = allocate(tmp_path, 'default')
    with connect() as conn:
        conn.execute('CREATE TABLE no_unique_control (resource TEXT, car TEXT)')
    barrier = threading.Barrier(10)
    def insert(index):
        barrier.wait(timeout=15)
        with connect() as conn:
            conn.execute('INSERT INTO no_unique_control VALUES (?,?)', ('A-01', str(index)))
    with ThreadPoolExecutor(10) as executor:
        list(executor.map(insert, range(10)))
    with connect() as conn:
        assert conn.execute('SELECT count(*) FROM no_unique_control').fetchone()[0] == 10
    # 의도적 반례: 쓰기 직렬화/commit만으로 업무 중복을 막지 못한다.
