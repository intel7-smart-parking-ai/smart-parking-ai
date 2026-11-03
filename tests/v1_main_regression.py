"""main worktree에서만 실행하는 기존53개 회귀용 DB 경로 제한 실행기."""
import os
from pathlib import Path
import sqlite3
import threading
import sys
import pytest
from v1_support import allocate


if __name__ == '__main__':
    if not (Path.cwd() / 'backend/app/billing/db.py').is_file():
        raise RuntimeError('billing이 있는 main worktree에서 실행해야 합니다.')
    sys.path.insert(0, str(Path.cwd()))
    base = Path(os.environ['KAN17_V1_REGRESSION_ROOT']).absolute()
    if base.exists():
        raise RuntimeError('새 임시 실행 디렉터리가 필요합니다.')
    original = sqlite3.connect
    mapping, lock = {}, threading.Lock()
    def protected(database, *args, **kwargs):
        path = Path(database).absolute()
        if not path.is_relative_to(base) or path.name.lower() == 'smart_parking.db':
            raise AssertionError('실사용/외부 DB 연결 금지')
        with lock:
            if str(path) not in mapping:
                if path.exists():
                    raise AssertionError('기존 파일 열기 금지')
                target, connector, _ = allocate(path.parent, 'default', raw_connect=original)
                mapping[str(path)] = (target, connector)
            target, connector = mapping[str(path)]
        return connector(target, *args, **kwargs)
    sqlite3.connect = protected
    raise SystemExit(pytest.main(['tests/', '-q', '-p', 'no:cacheprovider', '--basetemp', str(base)]))
