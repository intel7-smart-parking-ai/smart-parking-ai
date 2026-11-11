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
        locale.reset_shapes_for_tests()
        self.addCleanup(locale.reset_shapes_for_tests)
        locale.clear_seed_for_tests()
        self.addCleanup(locale.load_seed)
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


class QuestionTranslationTests(unittest.TestCase):
    """질문 번역: 숫자는 자리표시로 가려 보내고, 검사에 걸리면 한 번 더 받는다."""

    def setUp(self):
        locale._question_to_korean.cache_clear()
        self.addCleanup(locale._question_to_korean.cache_clear)
        for p in (patch.object(config, "LLM_PROVIDER", "openrouter"), patch.object(config, "api_key", return_value="test-only"),
                  patch.object(config, "LLM_MODEL", "test/model")):
            p.start()
            self.addCleanup(p.stop)

    def reply(self, korean):
        return {"choices": [{"message": {"content": '{"korean": "' + korean + '"}'}}]}

    def test_model_never_sees_the_numbers(self):
        sent = []

        def fake(request, timeout):
            sent.append(request.data.decode())
            return self.reply("내 차 __NUMBER0__ 어디 있어?")
        with patch.object(locale, "load_json", side_effect=fake):
            self.assertEqual(locale.question_to_korean("我的车0475在哪里？", "zh-CN"), "내 차 0475 어디 있어?")   # 앞자리 0 보존
        self.assertNotIn("0475", sent[0])
        self.assertIn("__NUMBER0__", sent[0])

    def test_plate_and_time_are_each_one_placeholder(self):
        masked, replacements = locale._mask_question("Where is 17가1724? I parked at 3:30 for 2 hours")
        self.assertEqual(masked, "Where is __NUMBER0__? I parked at __NUMBER1__ for __NUMBER2__ hours")
        self.assertEqual(list(replacements.values()), ["17가1724", "3:30", "2"])

    def test_rewritten_placeholder_is_accepted_by_its_number(self):
        with patch.object(locale, "load_json", return_value=self.reply("%NUMBER0__번 차 찾아줘")):
            self.assertEqual(locale.question_to_korean("Find car 8928", "en"), "8928번 차 찾아줘")

    def test_dropped_or_invented_number_is_retried_then_falls_back(self):
        answers = iter([self.reply("차 찾아줘"), self.reply("__NUMBER0__번 차 찾아줘")])
        with patch.object(locale, "load_json", side_effect=lambda request, timeout: next(answers)):
            self.assertEqual(locale.question_to_korean("Find car 8928", "en"), "8928번 차 찾아줘")       # 두 번째에 성공
        locale._question_to_korean.cache_clear()
        with patch.object(locale, "load_json", return_value=self.reply("__NUMBER0__번 차 5대 찾아줘")):
            self.assertEqual(locale.question_to_korean("Find car 8928", "en"), "Find car 8928")        # 숫자를 지어내면 원문

    def test_number_words_may_become_digits(self):
        with patch.object(locale, "load_json", return_value=self.reply("공항에 2시간 주차하면 얼마예요?")):
            self.assertEqual(locale.question_to_korean("How much for two hours at the airport?", "en"),
                             "공항에 2시간 주차하면 얼마예요?")

    def test_time_limit_comes_from_the_caller_and_allows_a_second_try(self):
        seen = []

        def fake(request, timeout):
            seen.append(timeout)
            if len(seen) == 1:
                raise TimeoutError("simulated")
            return self.reply("__NUMBER0__번 차 찾아줘")
        token = locale.translate_timeout.set(3.0)
        try:
            with patch.object(locale, "load_json", side_effect=fake):
                self.assertEqual(locale.question_to_korean("Find car 1724", "en"), "1724번 차 찾아줘")
        finally:
            locale.translate_timeout.reset(token)
        self.assertEqual(len(seen), 2)
        self.assertLessEqual(seen[0], locale._Q_ATTEMPT_SEC)


