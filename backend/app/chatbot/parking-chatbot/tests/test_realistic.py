"""직접 작성한 현실적 표현과 대화 시나리오. 실제 이용자 로그/현장 성능 평가와 구분한다."""
import csv
from pathlib import Path
import unittest
from unittest.mock import patch
from chatbot import engine as e
from chatbot.parser import parse
from chatbot.adapters import CachedAdapter, MockAdapter


class RealisticTests(unittest.TestCase):
    def setUp(self):
        data = patch.object(e, "data", CachedAdapter(MockAdapter()))
        data.start(); self.addCleanup(data.stop)
        llm = patch.object(e.config, "LLM_PROVIDER", "none")
        llm.start(); self.addCleanup(llm.stop)
        e.reset_all()

    def say(self, text):
        return e.handle("realistic", text=text)

    def test_realistic_question_set(self):
        with Path(__file__).with_name("questions_realistic.csv").open(encoding="utf-8") as file:
            rows = list(csv.DictReader(file))
        for row in rows:
            with self.subTest(question=row["question"]):
                result = parse(row["question"])
                self.assertEqual((result.intent, result.params.get("lot_id", ""), result.params.get("car_id", "")),
                                 (row["intent"], row["lot_id"], row["car_id"]))

    def test_fee_asks_vehicle_then_continues(self):
        self.assertEqual(self.say("주차비 얼마 나왔어?")["flow"], "ask_vehicle")
        self.say("33번이요")
        result = self.say("맞아요")
        self.assertEqual(result["card"]["type"], "fee")
        self.assertEqual(result["card"]["data"]["car_id"], "33")

    def test_correct_vehicle_in_same_turn(self):
        self.say("33번 요금")
        self.say("아니 43번이에요")
        result = self.say("네")
        self.assertEqual(result["card"]["data"]["car_id"], "43")
        self.assertEqual(result["card"]["type"], "fee")

    def test_paid_but_cannot_exit_goes_to_site_admin(self):
        # v2.9: 출차 진단 제거. 출구 문제는 진단 없이 현장 관리자 연결 안내.
        self.say("33번 차 위치"); self.say("네")
        result = self.say("결제했는데 못 나가요")
        self.assertIsNone(result["card"])
        self.assertIn("현장 관리자", result["text"])
        self.assertIn("call_admin", [c["id"] for c in result["chips"]])
        self.assertFalse(e.data.get_fee("33")["paid"])

    def test_dont_call_does_not_register(self):
        self.say("관리자 부르지 말아줘")
        self.assertEqual(e.admin_calls(), [])

    def test_status_question_does_not_register(self):
        self.say("관리자가 확인했나요?")
        self.assertEqual(e.admin_calls(), [])

    def test_payment_cancel_then_yes_does_not_pay(self):
        self.say("33번 결제할게요"); self.say("네")
        self.say("결제하지 마세요"); self.say("네")
        self.assertFalse(e.data.get_fee("33")["paid"])

    def test_payment_then_different_query_does_not_pay(self):
        self.say("33번 결제할게요"); self.say("네")
        self.say("그럼 B는?"); self.say("네")
        self.assertFalse(e.data.get_fee("33")["paid"])

    def test_subscription_question_preserves_followup_lot_context(self):
        self.say("B 자리 나면 알려주세요")
        result = self.say("그럼 C는?")
        self.assertEqual(result["card"]["data"]["focus"], "C")

    def test_unknown_vehicle_still_allows_admin(self):
        self.say("34번 차 어디 있어요?"); self.say("네")
        result = self.say("직원 연결 부탁해요")
        self.assertEqual(result["card"]["type"], "admin_call")
        self.assertEqual(len(e.admin_calls()), 1)

    def test_unknown_question_does_not_change_vehicle(self):
        self.say("33번 차 위치"); self.say("네")
        self.say("43번 차 와이퍼 고장났어요")
        self.assertEqual(e.get_session("realistic").car_id, "33")

    def test_emergency_call_is_immediate_without_vehicle(self):
        e.data.set_alert(2, "A", 6)
        result = self.say("차단기 안 열리니 관리자 불러줘")
        self.assertEqual(result["card"]["type"], "admin_call")
        self.assertEqual(result["flow"], "idle")
