"""재현된 오류 및 주요 시연 흐름. python -m unittest discover -s tests -v"""
import json
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from datetime import timedelta
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from unittest.mock import patch

from chatbot import engine as e, incidents, parser
from chatbot.adapters import ApiAdapter, CachedAdapter, DataUnavailable, MockAdapter, now
from server_dev import Handler


def setUpModule():
    # 사용자의 .env가 api/LLM 모드여도 테스트는 외부 서버를 호출하지 않는다.
    global test_context
    test_context = ExitStack()
    test_context.enter_context(patch.object(e, "data", CachedAdapter(MockAdapter())))
    test_context.enter_context(patch.object(e.config, "LLM_PROVIDER", "none"))


def tearDownModule():
    test_context.close()


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        e.reset_all()

    def test_clear_vehicle_number_reads_fee_immediately(self):
        result = e.handle("s", text="33번 요금")
        self.assertEqual(result["flow"], "idle")
        self.assertIsNone(result["confirmation_token"])
        self.assertEqual(result["card"]["type"], "fee")
        self.assertEqual(result["card"]["data"]["car_id"], "33")

    def test_changed_topic_cancels_pending_payment(self):
        e.handle("s", text="33번 결제")
        e.handle("s", text="빈자리")
        result = e.handle("s", text="네")
        self.assertFalse(e.data.get_fee("33")["paid"])
        self.assertIn("확인할 내용이 없", result["text"])

    def test_denied_payment_vehicle_can_be_corrected_without_paying(self):
        old = e.handle("s", text="33번 결제")
        self.assertEqual(old["flow"], "confirm_vehicle")
        self.assertEqual(e.handle("s", text="아니요")["flow"], "ask_vehicle")
        corrected = e.handle("s", text="43")
        self.assertEqual(corrected["flow"], "confirm_vehicle")
        e.handle("s", chip_id="confirm_yes", confirmation_token=old["confirmation_token"])
        self.assertEqual(e.get_session("s").pending.token, corrected["confirmation_token"])
        result = e.handle("s", chip_id="confirm_yes", confirmation_token=corrected["confirmation_token"])
        self.assertEqual(result["flow"], "confirm_action")
        self.assertEqual(result["card"]["data"]["car_id"], "43")
        self.assertFalse(e.data.get_fee("33")["paid"])
        self.assertFalse(e.data.get_fee("43")["paid"])

    def test_payment_and_repeat(self):
        e.handle("s", text="33번 결제")
        quote = e.handle("s", text="네")
        self.assertEqual(quote["flow"], "confirm_action")
        self.assertEqual(e.handle("s", text="네")["card"]["data"]["status"], "paid")
        self.assertIn("이미 결제", e.handle("s", chip_id="pay")["text"])

    def test_free_payment_does_not_waive_later_fees(self):
        self.assertEqual(e.data.pay("23")["status"], "paid")
        e.data.inner.cars["23"]["entry_at"] = now() - timedelta(minutes=40)
        d = e.data.get_fee("23")
        self.assertGreater(d["fee"], 0)
        self.assertTrue(d["paid"])



    def test_waiting_without_waiters_skips_occupancy_read(self):
        with patch.object(e.data, "get_empty_spots", wraps=e.data.get_empty_spots) as occ, \
             patch.object(e.data, "get_alert", wraps=e.data.get_alert) as alert:
            e.updates("s", 0)
            self.assertEqual(occ.call_count, 0)
            self.assertEqual(alert.call_count, 1)

    def test_alert_response_reuses_one_snapshot(self):
        e.data.set_alert(2, "A", 6)
        with patch.object(e.data, "get_alert", wraps=e.data.get_alert) as alert:
            result = e.handle("s", chip_id="alert_where")
            self.assertEqual(alert.call_count, 1)
        self.assertEqual(result["card"]["data"]["version"], result["alert_version"])


    def test_alert_lifecycle_and_confirmed_report_immutable(self):
        e.data.set_alert(1, "A", 6)
        e.current_alert()
        self.assertIn("error", incidents.confirm(1))
        e.data.set_alert(2, "A", 6)
        alert = e.updates("s", 0)
        e.updates("s", 0, cursor=alert["cursor"])
        incidents.set_check(1, 0, True)
        e.data.set_alert(0)
        e.current_alert()
        incidents.set_notes(1, "현장 확인 완료")
        report = incidents.confirm(1)
        self.assertEqual(report["max_level"], 2)
        self.assertEqual(report["notified_count"], 1)
        self.assertEqual(incidents.confirm(1), report)
        self.assertIn("error", incidents.set_notes(1, "변경"))
        self.assertIn("error", incidents.set_check(1, 0, True))

    def test_changed_alert_location_creates_correct_report(self):
        e.data.set_alert(1, "A", 6)
        e.current_alert()
        e.data.set_alert(2, "B", 3)
        e.current_alert()
        reports = incidents.list_public()
        self.assertEqual(len(reports), 2)
        self.assertEqual(reports[0]["lot_id"], "B")
        self.assertEqual(reports[0]["spot_no"], 3)
        self.assertEqual(reports[0]["near"], ["20", "42", "22"])  # B A-03의 같은 줄 양옆(A-02, A-04)
        self.assertIsNotNone(reports[1]["closed_at"])

    def test_report_snapshot_cannot_modify_saved_checklist(self):
        e.data.set_alert(1, "A", 6)
        e.current_alert()
        result = incidents.list_public()[0]
        result["checklist"][0]["done_at"] = "fake"
        self.assertIsNone(incidents.list_public()[0]["checklist"][0]["done_at"])

    def test_backend_outage_is_not_reported_as_no_alert(self):
        with patch.object(e.data, "get_alert", side_effect=DataUnavailable()), \
             patch.object(e.data, "get_empty_spots", side_effect=DataUnavailable()):
            result = e.handle("s", chip_id="empty_all")
            self.assertTrue(result["meta"]["unavailable"])
            self.assertIn("확인할 수 없", result["banner"]["text"])
            result = e.handle("s", chip_id="alert_where")
            self.assertIn("확인할 수 없", result["text"])

    def test_payment_success_survives_followup_read_failure(self):
        e.handle("s", text="33번 결제")
        e.handle("s", text="네")
        fee = e.data.get_fee("33")
        with patch.object(e.data, "get_fee", side_effect=[fee, DataUnavailable()]):
            result = e.handle("s", text="네")
        self.assertEqual(result["card"]["data"]["status"], "paid")

    def test_failed_payment_confirmation_warns_about_unknown_result(self):
        e.handle("s", text="33번 결제")
        e.handle("s", text="네")
        with patch.object(e.data, "pay", side_effect=DataUnavailable()):
            result = e.handle("s", text="네")
        self.assertIn("다시 결제하지 말고", result["text"])

    def test_parallel_admin_calls_have_unique_ids(self):
        def register(n):
            e.handle(str(n), chip_id="call_admin")
            return e.handle(str(n), text="네")
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(register, range(30)))
        ids = [c["id"] for c in e.admin_calls()]
        self.assertEqual(len(set(ids)), 30)


