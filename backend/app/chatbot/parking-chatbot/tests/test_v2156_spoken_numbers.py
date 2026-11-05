"""Spoken-number regressions, with real parser/engine/HTTP boundaries."""
import importlib.util
import unittest
from unittest.mock import patch

from chatbot import config, engine as e, parser, templates as T
from chatbot.adapters import CachedAdapter, MockAdapter


class SpokenNumberParserTests(unittest.TestCase):
    def test_spaced_numbers_are_read_as_whole_cardinals(self):
        for phrase, expected in (("이십 삼 번", "23"), ("이 십 삼 번", "23"),
                                 ("삼 십 번", "30"), ("사 십 일 번", "41"),
                                 ("십 칠 번", "17"), ("구 십 구 번", "99")):
            with self.subTest(phrase=phrase):
                got = parser.parse_rules(phrase + " 차량 위치")
                self.assertEqual((got.intent, got.params.get("car_id")), ("find_my_car", expected))

    def test_cardinal_boundary_cases_one_through_ninety_nine(self):
        units = ("", "일", "이", "삼", "사", "오", "육", "칠", "팔", "구")
        for number in range(1, 100):
            tens, ones = divmod(number, 10)
            word = units[ones] if not tens else ("" if tens == 1 else units[tens]) + "십" + units[ones]
            expected = "short" if number < 10 else str(number)
            with self.subTest(number=number):
                self.assertEqual(parser.extract_vehicle(" ".join(word) + " 번 차량 위치")[0], expected)

    def test_number_only_and_correction_keep_existing_confirmation_semantics(self):
        for text in ("이십 삼 번", "아니 이십 삼 번", "삼십 삼 번 말고 이십 삼 번",
                     "삼십 삼 번 말고 이십 삼 번 차량", "33번 말고 23번 차"):
            with self.subTest(text=text):
                self.assertEqual(parser.extract_vehicle(text), ("23", True))

    def test_correction_keeps_the_replacement_and_rejects_multiple_targets(self):
        cases = {"이십 삼 번 말고 삼 십 번 요금": "30",
                 "23번 말고 삼 십 번": "30",
                 "이십 삼 번 말고 다른 차": "missing",
                 "23번 말고 30번 41번 위치": "ambiguous",
                 "23번 말고 30번 말고 41번": "41"}
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(parser.extract_vehicle(text)[0], expected)

    def test_mixed_and_multiple_numbers_do_not_select_the_first(self):
        for text in ("이십 삼 번이랑 30번 차량 위치", "23번이랑 삼 십 번 요금",
                     "이십 삼 번이랑 사십 일 번 차량 위치", "23차량과 30차량 위치"):
            with self.subTest(text=text):
                self.assertEqual(parser.extract_vehicle(text)[0], "ambiguous")

    def test_unsupported_forms_never_expose_a_valid_suffix(self):
        for text in ("백 십일 번 차량 위치", "백이십삼번 요금", "일칠번 차량 위치",
                     "이십 3번 차량 위치", "2 3번 차량 위치", "마이너스 이십 삼 번 요금",
                     "플러스 삼 십 번 결제", "이십삼 점 오 번 차량 위치"):
            with self.subTest(text=text):
                self.assertEqual(parser.extract_vehicle(text)[0], "invalid")

    def test_non_vehicle_units_and_everyday_words_remain_unmodified(self):
        for text in ("이십 삼 번 칸 비었어?", "십 이 번 자리", "삼십 분 무료야?",
                     "이천삼백 원", "이번 요금은 얼마야?", "이번에 차 위치 다시 알려줘"):
            with self.subTest(text=text):
                self.assertIsNone(parser.extract_vehicle(text)[0])

    def test_existing_arabic_fullwidth_and_virtual_numbers_are_preserved(self):
        self.assertEqual(parser.extract_vehicle("２３번 차 위치")[0], "23")
        self.assertEqual(parser.extract_vehicle("05번 차 위치")[0], "short")
        self.assertIsNone(parser.extract_vehicle("0123번 차 위치")[0])
        self.assertEqual(parser.extract_virtual("0123번 차 위치"), "0123")
        self.assertEqual(parser.extract_virtual("17가1724 요금"), "17가1724")

    def test_unclear_purpose_is_not_guessed_from_a_recognized_number(self):
        got = parser.parse_rules("이십 삼 번 차량 조금 확인 좀 해 줘")
        self.assertEqual((got.intent, got.params.get("car_id")), ("unknown", "23"))

    def test_model_cannot_override_the_number_extracted_from_transcript(self):
        text = "이십 삼 번 차량 뿌잉"
        with patch.object(config, "LLM_ENGINE", "direct"), \
                patch("chatbot.llm.classify", return_value={"intent": "get_fee", "params": {"car_id": "41"}}) as classify:
            got = parser.parse(text)
        classify.assert_called_once_with(text)
        self.assertEqual(got.params["car_id"], "23")


