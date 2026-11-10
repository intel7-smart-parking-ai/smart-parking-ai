"""봇 점검(11/10 16:40, 봇 PC gemma 실제 요청) 반영 확인 — 대화 AI가 켜진 상태가 기본.

원칙: 코드는 사실을 넘기고 틀린 말을 막기만 한다. 글은 AI가 쓰고, 막히면 AI가 다시 쓴다(실패 시 규칙 답).
🟠1 결제 끝난 차를 '현재 요금 3,500원'처럼 미결제로 말함 → 근거 검사 paid_not_said → AI가 다시 씀
🟠2 '버튼을 누르면 출차를 진행' 같은 챗봇이 해 주는 출차 → unsupported_exit (사용자에게 출구 안내는 통과)
🟡3 중국어 '34个车位 个空余车位' 중복
🟡4 운영 시간 자료 없는 주차장 → 조회 결과 missing_by_lot + 지시문(코드가 문장을 덧붙이지 않음)
"""
import importlib.util
import unittest
from unittest.mock import patch

from chatbot import agents, config, engine as e
from chatbot.agents.evidence import check_answer
from chatbot.conversation_turn import _strip_fake_buttons
from chatbot.tools.read import ReadTools

HAS_LANGCHAIN = importlib.util.find_spec("langchain") is not None


def observed(paid=True):
    return [{"tool": "vehicle_info", "args": {}, "result": {"tool": "vehicle_info", "ok": True,
             "data": {"fee": {"car_id": "44", "fee": 3500, "paid": paid}}, "source": {}}}]


class PaidCheckTests(unittest.TestCase):
    def test_unpaid_talk_about_paid_car_is_rewritten(self):
        for answer in ("44번 차량의 현재 요금은 3,500원이에요.", "지금 나가면 3,500원이에요."):
            with self.subTest(answer=answer):
                self.assertEqual(check_answer(answer, "44번 차 얼마야?", observed()), "paid_not_said")

    def test_paid_said_in_any_words_passes(self):
        for answer in ("44번 차는 이미 결제했어요. 결제 금액은 3,500원이에요.",
                       "결제를 마치셨고 금액은 3,500원이었어요.",
                       "44번 차는 결제가 완료됐어요(3,500원)."):
            with self.subTest(answer=answer):
                self.assertIsNone(check_answer(answer, "44번 차 얼마야?", observed()))

    def test_unpaid_car_is_not_affected(self):
        self.assertIsNone(check_answer("44번 차량의 현재 요금은 3,500원이에요.", "44번 차 얼마야?", observed(paid=False)))


class ExitClaimTests(unittest.TestCase):
    def test_chatbot_doing_exit_is_blocked(self):
        for answer in ("결제는 이미 완료되어 있어요. 아래 버튼을 누르면 출차를 진행할 수 있습니다.",
                       "제가 출차를 처리해 드릴게요.", "차단기를 열어 드릴게요."):
            with self.subTest(answer=answer):
                self.assertIn(check_answer(answer, "이제 나갈게", observed()), {"unsupported_exit", "unsupported_action"})

    def test_guiding_user_to_exit_passes(self):
        for answer in ("이미 결제했어요. 출구로 가서 출차를 진행하시면 돼요.",
                       "이미 결제했어요. 출차가 끝나면 영수증을 확인해 주세요.",
                       "이미 결제했어요. 출차는 제가 처리할 수 없으니 출구에서 현장 안내를 따라 주세요."):
            with self.subTest(answer=answer):
                self.assertNotEqual(check_answer(answer, "이제 나갈게", observed()), "unsupported_exit")

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

    def test_single_lot_question_has_no_missing_list(self):
        data = ReadTools(e.data, e.current_alert).search_knowledge("신세계 몇 시까지 해?", ["D"])["data"]
        self.assertNotIn("missing_by_lot", data)


@unittest.skipUnless(HAS_LANGCHAIN, "optional LangChain not installed")
class PaidCarTurnTests(unittest.TestCase):
    """실제 턴: AI가 미결제처럼 쓰면 검사에 걸려 AI가 다시 쓴다. 다시 써도 틀리면 '이미 결제' 규칙 답."""

    def setUp(self):
        e.reset_all()
        self.addCleanup(e.reset_all)
        from datetime import datetime
        e.data.inner.cars["44"]["paid_at"] = datetime.now(config.KST)

    def ask(self, *answers, sid):
        from test_agent_runtime_v3 import ScriptedModel
        from langchain_core.messages import AIMessage
        model = ScriptedModel([AIMessage(content=a) for a in answers])
        run = agents.run_conversation
        with patch.object(config, "LLM_PROVIDER", "openrouter"), patch.object(config, "LLM_ENGINE", "langchain"), \
                patch.object(config, "api_key", return_value="test-only"), \
                patch.object(agents, "run_conversation", side_effect=lambda *args: run(*args, model=model)):
            return e.handle(sid, text="44번 차 얼마야?")

    def test_ai_rewrites_after_check(self):
        fee = e.data.get_fee("44")["fee"]
        r = self.ask(f"44번 차량의 현재 요금은 {fee:,}원이에요.", f"44번 차는 이미 결제했어요. 결제 금액은 {fee:,}원이에요.", sid="rw")
        self.assertEqual(r["meta"].get("source"), "llm")
        self.assertIn("이미 결제했어요", r["text"])
        self.assertNotIn("현재 요금", r["text"])

    def test_rule_answer_when_rewrite_fails(self):
        fee = e.data.get_fee("44")["fee"]
        bad = f"44번 차량의 현재 요금은 {fee:,}원이에요."
        r = self.ask(bad, bad, sid="fb")
        self.assertIn("이미 결제했어요", r["text"], r["meta"].get("route"))
        self.assertNotIn("현재 요금", r["text"])


if __name__ == "__main__":
    unittest.main()
