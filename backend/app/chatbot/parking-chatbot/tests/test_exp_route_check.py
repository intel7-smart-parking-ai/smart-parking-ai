"""[실험] 가로채기 확인(route_check, LLM_ROUTE_CHECK=1): 규칙이 직접 맡으려는 문장을 AI가 한 번 확인한다.

- 꺼져 있으면(기본) 호출하지 않는다. 지금과 같은 동작.
- AI가 '아니다'라고 하면 가로채지 않고 대화 AI로. '맞다'·실패·시간 초과는 규칙대로.
- 결제·연기 신고·Lv2 경보 중에는 확인하지 않는다.
"""
import importlib.util
import unittest
from unittest.mock import patch

from chatbot import agents, config, engine as e, route_check

HAS_LANGCHAIN = importlib.util.find_spec("langchain") is not None


@unittest.skipUnless(HAS_LANGCHAIN, "optional LangChain not installed")
class RouteCheckTests(unittest.TestCase):
    def setUp(self):
        e.reset_all()
        route_check.reset_for_tests()
        self.addCleanup(e.reset_all)
        self.addCleanup(route_check.reset_for_tests)

    def ask(self, text, verdict, sid="rc", enabled=True, alert=None, fast=False, parallel=False):
        """verdict: True/False/None 또는 예외 클래스. 반환: (응답, 확인 호출 수, 대화 AI 호출 수)."""
        from langchain_core.messages import AIMessage
        from test_agent_runtime_v3 import ScriptedModel
        counts = {"check": 0, "model": 0}

        def fake_ask(text, intent, timeout):
            counts["check"] += 1
            if isinstance(verdict, type) and issubclass(verdict, Exception):
                raise verdict("simulated")
            return verdict

        def answer(messages):
            counts["model"] += 1
            return AIMessage(content="확인했어요.")
        run = agents.run_conversation
        with patch.object(config, "LLM_PROVIDER", "openrouter"), patch.object(config, "LLM_ENGINE", "langchain"), \
                patch.object(config, "api_key", return_value="test-only"), patch.object(config, "LLM_ROUTE_CHECK", enabled), \
                patch.object(config, "LLM_MODEL", "test/model"), \
                patch.object(config, "LLM_ROUTE_CHECK_FAST", fast), patch.object(config, "LLM_ROUTE_CHECK_PARALLEL", parallel), \
                patch.object(route_check, "_ask", side_effect=fake_ask), \
                patch.object(agents, "run_conversation",
                             side_effect=lambda *a, **k: run(*a, model=ScriptedModel([answer] * 4), **k)):
            if alert:
                e.data.set_alert(*alert)
            r = e.handle(sid, text=text)
        return r, counts["check"], counts["model"]

    # ---------------------------------------------------------------- 꺼짐·실패 → 지금과 같은 동작
    def test_disabled_by_default_never_asks(self):
        self.assertFalse(config.LLM_ROUTE_CHECK)
        r, checks, _ = self.ask("개발원 주차장으로 안내해줘", False, enabled=False)
        self.assertEqual(checks, 0)
        self.assertIn("목적지로 정했어요", r["text"])

    def test_failure_or_timeout_keeps_rule(self):
        for verdict in (None, TimeoutError, OSError, ValueError):
            with self.subTest(verdict=verdict):
                e.reset_all()
                route_check.reset_for_tests()
                r, checks, _ = self.ask("개발원 주차장으로 안내해줘", verdict, sid=f"f{verdict}")
                self.assertEqual(checks, 1)
                self.assertIn("목적지로 정했어요", r["text"])
                self.assertIn("check:skip", r["meta"]["route"])

    # ---------------------------------------------------------------- 맞다 → 규칙대로 / 아니다 → 대화 AI
    def test_confirmed_destination_stays_with_rule(self):
        r, checks, model = self.ask("개발원 주차장으로 안내해줘", True)
        self.assertEqual((checks, model), (1, 0))
        self.assertIn("목적지로 정했어요", r["text"])
        self.assertIn("check:keep", r["meta"]["route"])

    def test_vetoed_destination_goes_to_ai(self):
        r, checks, model = self.ask("개발원 주차장으로 안내해줘", False)
        self.assertEqual(checks, 1)
        self.assertGreater(model, 0)
        self.assertNotIn("목적지로 정했어요", r["text"])
        self.assertIn("check:veto", r["meta"]["route"])
        self.assertIsNone(r.get("destination"))

    def test_vetoed_find_my_car_goes_to_ai(self):
        r, checks, model = self.ask("내 차 어디 있어?", False, sid="car")
        self.assertEqual(checks, 1)
        self.assertGreater(model, 0, r["meta"]["route"])

    def test_confirmed_find_my_car_asks_number(self):
        r, checks, model = self.ask("내 차 어디 있어?", True, sid="car2")
        self.assertEqual((checks, model), (1, 0))
        self.assertIn("차량 번호", r["text"])

    def test_vetoed_charging_and_forecast_go_to_ai(self):
        for i, text in enumerate(("공항 충전 현황 보여줘", "내일 오후 3시 공항 붐벼?")):
            with self.subTest(text=text):
                e.reset_all()
                route_check.reset_for_tests()
                r, checks, model = self.ask(text, False, sid=f"cf{i}")
                self.assertEqual(checks, 1)
                self.assertGreater(model, 0, r["meta"]["route"])
                self.assertNotIn("rule:charging_status", r["meta"]["route"])
                self.assertNotIn("rule:congestion_forecast", r["meta"]["route"])

    def test_forecast_follow_up_is_not_rechecked(self):
        first, checks, _ = self.ask("김해공항 3시에 혼잡하나?", True, sid="fu")
        self.assertEqual(checks, 1)
        self.assertIn("오전인지 오후인지", first["text"])
        second, checks, _ = self.ask("오후", False, sid="fu")     # 이어 받은 답은 확인하지 않는다
        self.assertEqual(checks, 0)
        self.assertIn("rule:congestion_forecast", second["meta"]["route"])

    # ---------------------------------------------------------------- 대상이 아닌 처리
    def test_payment_emergency_and_admin_are_never_checked(self):
        for i, text in enumerate(("44번 차 결제해줘", "차에서 연기 나요", "관리자 불러줘", "공항 자리 있어?")):
            with self.subTest(text=text):
                e.reset_all()
                route_check.reset_for_tests()
                _, checks, _ = self.ask(text, False, sid=f"p{i}")
                self.assertEqual(checks, 0)

    def test_lv2_alert_skips_check(self):
        r, checks, _ = self.ask("개발원 주차장으로 안내해줘", False, sid="lv2", alert=(2, "A", 3))
        self.assertEqual(checks, 0)

    def test_same_sentence_is_cached(self):
        self.ask("개발원 주차장으로 안내해줘", True, sid="c1")
        from langchain_core.messages import AIMessage  # noqa: F401
        calls = {"n": 0}

        def fake(text, intent, timeout):
            calls["n"] += 1
            return True
        with patch.object(config, "LLM_PROVIDER", "openrouter"), patch.object(config, "LLM_ENGINE", "langchain"), \
                patch.object(config, "api_key", return_value="test-only"), patch.object(config, "LLM_ROUTE_CHECK", True), \
                patch.object(config, "LLM_MODEL", "test/model"), patch.object(config, "LLM_ROUTE_CHECK_FAST", False), \
                patch.object(route_check, "_ask", side_effect=fake):
            e.handle("c2", text="개발원 주차장으로 안내해줘")
        self.assertEqual(calls["n"], 0)