class SpokenNumberFlowTests(unittest.TestCase):
    def setUp(self):
        p = patch.object(e, "data", CachedAdapter(MockAdapter()))
        p.start(); self.addCleanup(p.stop); e.reset_all()

    def test_corrected_transcript_asks_about_thirty_not_ten(self):
        response = e.handle("s", text="삼 십 번 차량 위치")
        self.assertEqual(response["flow"], "confirm_vehicle")
        self.assertEqual(e.get_session("s").pending.car_id, "30")
        self.assertIn("30번", response["text"])

    def test_correction_replaces_pending_vehicle_and_stale_token_cannot_approve(self):
        first = e.handle("s", text="이십 삼 번 차량 위치")
        second = e.handle("s", text="아니 삼 십 번")
        stale = e.handle("s", chip_id="confirm_yes", confirmation_token=first["confirmation_token"])
        self.assertIn("이전 질문", stale["text"])
        self.assertEqual(e.get_session("s").pending.car_id, "30")
        current = e.handle("s", chip_id="confirm_yes", confirmation_token=second["confirmation_token"])
        self.assertEqual(e.get_session("s").car_id, "30")
        self.assertNotIn("이전 질문", current["text"])

    def test_invalid_spoken_number_does_not_pay_the_selected_vehicle(self):
        e.get_session("s").car_id = "33"
        e.handle("s", text="결제할래")
        with patch.object(e.data.inner, "pay", wraps=e.data.inner.pay) as pay:
            rejected = e.handle("s", text="백 십일 번 결제")
            e.handle("s", text="네")
        self.assertEqual(rejected["text"], T.INVALID_VEHICLE)
        self.assertIsNone(rejected["card"])
        self.assertIsNone(e.get_session("s").pending)
        pay.assert_not_called()

    def test_negative_transcript_does_not_create_payment(self):
        e.get_session("s").car_id = "23"
        with patch.object(e.data.inner, "pay", wraps=e.data.inner.pay) as pay:
            got = e.handle("s", text="이십 삼 번 결제하지 마")
        self.assertEqual(got["meta"]["intent"], "cancel_action")
        self.assertIsNone(e.get_session("s").pending)
        pay.assert_not_called()

    def test_original_transcript_remains_in_session_history(self):
        text = "이십 삼 번 차량 위치"
        e.handle("s", text=text)
        self.assertIn(("user", text), e.get_session("s").history)


@unittest.skipUnless(all(importlib.util.find_spec(m) for m in ("fastapi", "httpx")), "fastapi/httpx unavailable")
class SpokenNumberHTTPTests(unittest.TestCase):
    def test_existing_chat_endpoint_accepts_spaced_transcript(self):
        from fastapi.testclient import TestClient
        from server_fastapi import app
        with patch.object(e, "data", CachedAdapter(MockAdapter())):
            e.reset_all()
            with TestClient(app) as client:
                response = client.post("/api/v1/chat", json={"session_id": "spoken-http", "text": "이십 삼 번 차량 위치"})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["flow"], "confirm_vehicle")
                self.assertIn("23번", response.json()["text"])
                self.assertEqual(client.post("/api/v1/chat", json={"session_id": "spoken-http", "text": "가" * 201}).status_code, 422)


if __name__ == "__main__":
    unittest.main()
