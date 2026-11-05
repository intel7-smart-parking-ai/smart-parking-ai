"""D: deterministic extreme inputs, negative utterances and optional fuzzing."""
import os
import json
from pathlib import Path
import random
import time
import unittest
from unittest.mock import patch
from chatbot import engine as e, parser
from chatbot.adapters import CachedAdapter, MockAdapter
from safety_check import assert_safe


NEGATIVES = [f"{target}{ending}" for target in ("관리자", "직원", "담당자", "상담원", "관리자님")
             for ending in (" 안 불러도 돼요", " 부르지 마세요", " 호출 안 해도 돼요", " 오지 말아 주세요", "는 나중에 불러 주세요")]
NEGATIVES += [f"{target}{ending}" for target in ("결제", "정산", "계산", "지불", "납부")
              for ending in (" 안 해요", " 하지 마세요", "는 나중에 할게요", " 안 할래요", "는 그만할게요")]


class RobustInputTests(unittest.TestCase):
    def setUp(self):
        self.p = patch.object(e, "data", CachedAdapter(MockAdapter()))
        self.p.start()
        self.addCleanup(self.p.stop)
        e.reset_all()

    def test_extreme_inputs_are_bounded(self):
        for text in ("", " \n ", "😀\x00\u202e", "<script>alert(1)</script>", "'; DROP TABLE", "가"*10000):
            began = time.monotonic()
            try:
                answer = e.handle("input", text=text)
            except ValueError as exc:
                self.assertGreater(len(text), 200)
                self.assertIn("200", str(exc))
            else:
                assert_safe(answer)
            self.assertLess(time.monotonic()-began, 1)

    def test_numbers_are_not_negative_or_long_suffixes(self):
        # v2.9 규칙(D-06): 두 자리 10~99만. 한 자리·0 시작은 "두 자리로", 그 밖은 범위 안내.
        cases = {"-1번 차 어디": "10부터 99", "001번 차 어디": "10부터 99", "99999번 차 어디": "10부터 99",
                 "100번 차 어디": "10부터 99", "0번 차 어디": "두 자리로", "5번 차 어디": "두 자리로", "09번 차 어디": "두 자리로"}
        for text, expected in cases.items():
            with self.subTest(text=text):
                e.reset_all()
                e.get_session("input").car_id = "33"
                answer = e.handle("input", text=text)
                self.assertIsNone(answer["card"])
                self.assertIsNone(e.get_session("input").pending)
                self.assertIn(expected, answer["text"])
        for text in ("11번", "１１번", "십일번"):
            self.assertEqual(parser.parse_rules(text).params.get("car_id"), "11")
        for text in ("1번", "１번", "일번"):
            self.assertEqual(parser.parse_rules(text).params.get("car_id"), "short")

    def test_two_numbers_request_one_target(self):
        answer = e.handle("input", text="11번이랑 12번 차 어디")
        self.assertIn("대상 차량 하나", answer["text"])
        self.assertIsNone(answer["card"])

    def test_fifty_negative_utterances_never_create_action(self):
        self.assertEqual(len(NEGATIVES), 50)
        for text in NEGATIVES:
            with self.subTest(text=text):
                e.reset_all()
                e.get_session("input").car_id = "33"
                with patch.object(e.data.inner, "pay", wraps=e.data.inner.pay) as pay:
                    answer = e.handle("input", text=text)
                    assert_safe(answer, {"negative": True, "new_payments": pay.call_count, "new_calls": len(e.admin_calls())})
                    self.assertIsNone(e.get_session("input").pending)
                    self.assertEqual(len(e.admin_calls()), 0)

    @unittest.skipUnless(os.getenv("RUN_SLOW") == "1", "RUN_SLOW=1")
    def test_slow_five_thousand_fuzz_inputs(self):
        rng = random.Random(1103)
        alphabet = "가나다ㄱㄴㄷ0123456789 -+!?\x00\u202e😀"
        maximum = 0
        for _ in range(5000):
            text = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 200)))
            began = time.perf_counter()
            assert_safe(e.handle("fuzz", text=text))
            maximum = max(maximum, time.perf_counter()-began)
        Path(".validation/robustness").mkdir(parents=True, exist_ok=True)
        Path(".validation/robustness/fuzz.json").write_text(json.dumps({"seed": 1103, "inputs": 5000, "max_ms": maximum*1000}), encoding="utf-8")
        self.assertLess(maximum, 1)
