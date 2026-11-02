"""선택 검사: Node.js + playwright가 필요합니다. 임시 서버를 자동 시작/종료합니다."""
import os
from pathlib import Path
import subprocess
import sys
import threading
from http.server import ThreadingHTTPServer
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ['DATA_SOURCE'] = 'mock'
os.environ['LLM_PROVIDER'] = 'none'
from server_dev import Handler
server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
thread = threading.Thread(target=server.serve_forever, daemon=True)
thread.start()
try:
    result = subprocess.run(['node', str(Path(__file__).with_name(sys.argv[1] if len(sys.argv)>1 else 'browser_checks.cjs'))],
                            env={**os.environ, 'TEST_BASE_URL': f'http://127.0.0.1:{server.server_port}'}, timeout=90)
    sys.exit(result.returncode)
finally:
    server.shutdown(); server.server_close(); thread.join()
