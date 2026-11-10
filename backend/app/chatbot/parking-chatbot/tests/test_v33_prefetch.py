"""v3.3: 확실한 조회는 코드가 먼저 실행해 대화 AI에 넘긴다(미리 조회, LLM_PREFETCH).

목적: 모델 호출을 2번(도구 선택 → 답)에서 1번으로 줄여 답을 빠르게 하고, 시간 초과로 규칙 답이 나가는 일을 줄인다.
AI는 넘겨받은 자료와 앞 대화를 엮어 답을 쓰고, 부족하면 조회를 더 할 수 있다(도구만 고르는 역할이 아니다).
무엇을 미리 조회할지는 규칙의 '의도'가 아니라 문장에서 바로 보이는 단서(주차장·차량·구역·기간)로만 정한다.
"""
import importlib.util
import json
import unittest
from unittest.mock import patch

from chatbot import agents, config, conversation_turn as ct, dialog_context, engine as e, plan
from chatbot.state import Session
from chatbot.tools.read import ReadTools

HAS_LANGCHAIN = importlib.util.find_spec("langchain") is not None


def prefetched(text, session=None, target=None, notice=None):
    session = session or Session()
    toolbox = ReadTools(e.data, e.current_alert, allowed_car_id=target, extra_car_ids=ct.explicit_cars(text)[1:], notice=notice)
    token = plan.use(plan.make(text))
    try:
        return [(row["tool"], row["args"]) for row in ct._prefetch(text, session, toolbox, target, notice)]
    finally:
        plan.release(token)


