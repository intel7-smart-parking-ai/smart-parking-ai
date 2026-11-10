"""Verify the embedded team route with actual HTTP reads and a local model peer."""
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
from threading import Thread
import time
import unittest


REPO = Path(__file__).resolve().parents[5]
AVAILABLE = (all(importlib.util.find_spec(name) for name in
                 ("fastapi", "httpx", "langchain", "langchain_openai"))
             and (REPO / "backend/app/chatbot/router.py").is_file())
ARRIVAL = "공항에 차를 몰고 가는데 그냥 들어가도 괜찮을까?"
FOLLOWUP = "그러면 거기 기본요금도 궁금해."
OUTAGE = "거기 들어가는 건 무리 없겠어?"


@unittest.skipUnless(AVAILABLE, "requires the team router, FastAPI and optional LangChain dependencies")
class EmbeddedAgentHTTPTests(unittest.TestCase):
    def test_model_reads_backend_data_and_preserves_turn_history(self):
        model_requests, backend_requests, backend_writes = [], [], []

        class Peer(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def reply(self, body, status=200):
                payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def do_GET(self):
                backend_requests.append(self.path)
                as_of = datetime.now(timezone.utc).isoformat()
                if self.path == "/api/v1/parking-lots":
                    items = [{"lot_id": "C", "name": "김해공항 주차장", "total_spaces": 3, "empty_spaces": 2}]
                elif self.path == "/api/v1/parking-lots/C/spaces":
                    items = [{"lot_id": "C", "space_id": f"A-{index:02d}", "car_id": None,
                              "status": status} for index, status in enumerate(("empty", "empty", "occupied"), 1)]
                elif self.path == "/api/v1/rate-policies":
                    items = [{"lot_id": "C", "base_minutes": 30, "base_fee": 3700,
                              "unit_minutes": 10, "extra_fee": 500, "daily_cap": 12000}]
                elif self.path == "/api/v1/alerts":
                    items = []
                else:
                    return self.reply({"detail": "Not Found"}, 404)
                self.reply({"items": items, "as_of": as_of})

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                if self.path != "/api/v1/chat/completions":
                    backend_writes.append((self.path, body))
                    return self.reply({"detail": "Unexpected write"}, 400)
                model_requests.append(body)
                messages = body["messages"]
                user = next(row["content"] for row in reversed(messages) if row["role"] == "user")
                if user == OUTAGE:
                    return self.reply({"error": {"message": "test provider outage"}}, 503)
                if user == ARRIVAL:
                    # Two model calls must have more than the backend's 2-second
                    # read deadline, while staying within the 6-second turn.
                    time.sleep(1.05)
                if messages[-1]["role"] == "tool":
                    row = json.loads(messages[-1]["content"])["data"]["rows"][0]
                    text = (f"김해공항 주차장의 기본요금은 {row['base_fee']:,}원이에요."
                            if user == FOLLOWUP else f"김해공항 주차장에 현재 {row['empty']}자리 있어요.")
                    message, reason = {"role": "assistant", "content": text}, "stop"
                else:
                    name = "fee_policies" if user == FOLLOWUP else "parking_status"
                    message = {"role": "assistant", "content": None, "tool_calls": [{
                        "id": "team-wire-read", "type": "function",
                        "function": {"name": name, "arguments": json.dumps({"lot_ids": ["C"]})},
                    }]}
                    reason = "tool_calls"
                self.reply({"id": "team-wire", "object": "chat.completion", "created": 0,
                            "model": body["model"], "choices": [
                                {"index": 0, "message": message, "finish_reason": reason}]})

        server = ThreadingHTTPServer(("127.0.0.1", 0), Peer)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        base = f"http://127.0.0.1:{server.server_port}"
        script = """
import json
import sys
from importlib import import_module
from fastapi import FastAPI
from fastapi.testclient import TestClient
from backend.app.chatbot.router import _PACKAGE, router

config = import_module(_PACKAGE + ".config")
config.OPENROUTER_BASE_URL = sys.argv[1] + "/api/v1"
app = FastAPI()
app.include_router(router)
with TestClient(app) as client:
    results = []
    for text in sys.argv[2:]:
        response = client.post("/api/v1/chat", json={"session_id": "wire", "text": text})
        assert response.status_code == 200, response.text
        results.append(response.json())
    chip = client.post("/api/v1/chat", json={"session_id": "wire", "chip_id": "empty_all"})
    assert chip.status_code == 200, chip.text
    results.append(chip.json())
print(json.dumps(results, ensure_ascii=False))
"""
        env = {key: value for key, value in os.environ.items()
               if key.lower() not in {"all_proxy", "http_proxy", "https_proxy"}}
        out = subprocess.run(
            [sys.executable, "-c", script, base, ARRIVAL, FOLLOWUP, OUTAGE], cwd=REPO,
            env={**env, "CHATBOT_EMBEDDED": "1", "DATA_SOURCE": "api", "BACKEND_BASE_URL": base,
                 "LLM_ENGINE": "langchain", "LLM_PROVIDER": "openrouter", "LLM_MODEL": "wire-test-model",
                 "OPENROUTER_API_KEY": "local-wire-test", "LLM_MAX_PER_SESSION_MIN": "10",
                 "LLM_MAX_PER_MIN": "30", "LLM_MAX_PER_DAY": "30", "PYTHONIOENCODING": "utf-8",
                 "REQUEST_BUDGET_SEC": "6", "BACKEND_TIMEOUT_SEC": "2", "LLM_TIMEOUT_SEC": "3",
                 "LLM_PREFETCH": "0"},   # 이 검사는 모델이 직접 도구를 부르는 통신 경로를 본다(미리 조회는 test_v33_prefetch)
            capture_output=True, encoding="utf-8", errors="replace", timeout=40,
        )
        self.assertEqual(out.returncode, 0, out.stderr)
        arrival, followup, outage, chip = json.loads(out.stdout)
        for reply, detail in ((arrival, "2자리"), (followup, "3,700원")):
            self.assertEqual(reply["meta"]["source"], "llm", reply)
            self.assertEqual(reply["meta"]["agent_status"], "ok", reply)
            self.assertEqual(reply["meta"]["model_calls"], 2)
            self.assertIn(detail, reply["text"])
            self.assertEqual(reply["messages"][-1]["text"], reply["text"])
            self.assertIsNone(reply["confirmation_token"])
        self.assertNotEqual(outage["meta"]["source"], "llm")
        self.assertEqual(chip["meta"]["source"], "chip")
        self.assertEqual(chip["card"]["data"]["lots"]["C"]["empty"], 2)
        self.assertFalse(backend_writes)
        self.assertIn("/api/v1/parking-lots/C/spaces", backend_requests)
        self.assertIn("/api/v1/rate-policies", backend_requests)
        self.assertEqual(len(model_requests), 5)
        self.assertTrue(all(request["model"] == "wire-test-model" for request in model_requests))
        follow_messages = model_requests[2]["messages"]
        self.assertEqual([row["content"] for row in follow_messages if row["role"] == "user"], [ARRIVAL, FOLLOWUP])
        self.assertTrue(any(row["role"] == "assistant" and "2자리" in row["content"] for row in follow_messages))
        vacancy = json.loads(model_requests[1]["messages"][-1]["content"])
        fees = json.loads(model_requests[3]["messages"][-1]["content"])
        self.assertEqual(vacancy["source"]["kind"], "internal_api")
        self.assertEqual(vacancy["data"]["rows"][0]["empty"], 2)
        self.assertEqual(fees["data"]["rows"][0]["base_fee"], 3700)
