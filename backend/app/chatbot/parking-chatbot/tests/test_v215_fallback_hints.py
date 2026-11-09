"""Contextual chips are optional and do not change business execution paths."""
import unittest
from unittest.mock import patch

from chatbot import config,engine as e,llm_router,parser
from chatbot.adapters import CachedAdapter,MockAdapter
from chatbot.agents import AgentResult
from tests.test_llm_router import conversation_reply
from chatbot.fallback_hints import select


class FallbackHintsTests(unittest.TestCase):
    def setUp(self):
        for item in (patch.object(e,"data",CachedAdapter(MockAdapter())),
                     patch.object(config,"LLM_PROVIDER","none"), patch.object(config,"FALLBACK_HINTS",True)):
            item.start();self.addCleanup(item.stop)
        e.reset_all()

    def ids(self, response):
        return [chip["id"] for chip in response["chips"]]

    def test_default_is_off_and_off_preserves_the_existing_first_reply(self):
        self.assertEqual(config.getenv("FALLBACK_HINTS","0"),"0")
        with patch.object(config,"FALLBACK_HINTS",False):
            result=e.handle("off",text="자동차 사용료를 산출해 줘")
        self.assertEqual(result["text"],e.T.UNKNOWN_FIRST)
        self.assertEqual(self.ids(result),self.ids({"chips":e._chips("default")}))

    def test_fee_and_location_use_existing_relevant_chips_without_actions(self):
        with patch.object(e.data,"pay",side_effect=AssertionError("display must not pay")):
            fee=e.handle("fee-hint",text="자동차 사용료를 산출해 줘")
            location=e.handle("location-hint",text="어느 모퉁이에 뒀는지 잊었네")
        self.assertEqual(self.ids(fee)[:3],["fee","pay","fee_rule"])
        self.assertEqual(self.ids(location)[:2],["find_car","change_vehicle"])
        for result in (fee,location):
            self.assertEqual(result["text"],e.T.UNKNOWN_FIRST)
            self.assertLessEqual(len(result["chips"]),4)
            self.assertEqual(result["flow"],"idle")
            self.assertEqual(len(self.ids(result)),len(set(self.ids(result))))
        self.assertEqual(e.admin_calls(),[])

    def test_missing_exit_payment_problem_and_admin_inquiry_chips_keep_defaults(self):
        for index,text in enumerate(("차량 통행 막대가 계속 내려와 있어", "카드 승인이 거절됐는데 어떻게 해야 하니", "근무하시는 분 계세요?")):
            with self.subTest(kind=index):
                self.assertEqual(parser.parse_rules(text).intent,"unknown")
                result=e.handle(f"missing-{index}",text=text)
                self.assertEqual(self.ids(result),self.ids({"chips":e._chips("default")}))
                self.assertNotIn("call_admin",self.ids(result))

    def test_destination_cancel_is_only_shown_for_a_selected_destination(self):
        e.handle("destination",chip_id="go:A")
        result=e.handle("destination",text="안내 그만해도 돼")
        self.assertEqual(self.ids(result)[:2],["destination_cancel","cancel_action"])
        self.assertIsNotNone(e.get_session("destination").destination)
        e.handle("destination",chip_id="destination_cancel")
        self.assertIsNone(e.get_session("destination").destination)
        no_destination=e.handle("no-destination",text="안내 그만해도 돼")
        self.assertEqual(self.ids(no_destination)[0],"cancel_action")
        self.assertNotIn("destination_cancel",self.ids(no_destination))

    def test_second_unknown_keeps_the_original_second_stage(self):
        e.handle("repeat",text="자동차 사용료를 산출해 줘")
        result=e.handle("repeat",text="자동차 사용료를 산출해 줘")
        self.assertEqual(result["text"],e.T.UNKNOWN_AGAIN)
        self.assertEqual(self.ids(result),list(e.SETS["unknown_again"]))
        self.assertEqual(e.admin_calls(),[])

    def test_level_two_always_overrides_the_optional_hints(self):
        e.data.set_alert(2,"A",5)
        with patch.object(llm_router,"route",side_effect=AssertionError("must not classify")):
            result=e.handle("alarm",text="자동차 사용료를 산출해 줘")
        self.assertEqual(self.ids(result),list(e.SETS["alert"]))
        self.assertIsNotNone(result["safety"])

    def test_agent_failures_use_same_fallback_without_retry(self):
        for reason in ("missing_key","ungrounded","timeout","rate_limited","not_installed"):
            with self.subTest(reason=reason), conversation_reply(lambda *_: AgentResult(status=reason)) as invoke:
                result=e.handle(reason,text="자동차 사용료를 산출해 줘")
                self.assertEqual(self.ids(result)[:2],["fee","pay"])
                invoke.assert_called_once()
        self.assertEqual(e.admin_calls(),[])

    def test_payment_chip_still_requires_vehicle_quote_and_confirmation(self):
        first=e.handle("pay",text="자동차 사용료를 산출해 줘")
        self.assertIn("pay",self.ids(first))
        self.assertEqual(e.handle("pay",chip_id="pay")["flow"],"ask_vehicle")
        self.assertEqual(e.handle("pay",text="33")["flow"],"confirm_vehicle")
        quote=e.handle("pay",text="네")
        self.assertEqual(quote["flow"],"confirm_action")
        self.assertFalse(e.data.get_fee("33")["paid"])
        result=e.handle("pay",chip_id="confirm_yes",confirmation_token=quote["confirmation_token"])
        self.assertEqual(result["card"]["type"],"payment")

    def test_rules_do_not_invoke_the_classifier_or_change_into_unknown(self):
        with patch.object(llm_router,"route",side_effect=AssertionError("rules must not classify")):
            for index,text in enumerate(("빈자리 몇 개 있어?","요금표 알려줘","경보 알려줘","고마워")):
                result=e.handle(f"rules-{index}",text=text)
                self.assertNotIn("fallback",result["meta"])

    def test_admin_call_is_never_a_ranked_candidate_even_with_a_selected_car(self):
        definitions={**e.CHIPS,"inquiry":{"intent":"admin_help","label":"검사용 문의"}}
        ids=select("직원 관리자 사람 근무",["fee","pay","find_car","call_admin"],definitions)
        self.assertEqual(ids[0],"inquiry")
        self.assertNotIn("call_admin",ids[:3])
        self.assertNotIn("inquiry",e.CHIPS)


if __name__ == "__main__":
    unittest.main()
