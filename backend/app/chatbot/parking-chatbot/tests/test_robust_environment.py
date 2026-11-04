"""H/F: process startup, console encoding, config rejection and real FastAPI HTTP load."""
from concurrent.futures import ThreadPoolExecutor
import importlib.util
import json
import os
from pathlib import Path
from safety_check import assert_safe
import socket
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request

ROOT = Path(__file__).resolve().parent.parent


class RobustEnvironmentTests(unittest.TestCase):
    def run_code(self, code, **extra):
        result = subprocess.run([sys.executable, "-c", code], cwd=ROOT,
            env={**os.environ, "DATA_SOURCE": "mock", "LLM_PROVIDER": "none", "PYTHONIOENCODING": "utf-8", **extra},
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10)
        return result

    def test_invalid_data_source_is_explicit_failure(self):
        result = self.run_code("from chatbot import config", DATA_SOURCE="abc")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("DATA_SOURCE", result.stderr)

    def test_cp949_console_and_unicode_working_path(self):
        result = self.run_code("from chatbot import engine as e; print(e.handle('s',text='빈자리')['text'])", PYTHONIOENCODING="cp949")
        self.assertEqual(result.returncode, 0, result.stderr)
        with tempfile.TemporaryDirectory(prefix="한글 경로 ") as directory:
            destination = Path(directory)
            for folder in ("chatbot", "tests", "static"):
                shutil.copytree(ROOT/folder, destination/folder, ignore=shutil.ignore_patterns("__pycache__"))
            result = subprocess.run([sys.executable, "tests/run_eval.py"], cwd=destination,
                env={**os.environ, "DATA_SOURCE": "mock", "LLM_PROVIDER": "none", "PYTHONIOENCODING": "utf-8"},
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_base_url_trailing_slash_and_port_in_use(self):
        result = self.run_code("from chatbot.adapters import ApiAdapter; assert ApiAdapter('http://127.0.0.1:1/').base=='http://127.0.0.1:1'")
        self.assertEqual(result.returncode, 0)
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            sock.listen()
            result = subprocess.run([sys.executable, "server_dev.py", str(sock.getsockname()[1])], cwd=ROOT,
                env={**os.environ, "PYTHONIOENCODING": "utf-8"}, capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=10)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Address already in use", result.stderr)

    @unittest.skipUnless(importlib.util.find_spec("fastapi") and importlib.util.find_spec("uvicorn"), "선택 HTTP 환경 미설치")
    def test_fastapi_fifty_concurrent_http_requests(self):
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        env = {**os.environ, "PYTHONIOENCODING": "utf-8", "DATA_SOURCE": "mock", "LLM_PROVIDER": "none"}
        process = subprocess.Popen([sys.executable, "-m", "uvicorn", "server_fastapi:app", "--port", str(port), "--host", "127.0.0.1"],
                                   cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        base = f"http://127.0.0.1:{port}"
        try:
            limit = time.monotonic()+5
            while time.monotonic() < limit:
                try:
                    with urllib.request.urlopen(base+"/api/v1/chat/config", timeout=.2) as response:
                        if response.status == 200: break
                except OSError: time.sleep(.02)
            else: self.fail("FastAPI startup deadline")
            def request(index):
                data = json.dumps({"session_id": "http"+str(index), "text": "빈자리"}).encode("utf-8")
                req = urllib.request.Request(base+"/api/v1/chat", data=data, headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=5) as response:
                    answer = json.loads(response.read().decode("utf-8"))
                assert_safe(answer)
            with ThreadPoolExecutor(max_workers=50) as pool:
                for f in [pool.submit(request, i) for i in range(50)]: f.result(6)
        finally:
            process.terminate()
            try: process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
