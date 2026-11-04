"""Record v2.10 wire shapes and detect missing fields or incompatible types."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = Path(__file__).with_name("public_contract_v210.json")
AVAILABLE = all(importlib.util.find_spec(name) for name in ("fastapi", "httpx"))


def shape(value):
    if isinstance(value, dict):
        return {key: shape(item) for key, item in value.items()}
    if isinstance(value, list):
        return [shape(item) for item in value]
    return type(value).__name__


def settings():
    from chatbot.state import Flow

    env = (ROOT / ".env.example").read_text(encoding="utf-8").splitlines()
    chips = json.loads((ROOT / "chatbot/chips.json").read_text(encoding="utf-8"))
    return {
        "env": sorted(line.split("=", 1)[0].strip() for line in env
                      if "=" in line and not line.lstrip().startswith("#")),
        "chips": sorted(chips["chips"]),
        "flow": {value.name: value.value for value in Flow},
    }


def http_contract():
    from fastapi.testclient import TestClient
    from chatbot import engine
    from chatbot.adapters import CachedAdapter, MockAdapter
    from server_fastapi import app, router

    prefix = "/api/v1/chat"
    with patch.object(engine, "data", CachedAdapter(MockAdapter())), TestClient(app) as client:
        engine.reset_all()

        def chat(sid, **body):
            return client.post(prefix, json={"session_id": sid, **body})

        def endpoint(path, body=None):
            if body is None:
                return client.get(prefix + path)
            return client.post(prefix + path, json=body)

        calls = {}
        calls["chat"] = chat("home", chip_id="empty_all")
        calls["vehicle_confirmation"] = chat("car", text="11번 차 어디 있어?")
        pending = chat("fee", text="11번 결제").json()
        calls["payment_confirmation"] = chat("fee", chip_id="confirm_yes",
                                             confirmation_token=pending.get("confirmation_token"))
        # The underlying location read can report entry before a slot is assigned.
        with patch.object(engine.data, "find_car", return_value={
            "car_id": "11", "lot_id": "A", "status": "pending", "spot_no": None,
        }):
            engine.get_session("pending").car_id = "11"
            calls["pending_location"] = chat("pending", chip_id="find_car")
        calls["demo_alert"] = endpoint("/demo/alert", {"level": 2, "lot_id": "A", "spot_no": 1})
        calls["lv2"] = chat("safe", chip_id="alert_near")
        calls["updates"] = endpoint("/updates?session_id=safe")
        calls["suggest"] = endpoint("/suggest?q=주차")
        calls["config"] = endpoint("/config")
        calls["incidents"] = endpoint("/incidents")
        calls["admin_calls"] = endpoint("/admin/calls")
        calls["demo_cars"] = endpoint("/demo/cars")
        engine.reset_all()
    return {
        "routes": sorted([route.path, sorted(route.methods)] for route in router.routes),
        "prefix": router.prefix,
        "responses": {key: [response.status_code, shape(response.json())] for key, response in calls.items()},
    }


class PublicContractTests(unittest.TestCase):
    def setUp(self):
        self.expected = json.loads(FIXTURE.read_text(encoding="utf-8"))

    def test_static_contract(self):
        self.assertEqual(settings(), self.expected["settings"])

    @unittest.skipUnless(AVAILABLE, "FastAPI/httpx unavailable")
    def test_http_contract(self):
        actual = http_contract()
        expected = self.expected["http"]
        self.assertEqual(actual["routes"], expected["routes"])
        self.assertEqual(actual["prefix"], expected["prefix"])
        for key, value in expected["responses"].items():
            with self.subTest(endpoint=key):
                self.assertEqual(value[0], 200)
                self.assert_shape(value, actual["responses"][key])

    def assert_shape(self, expected, actual):
        if isinstance(expected, dict):
            self.assertIsInstance(actual, dict)
            for key, value in expected.items():
                self.assertIn(key, actual)
                self.assert_shape(value, actual[key])
        elif isinstance(expected, list):
            self.assertIsInstance(actual, list)
            self.assertGreaterEqual(len(actual), len(expected))
            for first, second in zip(expected, actual):
                self.assert_shape(first, second)
        else:
            self.assertEqual(expected, actual)

    @unittest.skipUnless(AVAILABLE, "FastAPI/httpx unavailable")
    def test_embedded_routes(self):
        repo = ROOT.parents[3]
        if not (repo / "backend/app/chatbot/router.py").exists():
            self.skipTest("Run from backend/app/chatbot/parking-chatbot")
        code = (
            "import json; from backend.app.chatbot.router import router;"
            "print(json.dumps([router.prefix,len(router.routes)]))"
        )
        result = subprocess.run([sys.executable, "-c", code], cwd=repo,
                                env={**os.environ, "LLM_PROVIDER": "none", "PYTHONIOENCODING": "utf-8"},
                                capture_output=True, encoding="utf-8", timeout=20, check=True)
        expected = self.expected["http"]
        self.assertEqual(json.loads(result.stdout), [expected["prefix"], len(expected["routes"])])


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT))
    FIXTURE.write_text(json.dumps({"settings": settings(), "http": http_contract()},
                                  ensure_ascii=False, indent=2), encoding="utf-8")