class ShapeMemoryTests(unittest.TestCase):
    """문장 틀 기억: 숫자·이름만 다른 문장은 모델을 다시 부르지 않는다."""

    def setUp(self):
        locale.reset_shapes_for_tests()
        self.addCleanup(locale.reset_shapes_for_tests)
        locale.clear_seed_for_tests()
        self.addCleanup(locale.load_seed)
        self.addCleanup(locale.load_seed)
        for p in (patch.object(config, "LLM_PROVIDER", "openrouter"), patch.object(config, "api_key", return_value="test-only"),
                  patch.object(config, "LLM_MODEL", "test/model")):
            p.start()
            self.addCleanup(p.stop)

    def test_same_shape_with_other_numbers_skips_the_model(self):
        calls = []

        def fake(strings, language, timeout):
            calls.append(list(strings))
            return ["__NAME0__ has __COUNT1__."]
        with patch.object(locale, "_translate_call", side_effect=fake):
            first = locale._translate(("김해공항 주차장에 62자리 있어요.",), "en")
            second = locale._translate(("신세계백화점주차장에 10자리 있어요.",), "en")
        self.assertEqual(first, ("Gimhae Airport Parking has 62 spaces.",))
        self.assertEqual(second, ("Shinsegae Department Store Parking has 10 spaces.",))
        self.assertEqual(len(calls), 1)

    def test_known_shapes_work_even_when_the_model_is_down(self):
        with patch.object(locale, "_translate_call", return_value=["__NAME0__ has __COUNT1__."]):
            locale._translate(("김해공항 주차장에 62자리 있어요.",), "en")
        with patch.object(locale, "_translate_call", side_effect=TimeoutError("down")):
            result = locale.localize_response({"text": "개발원주차장에 5자리 있어요.", "chips": []}, "en")
        self.assertEqual(result["translation_status"], "translated")
        self.assertEqual(result["text"], "Development Center Parking has 5 spaces.")

    def test_only_unknown_strings_go_to_the_model(self):
        calls = []

        def fake(strings, language, timeout):
            calls.append(list(strings))
            return ["T" + "abcdefgh"[i] for i, _ in enumerate(strings)]
        with patch.object(locale, "_translate_call", side_effect=fake):
            locale._translate(("빈자리 보기", "요금 안내"), "en")
            locale._translate(("빈자리 보기", "관리자 호출"), "en")
        self.assertEqual(calls, [["빈자리 보기", "요금 안내"], ["관리자 호출"]])

    def test_rejected_translation_is_not_remembered(self):
        with patch.object(locale, "_translate_call", return_value=["no placeholder"]):
            with self.assertRaises(ValueError):
                locale._translate(("요금은 3,000원이에요.",), "en")
        self.assertEqual(locale.shape_stats()["learned_size"], 0)

    def test_seed_file_is_validated_on_load(self):
        import json
        import tempfile
        from pathlib import Path
        rows = {"en": {"__NAME0__에 __COUNT1__ 있어요.": "__NAME0__ has __COUNT1__.",        # 정상
                       "요금은 __AMOUNT0__이에요.": "The fee is 3000 won.",                 # 자리표시 빠짐 → 버림
                       "빈자리 보기": ""},                                                  # 빈 번역 → 버림
                "fr": {"빈자리 보기": "Voir"}}                                             # 지원하지 않는 언어 → 버림
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "seed.json"
            path.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
            self.assertEqual(locale.load_seed(path), 1)
            with patch.object(locale, "_translate_call", side_effect=AssertionError("모델을 부르면 안 된다")):
                self.assertEqual(locale._translate(("부산역주차장에 3자리 있어요.",), "en"),
                                 ("Busan Station Parking has 3 spaces.",))
            self.assertEqual(locale.load_seed(Path(folder) / "missing.json"), 0)     # 파일이 없어도 동작

    def test_shipped_seed_passes_validation(self):
        import json
        raw = json.loads(locale._SEED_PATH.read_text(encoding="utf-8")) if locale._SEED_PATH.exists() else {}
        total = sum(len(rows) for rows in raw.values())
        self.assertEqual(locale.load_seed(), total)       # 넣어 둔 틀이 하나도 버려지지 않는다


