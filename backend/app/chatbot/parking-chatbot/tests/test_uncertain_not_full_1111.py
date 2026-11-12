"""카메라 확인 중인 칸이 있는 주차장을 '만차'라고 말하지 않는다 (봇 점검 11/11, #120과 그 후속).

테스트 서버에서 카메라가 끊긴 개발원주차장(12칸 모두 '확인 필요')에 "개발원 주차장 자리 있어?"라고 묻자
대화 AI가 "만차라서 이용 가능한 자리가 없어요"라고 답했다. 서버는 빈자리 0·확인 필요 12로 주는데,
조회 도구가 이를 그냥 '만차'로 넘겼고 근거 검사도 통과시켰다.

#120: 카메라 확인 중이라는 말을 답에 넣었다.
후속(봇 #120 검증): "만차예요. (카메라 확인 중 12칸)"은 '만차'만 눈에 들어온다 → 확인 중인 칸이 있으면
'만차'라는 말 자체를 쓰지 않고 "확인된 빈자리가 없어요"라고 한다. 진짜 만차(확인 중 0칸)만 '만차'.
"""
import importlib.util
import unittest
from unittest.mock import patch

from chatbot import agents, config, engine as e, response_locale as locale
from chatbot.agents.evidence import REPAIR_HINTS, check_answer
from chatbot.tools.read import ReadTools

HAS_LANGCHAIN = importlib.util.find_spec("langchain") is not None


def camera_down(lot="A"):
    """그 주차장의 빈칸을 모두 '확인 필요'로 만든다 → 빈자리 0, 확인 중 N칸. 확인 중인 칸 수를 돌려준다."""
    pending = 0
    for number in range(1, e.data.inner.capacity[lot] + 1):
        try:
            e.data.set_uncertain(lot, number, True)
            pending += 1
        except ValueError:      # 이미 차가 있는 칸
            pass
    return pending