class PrefetchChoiceTests(unittest.TestCase):
    def setUp(self):
        e.reset_all()
        self.addCleanup(e.reset_all)

    def test_choices_follow_visible_clues(self):
        cases = {
            "지금 빈자리 있어?": [("compare_lots", {})],
            "공항 자리 있어?": [("parking_status", {"lot_ids": ["C"]})],
            "역 주차장 자리 있어?": [("parking_status", {"lot_ids": ["B"]})],
            "부산역이랑 공항 중 어디가 더 싸?": [("compare_lots", {"lot_ids": ["B", "C"]})],
            "김해공항 3일 주차할건데 예상 요금": [("estimate_fee", {"minutes": 4320, "lot_id": "C"})],
            "김해공항 요금 기준 알려줘": [("fee_policies", {"lot_ids": ["C"]})],
            "불 꺼져 있던데 정상이야?": [("alert_summary", {})],
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(prefetched(text), expected)

    def test_nothing_for_smalltalk_out_of_scope_or_facility(self):
        for text in ("고마워", "오늘 날씨 어때?"):
            with self.subTest(text=text):
                self.assertEqual(prefetched(text), [])
        # 시설 질문은 빈자리 대신 안내 자료를 미리 찾는다
        self.assertEqual([name for name, _ in prefetched("주차장 몇 시까지 해?")], ["search_knowledge"])
        self.assertEqual([name for name, _ in prefetched("실외 주차장은 어디야?")], ["search_knowledge", "compare_lots"])

    def test_cars_only_those_the_user_typed(self):
        self.assertEqual(prefetched("23번 차 어디 있어?", target="23"), [("vehicle_info", {"car_id": "23", "include_fee": True})])
        two = prefetched("23번이랑 41번 요금 비교해줘", target="23")
        self.assertEqual([args["car_id"] for _, args in two], ["23", "41"])
        s = Session()
        s.car_id = "23"
        self.assertEqual(prefetched("얼마 나왔어?", s, target="23"), [("vehicle_info", {"car_id": "23", "include_fee": True})])
        # 차를 말한 적 없으면 차량 조회를 하지 않는다
        self.assertEqual(prefetched("얼마 나왔어?"), [])

    def test_context_vehicle_type_and_focus_lot(self):
        s = Session()
        s.vehicle_type = "ev"
        self.assertEqual(prefetched("어디 대면 돼?", s), [("compare_lots", {"zone": "ev"})])
        s = Session()
        dialog_context.remember(s, "C")
        self.assertEqual(prefetched("거기 충전 칸은?", s), [("parking_status", {"lot_ids": ["C"], "zone": "ev"})])
        self.assertEqual(prefetched("요금은?", s), [("fee_policies", {"lot_ids": ["C"]})])
        self.assertEqual(prefetched("3시간이면?", s), [("estimate_fee", {"minutes": 180, "lot_id": "C"})])
        self.assertEqual(prefetched("거기 예약 돼?", s, notice={"topic": "reservation", "text": "예약 기능은 없어요."}),
                         [("service_notice", {}), ("parking_status", {"lot_ids": ["C"]})])

    def test_off_switch(self):
        with patch.object(config, "LLM_PREFETCH", False):
            self.assertEqual(prefetched("공항 자리 있어?"), [])


@unittest.skipUnless(HAS_LANGCHAIN, "optional LangChain not installed")
class PrefetchTurnTests(unittest.TestCase):
    def setUp(self):
        e.reset_all()
        self.addCleanup(e.reset_all)

    def ask(self, text, *answers, sid="pre"):
        from test_agent_runtime_v3 import ScriptedModel
        from langchain_core.messages import AIMessage
        self.model = ScriptedModel([a if not isinstance(a, str) else AIMessage(content=a) for a in answers])
        run = agents.run_conversation
        with patch.object(config, "LLM_PROVIDER", "openrouter"), patch.object(config, "LLM_ENGINE", "langchain"), \
                patch.object(config, "api_key", return_value="test-only"), \
                patch.object(agents, "run_conversation", side_effect=lambda *args: run(*args, model=self.model)):
            return e.handle(sid, text=text)

    def test_one_model_call_answers_from_prefetched_data(self):
        from langchain_core.messages import ToolMessage

        def answer(messages):
            from langchain_core.messages import AIMessage
            data = [json.loads(m.content) for m in messages if isinstance(m, ToolMessage)]
            self.assertEqual([d["tool"] for d in data], ["parking_status"])
            row = data[0]["data"]["rows"][0]
            return AIMessage(content=f"{row['name']}에는 지금 {row['empty']}자리가 비어 있어요. 여유가 있으니 편하게 오세요.")
        r = self.ask("공항 자리 있어?", answer)
        self.assertEqual((r["meta"]["source"], r["meta"]["model_calls"]), ("llm", 1))
        self.assertIn("김해공항 주차장에는 지금", r["text"])

    def test_model_can_still_read_more(self):
        from test_agent_runtime_v3 import tool_call
        from langchain_core.messages import AIMessage, ToolMessage

        def answer(messages):
            tools = [json.loads(m.content)["tool"] for m in messages if isinstance(m, ToolMessage)]
            self.assertEqual(tools, ["parking_status", "fee_policies"])
            return AIMessage(content="김해공항 주차장은 처음 30분 3,000원이에요.")
        r = self.ask("공항 자리 있어?", tool_call("fee_policies", {"lot_ids": ["C"]}), answer, sid="more")
        self.assertEqual((r["meta"]["source"], r["meta"]["model_calls"]), ("llm", 2))

    def test_evidence_check_uses_prefetched_data(self):
        # 미리 조회한 값과 다른 숫자를 말하면 여전히 막힌다(규칙 답으로)
        r = self.ask("공항 자리 있어?", "김해공항 주차장에는 지금 999자리가 비어 있어요.", "김해공항 주차장에는 지금 999자리가 비어 있어요.", sid="bad")
        self.assertNotIn("999", r["text"])
        self.assertNotEqual(r["meta"].get("source"), "llm")

    def test_smalltalk_has_no_prefetch_and_still_answers(self):
        r = self.ask("고마워", "천만에요. 필요한 게 있으면 또 말씀해 주세요.", sid="thanks")
        self.assertEqual(r["meta"]["source"], "llm")


class LeavingPrefetchTests(unittest.TestCase):
    def test_leaving_with_known_car_prefetches_that_car(self):
        e.reset_all()
        s = Session()
        s.car_id = "23"
        self.assertEqual(prefetched("이제 나갈게", s, target="23"), [("vehicle_info", {"car_id": "23", "include_fee": True})])


class EvictionOrderTests(unittest.TestCase):
    """#89 보완: 세션 한도에서 정리할 대화는 진행 중인 확인(결제 등)이 없는 것부터."""

    def test_pending_confirmation_is_evicted_last(self):
        e.reset_all()
        self.addCleanup(e.reset_all)
        with patch.object(config, "MAX_SESSIONS", 2), patch.object(config, "LLM_PROVIDER", "none"):
            self.assertEqual(e.handle("paying", text="44번 차 결제해줘")["flow"], "confirm_vehicle")   # 가장 오래됐지만 확인 진행 중
            e.handle("idle", text="빈자리 있어?")
            e.handle("new", text="빈자리 있어?")
            self.assertEqual(set(e.SESSIONS), {"paying", "new"})
            self.assertEqual(e.handle("paying", text="네")["flow"], "confirm_action")   # 결제 확인이 이어진다


if __name__ == "__main__":
    unittest.main()
