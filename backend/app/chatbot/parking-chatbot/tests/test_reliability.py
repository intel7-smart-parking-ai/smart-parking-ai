"""상태 전환·중복 요청·응답 유실·수정 충돌의 행동 검증."""
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from unittest.mock import patch

from chatbot import engine as e, incidents, parser
from chatbot.adapters import CachedAdapter, DataUnavailable, MockAdapter, now, iso
from chatbot.state import Conflict
from chatbot.http_api import change_report
from chatbot.notifications import Notifications


class ReliabilityTests(unittest.TestCase):
    def setUp(self):
        self.patcher = patch.object(e, "data", CachedAdapter(MockAdapter()))
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        self.llm = patch.object(e.config, "LLM_PROVIDER", "none")
        self.llm.start(); self.addCleanup(self.llm.stop)
        e.reset_all()

    def quote(self, sid="s"):
        e.handle(sid, text="7번 결제")
        return e.handle(sid, text="네")

    def close_report(self):
        e.data.set_alert(2, "A", 6); e.current_alert()
        e.data.set_alert(0); e.current_alert()
        return incidents.list_public()[0]

    def test_vehicle_confirmation_does_not_execute_payment(self):
        result = self.quote()
        self.assertEqual(result["flow"], "confirm_action")
        self.assertFalse(e.data.get_fee("07")["paid"])
        self.assertIn("1,500원", result["text"])

    def test_query_and_negative_phrases_do_not_become_actions(self):
        for phrase, intent in (("결제했어?", "get_fee"), ("결제 취소", "cancel_action"),
                               ("관리자 부르지 마", "cancel_action"), ("내 차 결제 상태", "get_fee")):
            with self.subTest(phrase=phrase):
                self.assertEqual(parser.parse_rules(phrase).intent, intent)

    def test_cancel_discards_payment(self):
        self.quote()
        e.handle("s", text="결제 취소")
        e.handle("s", text="네")
        self.assertFalse(e.data.get_fee("07")["paid"])

    def test_unrecognized_interruption_discards_action(self):
        self.quote()
        e.handle("s", text="뿌잉")
        e.handle("s", text="네")
        self.assertFalse(e.data.get_fee("07")["paid"])

    def test_old_confirmation_button_does_not_execute_new_action(self):
        old = self.quote()
        current = e.handle("s", chip_id="call_admin")
        result = e.handle("s", chip_id="confirm_yes", confirmation_token=old["confirmation_token"])
        self.assertEqual(len(e.admin_calls()), 1)
        self.assertFalse(e.data.get_fee("07")["paid"])
        self.assertEqual(result["confirmation_token"], current["confirmation_token"])

    def test_expired_confirmation_does_not_pay(self):
        self.quote()
        expires = e.get_session("s").pending.expires_at
        with patch("chatbot.state.time.monotonic", return_value=expires + 1):
            result = e.handle("s", text="네")
        self.assertIn("확인 시간이 지나", result["text"])
        self.assertFalse(e.data.get_fee("07")["paid"])

    def test_parallel_retries_return_same_payment_once(self):
        quote = self.quote()
        def confirm(_):
            return e.handle("s", chip_id="confirm_yes", confirmation_token=quote["confirmation_token"], request_id="same-command")
        with patch.object(e.data, "pay", wraps=e.data.pay) as pay:
            with ThreadPoolExecutor(max_workers=8) as pool:
                results = list(pool.map(confirm, range(12)))
            self.assertEqual(pay.call_count, 1)
        # 작업 결과는 같고, 화면 상태 버전/실제 조회 지연은 재시도마다 최신이다.
        self.assertTrue(all(r["card"] == results[0]["card"] for r in results))
        self.assertEqual(results[0]["card"]["data"]["status"], "paid")

    def test_request_id_reuse_with_different_payload_conflicts(self):
        e.handle("s", chip_id="empty_all", request_id="one")
        with self.assertRaises(Conflict):
            e.handle("s", chip_id="call_admin", request_id="one")

    def test_duplicate_admin_confirmation_registers_once(self):
        q = e.handle("s", chip_id="call_admin")
        for _ in range(3):
            e.handle("s", chip_id="confirm_yes", confirmation_token=q["confirmation_token"], request_id="admin-one")
        self.assertEqual(len(e.admin_calls()), 1)

    def test_quote_change_requires_new_confirmation(self):
        self.quote()
        e.data.inner.cars["07"]["entered_at"] -= timedelta(minutes=20)
        result = e.handle("s", text="네")
        self.assertEqual(result["flow"], "confirm_action")
        self.assertFalse(e.data.get_fee("07")["paid"])

    def test_uncertain_payment_is_not_repeated_in_another_session(self):
        self.quote()
        with patch.object(e.data, "pay", side_effect=DataUnavailable()):
            e.handle("s", text="네")
        result = self.quote("other")
        self.assertNotEqual(result["flow"], "confirm_action")
        self.assertIn("불확실", result["text"])

    def test_stale_fee_cannot_prepare_payment(self):
        e.handle("s", text="7번 결제")
        fee = {**e.data.get_fee("07"), "stale": True}
        with patch.object(e.data, "get_fee", return_value=fee):
            result = e.handle("s", text="네")
        self.assertNotEqual(result["flow"], "confirm_action")
        self.assertFalse(e.data.get_fee("07")["paid"])

    def test_lost_notification_response_can_be_retried_and_acknowledged(self):
        e.data.set_alert(1, "B", 4)
        first = e.updates("s", cursor=0)
        retry = e.updates("s", cursor=0)
        self.assertEqual(first["messages"], retry["messages"])
        self.assertEqual(e.updates("s", cursor=retry["cursor"])["messages"], [])

    def test_lv2_notification_cancels_pending_payment(self):
        q = self.quote()
        e.data.set_alert(2, "A", 6)
        notice = e.updates("s")["messages"][0]
        self.assertFalse(any(c["id"] == "confirm_yes" for c in notice["chips"]))
        self.assertIsNone(e.get_session("s").pending)
        e.handle("s", chip_id="confirm_yes", confirmation_token=q["confirmation_token"])
        self.assertFalse(e.data.get_fee("07")["paid"])

    def test_alert_receipt_count_requires_ack(self):
        e.data.set_alert(2, "A", 6)
        notification = e.updates("s")
        self.assertEqual(incidents.list_public()[0]["notified_count"], 0)
        e.updates("s", cursor=notification["cursor"])
        self.assertEqual(incidents.list_public()[0]["notified_count"], 1)



    def test_old_runtime_cannot_execute_action_after_reset(self):
        old = e.RUNTIME_ID
        e.reset_all()
        with self.assertRaises(Conflict):
            e.handle("s", chip_id="call_admin", runtime_id=old)
        response = e.updates("s", cursor=999, runtime_id=old)
        self.assertTrue(response["resync_required"])

    def test_backend_alert_version_reset_still_closes_report(self):
        e.data.inner.alert = {"level": 2, "version": 99, "lot_id": "A", "spot_no": 6}
        e.current_alert()
        e.data.inner.alert = {"level": 0, "version": 0}
        e.current_alert()
        self.assertIsNotNone(incidents.list_public()[0]["closed_at"])

    def test_malformed_backend_data_becomes_unavailable_response(self):
        with patch.object(e.data.inner, "get_empty_spots", return_value={"lots": {"A": {"total": 8, "empty": 99}}, "updated_at": iso(now())}):
            response = e.handle("s", chip_id="empty_all")
        self.assertTrue(response["meta"]["unavailable"])

    def test_old_success_response_is_not_treated_as_fresh(self):
        d = e.data.inner.get_empty_spots()
        d["updated_at"] = iso(now() - timedelta(hours=1))
        with patch.object(e.data.inner, "get_empty_spots", return_value=d):
            with self.assertRaises(DataUnavailable):
                e.data.get_empty_spots()

    def test_expired_cache_is_not_used(self):
        e.data.get_empty_spots()
        with patch.object(e.data.inner, "get_empty_spots", side_effect=OSError()), \
             patch.object(e.config, "CACHE_MAX_STALE_SEC", -1):
            with self.assertRaises(DataUnavailable):
                e.data.get_empty_spots()

    def test_failed_affected_vehicle_read_is_unknown_not_zero(self):
        with patch.object(e.data, "list_cars", side_effect=DataUnavailable()):
            e.data.set_alert(2, "A", 6); e.current_alert()
        report = incidents.list_public()[0]
        self.assertFalse(report["affected_known"])
        self.assertIn("영향 범위 미확인", report["report"])

    def test_report_checkbox_retry_is_idempotent(self):
        report = self.close_report()
        body = {"runtime_id": e.RUNTIME_ID, "expected_revision": report["revision"], "index": 0, "checked": True}
        first = change_report(report["id"], "check", body)
        second = change_report(report["id"], "check", body)
        self.assertEqual(first, second)
        self.assertTrue(second["checklist"][0]["done_at"])

    def test_second_admin_cannot_overwrite_newer_note(self):
        report = self.close_report()
        body = {"runtime_id": e.RUNTIME_ID, "expected_revision": report["revision"], "text": "첫 관리자 메모"}
        change_report(1, "notes", body)
        with self.assertRaises(Conflict):
            change_report(1, "notes", {**body, "text": "오래된 화면 메모"})
        self.assertEqual(incidents.list_public()[0]["notes"], "첫 관리자 메모")

    def test_confirm_and_notes_commit_together(self):
        e.data.set_alert(2, "A", 6); e.current_alert()
        report = incidents.list_public()[0]
        body = {"runtime_id": e.RUNTIME_ID, "expected_revision": report["revision"], "notes": "확정 메모"}
        with self.assertRaises(ValueError):
            change_report(1, "confirm", body)
        self.assertEqual(incidents.list_public()[0]["notes"], "")
        e.data.set_alert(0); e.current_alert()
        body["expected_revision"] = incidents.list_public()[0]["revision"]
        result = change_report(1, "confirm", body)
        self.assertEqual((result["status"], result["notes"]), ("확정", "확정 메모"))

    def test_reporting_old_runtime_cannot_edit_reused_report_id(self):
        report = self.close_report(); old = e.RUNTIME_ID
        e.reset_all(); self.close_report()
        with self.assertRaises(Conflict):
            change_report(1, "notes", {"runtime_id": old, "expected_revision": report["revision"], "text": "이전 보고서 메모"})

    def test_unknown_payment_status_is_not_reported_as_car_missing(self):
        with patch.object(e.data.inner, "pay", return_value={"status": "backend_error"}):
            with self.assertRaises(DataUnavailable):
                e.data.pay("07")


class QueuePolicyTests(unittest.TestCase):

    def test_expired_notification_reports_gap(self):
        queue = Notifications(ttl=10)
        with patch("chatbot.notifications.time.monotonic", return_value=100):
            queue.enqueue("s", {"text": "notice"})
        with patch("chatbot.notifications.time.monotonic", return_value=111):
            response = queue.read("s", 0)
        self.assertEqual(response["messages"], [])
        self.assertTrue(response["resync_required"])


if __name__ == "__main__":
    unittest.main()
