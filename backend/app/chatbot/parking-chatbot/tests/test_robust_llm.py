"""B: slow fake models are bounded without any provider network calls."""
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
import threading
import time
import unittest
from unittest.mock import patch
from chatbot import config, llm_router as r


class RobustLLMTests(unittest.TestCase):
    def test_ten_second_fake_call_falls_back_at_three_seconds(self):
        release = threading.Event()
        class Model:
            def bind_tools(self, tools): return self
            def invoke(self, messages):
                release.wait(10)
                return SimpleNamespace(tool_calls=[{"name": "call_admin", "args": {}}])
        settings = ("openai", "fake", 3, "fake-hash")
        with patch.object(r, "_settings", return_value=(settings, None)), patch.object(r, "_model", Model()), patch.object(r, "_model_key", settings), patch.object(config, "LLM_TIMEOUT_SEC", 3):
            try:
                began = time.monotonic()
                self.assertIsNone(r.route("분류할 문장"))
                self.assertGreaterEqual(time.monotonic()-began, 2.8)
                self.assertLess(time.monotonic()-began, 4)
            finally:
                release.set()
                for thread in threading.enumerate():
                    if thread.name == "parking-llm-route": thread.join(timeout=1)
            self.assertEqual(r.stats()["inflight"], 0)

    def test_five_simultaneous_unknowns_have_two_active_slots(self):
        entered, release = threading.Event(), threading.Event()
        state = {"active": 0, "peak": 0}
        lock = threading.Lock()
        class Model:
            def bind_tools(self, tools): return self
            def invoke(self, messages):
                with lock:
                    state["active"] += 1
                    state["peak"] = max(state["active"], state["peak"])
                entered.set()
                try:
                    release.wait(2)
                    return SimpleNamespace(tool_calls=[{"name": "help", "args": {}}])
                finally:
                    with lock: state["active"] -= 1
        settings = ("openai", "fake", .08, "fake-hash")
        with patch.object(r, "_settings", return_value=(settings, None)), patch.object(r, "_model", Model()), patch.object(r, "_model_key", settings), patch.object(config, "LLM_TIMEOUT_SEC", .08):
            try:
                with ThreadPoolExecutor(max_workers=5) as pool:
                    began = time.monotonic()
                    results = list(pool.map(r.route, ["뿌잉"]*5))
                self.assertEqual(results, [None]*5)
                self.assertLess(time.monotonic()-began, .8)
                self.assertLessEqual(state["peak"], 2)
                self.assertTrue(entered.is_set())
            finally:
                release.set()
                for thread in threading.enumerate():
                    if thread.name == "parking-llm-route": thread.join(timeout=1)
        self.assertEqual(r.stats()["inflight"], 0)
