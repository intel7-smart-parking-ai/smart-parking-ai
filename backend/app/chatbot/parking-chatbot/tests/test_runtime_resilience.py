"""멈춤·소켓 장애·세션 자원 상한을 시간 제한 아래 실제 실행한다."""
import socket
import threading
import time
import unittest
from unittest.mock import patch
from http.server import ThreadingHTTPServer
from chatbot import engine as e, config
from chatbot.state import Conflict
from chatbot.adapters import CachedAdapter, MockAdapter
from server_dev import Handler


class RuntimeResilienceTests(unittest.TestCase):
    def setUp(self):
        p = patch.object(e, 'data', CachedAdapter(MockAdapter()))
        p.start(); self.addCleanup(p.stop); e.reset_all()

    def test_busy_session_has_bounded_wait_and_other_session_works(self):
        session = e.get_session('busy')
        ready, release = threading.Event(), threading.Event()
        def hold():
            with session.lock:
                ready.set(); release.wait(2)
        worker = threading.Thread(target=hold); worker.start(); ready.wait(1)
        try:
            with patch.object(config, 'REQUEST_BUDGET_SEC', .05):
                started = time.monotonic()
                with self.assertRaises(Conflict): e.handle('busy', text='빈자리')
                self.assertLess(time.monotonic()-started, .5)
                self.assertIn('text', e.handle('free', text='빈자리'))
        finally:
            release.set(); worker.join(2)
        self.assertIn('text', e.handle('busy', text='빈자리'))

    def test_session_capacity_and_idle_cleanup_release_related_resources(self):
        with patch.object(config, 'MAX_SESSIONS', 3):
            for i in range(3):
                e.handle(str(i), chip_id='help')
                e.notifications.enqueue(str(i), {'text':'notice'})
            # 11/10 P0-3: 한도에서는 거절하지 않고 가장 오래 안 쓴 유휴 세션('0')을 정리한다.
            e.handle('overflow', text='빈자리')
            self.assertEqual(set(e.SESSIONS), {'1', '2', 'overflow'})
            self.assertNotIn('0', e.notifications.streams)
            # 모든 세션이 처리 중일 때만 거절한다.
            with patch.dict(e._session_users, {'1': 1, '2': 1, 'overflow': 1}):
                with self.assertRaises(Conflict): e.handle('blocked', text='빈자리')
            for session in e.SESSIONS.values(): session.last_seen -= config.SESSION_IDLE_SEC + 1
            e.handle('replacement', text='빈자리')
            self.assertEqual(set(e.SESSIONS), {'replacement'})
            self.assertFalse(any(s in e.notifications.streams for s in ['0','1','2','overflow']))

    def test_replay_history_log_and_notification_queues_stay_bounded(self):
        for i in range(1100):
            e.handle('bounded', chip_id='help', request_id=str(i))
            e.notifications.enqueue('bounded', {'text':'test'}, key='item-'+str(i))
        session = e.get_session('bounded')
        self.assertLessEqual(len(session.requests), 100)
        self.assertLessEqual(len(session.history), 100)
        self.assertLessEqual(len(e.LOG), 2000)
        result = e.notifications.read('bounded')
        self.assertEqual(len(result['messages']), 100)
        self.assertTrue(result['resync_required'])


class SocketResilienceTests(unittest.TestCase):
    def setUp(self):
        class ShortTimeoutHandler(Handler):
            timeout = .15
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), ShortTimeoutHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.close)

    def close(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(2)

    def request(self, head, body=b''):
        with socket.create_connection(self.server.server_address, timeout=1) as connection:
            connection.settimeout(.8)
            connection.sendall(head + body)
            response = b''
            while True:
                part = connection.recv(65536)
                if not part: break
                response += part
            return response

    def test_incomplete_body_times_out_and_health_request_still_works(self):
        response = self.request(b'POST /api/v1/chat HTTP/1.0\r\nContent-Length: 100\r\n\r\n', b'{')
        self.assertIn(b'408', response.split(b'\r\n')[0])
        healthy = self.request(b'GET /api/v1/chat/config HTTP/1.0\r\n\r\n')
        self.assertIn(b'200', healthy.split(b'\r\n')[0])

    def test_oversized_body_and_malformed_json_are_rejected(self):
        for header, body in [(b'65537',b''),(b'1',b'{')]:
            response = self.request(b'POST /api/v1/chat HTTP/1.0\r\nContent-Length: '+header+b'\r\n\r\n',body)
            self.assertIn(b'400',response.split(b'\r\n')[0])

    def test_deep_json_is_rejected_without_losing_server(self):
        body = b'{"x":' + b'[' * 1500 + b'0' + b']' * 1500 + b'}'
        response = self.request(b'POST /api/v1/chat HTTP/1.0\r\nContent-Length: '+str(len(body)).encode()+b'\r\n\r\n', body)
        self.assertIn(b'400', response.split(b'\r\n')[0])
        self.assertIn(b'200', self.request(b'GET /api/v1/chat/config HTTP/1.0\r\n\r\n').split(b'\r\n')[0])
