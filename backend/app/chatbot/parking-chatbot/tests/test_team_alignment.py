"""Team React response, lot-scoped API reads and billing identity boundaries."""
from concurrent.futures import ThreadPoolExecutor
import importlib.util
import threading
import unittest
from unittest.mock import patch

from chatbot import adapters, config, engine
from tests import test_api_contract as wire


class ScopedLotTests(unittest.TestCase):
    def test_concurrent_different_lots_keep_separate_spaces(self):
        self.addCleanup(config.catalog.reset)
        api = adapters.ApiAdapter("http://127.0.0.1:8000")
        cache = adapters.CachedAdapter(api)
        a_entered = threading.Event()
        release_a = threading.Event()
        a_path = "/api/v1/parking-lots/A/spaces"
        b_path = "/api/v1/parking-lots/B/spaces"
        seen = []

        def request(method, path, body=None):
            seen.append(path)
            if path == a_path:
                a_entered.set()
                if not release_a.wait(5):
                    raise RuntimeError("A fixture was not released")
            if path == config.API_PATHS["lots"]:
                return 200, {"items": [
                    {"lot_id": lot, "total_spaces": 1, "empty_spaces": 1} for lot in ("A", "B")
                ], "as_of": adapters.iso(adapters.now())}, {}
            return 200, {"items": [{"space_id": "A-01", "status": "empty", "space_type": "general"}]}, {}

        def read(lot):
            scope = adapters.request_lot.set(lot)
            try:
                return cache.get_empty_spots()
            finally:
                adapters.request_lot.reset(scope)

        with patch.object(api, "_request_uncached", side_effect=request), ThreadPoolExecutor(2) as pool:
            first = pool.submit(read, "A")
            try:
                self.assertTrue(a_entered.wait(2))
                second = pool.submit(read, "B")
                self.assertEqual(second.result(timeout=2)["space_states"], {"B": {"A-01": "empty"}})
            finally:
                release_a.set()
            self.assertEqual(first.result(timeout=2)["space_states"], {"A": {"A-01": "empty"}})
        self.assertEqual(seen.count(a_path), 1)
        self.assertEqual(seen.count(b_path), 1)


class BillingAlignmentTests(unittest.TestCase):
    def setUp(self):
        self.fixture = wire.ApiContractTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.fixture.session_id = "confirmed-visit"
        self.fixture.alerts = []

    def mutate_reply(self, path, apply):
        handler = self.fixture.server.RequestHandlerClass
        original = handler.reply

        def reply(instance, body, status=200):
            if instance.path == path and status in (200, 201):
                body = {**body}
                apply(body)
            return original(instance, body, status)

        handler.reply = reply

    def test_wrong_fee_visit_returns_no_card_or_payment_confirmation(self):
        self.assertEqual(self.fixture.ask("11번 차 요금", "seed")["card"]["type"], "fee")
        self.mutate_reply("/api/v1/sessions/confirmed-visit/fee",
                          lambda value: value.update(car_id="20"))
        for text in ("11번 차 요금", "11번 결제"):
            with self.subTest(text=text):
                response = self.fixture.ask(text, text)
                self.assertIsNone(response["card"])
                self.assertIsNone(response["confirmation_token"])
        self.assertEqual(self.fixture.posts(), [])

    def test_changed_visit_is_not_an_uncertain_payment(self):
        before = self.fixture.ask("11번 결제")
        self.fixture.session_id = "new-visit"
        reply = engine.handle("s", chip_id="confirm_yes", confirmation_token=before["confirmation_token"])
        self.assertIsNone(reply["confirmation_token"])
        self.assertEqual(self.fixture.posts(), [])
        self.assertFalse(engine._uncertain_payments)

    def test_team_receipt_disagreeing_with_quote_does_not_report_success(self):
        self.mutate_reply("/api/v1/mock-payments", lambda value: value.update(
            status="completed", amount_krw=3000, car_id="11", lot_id="A", session_id="confirmed-visit"
        ))
        before = self.fixture.ask("11번 결제")
        reply = engine.handle("s", chip_id="confirm_yes", confirmation_token=before["confirmation_token"])
        self.assertIsNone(reply["card"])
        self.assertIn("결제 요청의 처리 결과를 확인하지 못했어요", reply["text"])
        self.assertEqual(len(self.fixture.posts()), 1)
        self.assertEqual(len(engine._uncertain_payments), 1)


@unittest.skipUnless(all(importlib.util.find_spec(name) for name in ("fastapi", "httpx")),
                     "FastAPI/httpx unavailable")
class FrontendContractTests(unittest.TestCase):
    def test_embedded_projection_preserves_text_and_exposes_safety_to_text_client(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from chatbot.http_router import router

        app = FastAPI()
        app.include_router(router)
        with (patch.object(config, "EMBEDDED", True),
              patch.object(engine, "data", adapters.CachedAdapter(adapters.MockAdapter())),
              TestClient(app) as client):
            engine.reset_all()
            self.addCleanup(engine.reset_all)
            path = "/api/v1/chat"
            body = {"session_id": "team-panel", "text": "A 빈자리"}
            first = client.post(path, json=body).json()
            self.assertEqual(first["messages"], [{"text": first["text"]}])
            self.assertEqual(first["card"]["type"], "empty_spots")
            engine.data.set_alert(2, "A", 1)
            alarm = client.post(path, json={**body, "text": "도움말"}).json()
            self.assertEqual(alarm["messages"][-1]["text"], alarm["text"])
            self.assertIn(alarm["safety"]["guidance"], "\n".join(item["text"] for item in alarm["messages"]))