@unittest.skipUnless(HAS_LANGCHAIN, "optional LangChain not installed")
class FastPathTests(RouteCheckTests):
    """빠른 길: 그 처리의 말로만 된 문장은 확인을 생략한다. 모르는 낱말이 있으면 확인한다."""

    def test_plain_request_skips_check(self):
        r, checks, model = self.ask("개발원 주차장으로 안내해줘", False, fast=True)   # 확인했다면 '아니다'였을 문장
        self.assertEqual((checks, model), (0, 0))
        self.assertIn("목적지로 정했어요", r["text"])
        self.assertIn("check:sure", r["meta"]["route"])

    def test_sentence_with_other_words_is_still_checked(self):
        r, checks, model = self.ask("공항 주차장으로 가는 셔틀 있어?", False, fast=True)
        self.assertEqual(checks, 1)
        self.assertGreater(model, 0, r["meta"]["route"])
        self.assertNotIn("목적지로 정했어요", r["text"])

    def test_sure_only_for_the_handlers_own_words(self):
        sure = [("set_destination", "B로 갈게"), ("set_destination", "신세계로 가자"),
                ("set_destination", "부산역 주차장으로 안내 부탁해요"), ("find_my_car", "내 차 어디 있어?"),
                ("find_my_car", "제 차 주차비 얼마 나왔어요"), ("congestion_forecast", "모레 오전 10시에 공항 붐빌까?"),
                ("charging_status", "전기차 충전 현황"), ("charging_status", "신세계 충전 현황 좀 보여줘")]
        unsure = [("set_destination", "부산역으로 가는 버스 있어?"), ("set_destination", "D로 가면 얼마나 걸려?"),
                  ("set_destination", "A로 가지 마"), ("set_destination", "신세계 주차장으로 가는 도로 막혀?"),
                  ("find_my_car", "내 차 어디에 세워야 해?"), ("find_my_car", "차량 등록은 어디서 해?"),
                  ("find_my_car", "지금 차 몇 대 있어?"), ("congestion_forecast", "주말에 붐비면 요금 더 받아?"),
                  ("congestion_forecast", "오늘 공항 자리 있어?"), ("congestion_forecast", "다음 주에 요금 올라?"),
                  ("charging_status", "충전 요금은 얼마야?"), ("charging_status", "충전 현황판은 어디에 있어?"),
                  ("charging_status", "전기차 충전 무료야?"), ("payment", "결제해줘"), ("set_destination", "")]
        for intent, text in sure:
            self.assertTrue(route_check.sure(text, intent), text)
        for intent, text in unsure:
            self.assertFalse(route_check.sure(text, intent), text)


