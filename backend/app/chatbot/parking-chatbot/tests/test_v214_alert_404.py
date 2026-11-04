"""Missing alert API differs from an outage without asserting all-clear."""
import unittest
from unittest.mock import patch

from chatbot import engine as e
from chatbot import config
from tests import test_api_contract as wire


class Alert404Tests(unittest.TestCase):
    def setUp(self):
        self.fixture = wire.ApiContractTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)

    def test_three_vacancy_queries_match_the_healthy_format_and_chips(self):
        questions = ("A 빈자리", "B 빈자리", "전체 빈자리")
        answers = [e.handle("missing", text=text) for text in questions]
        self.assertEqual(sum("경보 상태를 확인할 수 없어요." in r["text"] for r in answers), 1)
        self.assertTrue(all(r["banner"] is None for r in answers))
        self.assertEqual(e.stats()["alerts_source"], "not_provided")
        self.fixture.alerts = []
        healthy = [e.handle("healthy", text=text) for text in questions]
        for before, after in zip(answers, healthy):
            self.assertEqual(before["text"].replace("\n경보 상태를 확인할 수 없어요.", ""), after["text"])
            self.assertEqual(before["chips"], after["chips"])

    def test_new_session_and_polling(self):
        first = e.handle("same", text="A 빈자리")
        for _ in range(3):
            e.updates("same")
        self.assertIn("경보 상태를 확인할 수 없어요.", first["text"])
        self.assertNotIn("경보 상태를 확인할 수 없어요.", e.handle("same", text="B 빈자리")["text"])
        self.assertIn("경보 상태를 확인할 수 없어요.", e.handle("other", text="B 빈자리")["text"])

    def test_direct_alert_query_is_always_explicit(self):
        for _ in range(3):
            self.assertIn(e.T.ALERT_UNAVAILABLE, e.handle("direct", text="경보 알려줘")["text"])

    def test_outage_and_missing_transitions_get_fresh_notice(self):
        e.handle("transition", text="A 빈자리")
        original = self.fixture.api._request_uncached
        for status in (503, 401, 403, 0, -1):
            def response(method, path, body=None):
                return (status, {}, {}) if path == config.API_PATHS["alerts"] else original(method, path, body)

            with self.subTest(status=status), patch.object(self.fixture.api, "_request_uncached", side_effect=response):
                answer = e.handle("transition", text="A 빈자리")
                self.assertIn("빈자리 1자리, 확인 중 1칸", answer["text"])
                self.assertEqual([c["id"] for c in answer["chips"]], ["empty_all", "call_admin"])
                self.assertEqual(e.stats()["alerts_source"], "unavailable")
            restored = e.handle("transition", text="A 빈자리")
            self.assertIn("경보 상태를 확인할 수 없어요.", restored["text"])
            self.assertEqual(e.stats()["alerts_source"], "not_provided")

    def test_recovery_and_last_level_two(self):
        e.handle("safe", text="A 빈자리")
        self.fixture.alerts = []
        healthy = e.handle("safe", text="A 빈자리")
        self.assertEqual(e.stats()["alerts_source"], "ok")
        self.assertIsNone(healthy["banner"])
        self.fixture.alerts = [{"alert_id":"v214", "type":"mock_fire_led", "status":"active",
                                "level":2, "lot_id":"A", "space_id":"A-02"}]
        active = e.handle("safe", text="A 빈자리")
        self.assertIsNotNone(active["safety"])
        self.fixture.alerts = None
        missing = e.handle("safe", text="A 빈자리")
        self.assertIsNotNone(missing["safety"])
        self.assertEqual(e.current_alert()["level"], 2)
        self.assertIsNotNone(missing["banner"])

    def test_missing_never_claims_no_alert(self):
        for text in ("A 빈자리", "B 빈자리", "전체 빈자리", "경보 알려줘"):
            answer = e.handle("claims", text=text)
            self.assertNotIn("경보 없음", answer["text"])
            self.assertNotIn("경보는 없", answer["text"])
            self.assertNotIn("안전해요", answer["text"])
