"""코드가 만드는 목록 줄을 조각으로 나눠 모델 없이 옮긴다 + 중국어·일본어 띄어쓰기 (봇 #120 검증의 남은 항목, 11/11).

- 전체 요약 줄('A 확인된 빈자리 없음 · B 만차 · C 62자리 (카메라 확인 중: A 5칸)')과 비교 줄('이름 · 빈자리 5칸, 기본요금 …')은
  주차장 상태의 조합마다 모양이 달라 줄 통째로는 미리 번역해 둘 수 없었다. 모델이 그때그때 옮기면서
  'checking 5 spaces cameras', '124 spaces total available parking spaces in the entire parking lot' 같은 말이 나왔다.
  → 구분 기호(' · ', ' / ', ', ')로 나눈 조각이 모두 아는 틀이면 조각별로 옮긴다. 하나라도 모르면 전처럼 모델에 묻는다.
- 중국어·일본어: 문장 안의 주차장 이름·개수 앞뒤 빈칸('目前 开发院停车场 已满')을 지운다.
"""
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from chatbot import config, engine as e, response_locale as locale

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import build_translation_seed as seed_tool  # noqa: E402


def camera_down(lot):
    for number in range(1, e.data.inner.capacity[lot] + 1):
        try:
            e.data.set_uncertain(lot, number, True)
        except ValueError:
            pass


