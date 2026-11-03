"""선택 검사: fastapi와 httpx가 설치된 환경에서 실행. 없으면 skip."""
import importlib.util
import unittest
from unittest.mock import patch

from chatbot import engine
from chatbot.adapters import CachedAdapter, MockAdapter

AVAILABLE = all(importlib.util.find_spec(m) for m in ("fastapi", "httpx"))


@unittest.skipUnless(AVAILABLE, "선택 의존성 fastapi/httpx 미설치")
class FastAPITests(unittest.TestCase):
    def setUp(self):
        from fastapi.testclient import TestClient
        from server_fastapi import app
        self.data_patch = patch.object(engine, "data", CachedAdapter(MockAdapter()))
        self.data_patch.start()
        self.addCleanup(self.data_patch.stop)
        engine.reset_all()
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def test_chat(self):
        r = self.client.post("/api/v1/chat", json={"session_id": "test", "chip_id": "empty_all"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["card"]["type"], "empty_spots")

    def test_alert_and_report(self):
        r = self.client.post("/api/v1/chat/demo/alert", json={"level": 2, "lot_id": "B", "spot_no": 3})
        self.assertEqual(r.status_code, 200)
        report = self.client.get("/api/v1/chat/incidents").json()["items"][0]
        self.assertEqual(report["lot_id"], "B")

    def test_invalid_alert(self):
        r = self.client.post("/api/v1/chat/demo/alert", json={"level": 9, "lot_id": "Z"})
        self.assertEqual(r.status_code, 422)

    def test_invalid_updates_session(self):
        r = self.client.get("/api/v1/chat/updates?session_id=")
        self.assertEqual(r.status_code, 422)

    def test_invalid_chat_body(self):
        r = self.client.post("/api/v1/chat", json={"text": []})
        self.assertEqual(r.status_code, 422)

    def test_removed_status_route_is_not_available(self):
        result = self.client.post("/api/v1/chat/admin/calls/1/status",json={})
        self.assertIn(result.status_code,(404,405))

    def test_static_pages(self):
        for path in ("/", "/admin.html"):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 200)

    def test_missing_session_is_rejected_in_both_endpoints(self):
        self.assertEqual(self.client.post("/api/v1/chat", json={"event":"start"}).status_code, 422)
        self.assertEqual(self.client.get("/api/v1/chat/updates").status_code, 422)
        self.assertNotIn("anon", engine.SESSIONS)

    def test_privileged_routes_require_configured_token(self):
        with patch.object(engine.config,"ADMIN_TOKEN","test-only"):
            for path in ("/admin/calls", "/incidents", "/stats", "/demo/cars"):
                self.assertEqual(self.client.get("/api/v1/chat"+path).status_code,401)
                self.assertEqual(self.client.get("/api/v1/chat"+path,headers={"Authorization":"Bearer test-only"}).status_code,200)
            self.assertEqual(self.client.post("/api/v1/chat/demo/reset").status_code,401)
            self.assertEqual(self.client.post("/api/v1/chat",json={"session_id":"public","chip_id":"empty_all"}).status_code,200)

    def test_demo_disable_is_enforced(self):
        with patch.object(engine.config,"DEMO_ENABLED",False):
            self.assertEqual(self.client.post("/api/v1/chat/demo/reset").status_code,404)
