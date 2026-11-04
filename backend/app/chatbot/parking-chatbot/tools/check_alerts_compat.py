"""Probe the real team alerts router in an isolated local app, without a model key.

Run this in a team checkout containing PR #46 and the chatbot correction.
This mounts routers only on the probe app; team source files are never changed.
"""
import importlib.util
import os
from pathlib import Path
import secrets
import socket
import sqlite3
import sys
import tempfile
import threading
import time
from importlib import import_module
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parents[3]


def run():
    import httpx
    import uvicorn

    sys.path.insert(0, str(REPO))
    with tempfile.TemporaryDirectory(prefix="chatbot-alerts-probe-") as directory, socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        base = f"http://127.0.0.1:{sock.getsockname()[1]}"
        token = secrets.token_hex(16)
        os.environ.update(DATA_SOURCE="api", BACKEND_BASE_URL=base, LLM_PROVIDER="none",
                          ALERTS_INTERNAL_TOKEN=token, ALERT_MOCK_FIRE_LEVEL="2")
        from backend.app.main import create_app
        from backend.app.alerts import router as alerts
        from backend.app.billing.db import get_db_conn
        from backend.app.chatbot.router import router, _PACKAGE

        e = import_module(_PACKAGE + ".engine")
        e.config.ALERT_SHARE_SEC = 0
        path = Path(directory) / "alerts.sqlite3"

        def connect():
            conn = sqlite3.connect(path, check_same_thread=False)
            try:
                yield conn
            finally:
                conn.close()

        app = create_app()
        app.dependency_overrides[get_db_conn] = connect
        app.include_router(alerts.router)
        app.include_router(alerts.internal_router)
        app.include_router(router)
        server = uvicorn.Server(uvicorn.Config(app, log_level="error"))
        thread = threading.Thread(target=server.run, kwargs={"sockets":[sock]}, daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + 10
            while not server.started:
                if not thread.is_alive() or time.monotonic() >= deadline:
                    raise RuntimeError("경보 검사 서버 시작 실패")
                time.sleep(.02)
            with httpx.Client(base_url=base, timeout=8, trust_env=False) as client:
                def create(event, *, space="A-02", source="simulation"):
                    response = client.post("/api/v1/internal/alerts", headers={"Authorization":"Bearer " + token},
                        json={"event_id":event, "lot_id":"A", "space_id":space, "source":source})
                    response.raise_for_status()
                    return response.json()

                def ask(label):
                    response = client.post("/api/v1/chat", json={"session_id":"alerts-probe", "text":"경보 알려줘"})
                    if response.is_error:
                        raise AssertionError(f"chat HTTP {response.status_code}: {response.text}")
                    answer = response.json()
                    print(label + "\n" + answer["text"], flush=True)
                    return answer

                active = create("probe-level-two")
                assert active["level"] == 2
                answer = ask("기본 Lv2·detected_at·simulation")
                assert answer["safety"] and answer["card"]["data"]["level"] == 2
                assert answer["card"]["data"]["started_at"] == active["detected_at"]
                assert "모의 이벤트" in answer["text"] and "모의 화재" in answer["text"]
                print("PASS default level two, time, mock source", flush=True)

                response = client.post(f"/api/v1/alerts/{active['alert_id']}/acknowledge")
                response.raise_for_status()
                assert "관리자가 확인 중" in ask("확인 처리")["text"]
                print("PASS acknowledge", flush=True)

                response = client.post(f"/api/v1/alerts/{active['alert_id']}/resolve")
                response.raise_for_status()
                answer = ask("해제·resolved 목록")
                assert answer["safety"] is None and "해제" in answer["text"]
                assert client.get("/api/v1/alerts").json()["items"][0]["status"] == "resolved"
                print("PASS resolve", flush=True)

                null_space = create("probe-null-space", space=None, source="manual")
                answer = ask("resolved+active 혼합·null 칸·manual")
                assert answer["safety"] and "정확한 칸 확인 중" in answer["text"]
                assert "모의 이벤트" in answer["text"]
                print("PASS mixed list, null space, manual source", flush=True)

                with sqlite3.connect(path) as conn:
                    conn.execute("UPDATE alerts SET confidence = NULL WHERE alert_id = ?", (null_space["alert_id"],))
                e.reset_all()
                answer = ask("nullable confidence")
                assert answer["safety"] and answer["card"]["data"]["confidence"] is None
                print("PASS nullable confidence preserves level two", flush=True)

                client.post(f"/api/v1/alerts/{null_space['alert_id']}/resolve").raise_for_status()
                with patch.object(alerts, "MOCK_FIRE_LEVEL", 1):
                    caution = create("probe-level-one", source="vision")
                answer = ask("명시 Lv1·vision")
                assert caution["level"] == 1 and answer["card"]["data"]["level"] == 1
                assert answer["safety"] is None and "모의 이벤트" not in answer["text"]
                print("PASS explicit level one, vision source", flush=True)
                print("PASS alerts compatibility 6/6", flush=True)
        finally:
            server.should_exit = True
            thread.join(8)
            if thread.is_alive():
                raise RuntimeError("경보 검사 서버 종료 실패")


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if (not (REPO / "backend/app/alerts/router.py").is_file()
            or not all(importlib.util.find_spec(x) for x in ("fastapi", "uvicorn", "httpx"))):
        print("PR #46 경보 코드와 팀 HTTP 의존성이 있는 작업 복사본에서 실행해 주세요.")
        return 0
    try:
        run()
        return 0
    except Exception as exc:
        print(f"FAIL alerts compatibility: {type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
