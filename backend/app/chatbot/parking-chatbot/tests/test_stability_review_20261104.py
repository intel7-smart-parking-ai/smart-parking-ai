"""Regression probes for response replay and incomplete team API snapshots."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import threading
import time
import unittest
from unittest.mock import patch

from chatbot import config, engine as e
from chatbot.adapters import CachedAdapter, DataUnavailable, MockAdapter, request_deadline
from chatbot.contracts import alerts_source
from tests import test_api_contract as wire


class ReplaySafetyTests(unittest.TestCase):
    def setUp(self):
        self.replace = patch.object(e, "data", CachedAdapter(MockAdapter()))
        self.replace.start()
        self.addCleanup(self.replace.stop)
        e.reset_all()

    def test_retry_adds_current_safety_after_new_level_two(self):
        e.handle("retry", text="빈자리", request_id="lost-response")
        e.data.set_alert(2, "A", 5)
        result = e.handle("retry", text="빈자리", request_id="lost-response")
        self.assertEqual(result["banner"]["level"], 2)
        self.assertEqual(result["safety"], e.T.safety_card(e.current_alert()))
        self.assertIsNotNone(result["safety"])
        self.assertTrue(result["meta"]["replayed"])

    def test_retry_removes_old_safety_after_confirmed_clear(self):
        e.data.set_alert(2, "A", 5)
        original = e.handle("retry", text="경보", request_id="lost-response")
        self.assertIsNotNone(original["safety"])
        e.data.set_alert(0)
        result = e.handle("retry", text="경보", request_id="lost-response")
        self.assertIsNone(result["banner"])
        self.assertIsNone(result["safety"])


class TeamBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.fixture = wire.ApiContractTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)

    def active_alert(self):
        self.fixture.alerts = [{"alert_id":"review", "type":"mock_fire_led", "status":"active",
                                "level":2, "lot_id":"A", "space_id":"A-02"}]
        self.assertEqual(e.current_alert()["level"], 2)

    def test_unexpected_refresh_error_keeps_previous_safety_for_other_requests(self):
        self.active_alert()
        with patch.object(e.data, "get_alert", side_effect=RuntimeError("injected observer failure")):
            with self.assertRaises(RuntimeError):
                e.current_alert()
        self.assertEqual(e._last_alert["level"], 2)
        self.assertTrue(e._last_alert["unavailable"])
        self.assertFalse(e._alert_refreshing)
        self.assertIsNotNone(e.busy_response()["safety"])

    def test_observer_error_does_not_publish_a_partially_processed_clear(self):
        self.active_alert()
        self.fixture.alerts = []
        with patch.object(e.incidents, "observe", side_effect=RuntimeError("injected observer failure")):
            with self.assertRaises(RuntimeError):
                e.current_alert()
        self.assertEqual(e._last_alert["level"], 2)
        self.assertTrue(e._last_alert["unavailable"])

    def test_refresh_wait_timeout_is_outage_even_after_previous_404(self):
        previous = e.current_alert()
        self.assertEqual(alerts_source(previous), "not_provided")
        entered, release = threading.Event(), threading.Event()

        def delayed():
            entered.set()
            if not release.wait(3):
                raise RuntimeError("test did not release refresh")
            return deepcopy(previous)

        with patch.object(e.data, "get_alert", side_effect=delayed), ThreadPoolExecutor(1) as pool:
            owner = pool.submit(e.current_alert)
            try:
                self.assertTrue(entered.wait(2))
                token = request_deadline.set(time.monotonic() - 1)
                try:
                    result = e.current_alert()
                finally:
                    request_deadline.reset(token)
            finally:
                release.set()
                owner.result(timeout=3)
        self.assertEqual(alerts_source(result), "unavailable")
        self.assertEqual(result["http_status"], 0)

    def test_paginated_alerts_never_clear_last_level_two(self):
        self.active_alert()
        self.fixture.alerts = []
        original = self.fixture.api._request_uncached

        def paged(method, path, body=None):
            status, value, headers = original(method, path, body)
            if path == config.API_PATHS["alerts"]:
                value = {**value, "next_cursor":"unread-page"}
            return status, value, headers

        with patch.object(self.fixture.api, "_request_uncached", side_effect=paged):
            result = e.current_alert()
        self.assertEqual(result["level"], 2)
        self.assertTrue(result.get("stale") or result.get("unavailable"))

    def test_nullable_server_confidence_preserves_explicit_level_two(self):
        self.fixture.alerts = [{"alert_id":"nullable", "type":"mock_fire_led", "status":"active",
                                "level":2, "lot_id":"A", "space_id":None, "confidence":None}]
        result = e.handle("nullable", text="경보 알려줘")
        self.assertIsNotNone(result["safety"])
        self.assertIsNone(result["card"]["data"]["confidence"])
        report = e.incidents.list_public()[0]
        self.assertIsNone(report["max_confidence"])
        self.assertIn("최대 신뢰도 미확인", report["report"])

    def test_unknown_confidence_becomes_known_on_acknowledgement(self):
        self.fixture.alerts = [{"alert_id":"nullable", "type":"mock_fire_led", "status":"active",
                                "level":2, "lot_id":"A", "space_id":None, "confidence":None}]
        e.handle("nullable", text="경보 알려줘")
        self.fixture.alerts[0].update(status="acknowledged", confidence=0.73)
        e.handle("nullable", text="경보 알려줘")
        report = e.incidents.list_public()[0]
        self.assertEqual(report["max_confidence"], 0.73)
        self.assertIn("최대 신뢰도 73%", report["report"])

    def test_paginated_lots_do_not_replace_the_complete_catalog(self):
        ids = config.catalog.ids()
        original = self.fixture.api._request_uncached

        def paged(method, path, body=None):
            status, value, headers = original(method, path, body)
            if path == config.API_PATHS["lots"]:
                value = {**value, "items":value["items"][:1], "next_cursor":"unread-page"}
            return status, value, headers

        with patch.object(self.fixture.api, "_request_uncached", side_effect=paged):
            with self.assertRaises(DataUnavailable):
                e.data.get_empty_spots()
        self.assertEqual(config.catalog.ids(), ids)

    def test_paginated_rates_are_not_presented_as_a_complete_tariff(self):
        policy = {"lot_id":"A", "base_minutes":30, "base_fee":1000,
                  "unit_minutes":10, "extra_fee":500, "daily_cap":10000}
        original = self.fixture.api._request_uncached

        def paged(method, path, body=None):
            if path == config.API_PATHS["rate_policies"]:
                return 200, {"items":[policy], "next_cursor":"unread-page"}, {}
            return original(method, path, body)

        with patch.object(self.fixture.api, "_request_uncached", side_effect=paged):
            result = e.data.get_rate_policies()
        self.assertTrue(result.get("unavailable"))
        self.assertNotIn("policies", result)


if __name__ == "__main__":
    unittest.main()
