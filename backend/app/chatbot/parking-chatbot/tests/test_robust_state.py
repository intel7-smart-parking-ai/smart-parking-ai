"""E: state isolation, confirmation lifetime, expiry and late classification."""
from concurrent.futures import ThreadPoolExecutor
import threading
import time
import unittest
from unittest.mock import patch
from chatbot import config, engine as e
from chatbot.adapters import CachedAdapter, MockAdapter
from safety_check import assert_safe
from tests.test_llm_router import admin_proposal, conversation_reply


class RobustStateTests(unittest.TestCase):
    def setUp(self):
        self.p = patch.object(e, "data", CachedAdapter(MockAdapter()))
        self.p.start()
        self.addCleanup(self.p.stop)
        e.reset_all()

    def quote(self, sid="s"):
        e.get_session(sid).car_id = "33"
        return e.handle(sid, text="결제")

    def test_cancel_new_confirmation_new_key_and_success_clears_state(self):
        self.quote()
        first = e.get_session("s").pending.params["idempotency_key"]
        e.handle("s", text="취소")
        self.assertIsNone(e.get_session("s").pending)
        quote = self.quote()
        second = e.get_session("s").pending.params["idempotency_key"]
        self.assertNotEqual(first, second)
        answer = e.handle("s", chip_id="confirm_yes", confirmation_token=quote["confirmation_token"])
        assert_safe(answer)
        self.assertIsNone(e.get_session("s").pending)
        self.assertTrue(e.data.get_fee("33")["paid"])

    def test_failed_request_replay_never_sends_second_payment(self):
        quote = self.quote()
        with patch.object(e.data.inner, "pay", side_effect=OSError("injected")) as send:
            first = e.handle("s", chip_id="confirm_yes", confirmation_token=quote["confirmation_token"], request_id="pay-1")
            second = e.handle("s", chip_id="confirm_yes", confirmation_token=quote["confirmation_token"], request_id="pay-1")
        self.assertEqual(send.call_count, 1)
        self.assertTrue(second["meta"]["replayed"])
        self.assertIn("확인", first["text"])
        self.assertIsNone(e.get_session("s").pending)

    def test_alert_transitions_cancel_confirmation_and_never_resume_payment(self):
        self.quote()
        e.data.set_alert(2, "A", 1)
        answer = e.handle("s", text="요금")
        assert_safe(answer, {"level": 2})
        self.assertIsNone(e.get_session("s").pending)
        e.data.set_alert(0)
        e.handle("s", text="네")
        self.assertFalse(e.data.get_fee("33")["paid"])

    def test_late_llm_result_ignored_if_alarm_changed(self):
        entered, release = threading.Event(), threading.Event()
        def delayed_proposal(*_):
            entered.set()
            self.assertTrue(release.wait(2))
            return admin_proposal()
        with conversation_reply(delayed_proposal):
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(e.handle, "s", text="현장에 누가 와 주실래")
                try:
                    self.assertTrue(entered.wait(1))
                    e.data.set_alert(2, "A", 1)
                finally:
                    release.set()
                answer = future.result(2)
        assert_safe(answer, {"level": 2, "new_calls": len(e.admin_calls())})
        self.assertEqual(len(e.admin_calls()), 0)

    def test_session_and_pending_expiry(self):
        old = e.get_session("old")
        old.last_seen = time.monotonic()-config.SESSION_IDLE_SEC-1
        e.get_session("new")
        self.assertNotIn("old", e.SESSIONS)
        self.quote()
        e.get_session("s").pending.expires_at = time.monotonic()-1
        answer = e.handle("s", text="네")
        self.assertIn("확인 시간", answer["text"])
        self.assertFalse(e.data.get_fee("33")["paid"])

    def test_public_views_and_chips_do_not_share_mutable_results(self):
        public = config.catalog.public()
        public["lots"][0]["name"] = "modified"
        public["zones"][0]["name"] = "modified"
        self.assertNotEqual(config.catalog.public()["lots"][0]["name"], "modified")
        self.assertNotEqual(config.catalog.public()["zones"][0]["name"], "modified")
        chips = config.catalog.chips()
        chips["lot_A"]["params"]["lot_id"] = "bad"
        self.assertEqual(config.catalog.chips()["lot_A"]["params"]["lot_id"], "A")
