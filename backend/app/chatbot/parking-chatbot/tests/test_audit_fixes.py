"""최종 검수에서 재현한 시간 제한·세션 경합·시연 입력 오류의 회귀 검사."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
import os
import shutil
import ssl
import subprocess
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

from chatbot import config, engine as e, llm
from chatbot.adapters import ApiAdapter, CachedAdapter, DataUnavailable, MockAdapter, request_deadline
from chatbot.state import Conflict
from chatbot.transport import load_json


class DeadlineTests(unittest.TestCase):
    def setUp(self):
        self.mode = "normal"
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                self.rfile.read(int(self.headers.get("Content-Length", 0)))
                self.do_GET()

            def do_GET(self):
                mode = owner.mode
                payload = b'{"value":' + b' ' * 60 + b'1}'
                try:
                    if mode == "headers":
                        for byte in b'HTTP/1.0 200 OK\r\nX-Slow: ' + b'x' * 60 + b'\r\n\r\n':
                            self.wfile.write(bytes([byte])); self.wfile.flush(); time.sleep(.01)
                        return
                    self.send_response(200)
                    if mode == "oversize":
                        self.send_header("Content-Length", str(1024 * 1024 + 1))
                    elif mode != "unknown-size":
                        self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    if mode == "oversize":
                        return
                    if mode == "body":
                        for byte in payload:
                            self.wfile.write(bytes([byte])); self.wfile.flush(); time.sleep(.01)
                    else:
                        self.wfile.write(payload)
                except (OSError, ValueError):
                    pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.close)
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def close(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(2)

    def bounded_call(self, fn):
        token = request_deadline.set(time.monotonic() + .12)
        started = time.monotonic()
        try:
            with patch.object(config, "BACKEND_TIMEOUT_SEC", .5), patch.object(config, "LLM_TIMEOUT_SEC", .5):
                with self.assertRaises(TimeoutError):
                    fn()
            self.assertLess(time.monotonic() - started, .5)
        finally:
            request_deadline.reset(token)

    def test_get_slow_body_times_out_and_next_request_recovers(self):
        self.mode = "body"
        self.bounded_call(lambda: ApiAdapter(self.url)._get("/slow"))
        self.mode = "normal"
        self.assertEqual(ApiAdapter(self.url)._get("/ok"), {"value": 1})

    def test_post_slow_body_times_out(self):
        self.mode = "body"
        self.bounded_call(lambda: ApiAdapter(self.url)._post("/slow", {}))

    def test_llm_transport_uses_same_deadline(self):
        self.mode = "body"
        self.bounded_call(lambda: llm._post_json(self.url + "/slow", {}, {}))

    def test_slow_headers_also_have_deadline(self):
        self.mode = "headers"
        self.bounded_call(lambda: ApiAdapter(self.url)._get("/slow"))

    def test_oversize_body_is_rejected_with_and_without_length(self):
        for mode in ("oversize", "unknown-size"):
            with self.subTest(mode=mode):
                self.mode = mode
                with self.assertRaises(ValueError):
                    load_json(urllib.request.Request(self.url), .5, max_bytes=16)

    def test_failed_shared_read_is_removed(self):
        self.mode = "body"
        adapter = CachedAdapter(ApiAdapter(self.url))
        with patch.object(config, "BACKEND_TIMEOUT_SEC", .08):
            with self.assertRaises(DataUnavailable):
                adapter.get_empty_spots()
        self.assertFalse(adapter.inflight)

    @unittest.skipUnless(shutil.which("openssl"), "TLS 검사 인증서 생성용 openssl 미설치")
    def test_https_verifies_certificate_and_times_out_slow_body(self):
        from pathlib import Path
        with tempfile.TemporaryDirectory() as directory:
            cert, key = Path(directory) / "cert.pem", Path(directory) / "key.pem"
            try:
                subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
                                "-subj", "/CN=127.0.0.1", "-addext", "subjectAltName=IP:127.0.0.1",
                                "-keyout", str(key), "-out", str(cert)], check=True, capture_output=True, timeout=10)
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
                # Windows용 openssl(Git 동봉 등)은 -subj 경로 변환·설정 파일 문제로 실패할 수 있음. 검사 환경 문제라 건너뜀
                self.skipTest(f"이 PC의 openssl로 검사용 인증서를 만들 수 없음: {exc}")
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(str(cert), str(key))
            server = ThreadingHTTPServer(("127.0.0.1", 0), self.server.RequestHandlerClass)
            server.socket = context.wrap_socket(server.socket, server_side=True)
            thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
            url = f"https://127.0.0.1:{server.server_port}"
            try:
                with self.assertRaises(urllib.error.URLError):
                    ApiAdapter(url)._get("/untrusted")
                with patch.dict(os.environ, {"SSL_CERT_FILE": str(cert)}):
                    self.assertEqual(ApiAdapter(url)._get("/ok"), {"value": 1})
                    self.mode = "body"
                    self.bounded_call(lambda: ApiAdapter(url)._get("/slow"))
            finally:
                server.shutdown(); server.server_close(); thread.join(2)


class SessionAndInputTests(unittest.TestCase):
    def setUp(self):
        p = patch.object(e, "data", CachedAdapter(MockAdapter()))
        p.start(); self.addCleanup(p.stop); e.reset_all()

    def test_idle_session_is_reserved_before_waiting_for_lock(self):
        old = e.get_session("returning")
        old.last_seen -= config.SESSION_IDLE_SEC + 1
        ready, release = threading.Event(), threading.Event()
        original = e._locked_session

        @contextmanager
        def paused(session):
            if session is old:
                ready.set()
                if not release.wait(3):
                    raise RuntimeError("test barrier timed out")
            with original(session) as deadline:
                yield deadline

        with patch.object(e, "_locked_session", paused), ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(e.handle, "returning", chip_id="go:D")
            try:
                self.assertTrue(ready.wait(2))
                e.handle("other", chip_id="help")
                self.assertIs(e.SESSIONS["returning"], old)
                with self.assertRaises(Conflict):
                    e.reset_all()
            finally:
                release.set()
            response = future.result(3)
        self.assertEqual(response["destination"]["lot_id"], "D")
        self.assertEqual(response["destination"], e.updates("returning")["destination"])
        self.assertFalse(e._session_users)
        e.reset_all()

    def test_failed_request_releases_session_reservation(self):
        with self.assertRaises(ValueError):
            e.handle("bad", request_id="")
        self.assertFalse(e._session_users)
        old = e.get_session("bad")
        old.last_seen -= config.SESSION_IDLE_SEC + 1
        e.handle("other", chip_id="help")
        self.assertNotIn("bad", e.SESSIONS)

    def test_equivalent_vehicle_numbers_cannot_duplicate_existing_car(self):
        for number in ("7", "07", "０７", "007", " 07 "):
            with self.subTest(number=number):
                result = e.data.enter_car("D", number)
                self.assertFalse(result["ok"])
                self.assertEqual(result["vehicle_no"], "07")
        self.assertEqual(len([c for c in e.data.list_cars() if int(c["vehicle_no"]) == 7]), 1)

    def test_invalid_or_empty_vehicle_does_not_change_occupancy(self):
        before = e.data.get_empty_spots()
        for number in ("", " ", "x", "0", "999", 7, False):
            with self.subTest(number=number), self.assertRaises(ValueError):
                e.data.enter_car("D", number)
        self.assertEqual(before["lots"], e.data.get_empty_spots()["lots"])
        self.assertTrue(e.data.enter_car("D")["demo"])

    def test_restriction_requires_actual_boolean(self):
        e.data.set_restriction("D", True)
        for value in ("false", "true", 0, 1, None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                e.data.set_restriction("D", value)
        self.assertIn("D", e.data.demo_state()["restrictions"])
        e.data.set_restriction("D", False)
        self.assertNotIn("D", e.data.demo_state()["restrictions"])


@unittest.skipUnless(all(importlib.util.find_spec(m) for m in ("fastapi", "httpx")), "선택 의존성 미설치")
class ServerParityTests(unittest.TestCase):
    def setUp(self):
        from fastapi.testclient import TestClient
        from server_fastapi import app
        from server_dev import Handler
        p = patch.object(e, "data", CachedAdapter(MockAdapter()))
        p.start(); self.addCleanup(p.stop); e.reset_all()
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.close)

    def close(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(2)

    def post_both(self, path, body):
        req = urllib.request.Request(f"http://127.0.0.1:{self.server.server_port}/api/v1/chat/demo/" + path,
                                     data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=2) as response:
                status = response.status
        except urllib.error.HTTPError as exc:
            status = exc.code
        return status, self.client.post("/api/v1/chat/demo/" + path, json=body).status_code

    def test_both_servers_reject_false_string_missing_and_null(self):
        for value in ("false", None, 0):
            with self.subTest(value=value):
                self.assertTrue(all(400 <= s < 500 for s in self.post_both("restriction", {"lot_id": "D", "restricted": value})))
        self.assertTrue(all(400 <= s < 500 for s in self.post_both("restriction", {"lot_id": "D"})))
        for value in (True, False):
            self.assertEqual(self.post_both("restriction", {"lot_id": "D", "restricted": value}), (200, 200))

    def test_empty_number_is_rejected_but_omitted_number_is_demo(self):
        self.assertTrue(all(400 <= s < 500 for s in self.post_both("enter", {"lot_id": "D", "vehicle_no": ""})))
        self.assertFalse(e.data.demo_state()["demo_occupied"])
        self.assertEqual(self.post_both("enter", {"lot_id": "D"}), (200, 200))

    def test_removed_status_route_cannot_change_call(self):
        e.handle("status-off", chip_id="call_admin")
        result = self.client.post("/api/v1/chat/admin/calls/1/status", json={"status":"확인 중"})
        self.assertIn(result.status_code, (404, 405))
        self.assertEqual(e.admin_calls()[0]["status"], "접수")