class PieceTranslationTests(unittest.TestCase):
    def setUp(self):
        e.reset_all()
        locale.load_seed()
        locale.reset_shapes_for_tests()
        self.addCleanup(e.reset_all)

    def known(self, text, language):
        with patch.object(locale, "_translate_call", side_effect=AssertionError("모델을 부르면 안 된다")):
            return locale._translate_known((text,), language)

    # ---- 전체 요약 줄: 상태 조합이 달라도 모델 없이
    def test_overview_line_is_translated_without_the_model_for_any_mix_of_states(self):
        for lots in ((), ("A",), ("A", "C"), ("C", "D")):
            e.reset_all()
            for lot in lots:
                camera_down(lot)
            text = e.handle("o" + "".join(lots), text="빈자리 알려줘")["text"]
            for language in locale.LANGUAGES:
                self.assertIsNotNone(self.known(text, language), (lots, language, text))

    def test_overview_wording(self):
        camera_down("A")
        line = e.handle("o1", text="빈자리 알려줘")["text"].split("\n")[1]
        self.assertIn("개발원주차장 확인된 빈자리 없음 · 부산역주차장 만차", line)
        en, zh, ja = (self.known(line, language)[0] for language in ("en", "zh-CN", "ja"))
        self.assertTrue(en.startswith("Development Center Parking No confirmed vacancy · Busan Station Parking Full · "), en)
        self.assertTrue(en.endswith("(being checked by camera: Development Center Parking 5 spaces)"), en)
        self.assertTrue(zh.startswith("开发院停车场暂无已确认空位 · 釜山站停车场已满 · "), zh)
        self.assertTrue(zh.endswith("（摄像头确认中：开发院停车场5个车位）"), zh)
        self.assertTrue(ja.startswith("開発院駐車場は確認済みの空きなし · 釜山駅駐車場は満車 · "), ja)
        self.assertTrue(ja.endswith("（カメラで確認中：開発院駐車場5台）"), ja)

    def test_camera_note_lists_several_lots(self):
        line = "개발원주차장 확인된 빈자리 없음 · 부산역주차장 확인된 빈자리 없음 · 김해공항 주차장 26자리 (카메라 확인 중: 개발원주차장 12칸, 부산역주차장 16칸)"
        self.assertTrue(self.known(line, "en")[0].endswith(
            "(being checked by camera: Development Center Parking 12 spaces, Busan Station Parking 16 spaces)"))
        self.assertTrue(self.known(line, "ja")[0].endswith("（カメラで確認中：開発院駐車場12台、釜山駅駐車場16台）"))

    # ---- 비교 줄
    def test_comparison_line(self):
        line = "개발원주차장 · 확인된 빈자리 없음, 카메라 확인 중 5칸, 기본요금 처음 30분 1,000원"
        self.assertEqual(self.known(line, "en")[0], "Development Center Parking · no confirmed vacancy, "
                         "5 spaces being checked by camera, base fee KRW 1,000 for the first 30 minutes")
        self.assertEqual(self.known(line, "zh-CN")[0], "开发院停车场 · 无已确认空位，摄像头确认中5个车位，基本费用：前30分钟1,000韩元")
        self.assertEqual(self.known(line, "ja")[0], "開発院駐車場 · 確認済みの空きなし、カメラで確認中5台、基本料金：最初の30分1,000ウォン")
        full = "부산역주차장 · 빈자리 0칸, 만차, 기본요금 처음 30분 2,000원"
        self.assertEqual(self.known(full, "en")[0],
                         "Busan Station Parking · 0 spaces available, full, base fee KRW 2,000 for the first 30 minutes")

    def test_other_lots_line_with_a_zone(self):
        line = "신세계백화점주차장 충전 구역 8자리 / 김해공항 주차장 충전 구역 6자리"
        self.assertEqual(self.known(line, "en")[0],
                         "Shinsegae Department Store Parking EV charging area 8 spaces / Gimhae Airport Parking EV charging area 6 spaces")
        self.assertEqual(self.known(line, "zh-CN")[0], "新世界百货停车场充电区8个车位 / 金海机场停车场充电区6个车位")

    def test_camera_checking_lot_is_never_translated_as_full(self):
        camera_down("A")
        for i, question in enumerate(("빈자리 알려줘", "개발원 주차장 자리 있어?", "개발원 주차장으로 안내해줘", "전기차 자리 있어?")):
            text = e.handle(f"n{i}", text=question)["text"]
            for language, name, full in (("en", "Development Center Parking", ("Full", "full")), ("zh-CN", "开发院停车场", ("已满",)),
                                         ("ja", "開発院駐車場", ("満車",))):
                out = self.known(text, language)
                self.assertIsNotNone(out, (question, language))
                for part in out[0].replace("\n", " · ").split(" · "):
                    if name in part and "camera" not in part and "摄像头" not in part and "カメラ" not in part:
                        self.assertFalse(any(word in part for word in full), (language, part))

    # ---- 조각을 모르면 전처럼 모델에 묻는다(지어내지 않는다)
    def test_unknown_piece_goes_to_the_model(self):
        for text in ("개발원주차장 · 자리가 넉넉해서 추천드려요, 기본요금 처음 30분 1,000원",   # 모르는 조각이 하나라도 있으면
                     "네, 알겠어요. 김해공항 주차장으로 안내할게요.",                             # 쉼표가 있는 보통 문장
                     "개발원주차장 공사 중 · 부산역주차장 만차"):
            self.assertIsNone(locale._translate_known((text,), "en"), text)
        calls = []

        def fake(masked, language, timeout):
            calls.append(list(masked))
            return ["__NAME0__ Under construction · __NAME1__ Full"]

        with patch.object(config, "LLM_PROVIDER", "openrouter"), patch.object(config, "api_key", return_value="test-only"), \
                patch.object(locale, "_translate_call", side_effect=fake):
            out = locale._translate(("개발원주차장 공사 중 · 부산역주차장 만차",), "en")
        self.assertEqual(out[0], "Development Center Parking Under construction · Busan Station Parking Full")
        self.assertEqual(calls, [["__NAME0__ 공사 중 · __NAME1__ 만차"]])       # 줄 통째로, 한 번만

    def test_known_lines_do_not_call_the_model_even_when_mixed_with_unknown_ones(self):
        calls = []

        def fake(masked, language, timeout):
            calls.append(list(masked))
            return ["Parking is plentiful today."]

        text = "오늘은 자리가 넉넉해요.\n개발원주차장 5자리 · 부산역주차장 만차"
        with patch.object(config, "LLM_PROVIDER", "openrouter"), patch.object(config, "api_key", return_value="test-only"), \
                patch.object(locale, "_translate_call", side_effect=fake):
            out = locale._translate((text,), "en")
        self.assertEqual(calls, [["오늘은 자리가 넉넉해요."]])                      # 모르는 줄만 묻는다
        self.assertEqual(out[0], "Parking is plentiful today.\nDevelopment Center Parking 5 spaces · Busan Station Parking Full")


