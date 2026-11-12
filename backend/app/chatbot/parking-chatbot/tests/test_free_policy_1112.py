"""챗봇이 무료 회차 정책을 안내한다 (11/12 리뷰 봇 99문항, PM 결정).

서버는 #140부터 요금 정책(/rate-policies)에 free_minutes 를 준다. 챗봇이 정해진 다섯 키만 남기고 버려서
- "무료 시간 있어?" → 기본요금만 답하고 5분 무료를 말하지 않았다
- "5분 안에 나가면 공짜야?" → "잘 이해하지 못했어요"
챗봇은 요금을 계산하지 않는다. 서버가 준 무료 분을 옮겨 적기만 한다(코드에 5를 적지 않는다).
"""
import importlib.util
import unittest
from unittest.mock import patch

from chatbot import agents, config, engine as e, parser, response_locale as locale, templates as T
from chatbot.adapters import ApiAdapter
from chatbot.agents.evidence import check_answer
from chatbot.tools.read import ReadTools, policy_text

BASE = {"base_minutes": 30, "base_fee": 1000, "unit_minutes": 10, "extra_fee": 500, "daily_cap": 10000}


def policies(free=5, **override):
    fees = {"A": 1000, "B": 2000, "C": 3000, "D": 4000}
    rows = {lot: {**BASE, "base_fee": fee, **({"free_minutes": free} if free is not None else {})} for lot, fee in fees.items()}
    for lot, minutes in override.items():
        rows[lot]["free_minutes"] = minutes
    return {"policies": rows}


class ServerPolicyTests(unittest.TestCase):
    def read(self, items):
        api = ApiAdapter("http://127.0.0.1:9")
        with patch.object(api, "_request", return_value=(200, {"items": items, "next_cursor": None}, {})):
            return api.get_rate_policies()["policies"]

    def test_free_minutes_is_kept_as_an_optional_value(self):
        got = self.read([{"lot_id": "A", **BASE, "free_minutes": 5}, {"lot_id": "B", **BASE, "free_minutes": 0},
                         {"lot_id": "C", **BASE}, {"lot_id": "D", **BASE, "free_minutes": "5"}])
        self.assertEqual(got["A"]["free_minutes"], 5)
        self.assertEqual(got["B"]["free_minutes"], 0)             # 0 = 무료 회차 끔
        self.assertNotIn("free_minutes", got["C"])
        self.assertNotIn("free_minutes", got["D"])                # 숫자가 아니면 버린다
        self.assertNotIn("free_minutes", self.read([{"lot_id": "A", **BASE, "free_minutes": -1}])["A"])

    def test_the_five_required_values_are_checked_as_before(self):
        self.assertEqual(self.read([{"lot_id": "A", **{**BASE, "base_fee": 0}, "free_minutes": 5}]), {})
        from chatbot.catalog import RATE_KEYS
        self.assertEqual(set(RATE_KEYS), set(BASE))               # 필수 키는 다섯 개 그대로(목업 검증·견적 비교가 이 기준)

    def test_mock_settings_still_load(self):
        for lot in config.catalog.ids():
            self.assertEqual(set(config.catalog.rate_policy(lot)), set(BASE))