@unittest.skipUnless(HAS_LANGCHAIN, "optional LangChain not installed")
class ParallelTests(RouteCheckTests):
    """동시 실행: 확인을 기다리지 않고 대화 AI를 함께 시작한다. 결과는 차례로 할 때와 같아야 한다."""

    def said(self, sid, text):
        return [row for row in e.sessions[sid].history if row == ("user", text)] if hasattr(e, "sessions") else None

    def test_veto_uses_the_answer_already_running(self):
        r, checks, model = self.ask("개발원 주차장으로 안내해줘", False, parallel=True)
        self.assertEqual(checks, 1)
        self.assertGreater(model, 0)
        self.assertEqual(r["text"], "확인했어요.")
        self.assertIn("check:defer", r["meta"]["route"])
        self.assertIn("check:veto", r["meta"]["route"])
        self.assertNotIn("check:redo", r["meta"]["route"])

    def test_keep_cancels_the_answer_and_runs_the_rule(self):
        r, checks, _ = self.ask("개발원 주차장으로 안내해줘", True, parallel=True, sid="pk")
        self.assertEqual(checks, 1)
        self.assertIn("목적지로 정했어요", r["text"])
        route = r["meta"]["route"]
        self.assertLess(route.index("check:defer"), route.index("check:keep"))
        self.assertLess(route.index("check:keep"), route.index("check:redo"))
        self.assertNotIn("agent:ok", route)

    def test_failure_or_timeout_keeps_rule(self):
        for verdict in (None, TimeoutError, OSError):
            with self.subTest(verdict=verdict):
                e.reset_all()
                route_check.reset_for_tests()
                r, checks, _ = self.ask("개발원 주차장으로 안내해줘", verdict, parallel=True, sid=f"pf{verdict}")
                self.assertEqual(checks, 1)
                self.assertIn("목적지로 정했어요", r["text"])
                self.assertIn("check:skip", r["meta"]["route"])

    def test_keep_for_every_handler(self):
        for i, (text, expected) in enumerate((("내 차 어디 있어?", "차량 번호"), ("공항 충전 현황 보여줘", "충전 현황"),
                                              ("내일 오후 3시 공항 붐벼?", None))):
            with self.subTest(text=text):
                e.reset_all()
                route_check.reset_for_tests()
                serial, _, _ = self.ask(text, True, sid=f"s{i}")
                e.reset_all()
                route_check.reset_for_tests()
                r, checks, _ = self.ask(text, True, parallel=True, sid=f"p{i}")
                self.assertEqual(checks, 1)
                self.assertEqual(r["text"], serial["text"])       # 차례로 할 때와 같은 규칙 답
                if expected:
                    self.assertIn(expected, r["text"])

    def test_question_is_recorded_once_after_redo(self):
        seen = {}
        original = e._handle_message

        def spy(s, *args):
            response = original(s, *args)
            seen["history"] = list(s.history)
            return response
        with patch.object(e, "_handle_message", side_effect=spy):
            self.ask("개발원 주차장으로 안내해줘", True, parallel=True, sid="once")
        self.assertEqual(sum(1 for row in seen["history"] if row == ("user", "개발원 주차장으로 안내해줘")), 1)

    def test_turn_that_never_reaches_the_ai_still_gets_the_verdict(self):
        from chatbot.tools.read import ReadTools
        with patch.object(ReadTools, "langchain_tools", side_effect=ImportError):
            r, checks, model = self.ask("개발원 주차장으로 안내해줘", True, parallel=True, sid="noai")
        self.assertEqual((checks, model), (1, 0))
        self.assertIn("목적지로 정했어요", r["text"])
        self.assertIn("check:keep", r["meta"]["route"])

    def test_parallel_check_may_wait_longer_than_serial(self):
        seen = []

        def fake(text, intent, timeout):
            seen.append(timeout)
            return False
        for parallel in (False, True):
            e.reset_all()
            route_check.reset_for_tests()
            with patch.object(config, "LLM_PROVIDER", "openrouter"), patch.object(config, "LLM_ENGINE", "langchain"),                     patch.object(config, "api_key", return_value="test-only"), patch.object(config, "LLM_ROUTE_CHECK", True),                     patch.object(config, "LLM_MODEL", "test/model"), patch.object(config, "LLM_ROUTE_CHECK_FAST", False),                     patch.object(config, "LLM_ROUTE_CHECK_PARALLEL", parallel),                     patch.object(route_check, "_ask", side_effect=fake),                     patch.object(agents, "run_conversation",
                                 side_effect=lambda *a, **k: agents.AgentResult(status="timeout")):   # 모델은 부르지 않는다
                e.handle(f"wait{parallel}", text="개발원 주차장으로 안내해줘")
        self.assertLessEqual(seen[0], config.LLM_ROUTE_CHECK_TIMEOUT_SEC)
        self.assertGreater(seen[1], config.LLM_ROUTE_CHECK_TIMEOUT_SEC)
        self.assertLessEqual(seen[1], config.LLM_ROUTE_CHECK_PARALLEL_TIMEOUT_SEC)

    def test_no_free_check_slot_keeps_rule_without_asking(self):
        for parallel in (False, True):
            with self.subTest(parallel=parallel):
                e.reset_all()
                route_check.reset_for_tests()
                with patch.object(config, "LLM_ROUTE_CHECK_MAX_INFLIGHT", 1):
                    slots = route_check._slots()
                    self.assertTrue(slots.acquire(blocking=False))       # 다른 사용자의 확인이 자리를 쓰는 중
                    try:
                        r, checks, model = self.ask("개발원 주차장으로 안내해줘", False, parallel=parallel, sid=f"full{parallel}")
                    finally:
                        slots.release()
                    self.assertEqual((checks, model), (0, 0))
                    self.assertIn("목적지로 정했어요", r["text"])
                    self.assertIn("check:skip", r["meta"]["route"])
                    self.assertTrue(slots.acquire(blocking=False))       # 자리가 새지 않았다
                    slots.release()

    def test_check_slot_is_returned_after_every_outcome(self):
        for verdict in (True, False, None, TimeoutError):
            for parallel in (False, True):
                with self.subTest(verdict=verdict, parallel=parallel):
                    e.reset_all()
                    route_check.reset_for_tests()
                    with patch.object(config, "LLM_ROUTE_CHECK_MAX_INFLIGHT", 1):
                        self.ask("개발원 주차장으로 안내해줘", verdict, parallel=parallel, sid=f"slot{verdict}{parallel}")
                        slots = route_check._slots()
                        self.assertTrue(slots.acquire(timeout=1.0))
                        slots.release()

    def test_slow_check_is_not_waited_past_its_limit(self):
        import time

        def slow(text, intent, timeout):
            time.sleep(1.2)          # 조회가 제한 시간(0.9초)보다 오래 걸린다
            return False
        started = time.monotonic()
        with patch.object(config, "LLM_PROVIDER", "openrouter"), patch.object(config, "LLM_ENGINE", "langchain"),                 patch.object(config, "api_key", return_value="test-only"), patch.object(config, "LLM_ROUTE_CHECK", True),                 patch.object(config, "LLM_MODEL", "test/model"), patch.object(config, "LLM_ROUTE_CHECK_FAST", False),                 patch.object(config, "LLM_ROUTE_CHECK_PARALLEL", True),                 patch.object(config, "LLM_ROUTE_CHECK_PARALLEL_TIMEOUT_SEC", 0.9),                 patch.object(route_check, "_ask", side_effect=slow),                 patch.object(agents, "run_conversation",
                             side_effect=lambda *a, **k: agents.AgentResult(status="timeout")):
            r = e.handle("slowcheck", text="개발원 주차장으로 안내해줘")
        self.assertLess(time.monotonic() - started, 1.15)
        self.assertIn("목적지로 정했어요", r["text"])          # 늦은 확인은 버리고 규칙대로
        self.assertIn("check:skip", r["meta"]["route"])
        time.sleep(0.4)                                       # 뒤에 남은 조회 스레드가 끝나며 자리를 반납한다
        self.assertTrue(route_check._slots().acquire(blocking=False))
        route_check._slots().release()

    def test_second_handler_in_the_same_turn_is_checked_in_order(self):
        token = route_check.begin_turn()
        try:
            with patch.object(config, "LLM_PROVIDER", "openrouter"), patch.object(config, "LLM_ENGINE", "langchain"), \
                    patch.object(config, "api_key", return_value="test-only"), patch.object(config, "LLM_ROUTE_CHECK", True), \
                    patch.object(config, "LLM_MODEL", "test/model"), patch.object(config, "LLM_ROUTE_CHECK_FAST", False), \
                    patch.object(config, "LLM_ROUTE_CHECK_PARALLEL", True), \
                    patch.object(route_check, "_ask", return_value=True):
                self.assertFalse(route_check.keep("공항 충전 현황 보여줘", "charging_status"))    # 미룸(일단 대화 AI 쪽)
                self.assertTrue(route_check.keep("공항 충전 현황 보여줘", "set_destination"))    # 두 번째는 차례로 확인
                self.assertTrue(route_check.settle(route_check.pending()))
                self.assertTrue(route_check.keep("공항 충전 현황 보여줘", "charging_status"))     # 이번 턴 결과를 기억
        finally:
            route_check.end_turn(token)


class ParseTests(unittest.TestCase):
    def test_parse(self):
        self.assertIs(route_check._parse('{"match": true}'), True)
        self.assertIs(route_check._parse('답: {"match": false}'), False)
        self.assertIs(route_check._parse("false"), False)
        self.assertIsNone(route_check._parse("잘 모르겠어요"))
        self.assertIsNone(route_check._parse(None))


if __name__ == "__main__":
    unittest.main()