class ParserTests(unittest.TestCase):
    def test_natural_vehicle_location(self):
        r = parser.parse_rules("17번 차 어디 있어?")
        self.assertEqual(r.intent, "find_my_car")
        self.assertEqual(r.params["car_id"], "17")

    def test_out_of_range_korean_number_not_partial_match(self):
        self.assertEqual(parser.parse_rules("오번 차 위치").params["car_id"], "short")  # 한 자리는 두 자리로 다시 묻는다

    def test_number_in_unrecognized_sentence_does_not_switch_vehicle(self):
        self.assertFalse(parser.parse_rules("17번 차 와이퍼 고장").vehicle_only)

    def test_malformed_llm_result_is_ignored(self):
        for payload in ([1], {"intent": []}, {"intent": "get_fee", "params": [1]}):
            with self.subTest(payload=payload), patch("chatbot.config.LLM_ENGINE", "direct"), patch("chatbot.llm.classify", return_value=payload):
                self.assertEqual(parser.parse("뿌잉").intent, "unknown")

    def test_llm_params_are_validated_and_rule_values_preserved(self):
        with patch("chatbot.config.LLM_ENGINE", "direct"), patch("chatbot.llm.classify", return_value={"intent": "get_fee", "params": {"car_id": "18", "lot_id": "Z", "extra": 1}}):
            r = parser.parse("17번 차 뿌잉")
        self.assertEqual(r.params, {"car_id": "17"})

    def test_llm_cannot_confirm_a_pending_action(self):
        with patch("chatbot.config.LLM_ENGINE", "direct"), patch("chatbot.llm.classify", return_value={"intent": "confirm_yes"}):
            self.assertEqual(parser.parse("뿌잉").intent, "unknown")


