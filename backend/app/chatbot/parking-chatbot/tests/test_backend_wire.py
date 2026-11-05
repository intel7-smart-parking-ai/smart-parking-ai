"""실제 로컬 HTTP 통신으로 연결층의 인증·요청·응답 계약을 검사한다."""
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from chatbot import config
from chatbot.adapters import ApiAdapter, CachedAdapter, MockAdapter


class BackendWireTests(unittest.TestCase):
    def setUp(self):
        self.backend = MockAdapter()
        self.received = []
        backend, received = self.backend, self.received
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def reply(self, body, status=200):
                payload = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers(); self.wfile.write(payload)
            def do_GET(self):
                received.append((self.path, dict(self.headers), None))
                if self.path == config.API_PATHS["lots"]:
                    snapshot = backend.get_empty_spots()
                    self.reply({"items": [{"lot_id": k, "total_spaces": v["total"], "empty_spaces": v["empty"],
                                          "uncertain_spaces": v["uncertain"], "unavailable_spaces": 0} for k, v in snapshot["lots"].items()],
                                "as_of": snapshot["updated_at"]})
                elif self.path.endswith("/spaces"):
                    self.reply({"items": []})
                elif self.path.endswith("/location") and "/33/" in self.path:
                    car = backend.find_car("33")
                    self.reply({"car_id": "33", "lot_id": car["lot_id"], "space_id": car["space_id"],
                                "session_id": "session-07", "status": "occupied", "observed_at": car["updated_at"]})
                elif self.path == config.API_PATHS["alerts"]:
                    self.reply({"items": [], "as_of": backend.get_empty_spots()["updated_at"]})
                else:
                    self.reply({"error": {"code": "VEHICLE_NOT_FOUND"}}, 404)
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                received.append((self.path, dict(self.headers), body))
                result = backend.pay("33", idempotency_key=body["idempotency_key"])
                self.reply({"amount_krw": result.get("amount", backend.get_fee("33")["fee"]), "payment_id": "payment-07",
                            "paid_at": result.get("paid_at")})
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.adapter = CachedAdapter(ApiAdapter(f"http://127.0.0.1:{self.server.server_port}"))

    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join()

    def test_read_contract_over_http(self):
        self.assertEqual(self.adapter.get_empty_spots()["lots"]["B"]["empty"], 0)
        self.assertEqual(self.adapter.get_alert()["level"], 0)

    def test_bearer_header_is_sent(self):
        with patch.object(config, "BACKEND_TOKEN", "test-only-token"):
            self.adapter.get_empty_spots()
        self.assertEqual(self.received[0][1]["Authorization"], "Bearer test-only-token")

    def test_missing_location_404_means_none(self):
        self.assertIsNone(self.adapter.find_car("24"))

    def test_payment_passes_quote_amount_visit_and_idempotency_key(self):
        fee = self.backend.get_fee("33")
        kwargs = {"quote_amount": fee["fee"], "entry_at": fee["entry_at"], "idempotency_key": "wire-payment", "quote_id": "quote-wire"}
        first = self.adapter.pay("33", **kwargs)
        second = self.adapter.pay("33", **kwargs)
        self.assertEqual(first, second)
        path, headers, body = next(x for x in self.received if x[2] is not None)
        self.assertEqual(body["idempotency_key"], "wire-payment")
        self.assertEqual(first["amount"], fee["fee"])
        self.assertEqual(body, {"session_id": "session-07", "quote_id": "quote-wire", "idempotency_key": "wire-payment", "payment_method": "MOCK_CARD"})


if __name__ == "__main__":
    unittest.main()
