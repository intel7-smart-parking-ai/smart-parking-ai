"""Run the six approved questions against an isolated team demo server."""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import secrets
import socket
import sys
import threading
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parents[3]


def available():
    return (all(importlib.util.find_spec(name) for name in ("fastapi", "uvicorn", "httpx"))
            and (REPO / "backend/app/demo_main.py").is_file()
            and (REPO / "backend/app/simulation/run_parking_demo.py").is_file())


class StopDemo(Exception):
    pass


def run_rehearsal():
    import httpx
    import uvicorn

    sys.path.insert(0, str(REPO))
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        base = f"http://127.0.0.1:{sock.getsockname()[1]}"
        token = secrets.token_hex(16)
        os.environ.update(DATA_SOURCE="api", LLM_PROVIDER="none", BACKEND_BASE_URL=base,
                          PREVIEW_DEMO_TOKEN=token)
        from backend.app.chatbot.team_preview import create_demo_app
        from backend.app.simulation.run_parking_demo import run_demo

        app = create_demo_app()
        server = uvicorn.Server(uvicorn.Config(app, log_level="error", lifespan="on"))
        serving = threading.Thread(target=server.run, kwargs={"sockets":[sock]}, daemon=True)
        ready, stop = threading.Event(), threading.Event()
        errors = []
        feeding = None

        def feed():
            def wait(seconds):
                if stop.wait(seconds):
                    raise StopDemo()

            def output(message):
                if message.startswith("점유 유지 중"):
                    ready.set()

            try:
                with httpx.Client(base_url=base, timeout=5, trust_env=False) as client:
                    run_demo(client, token, short=True, hold=True, wait=wait, output=output)
            except StopDemo:
                pass
            except Exception as exc:
                errors.append(type(exc).__name__)
                ready.set()

        serving.start()
        try:
            deadline = time.monotonic() + 10
            while not server.started:
                if not serving.is_alive() or time.monotonic() >= deadline:
                    raise RuntimeError("시연 서버 시작 실패")
                time.sleep(.02)
            feeding = threading.Thread(target=feed, daemon=True)
            feeding.start()
            if not ready.wait(12) or errors:
                raise RuntimeError("합성 차량 주차 확인 실패")
            transcript = []
            sid = "rehearsal-" + uuid.uuid4().hex

            with httpx.Client(base_url=base, timeout=8, trust_env=False) as client:
                def ask(label, text, previous=None):
                    body = {"session_id":sid, "text":text, "request_id":uuid.uuid4().hex}
                    if previous and previous.get("confirmation_token"):
                        body["confirmation_token"] = previous["confirmation_token"]
                    response = client.post("/api/v1/chat", json=body)
                    response.raise_for_status()
                    answer = response.json()
                    transcript.append({"question":label, "text":answer["text"]})
                    print(f"질문 {label}\n{answer['text']}\n", flush=True)
                    return answer

                def check(number, passed):
                    print(f"{'PASS' if passed else 'FAIL'} {number}", flush=True)
                    if not passed:
                        raise AssertionError(f"질문 {number} 기대 요지 불일치")

                answer = ask("① A 빈자리 있어?", "A 빈자리 있어?")
                shown = answer["card"]["data"]["lots"]["A"]["empty"]
                actual = next(row for row in client.get("/api/v1/parking-lots").json()["items"]
                              if row["lot_id"] == "A")["empty_spaces"]
                check(1, shown == actual == 9 and "※ 시연용 데이터예요" in answer["text"])

                answer = ask("② 10번 차 어디 있어?", "10번 차 어디 있어?")
                check(2, answer["flow"] == "idle" and answer["confirmation_token"] is None
                      and answer["card"]["type"] == "car_location"
                      and answer["card"]["data"]["car_id"] == "10"
                      and answer["card"]["data"]["space_id"] == "A-01")

                answer = ask("③ 요금 얼마야?", "요금 얼마야?")
                check(3, answer["card"]["type"] == "fee" and answer["card"]["data"]["quote_id"])

                answer = ask("④ 결제해 줘", "결제해 줘")
                check("4-확인", answer["flow"] == "confirm_action")
                amount = answer["card"]["data"]["fee"]
                answer = ask("④ 네", "네", answer)
                check(4, answer["card"]["type"] == "payment"
                      and answer["card"]["data"]["amount"] == amount)

                answer = ask("⑤ 요금표 알려줘", "요금표 알려줘")
                # 11/11: 요금표는 주차장마다 한 줄(처음 N분·이후·하루 최대)로 바뀌었다. 예전 한 줄 요약('기본 1,000원')도 허용한다.
                check(5, ("기본" in answer["text"] or "하루 최대" in answer["text"]) and "원" in answer["text"])

                answer = ask("⑥ 7번 차 어디야?", "7번 차 어디야?")
                check(6, "두 자리" in answer["text"] and answer["card"] is None)
            if errors:
                raise RuntimeError("합성 관측 유지 실패")
            return transcript
        finally:
            stop.set()
            if feeding is not None:
                feeding.join(timeout=6)
            server.should_exit = True
            serving.join(timeout=8)
            if serving.is_alive():
                raise RuntimeError("시연 서버 종료 실패")


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if not available():
        print("팀 레포와 FastAPI·uvicorn·httpx가 있어야 리허설을 실행할 수 있어요.")
        return 0
    try:
        run_rehearsal()
        print("PASS 11/06 리허설 6/6")
        return 0
    except Exception as exc:
        print(f"FAIL 리허설: {type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