class CacheTests(unittest.TestCase):
    def setUp(self):
        self.backend = MockAdapter()
        self.cache = CachedAdapter(self.backend)

    def test_missing_car_invalidates_old_location(self):
        self.cache.find_car("33")
        self.backend.exit_car("33")
        self.assertIsNone(self.cache.find_car("33"))
        with patch.object(self.backend, "find_car", side_effect=OSError("offline")):
            with self.assertRaises(DataUnavailable):
                self.cache.find_car("33")

    def test_cached_values_are_isolated_and_marked_stale(self):
        first = self.cache.get_empty_spots()
        first["lots"]["A"]["empty"] = 100
        with patch.object(self.backend, "get_empty_spots", side_effect=OSError("offline")):
            result = self.cache.get_empty_spots()
        self.assertTrue(result["stale"])
        self.assertEqual(result["lots"]["A"]["empty"], 5)  # 개발원 12칸 - 목업 7대

    def test_payment_invalidates_old_fee(self):
        self.cache.get_fee("33")
        self.cache.pay("33")
        with patch.object(self.backend, "get_fee", side_effect=OSError("offline")):
            with self.assertRaises(DataUnavailable):
                self.cache.get_fee("33")

    def test_reset_removes_old_cache(self):
        self.cache.get_empty_spots()
        self.cache.reset()
        self.assertEqual(self.cache.cache, {})

    def test_api_mode_does_not_pretend_demo_succeeded(self):
        api = ApiAdapter("http://127.0.0.1:1")
        with self.assertRaises(ValueError):
            api.set_alert(2, "A", 6)
        with self.assertRaises(ValueError):
            api.exit_car("33")


class DevHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def setUp(self):
        e.reset_all()

    def request(self, suffix, body=None):
        req = Request(self.base + "/api/v1/chat" + suffix, data=body,
                      headers={"Content-Type": "application/json"})
        try:
            with urlopen(req, timeout=5) as response:
                return response.status, json.load(response)
        except HTTPError as exc:
            return exc.code, json.load(exc)

    def test_malformed_json_returns_400(self):
        self.assertEqual(self.request("", b"{")[0], 400)

    def test_non_object_json_returns_400(self):
        self.assertEqual(self.request("", b"[]")[0], 400)

    def test_invalid_text_type_returns_400(self):
        self.assertEqual(self.request("", b'{"text":[]}')[0], 400)

    def test_invalid_alert_fields_return_400(self):
        for body in ({"level": 5}, {"level": 2, "lot_id": "Z"}, {"level": 1, "spot_no": 99}):
            with self.subTest(body=body):
                self.assertEqual(self.request("/demo/alert", json.dumps(body).encode())[0], 400)

    def test_invalid_since_returns_400(self):
        self.assertEqual(self.request("/updates?since=nope")[0], 400)

    def test_chat_and_alert_endpoints(self):
        self.assertEqual(self.request("", b'{"event":"start"}')[0], 400)
        self.assertEqual(self.request("", b'{"session_id":"http-test","event":"start"}')[0], 200)
        self.assertEqual(self.request("/demo/alert", b'{"level":2,"lot_id":"A","spot_no":6}')[0], 200)
        code, result = self.request("/updates?session_id=http&since=0")
        self.assertEqual(code, 200)
        self.assertEqual(result["messages"][0]["card"]["type"], "alert")

    def test_missing_session_updates_does_not_create_shared_session(self):
        self.assertEqual(self.request("/updates")[0],400)
        self.assertNotIn("anon",e.SESSIONS)

    def test_admin_token_and_demo_disable(self):
        with patch.object(e.config,"ADMIN_TOKEN","test-only"):
            self.assertEqual(self.request("/admin/calls")[0],401)
            self.assertEqual(self.request("/demo/reset",b'{}')[0],401)
        with patch.object(e.config,"DEMO_ENABLED",False):
            self.assertEqual(self.request("/demo/reset",b'{}')[0],404)


if __name__ == "__main__":
    unittest.main()
