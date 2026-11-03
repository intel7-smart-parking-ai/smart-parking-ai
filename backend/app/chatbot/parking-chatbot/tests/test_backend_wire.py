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
                if self.path == config.API_PATHS["occupancy"]:
                    self.reply(backend.get_empty_spots())
                elif self.path == config.API_PATHS["alerts"]:
                    self.reply(backend.get_alert())
                else:
                    self.reply({"error": "not found"}, 404)
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                received.append((self.path, dict(self.headers), body))
                result = backend.pay(body["vehicle_no"], idempotency_key=self.headers.get("Idempotency-Key"),
                                     expected_amount=body["expected_amount"], entered_at=body["entered_at"])
                self.reply(result)
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

    def test_payment_passes_expected_amount_visit_and_idempotency_key(self):
        fee = self.backend.get_fee("07")
        kwargs = {"expected_amount": fee["fee"], "entered_at": fee["entered_at"], "idempotency_key": "wire-payment"}
        first = self.adapter.pay("07", **kwargs)
        second = self.adapter.pay("07", **kwargs)
        self.assertEqual(first, second)
        path, headers, body = self.received[0]
        self.assertEqual(headers["Idempotency-Key"], "wire-payment")
        self.assertEqual(body["expected_amount"], fee["fee"])
        self.assertEqual(body["entered_at"], fee["entered_at"])


if __name__ == "__main__":
    unittest.main()
