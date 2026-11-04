"""표준 라이브러리 HTTP JSON 수신: 본문까지 시간·크기 한도를 적용한다."""
import http.client
import json
import socket
import threading
import time
import urllib.request
from urllib.error import HTTPError


MAX_JSON_BYTES = 1024 * 1024


def load_json(request, timeout, *, max_bytes=MAX_JSON_BYTES, with_metadata=False):
    deadline = time.monotonic() + timeout
    timers = []
    expired = threading.Event()

    def remaining():
        value = deadline - time.monotonic()
        if value <= 0:
            raise TimeoutError("응답 수신 시간 한도를 넘었습니다.")
        return value

    def connection_type(base):
        class Connection(base):
            def connect(self):
                self.timeout = remaining()
                super().connect()
                sock = self.sock
                sock.settimeout(remaining())

                def interrupt():
                    expired.set()
                    try:
                        # close()만으로는 다른 스레드의 진행 중인 read를 깨우지 못한다.
                        sock.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass

                timer = threading.Timer(remaining(), interrupt)
                timer.daemon = True
                timers.append(timer)
                timer.start()
        return Connection

    http_connection = connection_type(http.client.HTTPConnection)
    https_connection = connection_type(http.client.HTTPSConnection)

    class HTTP(urllib.request.HTTPHandler):
        def http_open(self, req):
            return self.do_open(http_connection, req)

    class HTTPS(urllib.request.HTTPSHandler):
        def https_open(self, req):
            return self.do_open(https_connection, req, context=self._context)

    try:
        # 기존 urllib의 인증서 검증, 프록시와 redirect 동작을 유지한다.
        opener = urllib.request.build_opener(HTTP(), HTTPS())
        try:
            response = opener.open(request, timeout=remaining())
        except HTTPError as exc:
            if not with_metadata:
                raise
            response = exc
        with response:
            remaining()
            length = response.headers.get("Content-Length")
            if length is not None and int(length) > max_bytes:
                raise ValueError("응답 본문 크기 한도를 넘었습니다.")
            body = response.read(max_bytes + 1)
            remaining()
            if len(body) > max_bytes:
                raise ValueError("응답 본문 크기 한도를 넘었습니다.")
            if length is not None and len(body) != int(length):
                raise ValueError("응답 본문이 끝까지 수신되지 않았습니다.")
            try:
                value = json.loads(body.decode("utf-8"))
            except ValueError:
                if not with_metadata or response.status < 400:
                    raise
                value = {}  # Preserve the status even when a proxy returned HTML.
            return (response.status, value, dict(response.headers)) if with_metadata else value
    except (OSError, ValueError, http.client.HTTPException) as exc:
        if expired.is_set() or time.monotonic() >= deadline:
            raise TimeoutError("응답 수신 시간 한도를 넘었습니다.") from exc
        if isinstance(exc, http.client.HTTPException):
            raise OSError("유효한 HTTP 응답을 수신하지 못했습니다.") from exc
        raise
    finally:
        for timer in timers:
            timer.cancel()
        for timer in timers:
            timer.join()
