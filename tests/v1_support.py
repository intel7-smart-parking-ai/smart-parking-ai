"""V1 전용 임시 파일/설정/측정 도구. 운영 코드에서 import하지 않는다."""
import importlib.util
import json
import math
import os
from pathlib import Path
import sqlite3
import sys
from uuid import uuid4

PROFILES = ['default', 'wal', 'timeout', 'wal_timeout']
ROOT = Path(__file__).resolve().parents[1]


def allocate(tmp_path, profile, raw_connect=None):
    # main 별도 프로세스에서도 PR 브랜치를 병합하지 않고 동일 설정 가드를 읽어 사용한다.
    name = '_kan17_v1_guard'
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, ROOT / 'backend/app/database/settings.py')
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    guard = sys.modules[name]
    target = tmp_path / f'kan17-verify-{uuid4().hex}.sqlite3'
    from sqlalchemy.engine import URL
    settings = guard.DatabaseSettings.from_env(test_root=tmp_path,
        environ={'DATABASE_URL': URL.create('sqlite', database=str(target)).render_as_string()})
    guard.validate_target(settings.url, tmp_path)
    fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
    identity = os.fstat(fd)
    os.close(fd)
    original = raw_connect or sqlite3.connect

    class ClosingConnection(sqlite3.Connection):
        def __exit__(self, *args):
            try:
                return super().__exit__(*args)
            finally:
                self.close()

    def connect(database=target, *args, **kwargs):
        guard.validate_target(settings.url, tmp_path)
        if Path(database) != target:
            raise AssertionError('허용한 검증 파일 외 DB 연결 금지')
        current = target.lstat()
        if (current.st_dev, current.st_ino) != (identity.st_dev, identity.st_ino) or current.st_nlink != 1:
            raise AssertionError('검증 파일 변경/별칭 금지')
        kwargs.setdefault('factory', ClosingConnection)
        conn = original(target.as_uri() + '?mode=rw', *args, uri=True, **kwargs)
        if 'timeout' in profile:
            conn.execute('PRAGMA busy_timeout=10000')
        return conn

    with connect() as conn:
        if 'wal' in profile:
            assert conn.execute('PRAGMA journal_mode=WAL').fetchone()[0] == 'wal'
        config = {'journal_mode': conn.execute('PRAGMA journal_mode').fetchone()[0],
                  'busy_timeout_probe_ms': conn.execute('PRAGMA busy_timeout').fetchone()[0]}
    return target, connect, config


def summarize(values):
    ordered = sorted(values)
    return {'count': len(values), 'mean_ms': round(sum(values)/len(values), 3),
            'p95_ms': round(ordered[math.ceil(.95*len(ordered))-1], 3)}


def save(name, data):
    configured = os.environ.get('KAN17_V1_RESULTS')
    if not configured:
        return  # 일반 회귀 실행은 결과 파일 저장 없이도 검증할 수 있다.
    directory = Path(configured)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / (name + '.json')).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
