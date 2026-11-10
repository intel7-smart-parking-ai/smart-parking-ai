"""충전 현황 질문 → 화면의 충전 현황 카드(charging_status)로 연결 (PM 11/09, 김채린 charging_intent 이식)."""
import unittest
import uuid

from chatbot import engine as e


class ChargingCardTests(unittest.TestCase):
    def ask(self, text):
        return e.handle(uuid.uuid4().hex, text=text)

    def test_status_questions_open_charging_card(self):
        for text, lot in (("충전 현황 보여줘", None), ("신세계 충전기 상태", "D"), ("김해공항 충전 얼마나 남았어", "C")):
            with self.subTest(text=text):
                r = self.ask(text)
                self.assertEqual(r["meta"]["intent"], "charging_status")
                self.assertEqual(r["card"]["type"], "charging_status")
                if lot:
                    self.assertEqual(r["card"]["data"]["lot_id"], lot)

    def test_foreign_language_status_questions_open_charging_card(self):
        # D-67 A안: #78의 영어·중국어·일본어 인식만 main 형식으로 이식
        for text in ("EV charging status at Gimhae Airport", "charger status", "how long until fully charged?",
                     "充电状态", "充電状況を見せて"):
            with self.subTest(text=text):
                r = self.ask(text)
                self.assertEqual(r["meta"]["intent"], "charging_status")
                self.assertEqual(r["card"]["type"], "charging_status")

    def test_foreign_fee_or_safety_questions_do_not_open_charging_card(self):
        for text in ("charging fee?", "charger payment", "charging status fire alert", "充电状态 费用"):
            with self.subTest(text=text):
                self.assertNotEqual(self.ask(text)["meta"].get("intent"), "charging_status")

    def test_other_charging_words_keep_their_intent(self):
        self.assertNotEqual(self.ask("전기차 충전 자리 비었어?")["meta"]["intent"], "charging_status")
        self.assertNotEqual(self.ask("충전 요금 결제해줘")["meta"]["intent"], "charging_status")


if __name__ == "__main__":
    unittest.main()


class ChargingVsVacancyTests(unittest.TestCase):
    """v3.3 통합: '남은/남았'만 있는 빈자리 질문은 충전 현황 카드가 아니다(대화 AI·빈자리 조회로)."""

    def test_remaining_spots_are_vacancy(self):
        from chatbot.charging_intent import wants_status
        for text in ("충전 칸 남은 자리 있어?", "충전 자리 몇 개 남았어?"):
            with self.subTest(text=text):
                self.assertFalse(wants_status(text))
        for text in ("전기차 충전 남은 시간", "공항 충전 현황", "충전 중인 차 언제 끝나?"):
            with self.subTest(text=text):
                self.assertTrue(wants_status(text))

