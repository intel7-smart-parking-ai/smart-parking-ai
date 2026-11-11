"""봇 점검 11/11(PR #111 검토, 500문항 × 동시 4명)에서 남은 문제.

- 출차 질문 '1780 출차해도 돼?', '9549 나가도 되나요?' → "잘 이해하지 못했어요"
- 오타 '내차 1780 어딧어', '빈짜리 몇개야'
- 외국어 답이 한국어로 나옴(26~36%): 번역 자리 2개를 못 잡으면 바로 포기, 일본어 '1日'이 숫자 추가로 걸림
- 질문 번역이 실패해 외국어 원문이 그대로 들어온 'Find car 8928' → 기본 답
"""
import threading
import time
import unittest
from unittest.mock import patch

from chatbot import config, engine as e, response_locale as locale
from chatbot.parser import extract_virtual, normalize_colloquial


class ExitQuestionTests(unittest.TestCase):
    def setUp(self):
        e.reset_all()
        self.addCleanup(e.reset_all)

    def ask(self, text, sid="exit"):
        return e.handle(sid, text=text)

    def test_virtual_car_exit_question_is_answered_from_server_payment_state(self):
        for i, text in enumerate(("1724 출차해도 돼?", "1724 나가도 되나요?", "17가1724 출차 가능해?")):
            r = self.ask(text, f"v{i}")
            self.assertIn("17가1724 차량은", r["text"], text)
            self.assertIn("출차 전에 요금을 먼저 결제해 주세요", r["text"], text)
            self.assertNotIn("이해하지 못했어요", r["text"], text)

    def test_unknown_virtual_number_says_not_found_instead_of_not_understood(self):
        r = self.ask("1780 출차해도 돼?")
        self.assertIn("1780", r["text"])
        self.assertIn("찾지 못했어요", r["text"])

    def test_paid_virtual_car_may_leave(self):
        with patch.object(e.data, "virtual_fee", return_value={"fee": 3000, "minutes": 30, "paid": True}):
            r = self.ask("1724 출차해도 돼?")
        self.assertIn("결제가 끝난 차량이라 출차하시면 돼요", r["text"])
        self.assertNotIn("먼저 결제해 주세요", r["text"])

    def test_real_car_exit_question_shows_the_fee(self):
        for i, text in enumerate(("23번 출차해도 돼?", "23번 나가도 되나요?")):
            r = self.ask(text, f"r{i}")
            self.assertIn("원", r["text"], text)
            self.assertNotIn("이해하지 못했어요", r["text"], text)

    def test_exit_question_without_a_car_asks_for_the_number(self):
        self.assertIn("차량 번호를 알려 주시면", self.ask("출차해도 돼?")["text"])

    def test_location_question_does_not_get_exit_advice(self):
        self.assertNotIn("출차", self.ask("1724 어디 있어?")["text"])


class TypoTests(unittest.TestCase):
    def setUp(self):
        e.reset_all()
        self.addCleanup(e.reset_all)

    def test_common_typos_are_normalized(self):
        self.assertEqual(normalize_colloquial("내차 1780 어딧어"), "내 차 1780 어디 있어")
        self.assertEqual(normalize_colloquial("빈짜리 몇개야"), "빈자리 몇개야")
        self.assertEqual(normalize_colloquial("23번 결재해줘"), "23번 결제해줘")
        self.assertEqual(normalize_colloquial("결재 서류"), "결제 서류")
        self.assertEqual(normalize_colloquial("17가1724 어디 있어?"), "17가1724 어디 있어?")     # 번호판은 그대로

    def test_typo_questions_are_answered(self):
        self.assertIn("17가1724 차량은", e.handle("t1", text="내차 1724 어딧어")["text"])
        self.assertIn("빈자리", e.handle("t2", text="빈짜리 몇개야")["text"])
        self.assertIn("23번 차 맞으세요", e.handle("t3", text="23번 결재해줘")["text"])

    def test_history_keeps_what_the_user_typed(self):
        seen = {}
        original = e._handle_message

        def spy(s, *args):
            response = original(s, *args)
            seen["history"] = list(s.history)
            return response
        with patch.object(e, "_handle_message", side_effect=spy):
            e.handle("t4", text="빈짜리 몇개야")
        self.assertIn(("user", "빈짜리 몇개야"), seen["history"])


class VirtualNumberTests(unittest.TestCase):
    def test_four_digits_need_a_vehicle_context(self):
        for text in ("1724 출차해도 돼?", "1724 나가도 되나요?", "내 차 1724", "Find car 1724", "Where is car 1724?",
                     "1724 車はどこですか"):
            self.assertEqual(extract_virtual(text), "1724", text)
        for text in ("2026년에 열었어?", "자리 1724개 있어?", "3000원이야?", "1724"[:0] + "주차장 1724"):
            self.assertIsNone(extract_virtual(text), text)

    def test_untranslated_foreign_question_still_finds_the_car(self):
        e.reset_all()
        self.addCleanup(e.reset_all)
        self.assertIn("17가1724 차량은", e.handle("f1", text="Find car 1724")["text"])