class VirtualVehicleToolTests(unittest.TestCase):
    """가상 주차장 차량을 대화 AI가 조회 도구로 읽는다(복합 질문). 단순 질문은 규칙이 그대로 답한다."""

    def setUp(self):
        e.reset_all()
        self.addCleanup(e.reset_all)

    def tools(self, *numbers):
        from chatbot.tools.read import ReadTools
        return ReadTools(e.data, e.current_alert, virtual_numbers=numbers)

    def test_tool_reads_location_fee_and_payment_state(self):
        result = self.tools("1724").virtual_vehicle_info("1724")
        self.assertTrue(result["ok"])
        data = result["data"]
        self.assertEqual((data["plate_number"], data["location"]["lot_id"], data["location"]["space_id"]),
                         ("17가1724", "C", "A-07"))
        self.assertEqual((data["fee"]["fee"], data["fee"]["paid"]), (3000, False))
        self.assertIn("차량번호 조회", data["payment_guide"])

    def test_only_the_number_the_user_said_can_be_read(self):
        tools = self.tools("1724")
        self.assertFalse(tools.virtual_vehicle_info("5678")["ok"])            # 남의 차
        self.assertFalse(tools.virtual_vehicle_info("")["ok"])
        self.assertFalse(self.tools().virtual_vehicle_info("1724")["ok"])     # 번호를 말하지 않은 턴
        self.assertTrue(tools.virtual_vehicle_info("17가1724")["ok"])          # 같은 차의 전체 번호판

    def test_unknown_number_is_reported_not_invented(self):
        result = self.tools("9999").virtual_vehicle_info("9999")
        self.assertFalse(result["ok"])
        self.assertIn("찾지 못했습니다", result["error"])

    def test_tool_is_listed_only_when_a_virtual_number_is_present(self):
        self.assertNotIn("virtual_vehicle_info", [t.name for t in self.tools().langchain_tools()])
        self.assertIn("virtual_vehicle_info", [t.name for t in self.tools("1724").langchain_tools()])

    def test_evidence_check_accepts_true_and_blocks_false_statements(self):
        from chatbot.agents.evidence import check_answer
        result = self.tools("1724").virtual_vehicle_info("1724")
        seen = [{"tool": "virtual_vehicle_info", "call_id": "1", "result": result}]
        entry = result["data"]["location"]["entry_time_text"]
        good = (f"17가1724 차량은 김해공항 주차장 A-07에 있어요. {entry}에 들어와서 29분째 주차 중이에요. "
                "지금까지 요금은 3,000원이고 아직 결제 전이에요. 결제는 화면의 차량번호 조회에서 진행해 주세요.")
        self.assertIsNone(check_answer(good, "1724 얼마 나왔어?", seen))
        for wrong in (good.replace("A-07", "B-03"), good.replace("3,000", "5,000"),
                      good.replace("김해공항 주차장", "신세계백화점주차장"),
                      "17가1724 차량 결제해 드렸어요.", "17가1724 차량은 이미 결제가 끝났어요.",
                      "제가 17가1724 차량 출차를 처리해 드릴게요."):
            self.assertIsNotNone(check_answer(wrong, "1724 얼마 나왔어?", seen), wrong)

    def test_simple_questions_stay_with_the_rule_and_compound_ones_go_to_the_ai(self):
        from chatbot.conversation_turn import virtual_compound
        for text in ("1724 어디 있어?", "1724 요금 얼마야?", "1724 출차해도 돼?", "1724 결제했어?", "1724 60분 뒤 얼마야?",
                     "Find car 1724"):
            self.assertFalse(virtual_compound(text), text)
        for text in ("1724 얼마 나왔어, 근처에 충전 자리도 있어?", "17가1724 어디 있고 장애인 칸은 남았어?",
                     "1724 차 찾아주고 신세계 빈자리도 알려줘"):
            self.assertTrue(virtual_compound(text), text)

    def test_ai_answers_a_compound_question_with_the_tool(self):
        import importlib.util
        if importlib.util.find_spec("langchain") is None:
            self.skipTest("optional LangChain not installed")
        import json
        from langchain_core.messages import AIMessage, ToolMessage
        from test_agent_runtime_v3 import ScriptedModel
        from chatbot import agents
        seen = {}

        def answer(messages):
            rows = [json.loads(m.content) for m in messages if isinstance(m, ToolMessage)]
            seen["tools"] = [row.get("tool") for row in rows]
            car = next(row["data"] for row in rows if row.get("tool") == "virtual_vehicle_info")
            return AIMessage(content=f"{car['plate_number']} 차량은 {car['location']['lot_name']} {car['location']['space_id']}에 있어요. "
                                     f"지금까지 요금은 {car['fee']['fee']:,}원이에요.")
        run = agents.run_conversation
        with patch.object(config, "LLM_PROVIDER", "openrouter"), patch.object(config, "LLM_ENGINE", "langchain"), \
                patch.object(config, "api_key", return_value="test-only"), patch.object(config, "LLM_MODEL", "test/model"), \
                patch.object(agents, "run_conversation",
                             side_effect=lambda *a, **k: run(*a, model=ScriptedModel([answer] * 4), **k)):
            r = e.handle("vv", text="1724 얼마 나왔어, 근처에 충전 자리도 있어?")
            self.assertIn("virtual_vehicle_info", seen["tools"])                 # 코드가 미리 조회해 넘겼다
            self.assertIn("17가1724 차량은 김해공항 주차장 A-07에 있어요", r["text"])
            self.assertIn("agent:answer", r["meta"]["route"])
            simple = e.handle("vv2", text="1724 어디 있어?")
            self.assertIn("rule:find_my_car", simple["meta"]["route"])           # 단순 질문은 규칙 그대로

    def test_ai_failure_falls_back_to_the_rule_answer(self):
        from chatbot import agents
        with patch.object(config, "LLM_PROVIDER", "openrouter"), patch.object(config, "LLM_ENGINE", "langchain"), \
                patch.object(config, "api_key", return_value="test-only"), patch.object(config, "LLM_MODEL", "test/model"), \
                patch.object(agents, "run_conversation", side_effect=lambda *a, **k: agents.AgentResult(status="timeout")):
            r = e.handle("vf", text="1724 얼마 나왔어, 근처에 충전 자리도 있어?")
        self.assertNotIn("이해하지 못했어요", r["text"])
        self.assertNotIn("차량 번호를 알려주세요", r["text"])

    def test_rule_fallback_keeps_the_car_answer(self):
        seen = {}
        original = e._handle_message

        def spy(s, *args):
            response = original(s, *args)
            seen["bot"] = [row for row in s.history if row[0] == "bot"]
            return response
        with patch.object(e, "_handle_message", side_effect=spy):
            text = e.handle("vm", text="17가1724 어디 있고 장애인 칸은 남았어?")["text"]     # 모델 없음 → 규칙 답
        self.assertIn("17가1724 차량은 김해공항 주차장 A-07에 있어요", text)
        self.assertIn("장애인 구역 빈자리", text)
        self.assertEqual(len(seen["bot"]), 1)                 # 대화 기록에는 합친 답 하나만 남는다
        self.assertEqual(seen["bot"][0][1], text)

    def test_tool_tells_the_model_not_to_estimate_again(self):
        data = self.tools("1724").virtual_vehicle_info("1724")["data"]
        self.assertIn("estimate_fee를 쓰지 않는다", data["fee_guide"])


if __name__ == "__main__":
    unittest.main()
