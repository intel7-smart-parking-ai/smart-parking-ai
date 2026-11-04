"""A small shared worker pool must leave capacity for the team's sync API."""
import asyncio
from concurrent.futures import TimeoutError
import importlib.util
import threading
import unittest
from unittest.mock import patch

AVAILABLE = all(importlib.util.find_spec(name) for name in ("fastapi", "httpx"))


@unittest.skipUnless(AVAILABLE, "FastAPI/httpx unavailable")
class EmbeddedCapacityTests(unittest.IsolatedAsyncioTestCase):
    async def test_sync_backend_can_progress_under_chat_load(self):
        import anyio
        from fastapi import FastAPI
        import httpx

        from chatbot import config, engine
        from chatbot.adapters import ApiAdapter, CachedAdapter, iso, now
        from server_fastapi import router

        app = FastAPI()
        app.include_router(router)
        loop = asyncio.get_running_loop()

        @app.get("/api/v1/alerts")
        def alerts():
            return {"items": [], "as_of": iso(now())}

        adapter = ApiAdapter(config.BACKEND_BASE_URL)
        failures = []
        barrier = threading.Barrier(4, timeout=5)
        handle = engine.handle

        def simultaneous_handle(*args, **kwargs):
            barrier.wait()
            # Exercise a synchronous team API dependency before the chatbot reply.
            try:
                backend_request("GET", "/api/v1/alerts")
            except TimeoutError:
                pass
            return handle(*args, **kwargs)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://127.0.0.1:8000") as client:
            def backend_request(method, path, body=None):
                future = asyncio.run_coroutine_threadsafe(client.request(method, path, json=body), loop)
                try:
                    reply = future.result(1.0)
                except TimeoutError:
                    failures.append(path)
                    future.cancel()
                    raise
                return reply.status_code, reply.json(), reply.headers

            limiter = anyio.to_thread.current_default_thread_limiter()
            old_capacity = limiter.total_tokens
            limiter.total_tokens = 4
            try:
                with (patch.object(adapter, "_request_uncached", side_effect=backend_request),
                      patch.object(engine, "data", CachedAdapter(adapter)),
                      patch.object(engine, "handle", side_effect=simultaneous_handle),
                      patch.object(config, "DATA_SOURCE", "api"),
                      patch.object(config, "EMBEDDED_CHAT_WORKERS", 4, create=True),
                      patch.object(config, "EMBEDDED", True),
                      patch.object(config, "ALERT_SHARE_SEC", 1)):
                    engine.reset_all()
                    try:
                        replies = await asyncio.wait_for(asyncio.gather(*(
                            client.post("/api/v1/chat", json={"session_id": f"load-{i}", "chip_id": "help"})
                            for i in range(16)
                        )), timeout=8)
                        self.assertEqual([reply.status_code for reply in replies], [200] * 16)
                        self.assertEqual(failures, [])
                    finally:
                        engine.reset_all()
            finally:
                limiter.total_tokens = old_capacity


@unittest.skipUnless(AVAILABLE, "FastAPI/httpx unavailable")
class EmbeddedImportTests(unittest.TestCase):
    def run_probe(self, code):
        import os
        from pathlib import Path
        import subprocess
        import sys

        repo = Path(__file__).resolve().parents[5]
        return subprocess.run(
            [sys.executable, "-c", code], cwd=repo,
            env={**os.environ, "LLM_PROVIDER": "none", "PYTHONIOENCODING": "utf-8"},
            capture_output=True, encoding="utf-8", timeout=15,
        )

    def test_no_standalone_app_or_thread_created(self):
        result = self.run_probe("""
import os
import sys
import threading
from unittest.mock import patch
from fastapi import FastAPI
paths = list(sys.path)
environment = dict(os.environ)
threads = set(threading.enumerate())
with patch.object(FastAPI, "__init__", side_effect=AssertionError("standalone app created")):
    from backend.app.chatbot.router import router
assert router.prefix == "/api/v1/chat"
assert paths == sys.path and environment == dict(os.environ)
assert threads == set(threading.enumerate())
assert "server_fastapi" not in sys.modules and "chatbot" not in sys.modules
""")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_foreign_chatbot_package_fails_explicitly(self):
        result = self.run_probe("""
import sys
from types import ModuleType
sys.modules["chatbot"] = ModuleType("chatbot")
from backend.app.chatbot.router import router
""")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("different top-level 'chatbot' package", result.stderr)