class RuleAnswerTests(unittest.TestCase):
    def setUp(self):
        e.reset_all()
        locale.load_seed()
        self.addCleanup(e.reset_all)

    def ask(self, text, free=5, session="f", **override):
        with patch.object(e.data, "get_rate_policies", return_value=policies(free, **override)):
            return e.handle(session + text, text=text)

    def test_free_period_questions_are_answered_with_the_server_minutes(self):
        for text in ("무료 시간 있어?", "5분 안에 나가면 공짜야?", "무료 회차 몇 분이야?", "몇 분까지 무료야?", "무료 주차 돼?"):
            r = self.ask(text)
            self.assertEqual(r["text"], "네, 입차 후 5분 이내에 출차하면 무료(0원)예요. 5분이 지나면 기본요금부터 계산돼요.", text)
            self.assertIn("rule:fee_rule", r["meta"]["route"], text)
        self.assertIn("입차 후 7분 이내", self.ask("무료 시간 있어?", free=7)["text"])        # 코드에 5를 적지 않았다

    def test_no_free_period(self):
        expected = "지금은 무료 회차 시간이 없어요. 입차하면 기본요금부터 계산돼요."
        self.assertEqual(self.ask("무료 시간 있어?", free=0)["text"], expected)
        self.assertEqual(self.ask("무료 시간 있어?", free=None)["text"], expected)          # 서버가 주지 않을 때
        self.assertEqual(e.handle("mock", text="무료 시간 있어?")["text"], expected)        # 목업 설정에는 무료 분이 없다

    def test_different_minutes_per_lot(self):
        text = self.ask("무료 시간 있어?", B=0, C=10)["text"]
        self.assertEqual(text.split("\n")[0], "주차장마다 무료 시간이 달라요.")
        self.assertIn("· 개발원주차장: 입차 후 5분 이내 무료", text)
        self.assertIn("· 김해공항 주차장: 입차 후 10분 이내 무료", text)
        self.assertNotIn("부산역주차장", text)

    def test_fee_answers_mention_the_free_period(self):
        table = self.ask("주차요금 안내해줘")["text"].split("\n")
        self.assertEqual(table[0], "주차장별 요금표예요.")
        self.assertEqual(table[-2], "입차 후 5분 이내에 출차하면 무료예요.")
        self.assertEqual(len(table), 3 + len(config.catalog.ids()))
        self.assertIn("입차 후 5분 이내에 출차하면 무료예요.", self.ask("부산역 요금 얼마야?")["text"])
        self.assertNotIn("무료", self.ask("주차요금 안내해줘", free=0)["text"])
        self.assertNotIn("무료", e.handle("mock2", text="주차요금 안내해줘")["text"])        # 목업은 전과 같다

    def test_requests_to_waive_the_fee_are_still_refused(self):
        for text in ("무료로 해줘", "요금 깎아줘", "공짜로 해주면 안 돼?"):
            r = self.ask(text)
            self.assertIn("해 드릴 수 없어요", r["text"], text)
            self.assertNotIn("이내에 출차하면 무료", r["text"], text)

    def test_other_questions_keep_their_meaning(self):
        cases = (("23번 요금 얼마야?", "get_fee"), ("요금 얼마야?", "get_fee"), ("주차요금 안내해줘", "fee_rule"),
                 ("전기차 충전 무료야?", None), ("Are there free spaces?", None), ("내 차 무료야?", None))
        for text, intent in cases:
            r = parser.parse_rules(text)
            self.assertFalse(r.params.get("free_info"), text)
            if intent:
                self.assertEqual(r.intent, intent, text)

    def test_foreign_originals_are_recognised_too(self):
        # 질문 번역이 실패해 원문이 그대로 들어온 경우
        for text in ("Is there a free period?", "無料時間はありますか？", "有免费时间吗？", "free parking?"):
            self.assertTrue(parser.parse_rules(text).params.get("free_info"), text)

    def test_model_is_not_called_for_the_free_period_answer(self):
        with patch.object(config, "LLM_PROVIDER", "openrouter"), patch.object(config, "LLM_ENGINE", "langchain"), \
                patch.object(config, "api_key", return_value="test-only"), \
                patch.object(agents, "run_conversation", side_effect=AssertionError("모델을 부르면 안 된다")):
            r = self.ask("무료 시간 있어?")
        self.assertIn("agent:skip", r["meta"]["route"])

    def test_answers_are_translated_without_the_model(self):
        texts = [self.ask(q, session=f"t{i}")["text"] for i, q in enumerate(("무료 시간 있어?", "주차요금 안내해줘", "부산역 요금 얼마야?"))]
        texts += [T.free_rule(policies(0)["policies"]), T.free_rule(policies(5, B=0)["policies"])]
        with patch.object(locale, "_translate_call", side_effect=AssertionError("모델을 부르면 안 된다")):
            for text in texts:
                for language in locale.LANGUAGES:
                    self.assertIsNotNone(locale._translate_known((text,), language), (language, text))
            english = locale._translate_known((texts[0],), "en")[0]
        self.assertEqual(english, "Yes, leaving within 5 minutes of entry is free (KRW 0). After 5 minutes, the base fee applies.")

    def test_http_answer_in_english(self):
        if importlib.util.find_spec("fastapi") is None:
            self.skipTest("FastAPI not installed")
        from fastapi.testclient import TestClient
        from server_fastapi import app
        with patch.object(e.data, "get_rate_policies", return_value=policies(5)):
            r = TestClient(app).post("/api/v1/chat", json={"session_id": "free-en", "text": "Is there a free period?", "language": "en"}).json()
        self.assertIn("leaving within 5 minutes of entry is free", r["text"])


class ModelSideTests(unittest.TestCase):
    def setUp(self):
        e.reset_all()
        self.addCleanup(e.reset_all)

    def test_policy_text_and_tool_row_carry_the_free_minutes(self):
        self.assertIn("(입차 후 5분 이내 출차는 무료, 0원)", policy_text({**BASE, "free_minutes": 5}))
        self.assertNotIn("무료", policy_text({**BASE, "free_minutes": 0}))
        self.assertNotIn("무료", policy_text(BASE))
        with patch.object(e.data, "get_rate_policies", return_value=policies(5)):
            result = ReadTools(e.data, e.current_alert).fee_policies(["B"])
        row = result["data"]["rows"][0]
        self.assertEqual(row["free_minutes"], 5)
        self.assertIn("입차 후 5분 이내 출차는 무료", row["policy_text"])
        seen = [{"tool": "fee_policies", "call_id": "1", "result": result}]
        good = "부산역주차장은 처음 30분에 2,000원이에요. 입차 후 5분 이내에 출차하면 무료(0원)예요."
        self.assertIsNone(check_answer(good, "부산역 무료 시간 있어?", seen))
        self.assertIsNotNone(check_answer("부산역주차장은 입차 후 15분 이내에 출차하면 무료예요.", "부산역 무료 시간 있어?", seen))


if __name__ == "__main__":
    unittest.main()
