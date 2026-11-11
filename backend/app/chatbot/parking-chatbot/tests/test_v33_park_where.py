"""실모델 확인(11/10 저녁): '아버지가 장애인 차량이야. 어디 세우는 게 나아?'를 규칙이 '내 차 찾기'로 읽어
대화 AI를 건너뛰고 '신세계 장애인 구역 8자리'만 답했다. 주차할 곳을 묻는 문장은 대화 AI로 보낸다.
이미 세운 차를 찾는 문장('내 차 어디 있어?')은 지금처럼 규칙이 차량 번호를 되묻는다.
"""
import importlib.util
import unittest
from unittest.mock import patch

from chatbot import agents, config, engine as e
from chatbot.conversation_turn import _FIND_CAR, _PARK_WHERE

HAS_LANGCHAIN = importlib.util.find_spec("langchain") is not None
PARK_WHERE = ("부모님 모시고 백화점 갔다가 저녁에 기차 타야 하는데 아버지가 장애인 차량이야. 어디 세우는 게 나아?",
              "장애인 차량은 어디 주차해?", "큰 차량인데 어디가 편해?", "차량이 큰데 세울 데 있어?")
FIND_CAR = ("내 차 어디 있어?", "제 차 위치 알려줘", "차량 어디 있는지 찾아줘", "내 차 어디 세워뒀더라?",
            "차 어디 주차했는지 모르겠어")


class PatternTests(unittest.TestCase):
    def test_park_where_and_find_car_are_told_apart(self):
        for text in PARK_WHERE:
            with self.subTest(text=text):
                self.assertTrue(_PARK_WHERE.search(text) and not _FIND_CAR.search(text))
        for text in FIND_CAR:
            with self.subTest(text=text):
                self.assertFalse(_PARK_WHERE.search(text) and not _FIND_CAR.search(text))


@unittest.skipUnless(HAS_LANGCHAIN, "optional LangChain not installed")
class RouteTests(unittest.TestCase):
    def setUp(self):
        e.reset_all()
        self.addCleanup(e.reset_all)

    def ask(self, text, sid):
        from langchain_core.messages import AIMessage
        from test_agent_runtime_v3 import ScriptedModel
        self.calls = 0

        def answer(messages):
            self.calls += 1
            return AIMessage(content="확인했어요.")
        run = agents.run_conversation
        with patch.object(config, "LLM_PROVIDER", "openrouter"), patch.object(config, "LLM_ENGINE", "langchain"), \
                patch.object(config, "api_key", return_value="test-only"), \
                patch.object(agents, "run_conversation", side_effect=lambda *a: run(*a, model=ScriptedModel([answer] * 4))):
            return e.handle(sid, text=text)

    def test_park_where_reaches_the_ai(self):
        for i, text in enumerate(PARK_WHERE):
            with self.subTest(text=text):
                r = self.ask(text, f"pw{i}")
                self.assertGreater(self.calls, 0, r["meta"].get("route"))
                if i < 3:   # 고치기 전에는 '내 차 찾기'로 읽혀 AI를 건너뛰던 문장들
                    self.assertIn("plan:park_where", r["meta"].get("route", ""))

    def test_find_car_without_number_still_asks_number(self):
        for i, text in enumerate(FIND_CAR[:3]):
            with self.subTest(text=text):
                r = self.ask(text, f"fc{i}")
                self.assertEqual(self.calls, 0, r["meta"].get("route"))
                self.assertIn("차량 번호", r["text"])


class PurposeLotTests(unittest.TestCase):
    """이름으로 말한 곳(백화점→신세계)이 있어도 목적으로 말한 곳(기차→부산역)을 함께 조회한다.
    고치기 전: 신세계만 미리 조회 → Haiku는 부산역을 빼고 답했고, gemma는 모델을 5번 부르다 규칙 답."""

    def setUp(self):
        e.reset_all()
        self.addCleanup(e.reset_all)

    def test_named_and_purpose_lots_are_compared(self):
        from test_v33_prefetch import prefetched
        self.assertEqual(prefetched(PARK_WHERE[0]), [("compare_lots", {"lot_ids": ["D", "B"], "zone": "accessible"})])

    def test_single_lot_questions_are_unchanged(self):
        from test_v33_prefetch import prefetched
        self.assertEqual(prefetched("공항 자리 있어?"), [("parking_status", {"lot_ids": ["C"]})])
        self.assertEqual(prefetched("신세계 쇼핑하러 가는데 자리 있어?"), [("parking_status", {"lot_ids": ["D"]})])


if __name__ == "__main__":
    unittest.main()
