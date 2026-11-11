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
        self.assertIn("결제가 끝난 차량이에요. 출구에서 현장 안내를 따라 출차해 주세요.", r["text"])
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

    def setUp(self):
        locale._translate.cache_clear()
        self.addCleanup(locale._translate.cache_clear)
        for p in (patch.object(config, "LLM_PROVIDER", "openrouter"), patch.object(config, "api_key", return_value="test-only"),
                  patch.object(config, "LLM_MODEL", "test/model")):
            p.start()
            self.addCleanup(p.stop)

    # ---- 일본어 '1日' (P0-1 a)
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

    # ---- 자리표시를 모델이 고쳐 쓴 경우(근본 원인, 11/11 실측)
    def test_placeholder_is_matched_by_its_number_not_its_spelling(self):
        text = "23번 차량은 현재 개발원주차장 A-03 구역에 있어요. 오전 10시 13분에 입차했어요."
        masked, replacements = locale._mask(text, "en")
        self.assertEqual(masked, "__NUMBER0__번 차량은 현재 __NAME1__ __SPACE2__ 구역에 있어요. "
                                 "오전 __NUMBER3__시 __NUMBER4__분에 입차했어요.")
        broken = "Vehicle __PLATE0__ is in area __SPACE2__ of __NAME1__. It entered at __NUMBER3__:%NUMBER4__ AM."
        self.assertEqual(locale._restore(broken, masked, replacements),
                         "Vehicle 23 is in area A-03 of Development Center Parking. It entered at 10:13 AM.")

    def test_missing_duplicated_or_unknown_placeholder_is_still_rejected(self):
        masked, replacements = locale._mask("23번 차량은 A-03에 있어요. 요금은 3,000원이에요.", "en")
        for broken in ("Vehicle __NUMBER0__ is at __SPACE1__.",                                   # 요금이 빠짐
                       "Vehicle __NUMBER0__ is at __SPACE1__. Fee __AMOUNT2__ __AMOUNT2__.",        # 두 번 씀
                       "Vehicle __NUMBER0__ is at __SPACE1__. Fee __AMOUNT2__ and __AMOUNT9__."):   # 없는 번호
            with self.assertRaises(ValueError):
                locale._restore(broken, masked, replacements)

    # ---- 번역 호출: 실패한 문장만 다시, 멈춘 호출은 한 번 더
    def test_only_the_failed_string_is_translated_again(self):
        calls = []

        def fake(strings, language, timeout):
            calls.append(list(strings))
            if len(calls) == 1:
                return ["Up to __AMOUNT0__ per day.", "broken without the placeholder"]
            return ["Vehicle __NUMBER0__ is here."]
        with patch.object(locale, "_translate_call", side_effect=fake):
            out = locale._translate(("하루 최대 40,000원이에요.", "23번 차량이 여기 있어요."), "en")
        self.assertEqual(out, ("Up to KRW 40,000 per day.", "Vehicle 23 is here."))
        self.assertEqual([len(c) for c in calls], [2, 1])

    def test_stuck_call_is_tried_once_more_when_time_remains(self):
        calls = []

        def fake(strings, language, timeout):
            calls.append(timeout)
            if len(calls) == 1:
                raise TimeoutError("simulated")
            return ["OK"]
        with patch.object(locale, "_translate_call", side_effect=fake):
            self.assertEqual(locale._translate(("안내예요.",), "en"), ("OK",))
        self.assertEqual(len(calls), 2)
        self.assertLessEqual(calls[0], locale._ATTEMPT_SEC)

    def test_no_second_call_without_time(self):
        calls = []

        def fake(strings, language, timeout):
            calls.append(timeout)
            raise TimeoutError("simulated")
        token = locale.translate_timeout.set(2.0)
        try:
            with patch.object(locale, "_translate_call", side_effect=fake):
                result = locale.localize_response(self.response(), "en")
        finally:
            locale.translate_timeout.reset(token)
        self.assertEqual((len(calls), result["translation_status"]), (1, "unavailable"))

    def test_two_failures_keep_the_verified_korean_text(self):
        with patch.object(locale, "_translate_call", return_value=["no placeholder at all"]):
            result = locale.localize_response(self.response(), "ja")
        self.assertEqual(result["translation_status"], "unavailable")
        self.assertIn("하루 최대 40,000원이에요.", result["text"])

    # ---- 내용이 None으로 온 응답(감사 P0, 전에는 500)
    def test_empty_model_content_falls_back_instead_of_crashing(self):
        empty = {"choices": [{"message": {"content": None}}]}
        locale._question_to_korean.cache_clear()
        self.addCleanup(locale._question_to_korean.cache_clear)
        with patch.object(locale, "load_json", return_value=empty):
            result = locale.localize_response(self.response(), "en")
            self.assertEqual(result["translation_status"], "unavailable")
            self.assertEqual(locale.question_to_korean("Find car 1724", "en"), "Find car 1724")

    # ---- 번역 자리(P0-1 b)
    def test_default_limit_is_unchanged(self):
        self.assertEqual(config.TRANSLATE_MAX_INFLIGHT, 2)      # 한도 변경은 PM 결정 사항

    def test_waits_for_a_translation_slot_instead_of_giving_up(self):
        slots = locale._slots()
        taken = [slots.acquire(blocking=False) for _ in range(config.TRANSLATE_MAX_INFLIGHT)]
        self.assertTrue(all(taken))
        released = []

        def free_one():
            time.sleep(0.3)
            slots.release()
            released.append(True)
        threading.Thread(target=free_one).start()
        try:
            with patch.object(locale, "_translate_call",
                              side_effect=lambda strings, language, timeout: ["OK"] * len(strings)):
                result = locale.localize_response(self.response("안내예요."), "en")
        finally:
            for _ in taken[1:]:
                slots.release()
        self.assertEqual(result["translation_status"], "translated")
        self.assertTrue(released)
        self.assertTrue(slots.acquire(blocking=False))     # 자리가 새지 않았다
        slots.release()

    def test_gives_up_quickly_when_there_is_no_time_to_wait(self):
        slots = locale._slots()
        taken = [slots.acquire(blocking=False) for _ in range(config.TRANSLATE_MAX_INFLIGHT)]
        token = locale.translate_timeout.set(2.0)                  # 번역에 쓸 3초도 남지 않음 → 기다리지 않는다
        started = time.monotonic()
        try:
            result = locale.localize_response(self.response(), "en")
        finally:
            locale.translate_timeout.reset(token)
            for _ in taken:
                slots.release()
        self.assertLess(time.monotonic() - started, 0.5)
        self.assertEqual(result["translation_status"], "unavailable")
        self.assertIn("하루 최대 40,000원이에요.", result["text"])    # 확인된 한국어 원문은 남긴다

    def test_question_translation_uses_the_callers_time_limit(self):
        seen = []

        def fake(request, timeout):
            seen.append(timeout)
            return {"choices": [{"message": {"content": '{"korean": "차 1724 찾아줘"}'}}]}
        locale._question_to_korean.cache_clear()
        self.addCleanup(locale._question_to_korean.cache_clear)
        token = locale.translate_timeout.set(3.0)
        try:
            with patch.object(locale, "load_json", side_effect=fake):
                self.assertEqual(locale.question_to_korean("Find car 1724", "en"), "차 1724 찾아줘")
        finally:
            locale.translate_timeout.reset(token)
        self.assertEqual(seen, [3.0])


