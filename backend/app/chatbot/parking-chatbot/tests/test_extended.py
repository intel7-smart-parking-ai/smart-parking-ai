"""보정용 50문장 / 첫 보정 후 처음 실행한 확인용 20문장 및 부작용 검사."""
import csv
from pathlib import Path
import unittest
from unittest.mock import patch
from chatbot import engine as e
from chatbot.parser import parse
from chatbot.adapters import CachedAdapter, MockAdapter


class ExtendedTests(unittest.TestCase):
    def setUp(self):
        for obj, name, value in ((e, "data", CachedAdapter(MockAdapter())), (e.config, "LLM_PROVIDER", "none")):
            p = patch.object(obj, name, value); p.start(); self.addCleanup(p.stop)
        e.reset_all()

    def check_file(self, name):
        with Path(__file__).with_name(name).open(encoding="utf-8") as f:
            for row in csv.DictReader(f):
                with self.subTest(question=row["question"]):
                    r = parse(row["question"])
                    self.assertEqual((r.intent, r.params.get("lot_id", ""), r.params.get("vehicle_no", "")),
                                     (row["intent"], row["lot_id"], row["vehicle_no"]))

    def test_development_set(self):
        self.check_file("questions_extended.csv")

    def test_reserved_set(self):
        self.check_file("questions_holdout.csv")

    def vehicle(self):
        e.handle("s", text="7번 차 위치"); e.handle("s", text="네")

    def test_decline_admin_does_not_call(self):
        for phrase in ("관리자 필요 없어", "직원은 부르지 마", "호출하지 말고 기다려"):
            e.handle("s", text=phrase)
        self.assertEqual(e.admin_calls(), [])

    def test_admin_status_query_does_not_create(self):
        for phrase in ("직원 불렀는데 확인했나요", "관리자 언제 오나요", "관리자 호출됐나요"):
            e.handle("s", text=phrase)
        self.assertEqual(e.admin_calls(), [])

    def test_payment_status_is_not_payment(self):
        self.vehicle()
        for phrase in ("결제된 건지 알려줘", "정산 여부만 확인하고 싶어"):
            result = e.handle("s", text=phrase)
            self.assertEqual(result["flow"], "idle")
            self.assertEqual(result["card"]["type"], "fee")
        self.assertFalse(e.data.get_fee("07")["paid"])

    def test_query_instead_of_payment_clears_quote(self):
        self.vehicle(); e.handle("s", text="결제할게요")
        self.assertEqual(e.get_session("s").flow.value, "confirm_action")
        e.handle("s", text="결제 말고 요금만 보여줘")
        e.handle("s", text="네")
        self.assertFalse(e.data.get_fee("07")["paid"])

    def test_soft_decline_cancels_quote(self):
        self.vehicle()
        for phrase in ("결제 말아줘", "결제 안해도 돼", "아직 정산하지 말아주세요"):
            e.handle("s", text="결제할게요")
            e.handle("s", text=phrase); e.handle("s", text="네")
            self.assertFalse(e.data.get_fee("07")["paid"])

    def test_natural_confirmation_completes_vehicle_query(self):
        e.handle("s", text="7번 요금")
        r = e.handle("s", text="네, 맞습니다")
        self.assertEqual(r["card"]["type"], "fee")
        self.assertEqual(r["vehicle_context"]["vehicle_no"], "07")

    def test_subscription_decline_returns_current_lookup(self):
        e.handle("s", text="B 자리 나면 알려줘")
        r = e.handle("s", text="대기하지 않을게요")
        self.assertIn("알려드리는 기능은 없어요", r["text"])
        self.assertEqual(r["card"]["type"], "empty_spots")

    def test_unsupported_request_does_not_execute_pending_payment(self):
        self.vehicle(); e.handle("s", text="결제할게요")
        e.handle("s", text="영수증 이메일로 보내줘")
        e.handle("s", text="네")
        self.assertFalse(e.data.get_fee("07")["paid"])
