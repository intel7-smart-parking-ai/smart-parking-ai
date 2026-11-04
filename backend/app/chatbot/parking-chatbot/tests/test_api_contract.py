"""Team contract over real HTTP, without FastAPI or an external server."""
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import unittest
from urllib.parse import quote
from unittest.mock import patch

from chatbot import config, engine as e
from datetime import timedelta

from chatbot.adapters import ApiAdapter, CachedAdapter, DataUnavailable, iso, now


class ApiContractTests(unittest.TestCase):
    def setUp(self):
        self.settings = deepcopy(config.catalog.settings)
        self.requests = []
        self.mock_header = True
        self.session_id = None
        self.payment_status = "unpaid"
        self.payment_code = None
        self.payment_amount = 2500
        self.fee_missing = False
        self.location_state = "occupied"
        self.location_space = "A-01"
        self.alerts = None
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def reply(self, body, status=200):
                data = json.dumps(body).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                if owner.mock_header:
                    self.send_header("X-ParkFlow-Data-Source", "mock")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                owner.requests.append(("GET", self.path, None))
                if self.path == "/api/v1/parking-lots":
                    self.reply({"items": [{"lot_id": k, "total_spaces": 4, "empty_spaces": 1,
                        "uncertain_spaces": 1, "unavailable_spaces": 1} for k in "ABC"], "as_of": iso(now())})
                elif self.path.endswith("/spaces"):
                    lot = self.path.split("/")[-2]
                    self.reply({"items": [{"space_id": lot+"-"+str(n).zfill(2), "lot_id": lot,
                        "car_id": None, "status": status} for n, status in enumerate(
                            ("occupied", "empty", "uncertain", "unavailable"), 1)], "as_of": iso(now())})
                elif self.path == "/api/v1/vehicles/11/location":
                    self.reply({"car_id": "11", "lot_id": "A", "space_id": owner.location_space,
                        "session_id": owner.session_id, "status": owner.location_state,
                        "confidence": .8, "observed_at": iso(now())})
                elif self.path.startswith("/api/v1/vehicles/"):
                    self.reply({"error": {"code": "VEHICLE_NOT_FOUND", "message": "do not expose"}}, 404)
                elif self.path.startswith("/api/v1/sessions/"):
                    if owner.fee_missing:
                        self.reply({"detail": "Not Found"}, 404)
                    else:
                        # 실제 billing 응답 키(billing_router._calculate_fee_helper + 견적)
                        self.reply({"session_id": owner.session_id, "lot_id": "A", "space_id": "A-01", "car_id": "11",
                            "amount_krw": 2500, "duration_minutes": 70, "entry_at": iso(now()), "exit_at": None,
                            "target_at": iso(now()), "fee_breakdown": {}, "payment_status": owner.payment_status,
                            "is_daily_capped": False, "rate_policy": {"base_minutes": 30, "base_fee": 1000,
                            "unit_minutes": 10, "extra_fee": 500, "daily_cap": 10000},
                            "quote_id": "quote-1", "quote_expires_at": iso(now() + timedelta(minutes=5))})
                elif self.path == "/api/v1/alerts" and owner.alerts is not None:
                    self.reply({"items": owner.alerts, "as_of": iso(now())})
                else:
                    self.reply({"detail": "Not Found"}, 404)

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])).decode("utf-8"))
                owner.requests.append(("POST", self.path, body))
                if owner.payment_code:
                    self.reply({"error": {"code": owner.payment_code, "message": "untrusted"}}, 409)
                else:
                    self.reply({"amount_krw": owner.payment_amount, "payment_id": "payment-1",
                        "idempotent_replayed": len(owner.posts()) > 1, "paid_at": iso(now())}, 201)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.api = ApiAdapter(f"http://127.0.0.1:{self.server.server_port}")
        self.adapter = CachedAdapter(self.api)
        self.patches = [patch.object(config, "DATA_SOURCE", "api"), patch.object(config, "ALERT_SHARE_SEC", 0),
                        patch.object(e, "data", self.adapter)]
        for p in self.patches:
            p.start()
        e.reset_all()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        config.catalog.configure(self.settings)

    def posts(self):
        return [r for r in self.requests if r[0] == "POST"]

    def ask(self, text, sid="s"):
        response = e.handle(sid, text=text)
        if response["flow"] == "confirm_vehicle":
            response = e.handle(sid, text="네")
        return response

    def pay(self):
        self.session_id = "session-01"
        response = self.ask("11번 결제")
        token = response["confirmation_token"]
        return e.handle("s", chip_id="confirm_yes", confirmation_token=token)

    def test_01_server_counts_and_uncertain(self):
        r = self.ask("개발원주차장 빈자리 몇 개야?")
        self.assertEqual(r["card"]["data"]["lots"]["A"]["total"], 4)
        self.assertEqual(r["card"]["data"]["lots"]["A"]["empty"], 1)
        self.assertIn("1자리", r["text"])
        self.assertIn("확인 중 1칸", r["text"])

    def test_02_leading_zero_location(self):
        r = self.ask("11번 차 어디 있어?")
        self.assertIn(("GET", "/api/v1/vehicles/11/location", None), self.requests)
        self.assertIn("개발원주차장 A-01", r["text"])

    def test_03_missing_vehicle(self):
        self.assertIsNone(self.api.find_car("99"))
        r = self.ask("34번 차 어디 있어?")
        self.assertIsNone(r["card"])
        self.assertIn("입차 기록이 없어요", r["text"])

    def test_04_null_session_no_fee_request(self):
        r = self.ask("11번 차 요금 얼마야?")
        self.assertIn("요금 정보를 확인할 수 없어요", r["text"])
        self.assertFalse(any("/sessions/" in x[1] for x in self.requests))
        self.assertNotIn("fee:11", self.adapter.cache)

    def test_05_fee_is_server_amount(self):
        self.session_id = "session-01"
        with patch("chatbot.adapters.calc_fee", side_effect=AssertionError("local fee forbidden")):
            r = self.ask("11번 요금")
        self.assertIn("2,500원", r["text"])

    def test_06_exact_payment_body(self):
        self.pay()
        body = self.posts()[0][2]
        self.assertEqual(set(body), {"session_id", "quote_id", "idempotency_key", "payment_method"})
        self.assertEqual(body["quote_id"], "quote-1")
        self.assertEqual(body["payment_method"], "MOCK_CARD")
        import uuid
        self.assertEqual(uuid.UUID(body["idempotency_key"]).version, 4)

    def test_07_payment_retry_reuses_key(self):
        self.session_id = "session-01"
        r = self.ask("11번 결제")
        pending = e.get_session("s").pending
        key = pending.params["idempotency_key"]
        quote_id = pending.params["quote_id"]
        first = self.api.pay("11", idempotency_key=key, quote_id=quote_id)
        second = self.api.pay("11", idempotency_key=key, quote_id=quote_id)
        self.assertEqual([x[2]["idempotency_key"] for x in self.posts()], [key, key])
        self.assertFalse(first["idempotent_replayed"])
        self.assertTrue(second["idempotent_replayed"])
        self.assertEqual(r["flow"], "confirm_action")

    def test_08_already_paid(self):
        self.payment_code = "ALREADY_PAID"
        self.assertIn("이미 결제", self.pay()["text"])

    def test_09_idempotency_conflict_no_retry(self):
        self.payment_code = "IDEMPOTENCY_CONFLICT"
        r = self.pay()
        self.assertIn("결제 요청이 겹쳤어요", r["text"])
        self.assertEqual(len(self.posts()), 1)

    def test_10_unregistered_billing(self):
        self.session_id = "session-01"
        self.fee_missing = True
        self.assertIn("확인할 수 없어요", self.ask("11번 요금")["text"])

    def test_11_unknown_alert_is_not_level_zero(self):
        r = self.ask("경보 알려줘")
        self.assertIn("경보 상태를 확인할 수 없어요", r["text"])
        self.assertNotIn("현재 경보는 없어요", r["text"])
        self.assertNotIn("alert", self.adapter.cache)

    def test_12_mock_notice_once(self):
        r = self.ask("A 빈자리")
        self.assertTrue(r["text"].endswith("※ 시연용 데이터예요"))
        self.assertEqual(r["text"].count("※ 시연용 데이터예요"), 1)

    def test_13_closed_port_never_uses_mock(self):
        self.api.base = "http://127.0.0.1:1"
        for text in ("A 빈자리", "11번 요금", "경보"):
            r = self.ask(text, text)
            self.assertIn("확인", r["text"])
            self.assertIsNone(r["card"])

    def test_14_absent_lot_not_local_capacity(self):
        r = self.ask("신세계백화점주차장 빈자리")
        self.assertIn("정보가 아직 연결되지", r["text"])
        self.assertIsNone(r["card"])
        self.assertEqual(config.catalog.ids(), list("ABC"))

    def test_15_canceled_spelling(self):
        self.session_id = "session-01"
        self.payment_status = "canceled"
        self.assertIn("결제가 취소된 상태", self.ask("11번 요금")["text"])
        self.payment_status = "cancelled"
        self.assertEqual(self.api.get_fee("11")["status"], "unavailable")

    def test_16_opaque_quoted_session(self):
        for sid in ("3f2a4f6b-6551-4de1-a88e-bcc0c16af2de", "uuid/with ?# 한글"):
            self.session_id = sid
            self.assertEqual(self.api.get_fee("11")["fee"], 2500)
            self.assertIn(("GET", "/api/v1/sessions/"+quote(sid, safe="")+"/fee", None), self.requests)

    def test_17_no_header_no_mock_notice(self):
        self.mock_header = False
        self.assertNotIn("시연용 데이터", self.ask("A 빈자리")["text"])

    def test_18_anonymous_space_has_no_payment(self):
        snapshot = self.adapter.get_empty_spots()
        self.assertIsNone(snapshot["unrecognized"][0]["session_id"])
        r = self.ask("34번 차 어디 있어?")
        self.assertFalse({"pay", "confirm_yes", "fee"} & {c["id"] for c in r["chips"]})

    def test_19_acknowledged_level_two_stays_safe(self):
        self.alerts = [{"alert_id": "a1", "lot_id": "A", "space_id": "A-01", "level": 2,
                        "status": "acknowledged", "type": "mock_fire_led", "occurred_at": iso(now())}]
        r = self.ask("경보 알려줘")
        self.assertIn("사람 먼저", r["safety"]["title"])
        self.assertIn("들어오지 마세요", r["text"])
        self.alerts = None
        r = self.ask("경보 알려줘")
        self.assertIsNotNone(r["safety"])
        self.assertIn("경보 상태를 확인할 수 없어요", r["text"])

    def test_20_server_payment_amount_replaces_quote(self):
        self.payment_amount = 3000
        r = self.pay()
        self.assertIn("3,000원", r["text"])
        self.assertIn("금액이 갱신됐어요", r["text"])
        self.assertEqual(r["card"]["data"]["amount"], 3000)

    def test_21_session_change_after_quote_never_pays_new_visit(self):
        self.session_id = "first-visit"
        r = self.ask("11번 결제")
        self.session_id = "second-visit"
        result = e.handle("s", chip_id="confirm_yes", confirmation_token=r["confirmation_token"])
        self.assertEqual(self.posts(), [])
        self.assertIn("확인", result["text"])
        self.assertIsNone(e.get_session("s").pending)

    def test_22_alert_client_error_keeps_last_level_two(self):
        self.api._last_alert = {"level": 2, "version": 3, "lot_id": "A", "spot_no": 1}
        with patch.object(self.api, "_request", return_value=(422, {"error": {"code": "INVALID_INPUT"}}, {})):
            result = self.api.get_alert()
        self.assertEqual(result["level"], 2)
        self.assertTrue(result["unavailable"])

    def test_23_malformed_lots_fail_without_generated_data(self):
        with patch.object(self.api, "_request", return_value=(200, {"items": [{}]}, {})):
            with self.assertRaises(DataUnavailable):
                self.adapter.get_empty_spots()
        self.assertEqual(self.adapter.cache, {})

    def test_24_api_llm_vehicle_must_come_from_user(self):
        from chatbot.parser import parse
        with patch("chatbot.llm_router.route", return_value={"intent": "get_fee", "params": {"car_id": "17"}}):
            result = parse("자동차 사용료를 산출해 줘")
        self.assertEqual(result.params, {})