class SpacingTests(unittest.TestCase):
    def restore(self, translated, korean, language):
        masked, replacements = locale._mask(korean, language)
        return locale._restore(translated, masked, replacements)

    def test_chinese_sentence_has_no_gaps_around_the_lot_name(self):
        self.assertEqual(self.restore("目前 __NAME0__ 已满。", "지금 개발원주차장은 만차예요.", "zh-CN"), "目前开发院停车场已满。")
        self.assertEqual(self.restore("__NAME0__ 已满。", "개발원주차장은 만차예요.", "zh-CN"), "开发院停车场已满。")
        self.assertEqual(self.restore("__NAME0__ 充电区有 __COUNT1__。", "개발원주차장 충전 구역에 2자리 있어요.", "zh-CN"),
                         "开发院停车场充电区有2个车位。")

    def test_japanese_sentence_has_no_gaps_around_counts(self):
        self.assertEqual(self.restore("駐車場全体の空きは合計 __COUNT0__ です。", "전체 주차장 빈자리는 총 124자리예요.", "ja"),
                         "駐車場全体の空きは合計124台です。")

    def test_list_lines_keep_their_separators(self):
        # 문장이 아닌 목록 줄: 줄 맨 앞 이름 뒤의 빈칸과 ' · ' 구분은 남긴다
        self.assertEqual(self.restore("__NAME0__ 无空位 · __NAME1__ 已满", "개발원주차장 자리 없음 · 부산역주차장 만차", "zh-CN"),
                         "开发院停车场 无空位 · 釜山站停车场 已满")

    def test_english_is_untouched(self):
        self.assertEqual(self.restore("__NAME0__ is full.", "개발원주차장은 만차예요.", "en"), "Development Center Parking is full.")

    def test_there_is_one_space(self):
        self.assertEqual(self.restore("There are __COUNT1__ available in the __NAME0__ accessible parking area.",
                                      "개발원주차장 장애인 구역에 1자리 있어요.", "en"),
                         "There is 1 space available in the Development Center Parking accessible parking area.")
        self.assertEqual(self.restore("There are __COUNT1__ available in the __NAME0__ accessible parking area.",
                                      "개발원주차장 장애인 구역에 11자리 있어요.", "en"),
                         "There are 11 spaces available in the Development Center Parking accessible parking area.")


class HandWrittenSeedTests(unittest.TestCase):
    def test_every_hand_written_line_is_in_the_shipped_seed_and_valid(self):
        shipped = json.loads((ROOT / "chatbot" / "translation_seed.json").read_text(encoding="utf-8"))
        for masked, translations in seed_tool.HAND.items():
            self.assertEqual(len(translations), 3, masked)
            for language, translated in zip(("en", "zh-CN", "ja"), translations):
                self.assertEqual(shipped[language].get(masked), translated, (language, masked))   # --fix-only 를 돌렸는가
                self.assertTrue(locale._valid_shape(masked, translated), (language, masked))

    def test_fixes_are_already_applied_to_the_shipped_seed(self):
        shipped = json.loads((ROOT / "chatbot" / "translation_seed.json").read_text(encoding="utf-8"))
        self.assertEqual(seed_tool.apply_fixes(shipped), 0)

    def test_all_shipped_lines_load(self):
        shipped = json.loads((ROOT / "chatbot" / "translation_seed.json").read_text(encoding="utf-8"))
        self.assertEqual(locale.load_seed(), sum(len(rows) for rows in shipped.values()))


if __name__ == "__main__":
    unittest.main()