class RemainingItemsTests(unittest.TestCase):
    def setUp(self):
        e.reset_all()
        self.addCleanup(e.reset_all)

    def test_vehicle_prompt_mentions_both_number_kinds(self):
        text = e.handle("ask", text="내 차 어디 있어?")["text"]
        self.assertIn("두 자리", text)
        self.assertIn("번호판 뒤 4자리", text)                      # 가상 C·D 차에 '두 자리'만 되묻던 문제(P1-2)
        self.assertIn("17가1724 차량은", e.handle("ask", text="1724")["text"])

    def test_four_digit_fee_question_is_looked_up_directly(self):
        for i, text in enumerate(("1724번 차량의 주차 요금은 얼마인가요?", "How much is the parking fee for 1724?")):
            self.assertIn("17가1724 차량은", e.handle(f"fee{i}", text=text)["text"], text)

    def test_untranslated_chinese_question_with_leading_zero(self):
        self.assertEqual(extract_virtual("我的车0475在哪里？"), "0475")      # P1-3: 앞자리 0 보존
        self.assertIn("0475", e.handle("zh", text="我的车0475在哪里？")["text"])

    def test_canceled_payment_is_said(self):
        fee = {"fee": 3000, "minutes": 30, "paid": False, "payment_status": "canceled"}
        with patch.object(e.data, "virtual_fee", return_value=fee):
            self.assertIn("이전 결제는 취소된 상태예요", e.handle("c1", text="1724 결제했어?")["text"])

    def test_unpaid_car_asked_about_payment(self):
        text = e.handle("c2", text="1724 결제했어?")["text"]
        self.assertIn("아직 결제 전이에요", text)
        self.assertNotIn("취소", text)

    def test_exit_question_during_evacuation_alert_gives_safety_first(self):
        e.data.set_alert(2, "C", 3)
        for i, text in enumerate(("1724 출차해도 돼?", "23번 나가도 되나요?")):
            r = e.handle(f"lv{i}", text=text)
            self.assertIn("경보 중에는 현장 안전 안내를 먼저 따라 주세요", r["text"], text)
            self.assertNotIn("결제해 주세요", r["text"], text)

    def test_paid_virtual_car_is_pointed_to_the_exit_staff(self):
        with patch.object(e.data, "virtual_fee", return_value={"fee": 3000, "minutes": 30, "paid": True}):
            text = e.handle("p1", text="Can 1724 leave now?")["text"]     # 번역 실패 원문도 출차 질문으로 본다
        self.assertIn("결제가 끝난 차량이에요. 출구에서 현장 안내를 따라 출차해 주세요.", text)


if __name__ == "__main__":
    unittest.main()
