"""설치 없이 바로 실행하는 테스트 서버 (파이썬 표준 라이브러리만 사용).

    python server_dev.py          → 이용자 화면 http://127.0.0.1:8080
                                     관리자 화면 http://127.0.0.1:8080/admin.html

팀 백엔드에 붙일 때는 server_fastapi.py 를 사용 (같은 엔진, 같은 API 경로).
"""
import json
import re
import sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from chatbot import engine, incidents
from chatbot.state import Conflict
from chatbot.adapters import DataUnavailable
from chatbot.contracts import alerts_source
from chatbot.http_api import change_report
from chatbot.access import require_admin, AccessDenied

STATIC = Path(__file__).parent / "static"
RE_INC = re.compile(r"^/api/v1/chat/incidents/(\d+)/(check|notes|confirm)$")


class Handler(SimpleHTTPRequestHandler):
    # 헤더·본문을 끝까지 보내지 않는 연결이 스레드를 무기한 점유하지 않게 한다.
    timeout = 10
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(STATIC), **kwargs)

    def log_message(self, fmt, *args):
        pass

    def _json(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if not 0 <= n <= 65536:
            raise ValueError("요청 본문은 64KiB 이하여야 합니다.")
        raw = self.rfile.read(n) if n else b""
        if len(raw) != n:
            raise ValueError("요청 본문이 끝까지 수신되지 않았습니다.")
        body = json.loads(raw) if n else {}
        if not isinstance(body, dict):
            raise ValueError("요청 본문은 JSON 객체여야 합니다.")
        return body

    def do_GET(self):
        try:
            self._access()
            return self._get()
        except AccessDenied as exc:
            return self._json({"error": str(exc)}, exc.status)
        except Conflict as exc:
            return self._json({"error": str(exc)}, 409)
        except DataUnavailable:
            return self._json({"error": "현재 정보를 가져올 수 없습니다."}, 503)
        except TimeoutError:
            self.close_connection = True
            return self._json({"error": "요청 수신 시간이 초과됐습니다."}, 408)
        except (ValueError, TypeError, RecursionError) as exc:
            return self._json({"error": str(exc)}, 400)

    def _get(self):
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        routes = {
            "/api/v1/chat/config": self._parking_config,
            "/api/v1/chat/suggest": lambda: {"items": engine.suggest(q.get("q", ""))},
            "/api/v1/chat/updates": lambda: engine.updates(q.get("session_id"), int(q.get("since", 0)), int(q.get("cursor", 0)), q.get("runtime_id")),
            "/api/v1/chat/admin/calls": lambda: {"items": engine.admin_calls()},
            "/api/v1/chat/incidents": self._incidents,
            "/api/v1/chat/stats": lambda: engine.stats(),
            "/api/v1/chat/demo/cars": lambda: {"items": engine.data.list_cars(), **engine.data.demo_state()},
        }
        if u.path in routes:
            return self._json(routes[u.path]())
        return super().do_GET()

    def _parking_config(self):
        engine.data._ensure_catalog()
        return engine.config.catalog.public()

    def _incidents(self):
        alert = engine.current_alert()
        return {"items": incidents.list_public(), "runtime_id": engine.RUNTIME_ID,
                "alerts_source": alerts_source(alert)}

    def do_POST(self):
        try:
            self._access()
            return self._post()
        except AccessDenied as exc:
            return self._json({"error": str(exc)}, exc.status)
        except Conflict as exc:
            return self._json({"error": str(exc)}, 409)
        except DataUnavailable:
            return self._json({"error": "현재 정보를 가져올 수 없습니다."}, 503)
        except TimeoutError:
            self.close_connection = True
            return self._json({"error": "요청 수신 시간이 초과됐습니다."}, 408)
        except (ValueError, TypeError, RecursionError) as exc:
            return self._json({"error": str(exc)}, 400)

    def _post(self):
        u = urlparse(self.path)
        b = self._body()
        if u.path == "/api/v1/chat":
            return self._json(engine.handle(b.get("session_id"), b.get("text"), b.get("chip_id"), b.get("event"), b.get("request_id"), b.get("confirmation_token"), b.get("runtime_id")))
        m = RE_INC.match(u.path)
        if m:
            inc_id, action = int(m.group(1)), m.group(2)
            return self._json(change_report(inc_id, action, b))
        if u.path == "/api/v1/chat/demo/alert":
            r = engine.data.set_alert(b.get("level", 0), b.get("lot_id"), b.get("spot_no"))
            engine.current_alert()
            return self._json(r)
        if u.path == "/api/v1/chat/demo/exit":
            return self._json(engine.data.exit_car(str(b.get("car_id", "")).zfill(2)))
        if u.path == "/api/v1/chat/demo/enter":
            return self._json(engine.data.enter_car(b.get("lot_id"), b.get("car_id")))
        if u.path == "/api/v1/chat/demo/fill":
            return self._json(engine.data.fill_lot(b.get("lot_id")))
        if u.path == "/api/v1/chat/demo/clear":
            return self._json(engine.data.clear_demo(b.get("lot_id") or None))
        if u.path == "/api/v1/chat/demo/unrecognized":
            return self._json(engine.data.enter_unrecognized(b.get("lot_id")))
        if u.path == "/api/v1/chat/demo/uncertain":
            return self._json(engine.data.set_uncertain(b.get("lot_id"), b.get("spot_no"), b.get("uncertain")))
        if u.path == "/api/v1/chat/demo/restriction":
            return self._json(engine.data.set_restriction(b.get("lot_id"), b.get("restricted"), b.get("reason", "")))
        if u.path == "/api/v1/chat/demo/reset":
            engine.reset_all()
            return self._json({"ok": True})
        return self._json({"error": "not found"}, 404)

    def _access(self):
        path = urlparse(self.path).path
        if any(path.startswith("/api/v1/chat/" + prefix) for prefix in ("admin/", "incidents", "stats", "demo/")):
            require_admin(self.headers.get("Authorization"), self.client_address[0], "/demo/" in path)


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8080
    print(f"이용자 화면: http://127.0.0.1:{port}   관리자 화면: http://127.0.0.1:{port}/admin.html")
    import logging
    logging.basicConfig(level=logging.INFO)
    engine.config.log_startup()
    try:
        server = ThreadingHTTPServer((engine.config.getenv("HOST", "127.0.0.1"), port), Handler)
    except OSError as exc:
        # OS마다 문구가 다르다(리눅스 Address already in use / Windows WinError 10048). 공통 안내를 먼저 쓴다.
        print(f"PORT_UNAVAILABLE: {port}번 포트를 열 수 없어요(이미 사용 중일 수 있음). 다른 포트로 실행해 주세요. ({exc})", file=sys.stderr)
        raise SystemExit(1)
    server.serve_forever()
