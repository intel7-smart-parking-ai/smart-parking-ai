"""봇 점검(11/10 16:40, 봇 PC gemma 실제 요청) 반영 확인.

🟠1 결제 끝난 차 '얼마야?'에 AI가 '현재 요금 3,500원'만 → 결제 완료를 먼저
🟠2 '이제 나갈게'에 '아래 버튼을 누르면 출차를 진행' + '[출차하기]' → 없는 기능 약속 차단, 가짜 버튼 제거
🟡3 중국어 '34个车位 个空余车位' 중복
🟡4 '주차장 몇 시까지 해?' AI 답에 자료 없는 주차장(개발원·부산역) 언급 없음
"""
import importlib.util
import json
import unittest
from unittest.mock import patch

from chatbot import agents, config, engine as e
from chatbot.agents.evidence import check_answer
from chatbot.conversation_turn import _fix_facts, _strip_fake_buttons
from chatbot.tools.read import ReadTools

HAS_LANGCHAIN = importlib.util.find_spec("langchain") is not None
PAID = {"tool": "vehicle_info", "data": {"fee": {"car_id": "44", "fee": 3500, "paid": True}}}


class PaidCarTests(unittest.TestCase):
    def test_paid_car_answer_says_paid_first(self):
        out = _fix_facts("44번 차량의 현재 요금은 3,500원이에요. 개발원주차장에 1시간 12분 주차하셨어요.", [PAID])
        self.assertTrue(out.startswith("44번 차는 이미 결제했어요(결제 금액 3,500원)."), out)
        self.assertNotIn("현재 요금", out)
        self.assertIn("결제한 요금은 3,500원", out)

    def test_already_said_or_location_only_is_left_alone(self):
        said = "44번 차는 이미 결제가 완료됐어요. 결제 금액은 3,500원이에요."
        self.assertEqual(_fix_facts(said, [PAID]), said)
        where = "44번 차는 개발원주차장 A-04에 있어요."
        self.assertEqual(_fix_facts(where, [PAID]), where)

    def test_unpaid_is_left_alone(self):
        unpaid = {"tool": "vehicle_info", "data": {"fee": {"car_id": "44", "fee": 3500, "paid": False}}}
        self.assertEqual(_fix_facts("44번 차 현재 요금은 3,500원이에요.", [unpaid]), "44번 차 현재 요금은 3,500원이에요.")


class ExitClaimTests(unittest.TestCase):
    OBS = [{"tool": "vehicle_info", "args": {}, "result": {"tool": "vehicle_info", "ok": True,
            "data": {"fee": {"car_id": "44", "fee": 3500, "paid": True}}, "source": {}}}]

    def test_exit_promise_is_blocked(self):
        for answer in ("결제는 이미 완료되어 있어요. 아래 버튼을 누르면 출차를 진행할 수 있습니다.",
                       "차단기를 열어 드릴게요.", "결제가 끝났어요. [출차하기]"):
            with self.subTest(answer=answer):
                # 출차 약속은 막혀야 한다(앞선 실행 주장 검사가 먼저 잡아도 차단은 같다)
                self.assertIn(check_answer(answer, "이제 나갈게", self.OBS), {"unsupported_exit", "unsupported_action"})

    def test_honest_exit_guidance_passes(self):
        ok = "결제는 이미 끝났어요. 출차는 제가 처리할 수 없으니 출구에서 현장 안내를 따라 주세요."
        self.assertNotEqual(check_answer(ok, "이제 나갈게", self.OBS), "unsupported_exit")

    def test_fake_exit_button_removed(self):
        self.assertEqual(_strip_fake_buttons("결제는 이미 완료되어 있어요. [출차하기]"), "결제는 이미 완료되어 있어요.")


class TranslationTests(unittest.TestCase):
    def test_chinese_and_japanese_duplicates(self):
        from chatbot import response_locale as rl
        token = "__COUNT0__"
        cases = (("金海机场停车场目前有__COUNT0__ 个空余车位。", "34个车位", "金海机场停车场目前有34个空余车位。"),
                 ("有__COUNT0__ 个空车位。", "33个车位", "有33个空车位。"),
                 ("有__COUNT0__。", "33个车位", "有33个车位。"),
                 ("__COUNT0__ 台あります", "5台", "5台あります"))
        for translated, value, want in cases:
            with self.subTest(translated=translated):
                self.assertEqual(rl._restore(translated, token, {token: value}), want)


class MissingByLotTests(unittest.TestCase):
    def setUp(self):
        e.reset_all()
        self.addCleanup(e.reset_all)

    def test_knowledge_lists_lots_without_hours(self):
        data = ReadTools(e.data, e.current_alert).search_knowledge("주차장 몇 시까지 해?")["data"]
        self.assertEqual(data.get("missing_by_lot"), {"운영 시간": ["개발원주차장", "부산역주차장"]})

    def test_answer_gets_missing_lots_appended(self):
        knowledge = {"tool": "search_knowledge", "data": {"missing_by_lot": {"운영 시간": ["개발원주차장", "부산역주차장"]}}}
        out = _fix_facts("김해공항 주차장은 24시간, 신세계는 10:00~22:00 입차예요. 다른 주차장이 궁금하면 말씀해 주세요.", [knowledge])
        self.assertTrue(out.endswith("개발원주차장·부산역주차장은 운영 시간 자료가 아직 없어요."), out)
        said = "개발원주차장과 부산역주차장은 운영 시간 자료가 없어요."
        self.assertEqual(_fix_facts(said, [knowledge]), said)


@unittest.skipUnless(HAS_LANGCHAIN, "optional LangChain not installed")
class PaidCarTurnTests(unittest.TestCase):
    """실제 턴: 결제 완료 뒤 AI가 '현재 요금'만 말해도 화면 답은 결제 완료부터."""

    def setUp(self):
        e.reset_all()
        self.addCleanup(e.reset_all)

    def test_turn(self):
        from test_agent_runtime_v3 import ScriptedModel
        from langchain_core.messages import AIMessage
        e.data.inner.cars["44"]["paid_at"] = e.now() if hasattr(e, "now") else __import__("datetime").datetime.now(config.KST)
        fee = e.data.get_fee("44")
        model = ScriptedModel([AIMessage(content=f"44번 차량의 현재 요금은 {fee['fee']:,}원이에요.")])
        run = agents.run_conversation
        with patch.object(config, "LLM_PROVIDER", "openrouter"), patch.object(config, "LLM_ENGINE", "langchain"), \
                patch.object(config, "api_key", return_value="test-only"), \
                patch.object(agents, "run_conversation", side_effect=lambda *args: run(*args, model=model)):
            r = e.handle("paid", text="44번 차 얼마야?")
        self.assertIn("이미 결제했어요", r["text"], r["meta"].get("route"))


if __name__ == "__main__":
    unittest.main()
