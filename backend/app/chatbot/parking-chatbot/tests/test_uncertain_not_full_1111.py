"""카메라 확인 중인 칸이 있는 주차장을 '만차'라고만 말하지 않는다 (봇 점검 11/11, #117 병합 뒤).

테스트 서버에서 카메라가 끊긴 개발원주차장(12칸 모두 '확인 필요')에 "개발원 주차장 자리 있어?"라고 묻자
대화 AI가 "만차라서 이용 가능한 자리가 없어요"라고 답했다. 서버는 빈자리 0·확인 필요 12로 주는데,
조회 도구가 이를 그냥 '만차'로 넘겼고 근거 검사도 통과시켰다. 규칙 답은 '(카메라 확인 중 N칸)'을 붙인다.
"""
import importlib.util
import unittest
from unittest.mock import patch

from chatbot import agents, config, engine as e
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
    def test_tool_row_says_the_camera_is_checking(self):
        result, _ = self.status(["A"])
        row = result["data"]["rows"][0]
        self.assertEqual((row["empty"], row["uncertain"]), (0, self.pending))
        self.assertIn(f"카메라 확인 중 {self.pending}칸", row["camera_check"])

    def test_really_full_lot_has_no_camera_note(self):
        result, _ = self.status(["B"])                      # 모의 데이터의 부산역은 확인 중인 칸 없이 만차
        row = result["data"]["rows"][0]
        self.assertEqual((row["empty"], row["availability"]), (0, "full"))
        self.assertNotIn("camera_check", row)

    def test_compare_rows_carry_the_note_too(self):
        rows = ReadTools(e.data, e.current_alert).compare_lots(None)["data"]["rows"]
        self.assertIn("camera_check", next(row for row in rows if row["lot_id"] == "A"))

    # ---- 근거 검사
    def test_full_claim_without_the_camera_note_is_blocked(self):
        _, seen = self.status(["A"])
        for answer in ("개발원주차장은 현재 만차라서 이용 가능한 자리가 없어요.", "개발원주차장은 지금 만차예요.",
                       "개발원주차장에는 빈자리가 없어요."):
            self.assertEqual(check_answer(answer, "개발원 주차장 자리 있어?", seen), "full_without_camera_note", answer)
        self.assertIn("카메라 확인 중", REPAIR_HINTS["full_without_camera_note"])

    def test_answer_that_mentions_the_camera_check_passes(self):
        _, seen = self.status(["A"])
        n = self.pending
        for answer in (f"개발원주차장은 확인된 빈자리가 없어요. {n}칸은 카메라 확인 중이라 비었는지 알 수 없어요.",
                       f"개발원주차장은 만차예요. 다만 {n}칸은 카메라 확인 중이에요."):
            self.assertIsNone(check_answer(answer, "개발원 주차장 자리 있어?", seen), answer)

    def test_other_lots_are_judged_separately(self):
        result = ReadTools(e.data, e.current_alert).compare_lots(None)
        seen = [{"tool": "compare_lots", "call_id": "1", "result": result}]
        self.assertIsNone(check_answer("부산역주차장은 만차예요. 김해공항 주차장에는 62자리가 있어요.", "자리 있어?", seen))
        self.assertEqual(check_answer("개발원주차장은 만차예요. 김해공항 주차장에는 62자리가 있어요.", "자리 있어?", seen),
                         "full_without_camera_note")

    # ---- 규칙 답
    def test_rule_answers_carry_the_note(self):
        note = f"(카메라 확인 중 {self.pending}칸)"
        self.assertIn(note, e.handle("r1", text="개발원 주차장 자리 있어?")["text"])
        self.assertIn(note, e.handle("r2", text="개발원 주차장으로 안내해줘")["text"])     # 목적지로 고를 때도 알린다
        self.assertNotIn("카메라", e.handle("r3", text="부산역 주차장으로 안내해줘")["text"])

    def test_code_rendered_comparison_carries_the_note(self):
        from chatbot.dialog.render import _parking_status_line, render_observed_parking
        result, _ = self.status(["A", "C"])
        rows = result["data"]["rows"]
        self.assertIn(f"카메라 확인 중 {self.pending}칸", _parking_status_line(rows[0], None))
        self.assertNotIn("카메라", _parking_status_line(rows[1], None))
        text = render_observed_parking(result).text       # 대화 AI가 시간 초과일 때 코드가 만드는 비교 답
        self.assertIn(f"개발원주차장 · 빈자리 0칸, 만차, 카메라 확인 중 {self.pending}칸", text)

    # ---- 대화 AI: 만차라고만 하면 고쳐 쓰고, 못 고치면 규칙 답
    @unittest.skipUnless(HAS_LANGCHAIN, "optional LangChain not installed")
    def test_ai_answer_is_rewritten_with_the_note(self):
        from langchain_core.messages import AIMessage
        from test_agent_runtime_v3 import ScriptedModel
        n = self.pending
        drafts = iter(["개발원주차장은 현재 만차라서 이용 가능한 자리가 없어요.",
                       f"개발원주차장은 확인된 빈자리가 없어요. {n}칸은 카메라 확인 중이라 비었는지 알 수 없어요."])
        run = agents.run_conversation
        with patch.object(config, "LLM_PROVIDER", "openrouter"), patch.object(config, "LLM_ENGINE", "langchain"), \
                patch.object(config, "api_key", return_value="test-only"), patch.object(config, "LLM_MODEL", "test/model"), \
                patch.object(agents, "run_conversation", side_effect=lambda *a, **k: run(
                    *a, model=ScriptedModel([lambda messages: AIMessage(content=next(drafts))] * 4), **k)):
            r = e.handle("ai1", text="개발원 주차장 자리 있어?")
        self.assertIn("카메라 확인 중", r["text"])
        self.assertNotIn("만차라서", r["text"])
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
        self.assertIn(f"(카메라 확인 중 {self.pending}칸)", r["text"])
        self.assertNotIn("만차라서 이용 가능한 자리가 없어요", r["text"])


if __name__ == "__main__":
    unittest.main()
