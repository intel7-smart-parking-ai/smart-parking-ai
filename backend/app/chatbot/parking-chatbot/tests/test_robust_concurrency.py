"""F: same-session actions, independent sessions, alarm races and explicit soak."""
from concurrent.futures import ThreadPoolExecutor
import gc
import json
import os
from pathlib import Path
import threading
import time
import tracemalloc
import unittest
from unittest.mock import patch
from chatbot import engine as e
from chatbot.adapters import CachedAdapter, MockAdapter
from safety_check import assert_safe


class RobustConcurrencyTests(unittest.TestCase):
    def setUp(self):
        self.p = patch.object(e, "data", CachedAdapter(MockAdapter()))
        self.p.start()
        self.addCleanup(self.p.stop)
        e.reset_all()

    def test_twenty_users_fifty_questions_are_isolated(self):
        def user(index):
            sid = "user"+str(index)
            number = "33" if index % 2 else "43"
            e.get_session(sid).car_id = number
            for i in range(50):
                answer = e.handle(sid, text="내 차 위치" if i % 2 else "요금")
                assert_safe(answer)
                self.assertEqual(answer["vehicle_context"]["car_id"], number)
                self.assertEqual(answer["card"]["data"]["car_id"], number)
        with ThreadPoolExecutor(max_workers=20) as pool:
            for future in [pool.submit(user, i) for i in range(20)]:
                future.result(10)

    def test_ten_payment_confirmations_and_admin_clicks_make_one_each(self):
        e.get_session("p").car_id = "33"
        quote = e.handle("p", text="결제")
        with patch.object(e.data.inner, "pay", wraps=e.data.inner.pay) as pay:
            with ThreadPoolExecutor(max_workers=10) as pool:
                answers = list(pool.map(lambda _: e.handle("p", chip_id="confirm_yes", confirmation_token=quote["confirmation_token"]), range(10)))
            self.assertEqual(pay.call_count, 1)
        for answer in answers: assert_safe(answer)
        with ThreadPoolExecutor(max_workers=10) as pool:
            answers = list(pool.map(lambda _: e.handle("a", chip_id="call_admin"), range(10)))
        self.assertEqual(len(e.admin_calls()), 1)
        for answer in answers: assert_safe(answer)

    def test_twenty_readers_during_alarm_transitions(self):
        begin = threading.Event()
        def mutate():
            begin.wait(2)
            for i in range(40):
                e.data.set_alert(2 if i % 2 else 0, "A", 1)
                time.sleep(.001)
        def user(index):
            begin.wait(2)
            for _ in range(20):
                assert_safe(e.handle("alarm"+str(index), text="경보"))
        with ThreadPoolExecutor(max_workers=21) as pool:
            futures = [pool.submit(mutate)] + [pool.submit(user, i) for i in range(20)]
            begin.set()
            for future in futures: future.result(10)

    @unittest.skipUnless(os.getenv("RUN_SLOW") == "1", "RUN_SLOW=1")
    def test_slow_twenty_thousand_requests_memory_plateau(self):
        samples, latency = [], []
        tracemalloc.start()
        try:
            for block in range(10):
                times = []
                for i in range(2000):
                    before = time.perf_counter()
                    assert_safe(e.handle("soak", text=("A 빈자리", "33번 위치", "요금", "고마워")[i % 4]))
                    times.append((time.perf_counter()-before)*1000)
                latency.append(sorted(times)[int(len(times)*.95)])
                gc.collect()
                samples.append(tracemalloc.get_traced_memory()[0])
            result = {"requests": 20000, "memory_current_bytes": samples, "block_p95_ms": latency}
            Path(".validation/robustness").mkdir(parents=True, exist_ok=True)
            Path(".validation/robustness/soak.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
            self.assertLess(samples[-1]-samples[2], 2_000_000)
            self.assertLess(latency[-1], latency[0]*2)
        finally:
            tracemalloc.stop()
