from unittest.mock import patch as _patch
"""2026-11-01 분석에서 재현한 결함을 기대 동작으로 고정한 회귀 검사."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import threading
import time
import unittest
import csv
from pathlib import Path
from unittest.mock import patch

from chatbot import engine as e, incidents, parser, config
from chatbot.adapters import CachedAdapter, MockAdapter, DataUnavailable, now, iso, request_deadline
from chatbot.access import require_admin, AccessDenied


class StabilityTests(unittest.TestCase):
    def setUp(self):
        p = patch.object(e, "data", CachedAdapter(MockAdapter()))
        p.start(); self.addCleanup(p.stop)
        e.reset_all()

    def choose(self, no="33", sid="s"):
        e.handle(sid, text=f"{no}번 차 위치")
        return e.handle(sid, text="네")

    def quote(self):
        e.handle("s", text="33번 결제")
        return e.handle("s", text="네")

    def alert(self, level, lot="A", spot=6):
        e.data.set_alert(level, lot, spot)
        return e.current_alert()

    def test_audit_language_regressions(self):
        with Path(__file__).with_name("questions_stability.csv").open(encoding="utf-8") as stream:
            for row in csv.DictReader(stream):
                with self.subTest(question=row["question"]):
                    r=parser.parse_rules(row["question"])
                    self.assertEqual((r.intent,r.params.get("lot_id",""),r.params.get("car_id","")),
                                     (row["intent"],row["lot_id"],row["car_id"]))

    def test_exhausted_external_budget_does_not_send_payment(self):
        from chatbot.adapters import ApiAdapter
        token = request_deadline.set(time.monotonic()-1)
        try:
            with patch("chatbot.adapters.urllib.request.urlopen") as send:
                with self.assertRaises(DataUnavailable): CachedAdapter(ApiAdapter("http://127.0.0.1:1")).pay("33")
            self.assertEqual(send.call_count,0)
        finally:
            request_deadline.reset(token)

    def test_new_incident_does_not_reuse_normal_call(self):
        self.choose()
        old = e.handle("s", chip_id="call_admin")["card"]["data"]
        a = self.alert(2)
        new = e.handle("s", chip_id="call_admin")["card"]["data"]
        self.assertNotEqual(old["id"], new["id"])
        self.assertEqual(new["incident_id"], a["incident_id"])
        self.assertIn("Lv2 개발원주차장 A-06", new["summary"])
        self.assertEqual(incidents.active()["admin_calls"], [new["id"]])

    def test_escalation_refreshes_same_call_and_revision(self):
        self.alert(1)
        old = e.handle("s", chip_id="call_admin")["card"]["data"]
        self.alert(2)
        new = e.handle("s", chip_id="call_admin")["card"]["data"]
        self.assertEqual(old["id"], new["id"])
        self.assertGreater(new["revision"], old["revision"])
        self.assertIn("Lv2", new["summary"])
        self.assertEqual(incidents.active()["admin_calls"], [new["id"]])

    def test_incident_change_during_summary_keeps_original_target(self):
        self.choose(); first = self.alert(2)
        entered, release = threading.Event(), threading.Event()
        original = e.data.get_fee
        def delayed(v):
            entered.set()
            if not release.wait(3):
                raise RuntimeError("test barrier timed out")
            return original(v)
        with patch.object(e.data, "get_fee", side_effect=delayed):
            with ThreadPoolExecutor(max_workers=1) as pool:
                task = pool.submit(e.handle, "s", chip_id="call_admin")
                try:
                    self.assertTrue(entered.wait(3))
                    second = self.alert(2, "B", 3)
                finally:
                    release.set()
                call = task.result(3)["card"]["data"]
        reports = {x["id"]: x for x in incidents.list_public()}
        self.assertEqual(call["incident_id"], first["incident_id"])
        self.assertIn(f"보고서 #{first['incident_id']}", call["summary"])
        self.assertIn("Lv2 개발원주차장 A-06", call["summary"])
        self.assertIn(call["id"], reports[first["incident_id"]]["admin_calls"])
        self.assertEqual(reports[second["incident_id"]]["admin_calls"], [])

    def test_call_uses_explicit_vehicle_without_changing_selected_vehicle(self):
        self.choose()
        call = e.handle("s", text="43번 차 관리자 불러주세요")["card"]["data"]
        self.assertEqual(call["car_id"], "43")
        self.assertIn("43번 차", call["summary"])
        self.assertEqual(e.get_session("s").car_id, "33")

    def test_negations_and_questions_do_not_create_calls(self):
        for phrase in ("관리자 불러주지 마세요 제가 해볼게요", "관리자 아직 안 왔어요",
                       "관리자를 호출하면 돈이 드나요?", "관리자 불러도 되나요?"):
            e.handle("s", text=phrase)
        self.assertEqual(e.admin_calls(), [])

    def test_payment_problem_and_method_do_not_open_payment(self):
        self.choose()
        for phrase in ("결제가 안 돼요", "결제 오류 났어요", "카드 결제 돼요?"):
            r = e.handle("s", text=phrase)
            self.assertEqual(r["flow"], "idle")
            self.assertIsNone(r["confirmation_token"])
        self.assertFalse(e.data.get_fee("33")["paid"])

    def test_cancel_payment_then_call_is_supported_limited_compound(self):
        self.quote()
        r = e.handle("s", text="결제 취소하고 관리자 불러줘")
        self.assertEqual(r["card"]["type"], "admin_call")
        self.assertIsNone(e.get_session("s").pending)
        self.assertFalse(e.data.get_fee("33")["paid"])

    def test_evacuation_and_smoke_are_not_hidden_by_admin_status(self):
        self.assertEqual(parser.parse_rules("직원 부르지 말고 대피 알려줘").intent, "evacuation_guide")
        self.assertEqual(parser.parse_rules("관리자를 불렀는데 연기가 더 나요").intent, "get_alerts")
        e.handle("s", text="직원 부르지 말고 대피 알려줘")
        self.assertEqual(e.admin_calls(), [])

    def test_explicit_correction_resumes_with_new_vehicle(self):
        e.handle("s", text="33번 결제")
        r = e.handle("s", text="아니 33번 말고 43번")
        self.assertIn("43번", r["text"])
        e.handle("s", text="네")
        self.assertEqual(e.get_session("s").car_id, "43")
        self.assertFalse(e.data.get_fee("33")["paid"])

    def test_correction_in_first_request_chooses_right_number(self):
        r = e.handle("s", text="33번 말고 43번 결제")
        self.assertIn("43번", r["text"])

    def test_ambiguous_vehicle_clears_old_confirmation(self):
        e.handle("s", text="33번 결제")
        e.handle("s", text="33번 43번 차량")
        e.handle("s", text="네")
        self.assertIsNone(e.get_session("s").car_id)
        self.assertFalse(e.data.get_fee("33")["paid"])

    def test_unknown_correction_does_not_approve_old_vehicle(self):
        e.handle("s", text="33번 결제")
        e.handle("s", text="아니 그 차 말고 다른 차")
        e.handle("s", text="네")
        self.assertIsNone(e.get_session("s").car_id)

    def test_replay_keeps_latest_banner_and_vehicle(self):
        self.choose()
        e.handle("s", chip_id="empty_all", request_id="lost")
        self.alert(2); e.data.exit_car("33")
        r = e.handle("s", chip_id="empty_all", request_id="lost")
        self.assertEqual(r["banner"]["level"], 2)
        self.assertEqual(r["vehicle_context"]["state"], "not_found")

    def test_replay_old_quote_does_not_hide_current_confirmation(self):
        e.handle("s", text="33번 결제")
        old = e.handle("s", text="네", request_id="lost")
        e.handle("s", chip_id="change_vehicle")
        new = e.handle("s", text="43")
        r = e.handle("s", text="네", request_id="lost")
        self.assertNotEqual(old["confirmation_token"], new["confirmation_token"])
        self.assertEqual(r["confirmation_token"], new["confirmation_token"])
        self.assertIn("43번", r["text"])

    def test_start_discards_incomplete_payment(self):
        q = self.quote()
        r = e.handle("s", event="start")
        self.assertIsNone(r["confirmation_token"])
        self.assertNotIn("confirm_yes", [x["id"] for x in r["chips"]])
        e.handle("s", chip_id="confirm_yes", confirmation_token=q["confirmation_token"])
        self.assertFalse(e.data.get_fee("33")["paid"])

    def test_poll_expires_confirmation_without_alert(self):
        self.quote(); e.get_session("s").pending.expires_at = time.monotonic() - 1
        r = e.updates("s")
        self.assertIsNone(e.get_session("s").pending)
        self.assertFalse(any(x["id"] == "confirm_yes" for x in r["chips"]))

    def test_lv2_cancels_quote_even_without_poll(self):
        q = self.quote(); self.alert(2)
        r = e.handle("s", chip_id="confirm_yes", confirmation_token=q["confirmation_token"])
        self.assertFalse(e.data.get_fee("33")["paid"])
        self.assertIn("evacuate", [x["id"] for x in r["chips"]])

    def test_alert_during_final_fee_read_cancels_payment(self):
        q = self.quote(); original = e.data.get_fee
        def fee(v):
            self.alert(2)
            return original(v)
        with patch.object(e.data, "get_fee", side_effect=fee), patch.object(e.data, "pay", wraps=e.data.pay) as pay:
            r = e.handle("s", chip_id="confirm_yes", confirmation_token=q["confirmation_token"])
        self.assertEqual(pay.call_count, 0)
        self.assertEqual(r["banner"]["level"], 2)

    def test_lv1_retains_confirmation_but_lv2_admin_notice_does_not_hide_evacuation(self):
        self.quote(); self.alert(1)
        self.assertIsNotNone(e.get_session("s").pending)
        self.assertIn("confirm_yes", [x["id"] for x in e.updates("s")["messages"][0]["chips"]])
        self.alert(2)
        c = e.handle("s", chip_id="call_admin")["card"]["data"]
        for m in e.updates("s")["messages"]:
            self.assertIn("evacuate", [x["id"] for x in m["chips"]])

    def test_transient_observed_alarm_delivered_as_history(self):
        e.handle("s", event="start"); self.alert(2); self.alert(0)
        r = e.updates("s")
        self.assertIsNone(r["banner"])
        self.assertTrue(any(m["meta"].get("historical") for m in r["messages"]))
        self.assertFalse(any("로 이동하세요" in m["text"] for m in r["messages"]))

    def test_queued_alarm_after_clear_does_not_repeat_evacuation(self):
        self.alert(2); e.updates("s"); self.alert(0)
        r = e.updates("s")
        self.assertFalse(any("로 이동하세요" in m["text"] for m in r["messages"]))

    def test_queued_lv2_after_downgrade_is_history(self):
        self.alert(2); e.updates("s"); self.alert(1)
        r = e.updates("s")
        self.assertFalse(any("로 이동하세요" in m["text"] for m in r["messages"]))
        self.assertTrue(r["messages"][0]["meta"]["historical"])

    def test_queued_clear_during_new_alarm_is_history(self):
        self.alert(2); first=e.updates("s"); self.alert(0)
        e.updates("s", cursor=first["cursor"])
        self.alert(2, "B", 3)
        r=e.updates("s", cursor=first["cursor"])
        # 정리본: 지나간 '해제' 메시지는 보내지 않는다. 현재 경보만 전달 (Claude 정리본 변경)
        self.assertFalse(any(m["meta"].get("historical") and not m["meta"].get("alert_level") for m in r["messages"]))
        self.assertEqual(r["messages"][-1]["meta"]["alert_level"], 2)
        self.assertEqual(r["messages"][-1]["meta"]["lot_id"], "B")
        self.assertEqual(r["banner"]["level"], 2)



    def test_wrong_vehicle_location_and_fee_are_rejected(self):
        for method, target in (("find_car", "33"), ("get_fee", "33")):
            inner = getattr(e.data.inner, method)
            with patch.object(e.data.inner, method, return_value=inner("43")):
                with self.assertRaises(DataUnavailable):
                    getattr(e.data, method)(target)

    def test_wrong_alert_range_and_timestamp_are_rejected(self):
        for change in ({"spot_no": 999}, {"started_at": "invalid"}):
            a = {"level": 2, "version": 1, "lot_id": "A", "spot_no": 6, **change}
            with patch.object(e.data.inner, "get_alert", return_value=a):
                with self.assertRaises(DataUnavailable): e.data.get_alert()

    def test_payment_amount_mismatch_blocks_further_payment(self):
        q = self.quote()
        with patch.object(e.data.inner, "pay", return_value={"status": "paid", "amount": 1, "paid_at": iso(now())}):
            r = e.handle("s", chip_id="confirm_yes", confirmation_token=q["confirmation_token"])
        self.assertIsNone(r["card"])
        self.assertNotEqual(e.handle("s", chip_id="pay")["flow"], "confirm_action")

    def test_paid_response_and_unpaid_fee_are_not_success_receipt(self):
        q = self.quote()
        with patch.object(e.data.inner, "pay", return_value={"status": "paid", "amount": 1500, "paid_at": iso(now())}):
            r = e.handle("s", chip_id="confirm_yes", confirmation_token=q["confirmation_token"])
        self.assertIsNone(r["card"])
        self.assertIn("관리자 확인", r["text"])

    def test_stale_admin_summary_is_labelled(self):
        self.choose(); e.data.get_fee("33")
        with patch.object(e.data.inner, "find_car", side_effect=OSError()), patch.object(e.data.inner, "get_fee", side_effect=OSError()):
            summary = e.handle("s", chip_id="call_admin")["card"]["data"]["summary"]
        self.assertIn("마지막으로 받은 정보", summary)
        self.assertIn("기준", summary)

    def test_failed_affected_read_recovers_on_escalation_with_time_label(self):
        with patch.object(e.data, "list_cars", side_effect=DataUnavailable()): self.alert(1)
        self.assertNotIn("근접 차량 없음", incidents.list_public()[0]["report"])
        self.alert(2)
        r = incidents.list_public()[0]
        self.assertTrue(r["affected_known"])
        self.assertTrue(r["affected_recovered"])
        self.assertIn("발생 당시 목록은 미확인", r["report"])

    def test_slow_concurrent_alert_fetch_is_shared_and_latency_includes_wait(self):
        barrier = threading.Barrier(12)
        original = e.data.inner.get_alert
        def slow():
            time.sleep(.04)
            return original()
        def request(i):
            barrier.wait(3); started = time.perf_counter()
            r = e.handle(f"load-{i}", chip_id="help")
            return (time.perf_counter() - started) * 1000, r["meta"]["latency_ms"]
        with patch.object(e.data.inner, "get_alert", side_effect=slow) as fetch:
            with ThreadPoolExecutor(max_workers=12) as pool: measurements = list(pool.map(request, range(12)))
        self.assertEqual(fetch.call_count, 1)
        self.assertLess(max(wall for wall, _ in measurements), 350)
        for wall, measured in measurements:
            self.assertGreaterEqual(measured, 30)
            self.assertLessEqual(abs(wall - measured), 20)


class AccessBoundaryTests(unittest.TestCase):
    def test_admin_token_required_and_not_replaced_by_revision(self):
        with patch.object(config, "ADMIN_TOKEN", "test-only"):
            with self.assertRaises(AccessDenied): require_admin(None, "127.0.0.1")
            require_admin("Bearer test-only", "192.0.2.1")

    def test_remote_mock_admin_without_token_is_denied(self):
        with patch.object(config, "ADMIN_TOKEN", ""):
            with self.assertRaises(AccessDenied): require_admin(None, "192.0.2.1")

    def test_demo_can_be_disabled_independently(self):
        with patch.object(config, "DEMO_ENABLED", False):
            with self.assertRaises(AccessDenied) as caught: require_admin(None, "127.0.0.1", demo=True)
        self.assertEqual(caught.exception.status, 404)
