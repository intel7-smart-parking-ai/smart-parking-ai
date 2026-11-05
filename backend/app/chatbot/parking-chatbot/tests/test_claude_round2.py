"""Claude 2차 지적: 거절의 공백 변형·대안 요청·취소 문구·미완성 정정."""
from itertools import product
import unittest
from unittest.mock import patch
from chatbot import engine as e
from chatbot.adapters import CachedAdapter, MockAdapter


class Round2Tests(unittest.TestCase):
    def setUp(self):
        p=patch.object(e,"data",CachedAdapter(MockAdapter()))
        p.start();self.addCleanup(p.stop)
        e.reset_all()

    def choose(self):
        e.handle("s",text="33번 차 위치")
        e.handle("s",text="네")

    def test_spacing_negations_never_register_admin_call(self):
        for verb, gap1, gap2, ending in product(("불러", "호출해", "연결해"), ("", " "), ("", " "), ("마세요", "말아줘", "않을게요")):
            phrase=f"관리자 {verb}{gap1}주지{gap2}{ending}"
            with self.subTest(phrase=phrase):
                e.handle("s",text=phrase)
                self.assertEqual(e.admin_calls(),[])
        e.handle("s",text="관리자 불러 주세요")
        self.assertEqual(len(e.admin_calls()),1)

    def test_instead_of_admin_runs_only_requested_query(self):
        self.choose()
        for phrase in ("관리자 말고 요금", "직원 말고 요금만 보여줘", "관리자 호출 말고 33번 요금"):
            with self.subTest(phrase=phrase):
                r=e.handle("s",text=phrase)
                self.assertEqual(r["card"]["type"],"fee")
                self.assertEqual(r["flow"],"idle")
                self.assertEqual(e.admin_calls(),[])

    def test_cancel_message_matches_topic(self):
        for phrase in ("직원은 안 불러도 돼요", "관리자 불러 주지 마세요", "취소", "결제 안 할게요"):
            with self.subTest(phrase=phrase):
                r=e.handle("s",text=phrase)
                self.assertNotIn("환불",r["text"])
        self.assertIn("환불",e.handle("s",text="결제 취소")["text"])

    def test_no_payment_exit_question_is_rule_query(self):
        self.choose();e.handle("s",chip_id="pay")
        r=e.handle("s",text="결제 안 해도 나갈 수 있어요?")
        self.assertEqual(r["meta"]["intent"],"fee_rule")
        self.assertIn("1,000원",r["text"])
        self.assertIsNone(r["confirmation_token"])
        e.handle("s",text="네")
        self.assertFalse(e.data.get_fee("33")["paid"])

    def test_missing_replacement_clears_selected_vehicle_and_old_confirmation(self):
        self.choose();q=e.handle("s",chip_id="pay")
        r=e.handle("s",text="33번 말고 다른 차")
        self.assertEqual(r["flow"],"ask_vehicle")
        self.assertIsNone(e.get_session("s").car_id)
        self.assertNotIn("33",r["text"])
        e.handle("s",chip_id="confirm_yes",confirmation_token=q["confirmation_token"])
        self.assertFalse(e.data.get_fee("33")["paid"])

    def test_missing_replacement_resumes_original_query_after_new_number(self):
        e.handle("s",text="33번 요금")
        e.handle("s",text="33번 말고 다른 차")
        e.handle("s",text="43")
        r=e.handle("s",text="네")
        self.assertEqual(r["card"]["type"],"fee")
        self.assertEqual(r["card"]["data"]["car_id"],"43")

    def test_excluded_admin_cannot_return_through_llm_fallback(self):
        with patch("chatbot.config.LLM_ENGINE", "direct"), patch("chatbot.llm.classify",return_value={"intent":"call_admin"}) as classify:
            r=e.handle("s",text="관리자 말고 뿌잉")
        self.assertEqual(classify.call_count,0)
        self.assertEqual(r["meta"]["intent"],"unknown")
        self.assertEqual(e.admin_calls(),[])
