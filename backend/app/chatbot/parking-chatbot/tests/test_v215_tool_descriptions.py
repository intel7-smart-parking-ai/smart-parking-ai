"""Keep the public tool inventory and examples while clarifying boundaries."""
import re
import unittest
from chatbot import llm_router, parser

EXPECTED = frozenset({"get_empty_spots","set_destination","cancel_destination","find_my_car","get_fee",
    "fee_rule","pay_fee","payment_help","payment_issue","exit_help","call_admin","call_status",
    "admin_help","get_alerts","get_violations","evacuation_guide","alert_near_my_car","change_vehicle",
    "cancel_action","help","thanks","unknown"})


class DescriptionTests(unittest.TestCase):
    def test_tool_names_are_the_original_twenty_two(self):
        self.assertEqual(set(llm_router._DESCRIPTIONS), EXPECTED)
        self.assertEqual(set(llm_router._DESCRIPTIONS), parser.KNOWN_INTENTS - {"confirm_yes","confirm_no"})

    def test_each_description_has_korean_example_boundary_and_two_sentences(self):
        for name, text in llm_router._DESCRIPTIONS.items():
            with self.subTest(tool=name):
                self.assertTrue(re.search(r"[가-힣]", text))
                self.assertNotRegex(text, r"[A-Za-z]")
                self.assertIn("예:", text)
                self.assertLessEqual(len(text), 260)
                self.assertLessEqual(text.count("."), 2)
                self.assertTrue(text.strip())


if __name__ == "__main__":
    unittest.main()