class TranslationTests(unittest.TestCase):
    def response(self, text="하루 최대 40,000원이에요."):
        return {"text": text, "chips": []}

    def test_number_words_may_become_digits(self):
        masked, replacements = locale._mask("하루 최대 40,000원이에요.", "ja")
        token = next(iter(replacements))
        self.assertEqual(locale._restore(f"1日最大{token}です。", masked, replacements), "1日最大40,000ウォンです。")
        masked, replacements = locale._mask("차 지붕 스티커의 두 자리 숫자예요.", "ja")
        self.assertEqual(locale._restore("車の屋根のステッカーの2桁の数字です。", masked, replacements),
                         "車の屋根のステッカーの2桁の数字です。")

    def test_other_added_numbers_are_still_rejected(self):
        masked, replacements = locale._mask("하루 최대 40,000원이에요.", "ja")
        token = next(iter(replacements))
        with self.assertRaises(ValueError):
            locale._restore(f"1日最大{token}、5台空いています。", masked, replacements)
        masked, replacements = locale._mask("빈자리가 있어요.", "en")
        with self.assertRaises(ValueError):
            locale._restore("There are 3 spaces.", masked, replacements)

    def test_waits_for_a_translation_slot_instead_of_giving_up(self):
        taken = [locale._slots.acquire(blocking=False) for _ in range(4)]
        self.assertTrue(all(taken))
        released = []

        def free_one():
            time.sleep(0.3)
            locale._slots.release()
            released.append(True)
        threading.Thread(target=free_one).start()
        try:
            with patch.object(locale, "_translate", side_effect=lambda strings, language: tuple("OK" for _ in strings)):
                result = locale.localize_response(self.response(), "en")
        finally:
            for _ in range(3):
                locale._slots.release()
        self.assertEqual(result["translation_status"], "translated")
        self.assertTrue(released)
        self.assertTrue(locale._slots.acquire(blocking=False))     # 자리가 새지 않았다
        locale._slots.release()

    def test_gives_up_quickly_when_there_is_no_time_to_wait(self):
        taken = [locale._slots.acquire(blocking=False) for _ in range(4)]
        token = locale.translate_timeout.set(2.0)                  # 번역에 쓸 3초도 남지 않음 → 기다리지 않는다
        started = time.monotonic()
        try:
            result = locale.localize_response(self.response(), "en")
        finally:
            locale.translate_timeout.reset(token)
            for _ in taken:
                locale._slots.release()
        self.assertLess(time.monotonic() - started, 0.5)
        self.assertEqual(result["translation_status"], "unavailable")
        self.assertIn("하루 최대 40,000원이에요.", result["text"])    # 확인된 한국어 원문은 남긴다

    def test_invalid_translation_is_retried_once(self):
        calls = []

        def flaky(strings, language):
            calls.append(1)
            if len(calls) == 1:
                raise ValueError("Translation changed protected facts")
            return tuple("OK" for _ in strings)
        with patch.object(locale, "_translate", side_effect=flaky):
            result = locale.localize_response(self.response(), "en")
        self.assertEqual((len(calls), result["translation_status"]), (2, "translated"))

    def test_timeout_is_not_retried(self):
        calls = []

        def slow(strings, language):
            calls.append(1)
            raise TimeoutError("simulated")
        with patch.object(locale, "_translate", side_effect=slow):
            result = locale.localize_response(self.response(), "en")
        self.assertEqual((len(calls), result["translation_status"]), (1, "unavailable"))

    def test_question_translation_uses_the_callers_time_limit(self):
        seen = []

        def fake(request, timeout):
            seen.append(timeout)
            return {"choices": [{"message": {"content": '{"korean": "차 1724 찾아줘"}'}}]}
        locale._question_to_korean.cache_clear()
        self.addCleanup(locale._question_to_korean.cache_clear)
        token = locale.translate_timeout.set(3.0)
        try:
            with patch.object(config, "LLM_PROVIDER", "openrouter"), patch.object(config, "api_key", return_value="test-only"), \
                    patch.object(config, "LLM_MODEL", "test/model"), patch.object(locale, "load_json", side_effect=fake):
                self.assertEqual(locale.question_to_korean("Find car 1724", "en"), "차 1724 찾아줘")
        finally:
            locale.translate_timeout.reset(token)
        self.assertEqual(seen, [3.0])


if __name__ == "__main__":
    unittest.main()
