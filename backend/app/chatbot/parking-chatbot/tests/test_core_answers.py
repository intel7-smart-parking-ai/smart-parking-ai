"""문장 오분류의 부작용과 확인 질문 뒤의 맥락 유지 회귀 검사."""
from copy import deepcopy
import unittest
from unittest.mock import patch

from chatbot import config, engine, parser
from chatbot.adapters import CachedAdapter, MockAdapter


class CoreAnswerTests(unittest.TestCase):
    def setUp(self):
        self.saved = deepcopy(config.catalog.settings)
        self.addCleanup(config.catalog.configure, self.saved)
        adapter = patch.object(engine, "data", CachedAdapter(MockAdapter()))
        adapter.start()
        self.addCleanup(adapter.stop)
        engine.reset_all()

    def ask(self, text=None, chip_id=None):
        return engine.handle("core-answer-test", text=text, chip_id=chip_id)

    def test_admin_cost_question_never_creates_a_call(self):
        for question in ("관리자 부르면 돈 들어?", "관리자 불러도 돈 들어요?", "관리자 부르면 비용 있어?"):
            response = self.ask(question)
            self.assertEqual(response["meta"]["intent"], "admin_help")
            self.assertFalse(engine.ADMIN_CALLS)
        self.assertEqual(self.ask("관리자 불러줘")["meta"]["intent"], "call_admin")
        self.assertTrue(engine.ADMIN_CALLS)

    def test_facility_question_survives_text_or_chip_choice(self):
        for choice in ({"text": "김해공항"}, {"chip_id": "lot_C"}):
            engine.reset_all()
            first = self.ask("화장실 어디야?")
            self.assertIn("어느 주차장", first["text"])
            response = self.ask(**choice)
            self.assertEqual(response["meta"]["intent"], "help")
            self.assertIn("김해공항", response["text"])
            self.assertIn("화장실", response["text"])
            self.assertIn("등록되지", response["text"])

    def test_explicit_new_question_does_not_reuse_facility(self):
        self.ask("화장실 어디야?")
        response = self.ask("공항 빈자리")
        self.assertEqual(response["meta"]["intent"], "get_empty_spots")

    def test_multiple_lots_requires_choice_and_preserves_policy(self):
        for choice in ({"text": "신세계"}, {"chip_id": "lot_D"}):
            engine.reset_all()
            first = self.ask("공항이랑 신세계 예약 가능해?")
            self.assertIn("하나를 골라", first["text"])
            response = self.ask(**choice)
            self.assertIn("신세계", response["text"])
            self.assertIn("예약 정책", response["text"])

    def test_expired_clarification_does_not_reuse_policy(self):
        from chatbot.dialog_context import resolve_facility
        from chatbot.state import Session
        session = Session(facility_question={"info_gap": "facility", "info_topic": "reservation"},
                          facility_question_at=1)
        with patch("chatbot.dialog_context.time.monotonic", return_value=100000):
            intent, _ = resolve_facility(session, "get_empty_spots", {"lot_id": "C"}, "공항", "rule")
        self.assertEqual(intent, "get_empty_spots")
        self.assertFalse(session.facility_question)

    def test_policy_is_grounded_in_configured_guide(self):
        settings = deepcopy(self.saved)
        lot = next(row for row in settings["lots"] if row["lot_id"] == "C")
        lot["guide"] = {"discount": "시연용 확인 정책: 할인은 적용하지 않습니다."}
        config.catalog.configure(settings)
        response = self.ask("김해공항 주차요금 할인돼?")
        self.assertIn(lot["guide"]["discount"], response["text"])
        response = self.ask("신세계 주차요금 할인돼?")
        self.assertIn("등록되지", response["text"])

    def test_policy_without_lot_asks_and_keeps_topic(self):
        self.assertIn("어느 주차장", self.ask("예약 가능해?")["text"])
        response = self.ask(chip_id="lot_D")
        self.assertIn("예약 정책", response["text"])
        self.assertIn("등록되지", response["text"])

    def test_safety_and_payment_intents_keep_priority(self):
        for question, expected in (("23번 차 결제해줘", "pay_fee"),
                                   ("할인받고 싶은데 화재 경보 있어?", "get_alerts"),
                                   ("결제 안 할래", "cancel_action")):
            self.assertEqual(parser.parse_rules(question).intent, expected)

    def test_lot_aliases_work_for_facilities_and_policies(self):
        for question, lot in (("신세계 엘리베이터 몇개야?", "D"), ("공항 예약 가능해?", "C"),
                              ("부산역 운영 시간", "B")):
            result = parser.parse_rules(question)
            self.assertEqual(result.intent, "help")
            self.assertEqual(result.params.get("lot_id"), lot)


if __name__ == "__main__":
    unittest.main()
