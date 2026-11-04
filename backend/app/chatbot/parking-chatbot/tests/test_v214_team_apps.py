"""Exercise both team factories in isolated processes without enumerating app.routes."""
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parents[3]
AVAILABLE = (all(importlib.util.find_spec(name) for name in ("fastapi", "httpx"))
             and (REPO / "backend/app/demo_main.py").is_file())


@unittest.skipUnless(AVAILABLE, "팀 레포 또는 FastAPI/httpx가 필요합니다.")
class TeamAppsTests(unittest.TestCase):
    def probe(self, mode):
        code = """
import os
import threading
from contextlib import ExitStack
from fastapi.testclient import TestClient
from backend.app.chatbot.router import router, _PACKAGE
from importlib import import_module
e = import_module(_PACKAGE + '.engine')
http = import_module(_PACKAGE + '.http_router')
llm = import_module(_PACKAGE + '.llm_router')
from backend.app.main import create_app
from backend.app.demo_main import create_app as create_demo_app
from backend.app.chatbot import team_preview
threads = set(threading.enumerate())
mode = os.environ['V214_APP_TEST']
if mode == 'main':
    apps = [create_app()]
elif mode == 'demo':
    apps = [create_demo_app(token='v214-local-token-only')]
elif mode == 'two':
    apps = [create_app(), create_demo_app(token='v214-local-token-only')]
else:
    apps = [team_preview.create_app(), team_preview.create_demo_app()]
runtime = e.RUNTIME_ID
slots, records = llm._slots, e.LOG
counts = llm.stats()['counts']
record_limit = records.maxlen
async def capacity_state():
    admission, workers = http._chat_capacity.get()
    return admission.value, workers.total_tokens, workers.borrowed_tokens
with ExitStack() as stack:
    clients = []
    for app in apps:
        if mode != 'preview':
            app.include_router(router)
        clients.append(stack.enter_context(TestClient(app)))
    for index, client in enumerate(clients):
        reply = client.post('/api/v1/chat', json={'session_id':f'app-{index}', 'text':'A 빈자리'})
        assert reply.status_code == 200, reply.text
        assert reply.json()['runtime_id'] == runtime
        assert reply.json()['text']
        stats = client.get('/api/v1/chat/stats').json()
        assert stats['llm']['inflight'] == 0
        assert not stats['llm']['ready'] and not stats['llm']['preparing']
        assert stats['llm']['counts'] == counts
        assert stats['retention']['uncertain_payments'] == 0
        assert stats['total'] == index + 1
        limit = e.config.CHAT_MAX_CONCURRENT
        assert client.portal.call(capacity_state) == (limit, limit, 0)
        assert llm._slots is slots and e.LOG is records and records.maxlen == record_limit
# AnyIO worker shutdown timing differs across Starlette versions. Check only
# chatbot LLM workers; the HTTP pool belongs to TestClient/AnyIO.
assert not [t for t in threading.enumerate() if t not in threads and t.name.startswith('parking-llm-')]
assert e.config.LLM_PROVIDER == 'none'
print(mode, len(apps), 'apps: POST 200')
"""
        result = subprocess.run(
            [sys.executable, "-c", code], cwd=REPO,
            env={**os.environ, "DATA_SOURCE":"mock", "LLM_PROVIDER":"none", "ADMIN_TOKEN":"",
                 "PREVIEW_DEMO_TOKEN":"v214-local-token-only", "V214_APP_TEST":mode,
                 "PYTHONIOENCODING":"utf-8"},
            capture_output=True, encoding="utf-8", errors="replace", timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("POST 200", result.stdout)

    def test_main_factory(self):
        self.probe("main")

    def test_demo_factory(self):
        self.probe("demo")

    def test_two_apps_share_one_runtime_safely(self):
        self.probe("two")

    def test_both_preview_factories(self):
        self.probe("preview")
