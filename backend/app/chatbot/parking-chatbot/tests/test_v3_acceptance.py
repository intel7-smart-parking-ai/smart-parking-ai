"""Service-level acceptance on the real public turn boundary, internal mock data only."""
import unittest
from unittest.mock import patch

from chatbot import agents, config, engine
from chatbot.adapters import CachedAdapter, DataUnavailable, MockAdapter
from test_agent_runtime_v3 import HAS_LANGCHAIN

if HAS_LANGCHAIN:
    from test_agent_runtime_v3 import AIMessage, ScriptedModel, tool_call


class ConversationAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.adapter = CachedAdapter(MockAdapter())
        for target, name, value in ((engine, "data", self.adapter), (config, "DATA_SOURCE", "mock"),
                                    (config, "LLM_PROVIDER", "none")):
            change = patch.object(target, name, value)
            change.start()
            self.addCleanup(change.stop)
        engine.reset_all()

    def ask(self, text, session="reader"):
        return engine.handle(session, text=text)

    @unittest.skipUnless(HAS_LANGCHAIN, "optional LangChain not installed")
    def test_rejects_model_contradictions_of_team_policy_and_live_status(self):
        scenarios = [
            # 허용 여부를 직접 묻는 문장은 turn_guard가 D-05로 먼저 답하므로, 모델 경로를 타는 규칙 질문으로 검사한다.
            ("일반차의 전기차 전용 칸 이용 규칙 알려줘",
             tool_call("search_knowledge", {"query": "전기차 전용 칸"}), "일반차도 주차할 수 있어요.",
             "일반 차량은 전기차·장애인 전용 칸에 주차할 수 없습니다.", "ok"),
            ("공항에 몇 자리 있어?", tool_call("parking_status", {"lot_ids": ["C"]}),
             "김해공항 주차장이 지금 만차여서 들어갈 수 없어요.", "빈자리 62칸", "ungrounded"),
        ]
        run = agents.run_conversation
        for index, (question, call, contradiction, expected, status) in enumerate(scenarios):
            with self.subTest(question=question):
                model = ScriptedModel([call, AIMessage(content=contradiction)])
                with patch.object(config, "LLM_PROVIDER", "openrouter"), \
                        patch.object(config, "LLM_ENGINE", "langchain"), \
                        patch.object(config, "api_key", return_value="test-only"), \
                        patch.object(agents, "run_conversation", side_effect=lambda *args: run(*args, model=model)):
                    result = self.ask(question, f"contradiction-{index}")
                self.assertIn(expected, result["text"])
                self.assertNotIn(contradiction, result["text"])
                self.assertEqual(result["meta"]["agent_status"], status)
                self.assertEqual(result["meta"]["source"], "rule")
                self.assertIsNone(result["confirmation_token"])

    def assert_compound(self, result, car_id="23"):
        self.assertEqual(result["flow"], "idle", result["text"])
        self.assertIsNone(result["confirmation_token"])
        expected = self.adapter.inner.find_car(car_id)
        fee = self.adapter.inner.get_fee(car_id)["fee"]
        self.assertIn(expected["space_id"], result["text"])
        self.assertIn(str(fee), result["text"].replace(",", ""))

    def assert_candidates(self, text):
        self.assertIn(config.catalog.name("C"), text)
        self.assertIn(config.catalog.name("D"), text)
        self.assertNotIn(config.catalog.name("A"), text)
        self.assertNotIn(config.catalog.name("B"), text)

    def test_a01_a02_a05_typed_and_spoken_reads_go_directly_to_requested_car(self):
        for number in ("23번", "이십 삼 번"):
            with self.subTest(number=number):
                result = self.ask(number + " 차 어디 있어?", number)
                self.assertEqual(result["flow"], "idle")
                self.assertIn(self.adapter.find_car("23")["space_id"], result["text"])
                self.assert_compound(self.ask(number + " 차 위치랑 요금 같이 알려줘", number))

    def test_a03_resume_all_goals_after_one_vehicle_question(self):
        self.assertEqual(self.ask("내 차 위치와 요금 알려줘")["flow"], "ask_vehicle")
        self.assert_compound(self.ask("23번"))

    def test_a04_multiple_numbers_do_not_query_arbitrary_vehicle(self):
        with patch.object(self.adapter, "find_car", wraps=self.adapter.find_car) as locate:
            result = self.ask("23번인지 30번인지 모르겠어. 차 위치 알려줘")
        locate.assert_not_called()
        self.assertIsNone(result["card"])
        self.assertRegex(result["text"], "여러|어느|하나")

    def test_a06_compound_read_and_payment_authorized_once(self):
        with patch.object(self.adapter, "pay", wraps=self.adapter.pay) as pay:
            result = self.ask("23번 차 위치랑 요금 알려주고 결제도 해줘")
            self.assertIn("A-03", result["text"])
            self.assertIn("1000", result["text"].replace(",", ""))
            self.assertEqual(result["flow"], "confirm_action")
            token = result["confirmation_token"]
            self.assertTrue(token)
            self.assertIn("confirm_yes", [item["id"] for item in result["chips"]])
            engine.handle("reader", chip_id="confirm_yes", confirmation_token="stale-token")
            pay.assert_not_called()
            kwargs = dict(chip_id="confirm_yes", confirmation_token=token, request_id="one-click")
            engine.handle("reader", **kwargs)
            engine.handle("reader", **kwargs)
            self.assertEqual(pay.call_count, 1)

    def test_a07_forbidden_payment_does_not_swallow_two_reads(self):
        with patch.object(self.adapter, "pay") as pay:
            self.assert_compound(self.ask("결제하지 말고 23번 차 위치랑 요금만 알려줘"))
        pay.assert_not_called()

    def test_a08_unspecified_preferences_compare_both_lots_and_rates(self):
        result = self.ask("공항이랑 신세계 중 어디가 좋아?")
        self.assert_candidates(result["text"])
        self.assertIn("3000", result["text"].replace(",", ""))
        self.assertIn("4000", result["text"].replace(",", ""))
        self.assertRegex(result["text"], "자리|빈칸")

    def test_a09_a12_keep_pair_and_change_only_requested_zone(self):
        self.ask("공항이랑 신세계 중 어디가 좋아?")
        ev = self.ask("두 곳 중 충전 자리 있고 기본요금 싼 곳은?")
        self.assert_candidates(ev["text"])
        self.assertIn("3000", ev["text"].replace(",", ""))
        space = self.ask("그럼 일반 자리는?")
        self.assert_candidates(space["text"])
        self.assertIn("40", space["text"])  # C: 64 - 6 EV - 16 accessible - 2 parked in general
        self.assertIn("46", space["text"])

    def test_a10_arrival_consultation_uses_current_data_and_explains_uncertainty(self):
        result = self.ask("전기차인데 공항 쪽에 30분 뒤 도착해. 자리 있을까?")
        self.assertIn(config.catalog.name("C"), result["text"])
        self.assertRegex(result["text"], "현재|지금")
        self.assertRegex(result["text"], "보장|예측|달라|확정|알 수 없")
        self.assertNotRegex(result["text"], r"\d+(?:\.\d+)?\s*%")
        self.assertIsNone(result["confirmation_token"])

    def test_a11_partial_failure_preserves_verified_location(self):
        with patch.object(self.adapter, "get_fee", side_effect=DataUnavailable("fee offline")):
            result = self.ask("23번 차 위치랑 요금 알려줘")
        self.assertIn("A-03", result["text"])
        self.assertRegex(result["text"], "요금")
        self.assertNotRegex(result["text"], r"\d[\d,]*\s*원")

    def test_a13_change_vehicle_and_isolate_sessions(self):
        self.ask("11번 차 위치 알려줘")
        self.assert_compound(self.ask("23번 차 위치랑 요금 알려줘"))
        self.assertEqual(engine.get_session("reader").car_id, "23")
        self.assertEqual(self.ask("내 차 위치랑 요금 알려줘", "new-page")["flow"], "ask_vehicle")

    def test_a14_internal_failure_never_substitutes_fee_from_another_source(self):
        with patch.object(self.adapter, "get_rate_policies", side_effect=DataUnavailable("offline")):
            result = self.ask("공항이랑 신세계 중 어디가 좋아?")
        self.assert_candidates(result["text"])
        self.assertRegex(result["text"], "자리|빈칸")
        self.assertNotRegex(result["text"], r"\d[\d,]*\s*원")
