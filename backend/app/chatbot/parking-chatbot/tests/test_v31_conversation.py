"""v3.1 자연스러운 대화: 계산 도구로 조합한 답이 화면까지 나가는지, 안전 경계는 그대로인지.

실제 모델 대신 조회 결과를 읽고 답을 쓰는 가짜 모델(ScriptedModel)을 쓴다. 실제 API는 부르지 않는다.
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from chatbot import agents, config, engine, llm_limits, monitoring
from chatbot.adapters import CachedAdapter, MockAdapter
from test_agent_runtime_v3 import HAS_LANGCHAIN

if HAS_LANGCHAIN:
    from langchain_core.messages import ToolMessage
    from test_agent_runtime_v3 import AIMessage, ScriptedModel, tool_call


def observations(messages):
    """이번 턴에 모델이 받은 도구 결과(JSON)."""
    rows = []
    for message in messages:
        if isinstance(message, ToolMessage):
            try:
                rows.append(json.loads(message.content))
            except (TypeError, ValueError):
                pass
    return rows


def last(messages, tool):
    return next(r for r in reversed(observations(messages)) if r.get("tool") == tool)


def answer(fn):
    """도구 결과를 읽고 자연스러운 답을 쓰는 가짜 모델 응답."""
    return lambda messages: AIMessage(content=fn(messages))


@unittest.skipUnless(HAS_LANGCHAIN, "optional LangChain not installed")
class NaturalConversationTests(unittest.TestCase):
    def setUp(self):
        self.adapter = CachedAdapter(MockAdapter())
        self.script = []
        self.model = ScriptedModel([])
        self.model._answers = self.script
        for target, attr, value in (
            (engine, "data", self.adapter), (config, "DATA_SOURCE", "mock"),
            (config, "LLM_ENGINE", "langchain"), (config, "LLM_PROVIDER", "openrouter"),
            (config, "LLM_MODEL", "test-only"), (config, "api_key", lambda _: "test-only"),
            (config, "MONITOR_BACKEND", "off"),
            (llm_limits, "limits", llm_limits.RouteLimits()),
            (agents.conversation, "_provider_model", lambda _: self.model),
        ):
            self.enterContext(patch.object(target, attr, value))
        engine.reset_all()
        self.addCleanup(engine.reset_all)

    def ask(self, text, *steps, session="natural"):
        self.script[:] = list(steps)
        return engine.handle(session, text=text)

    def assert_model_answer(self, result):
        self.assertEqual(result["meta"]["source"], "llm", result["text"])
        self.assertEqual(result["meta"]["agent_status"], "ok", result["text"])

    # ------------------------------------------------------------ 조합한 답이 나간다
    def test_comparison_uses_code_computed_differences(self):
        def write(messages):
            s = last(messages, "compare_lots")["data"]["summary"]
            return (f"{s['notes'][0]}이에요. 기본요금은 {s['cheapest_base']}이 {s['base_fee_diff']:,}원 더 싸요. "
                    f"자리 여유를 보면 {s['most_spaces']}, 요금을 보면 {s['cheapest_base']}이에요.")
        result = self.ask("공항이랑 신세계 중에 충전 자리 넉넉하고 싼 데 어디야?",
                          tool_call("compare_lots", {"lot_ids": ["C", "D"], "zone": "ev"}), answer(write))
        self.assert_model_answer(result)
        self.assertIn("2칸 많음", result["text"])
        self.assertIn("1,000원 더 싸요", result["text"])
        self.assertNotIn("혼잡 예측은 아직", result["text"])

    def test_elapsed_time_and_entry_clock_are_allowed(self):
        def write(messages):
            loc = last(messages, "vehicle_info")["data"]["location"]
            fee = last(messages, "vehicle_info")["data"]["fee"]
            return (f"23번 차는 {loc['lot_name']} {loc['space_id']}에 있고, {loc['entry_time_text']}에 들어와서 "
                    f"{loc['elapsed_text']}째예요. 지금까지 {fee['fee']:,}원이에요.")
        result = self.ask("23번 차 얼마나 있었고 지금 얼마야?",
                          tool_call("vehicle_info", {"car_id": "23"}), answer(write))
        self.assert_model_answer(result)
        self.assertRegex(result["text"], r"오[전후] \d+시")

    def test_future_fee_estimate(self):
        def write(messages):
            d = last(messages, "estimate_fee")["data"]
            return (f"지금 기준으로 {d['after_time_text']} 뒤 출차하면 약 {d['estimated_fee']:,}원이에요. "
                    f"지금보다 {d['increase']:,}원 늘어나고, 실제 금액은 출차할 때 다시 확인돼요.")
        result = self.ask("23번 차 30분 뒤에 나가면 얼마 나와?",
                          tool_call("estimate_fee", {"minutes": 30, "car_id": "23"}), answer(write))
        self.assert_model_answer(result)
        self.assertIn("2,000원", result["text"])
        self.assertIsNone(result["confirmation_token"])

    def test_future_vacancy_has_no_fixed_prefix(self):
        def write(messages):
            row = last(messages, "parking_status")["data"]["rows"][0]
            return (f"저녁 상황을 미리 알 수는 없지만, 지금 {row['name']} 자리는 {row['empty']}자리 남아 있어요. "
                    "출발 전에 한 번 더 물어봐 주세요.")
        result = self.ask("이따 저녁에 공항 자리 있을까?",
                          tool_call("parking_status", {"lot_ids": ["C"]}), answer(write))
        self.assert_model_answer(result)
        self.assertTrue(result["text"].startswith("저녁 상황을"))

    def test_unregistered_facility_answer_with_alternative(self):
        def write(messages):
            d = last(messages, "search_knowledge")["data"]
            return f"{d['unregistered'][0]} 위치는 아직 안내 자료에 없어요. 현장에서 필요하시면 관리자 호출 버튼으로 문의할 수 있어요."
        result = self.ask("공항에 엘리베이터 있어?",
                          tool_call("search_knowledge", {"query": "엘리베이터", "lot_ids": ["C"]}), answer(write))
        self.assert_model_answer(result)
        self.assertIn("엘리베이터", result["text"])

    def test_small_talk_without_tools(self):
        result = self.ask("고마워!", AIMessage(content="천만에요! 또 궁금한 게 있으면 편하게 물어보세요."))
        self.assert_model_answer(result)
        self.assertTrue(result["meta"].get("smalltalk"))

    def test_small_talk_with_operational_claim_falls_back(self):
        result = self.ask("고마워!", AIMessage(content="천만에요! 지금 공항은 자리가 넉넉해요."))
        self.assertNotIn("넉넉", result["text"])
        self.assertEqual(result["meta"]["source"], "rule")

    # ------------------------------------------------------------ 고쳐 쓰기
    def test_wrong_difference_is_repaired_once(self):
        def correct(messages):
            s = last(messages, "compare_lots")["data"]["summary"]
            return f"{s['cheapest_base']}이 기본요금이 {s['base_fee_diff']:,}원 더 싸요."
        result = self.ask("공항이랑 신세계 중 어디가 싸?",
                          tool_call("compare_lots", {"lot_ids": ["C", "D"]}),
                          AIMessage(content="김해공항 주차장이 기본요금이 3,000원 더 싸요."),
                          answer(correct))
        self.assert_model_answer(result)
        self.assertTrue(result["meta"].get("repaired"))
        self.assertIn("1,000원 더 싸요", result["text"])
        self.assertNotIn("3,000원 더", result["text"])

    def test_false_action_claim_never_reaches_screen(self):
        result = self.ask("23번 차 요금 얼마야?",
                          tool_call("vehicle_info", {"car_id": "23"}),
                          AIMessage(content="23번 차 요금은 1,000원이에요. 결제해 드렸어요."),
                          AIMessage(content="관리자를 불렀어요. 곧 갈 거예요."))
        self.assertNotIn("드렸", result["text"])
        self.assertNotIn("불렀", result["text"])
        self.assertIsNone(result["confirmation_token"])
        self.assertFalse(self.adapter.inner.payments)
        self.assertEqual(engine.admin_calls(), [])

    def test_repeated_answer_is_rewritten(self):
        same = "지금 김해공항 주차장 일반 자리가 넉넉하게 남아 있어요. 편하게 오세요."
        first = self.ask("공항 자리 있어?", tool_call("parking_status", {"lot_ids": ["C"]}), AIMessage(content=same),
                         session="repeat")
        self.assert_model_answer(first)
        second = self.ask("공항 자리 있어?", tool_call("parking_status", {"lot_ids": ["C"]}), AIMessage(content=same),
                          AIMessage(content="네, 김해공항 주차장은 지금도 여유 있어요."), session="repeat")
        self.assert_model_answer(second)
        self.assertNotEqual(first["text"], second["text"])

    def test_missing_goal_is_requested_before_fallback(self):
        self.enterContext(patch.object(config, "LLM_PREFETCH", False))   # 모델이 직접 조회하는 경로 검사
        # 위치·요금을 같이 물었는데 모델이 위치만 조회하면, 빠진 항목을 알려 주고 다시 조회하게 한다.
        def write(messages):
            d = last(messages, "vehicle_info")["data"]
            return f"23번 차는 {d['location']['space_id']}에 있고 지금까지 {d['fee']['fee']:,}원이에요."
        result = self.ask("23번 차 위치랑 요금 알려줘",
                          tool_call("vehicle_info", {"car_id": "23", "include_fee": False}),
                          AIMessage(content="23번 차는 A-03에 있어요."),
                          tool_call("vehicle_info", {"car_id": "23"}), answer(write))
        self.assert_model_answer(result)
        self.assertIn("1,000원", result["text"])

    # ------------------------------------------------------------ 예산·라우팅
    def test_five_questions_per_minute_keep_model_answers(self):
        sources = []
        for i in range(5):
            result = self.ask(f"공항 자리 있어{'?' * (i + 1)}", tool_call("parking_status", {"lot_ids": ["C"]}),
                              answer(lambda m, i=i: f"김해공항 주차장은 지금 {last(m, 'parking_status')['data']['rows'][0]['empty']}자리 남았어요" + "!" * (i + 1)),
                              session="budget")
            sources.append(result["meta"]["source"])
        self.assertEqual(sources, ["llm"] * 5)

    def test_alert_question_combines_with_my_car(self):
        self.adapter.inner.set_alert(1, "A", 6)
        engine.handle("alert", text="23번 차 어디 있어?")
        def write(messages):
            d = last(messages, "alert_summary")["data"]
            return f"지금은 {d['level_text']} 단계예요. {d['place']}이고 관리자가 확인하고 있어요."
        result = self.ask("경보 있어? 내 차 괜찮아?", tool_call("alert_summary", {}), answer(write), session="alert")
        self.assert_model_answer(result)
        self.assertIn("경보 아님", result["text"])

    def test_lv2_still_skips_model(self):
        self.adapter.inner.set_alert(2, "A", 6)
        result = self.ask("경보 있어?", AIMessage(content="괜찮아요."), session="lv2")
        self.assertNotEqual(result["meta"].get("source"), "llm")
        self.assertEqual(len(self.model._received), 0)

    def test_model_payment_proposal_only_offers_chip(self):
        def write(messages):
            fee = last(messages, "vehicle_info")["data"]["fee"]
            return f"23번 차는 지금까지 {fee['fee']:,}원이에요. 미리 결제하시려면 아래 버튼을 눌러 주세요."
        with patch.object(self.adapter, "pay", wraps=self.adapter.pay) as pay:
            result = self.ask("23번 차 요금 얼마야? 미리 낼래",
                              tool_call("vehicle_info", {"car_id": "23"}),
                              tool_call("propose_action", {"intent": "pay_fee", "params": {"car_id": "23"}}),
                              answer(write))
            pay.assert_not_called()
        self.assertIn("pay", [chip["id"] for chip in result["chips"]])
        self.assertIsNone(result["confirmation_token"])

    # ------------------------------------------------------------ 모니터링
    def test_local_monitoring_records_why(self):
        self.enterContext(patch.object(config, "LLM_PREFETCH", False))   # 모델이 직접 조회하는 경로 검사
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "turns.jsonl"
            with patch.object(config, "MONITOR_BACKEND", "local"), patch.object(config, "MONITOR_LOCAL_PATH", str(path)):
                monitoring.reset_for_tests()
                self.ask("공항이랑 신세계 중 어디가 싸?", tool_call("compare_lots", {"lot_ids": ["C", "D"]}),
                         AIMessage(content="김해공항 주차장이 3,000원 더 싸요."), AIMessage(content="김해공항 주차장이 4,500원 더 싸요."))
            row = json.loads(path.read_text(encoding="utf-8").splitlines()[-1])
        self.assertEqual(row["tools"], ["compare_lots"])
        self.assertEqual(row["agent"]["repair_reason"], "mismatched_lot_value")
        self.assertEqual(row["output"]["source"], "rule")
        self.assertNotIn("test-only", json.dumps(row))


if __name__ == "__main__":
    unittest.main()