class CameraCheckTests(unittest.TestCase):
    def setUp(self):
        e.reset_all()
        self.addCleanup(e.reset_all)
        self.pending = camera_down("A")
        self.assertGreater(self.pending, 0)

    def status(self, lots):
        result = ReadTools(e.data, e.current_alert).parking_status(lots)
        return result, [{"tool": "parking_status", "call_id": "1", "result": result}]

    # ---- 조회 결과
    def test_tool_row_says_unknown_not_full(self):
        result, _ = self.status(["A"])
        row = result["data"]["rows"][0]
        self.assertEqual((row["empty"], row["uncertain"]), (0, self.pending))
        self.assertEqual((row["availability"], row["reason"]), ("unknown", "확인된 빈자리 없음"))
        self.assertIn(f"카메라 확인 중 {self.pending}칸", row["camera_check"])
        self.assertIn("'만차'라고 하지 않는다", row["camera_check"])

    def test_really_full_lot_is_still_full(self):
        result, _ = self.status(["B"])                      # 모의 데이터의 부산역은 확인 중인 칸 없이 만차
        row = result["data"]["rows"][0]
        self.assertEqual((row["empty"], row["availability"], row["reason"]), (0, "full", "만차"))
        self.assertNotIn("camera_check", row)

    def test_compare_rows_carry_the_note_too(self):
        rows = ReadTools(e.data, e.current_alert).compare_lots(None)["data"]["rows"]
        self.assertIn("camera_check", next(row for row in rows if row["lot_id"] == "A"))

    # ---- 근거 검사
    def test_the_word_full_is_blocked_even_with_a_camera_note(self):
        _, seen = self.status(["A"])
        n = self.pending
        for answer in ("개발원주차장은 현재 만차라서 이용 가능한 자리가 없어요.", "개발원주차장은 지금 만차예요.",
                       f"개발원주차장은 만차예요. 다만 {n}칸은 카메라 확인 중이에요.",       # '만차'만 눈에 들어온다
                       "개발원주차장에는 빈자리가 없어요."):                                # 카메라 언급 없음
            self.assertEqual(check_answer(answer, "개발원 주차장 자리 있어?", seen), "full_without_camera_note", answer)
        self.assertIn("'만차'라는 말을 쓰지 마세요", REPAIR_HINTS["full_without_camera_note"])

    def test_no_confirmed_vacancy_with_the_camera_note_passes(self):
        _, seen = self.status(["A"])
        n = self.pending
        for answer in (f"개발원주차장은 확인된 빈자리가 없어요. {n}칸은 카메라 확인 중이라 비었는지 알 수 없어요.",
                       f"개발원주차장은 현재 확인된 빈자리는 없어요. 다만 {n}칸이 카메라로 확인 중이에요."):
            self.assertIsNone(check_answer(answer, "개발원 주차장 자리 있어?", seen), answer)

    def test_other_lots_are_judged_separately(self):
        result = ReadTools(e.data, e.current_alert).compare_lots(None)
        seen = [{"tool": "compare_lots", "call_id": "1", "result": result}]
        self.assertIsNone(check_answer("부산역주차장은 만차예요. 김해공항 주차장에는 62자리가 있어요.", "자리 있어?", seen))
        self.assertEqual(check_answer("개발원주차장은 만차예요. 김해공항 주차장에는 62자리가 있어요.", "자리 있어?", seen),
                         "full_without_camera_note")

    # ---- 규칙 답: '만차'라는 말 없이
    def test_rule_answers_say_no_confirmed_vacancy(self):
        note = f"(카메라 확인 중 {self.pending}칸)"
        for i, question in enumerate(("개발원 주차장 자리 있어?", "개발원 주차장으로 안내해줘")):
            text = e.handle(f"r{i}", text=question)["text"]
            self.assertIn(f"개발원주차장은 확인된 빈자리가 없어요. {note}", text, question)
            self.assertNotIn("만차", text, question)
        overview = e.handle("r8", text="빈자리 알려줘")["text"]
        self.assertIn("개발원주차장 확인된 빈자리 없음", overview)
        self.assertIn("부산역주차장 만차", overview)                    # 진짜 만차는 그대로
        self.assertNotIn("개발원주차장 만차", overview)

    def test_really_full_lot_keeps_the_word_full(self):
        for i, question in enumerate(("부산역 자리 있어?", "부산역 주차장으로 안내해줘")):
            text = e.handle(f"f{i}", text=question)["text"]
            self.assertIn("부산역주차장은 만차예요.", text, question)
            self.assertNotIn("카메라", text, question)

    def test_the_screen_card_does_not_say_full_either(self):
        # 11/12 시연 사이트 캡처: 글은 '개발원 주차장 확인된 빈자리 없음'인데 그 아래 카드는 개발원을 '만차'라고 표시했다.
        # 화면은 카드의 status_reason을 그대로 보여 준다(frontend/src/ChatResponseCard.jsx).
        for i, question in enumerate(("주차장별 빈자리 몇 개 있어?", "개발원 주차장 자리 있어?", "전기차 자리 있어?")):
            r = e.handle(f"k{i}", text=question)
            lots = r["card"]["data"]["lots"]
            self.assertEqual(lots["A"]["status_reason"], "확인된 빈자리 없음", question)
            self.assertEqual(lots["B"]["status_reason"], "만차", question)            # 진짜 만차는 그대로
            self.assertIsNone(lots["C"]["status_reason"], question)
            self.assertNotIn("만차", r["text"].split("부산역")[0], question)
        # 카드와 글이 같은 숫자를 말한다: 합계 = 빈자리가 있는 주차장의 display_empty 합
        r = e.handle("k9", text="주차장별 빈자리 몇 개 있어?")
        total = sum(lot["display_empty"] for lot in r["card"]["data"]["lots"].values() if not lot["status_reason"])
        self.assertIn(f"총 {total}자리", r["text"])

    def test_ranking_answer_does_not_say_full(self):
        # 11/12 전수검사: "어디에 자리 제일 많아?" → "다음은 … 개발원주차장 만차 · 부산역주차장 만차예요."
        text = e.handle("rank", text="어디에 자리 제일 많아?")["text"]
        self.assertIn("개발원주차장 확인된 빈자리 없음", text)
        self.assertNotIn("개발원주차장 만차", text)
        self.assertIn("부산역주차장 만차", text)                          # 진짜 만차는 그대로

    def test_code_rendered_comparison_has_no_full_word(self):
        from chatbot.dialog.render import _parking_status_line, render_observed_parking
        result, _ = self.status(["A", "B", "C"])
        rows = result["data"]["rows"]
        self.assertEqual(_parking_status_line(rows[0], None), f"확인된 빈자리 없음, 카메라 확인 중 {self.pending}칸")
        self.assertEqual(_parking_status_line(rows[1], None), "빈자리 0칸, 만차")
        self.assertNotIn("카메라", _parking_status_line(rows[2], None))
        text = render_observed_parking(result).text       # 대화 AI가 시간 초과일 때 코드가 만드는 비교 답
        self.assertIn(f"개발원주차장 · 확인된 빈자리 없음, 카메라 확인 중 {self.pending}칸", text)

    def test_destination_alert_when_the_lot_loses_confirmed_vacancy(self):
        from chatbot import navigation as N
        self.assertEqual(N.status_sentence("개발원주차장", "만차", pending=3), "개발원주차장은 확인된 빈자리가 없어요.")
        self.assertEqual(N.status_sentence("개발원주차장", "만차", changed=True, pending=3), "개발원주차장에 확인된 빈자리가 없어졌어요.")
        self.assertEqual(N.status_sentence("부산역주차장", "만차"), "부산역주차장은 만차예요.")
        self.assertEqual(N.status_sentence("부산역주차장", "만차", changed=True), "부산역주차장이 만차가 됐어요.")

    # ---- 번역(D): 개수 뒤의 동사
    def test_plural_count_takes_a_plural_verb(self):
        masked, replacements = locale._mask("12칸이 카메라로 확인 중이에요.", "en")
        self.assertEqual(locale._restore("__COUNT0__ is being verified by camera.", masked, replacements),
                         "12 spaces are being verified by camera.")
        masked, replacements = locale._mask("1칸이 카메라로 확인 중이에요.", "en")
        self.assertEqual(locale._restore("__COUNT0__ is being verified by camera.", masked, replacements),
                         "1 space is being verified by camera.")

    # ---- 대화 AI: 만차라고 하면 고쳐 쓰고, 못 고치면 규칙 답
    @unittest.skipUnless(HAS_LANGCHAIN, "optional LangChain not installed")
    def test_ai_answer_is_rewritten_without_the_word_full(self):
        from langchain_core.messages import AIMessage
        from test_agent_runtime_v3 import ScriptedModel
        n = self.pending
        drafts = iter([f"개발원주차장은 현재 만차예요. 다만 {n}칸은 카메라 확인 중이에요.",
                       f"개발원주차장은 확인된 빈자리가 없어요. {n}칸은 카메라 확인 중이라 비었는지 알 수 없어요."])
        run = agents.run_conversation
        with patch.object(config, "LLM_PROVIDER", "openrouter"), patch.object(config, "LLM_ENGINE", "langchain"), \
                patch.object(config, "api_key", return_value="test-only"), patch.object(config, "LLM_MODEL", "test/model"), \
                patch.object(agents, "run_conversation", side_effect=lambda *a, **k: run(
                    *a, model=ScriptedModel([lambda messages: AIMessage(content=next(drafts))] * 4), **k)):
            r = e.handle("ai1", text="개발원 주차장 자리 있어?")
        self.assertIn("카메라 확인 중", r["text"])
        self.assertNotIn("만차", r["text"])
        self.assertEqual(r["meta"].get("repair_reason"), "full_without_camera_note")

    @unittest.skipUnless(HAS_LANGCHAIN, "optional LangChain not installed")
    def test_ai_that_keeps_saying_full_falls_back_to_the_rule_answer(self):
        from langchain_core.messages import AIMessage
        from test_agent_runtime_v3 import ScriptedModel
        run = agents.run_conversation
        stubborn = lambda messages: AIMessage(content="개발원주차장은 현재 만차라서 이용 가능한 자리가 없어요.")   # noqa: E731
        with patch.object(config, "LLM_PROVIDER", "openrouter"), patch.object(config, "LLM_ENGINE", "langchain"), \
                patch.object(config, "api_key", return_value="test-only"), patch.object(config, "LLM_MODEL", "test/model"), \
                patch.object(agents, "run_conversation",
                             side_effect=lambda *a, **k: run(*a, model=ScriptedModel([stubborn] * 4), **k)):
            r = e.handle("ai2", text="개발원 주차장 자리 있어?")
        self.assertIn(f"확인된 빈자리가 없어요. (카메라 확인 중 {self.pending}칸)", r["text"])
        self.assertNotIn("만차", r["text"])


if __name__ == "__main__":
    unittest.main()
