"""반복 문장 하나가 서버를 멈추지 않는다 (11/12, 되돌아가기 폭증).

챗봇은 API와 같은 프로세스에서 돈다. 정규식 하나가 오래 걸리면 그동안 카메라 수신·화면 조회도 함께 멈춘다.
- parser._VEHICLE_RANGE: 한글 숫자 글자('오'·'이'…)가 이어지면 글자가 2자 늘 때마다 4배 → 24자 2.3초, 30자는 분 단위
- parser._RANK: '어느주차장' 뒤 공백 없는 긴 글자 덩어리 → 글자 수의 4제곱, 200자에 한 요청 7.9초
받아들이는 문자열은 그대로 두고, 같은 덩어리를 여러 방법으로 나눌 수 없게만 고쳤다.
"""
import itertools
import re
import time
import unittest

from chatbot import engine as e, parser

# 고치기 전의 식(같은 문자열을 받아들이는지 비교하는 기준). 긴 반복 문장에는 쓰지 않는다.
OLD_NUMBER = r"(?:\d{1,3}|[일이삼사오육칠팔구십]+(?:\s*[일이삼사오육칠팔구십]+)*)"
OLD_RANGE = re.compile(rf"(?<![\d.]){OLD_NUMBER}\s*번\s*대|"
                       rf"(?<![\d.]){OLD_NUMBER}\s*(?:번\s*)?(?:[~～〜∼–-]|부터)\s*{OLD_NUMBER}(?!\d)")
OLD_RANK_TAIL = re.compile(r"(?:어떤|어느|무슨)\s*주차장\S*\s*(?:\S+\s*){0,3}(?:좋을까|좋아|좋은|나을까|나아|추천|괜찮)")
NEW_RANK_TAIL = re.compile(r"(?:어떤|어느|무슨)\s*주차장\S*(?:\s+\S+){0,3}\s*(?:좋을까|좋아|좋은|나을까|나아|추천|괜찮)")


def seconds(fn, *args):
    started = time.perf_counter()
    fn(*args)
    return time.perf_counter() - started


class RepeatedInputTests(unittest.TestCase):
    def test_vehicle_range_is_fast_on_repeated_numeral_characters(self):
        # 24자: 고치기 전 2.3초. 여기서 먼저 걸러 200자 검사가 끝없이 돌지 않게 한다.
        for text in ("오" * 24, "이" * 24, "일이삼사오육" * 4):
            self.assertLess(seconds(parser._VEHICLE_RANGE.search, text), 0.2, text)
        for text in ("오" * 200, "이 " * 100, ("일이삼사오육" * 34)[:200], ("구십 " * 67)[:200], "삼" * 196 + "번 대"):
            self.assertLess(seconds(parser._VEHICLE_RANGE.search, text), 0.2, text[:12])

    def test_rank_is_fast_on_a_long_run_after_the_lot_word(self):
        # 200자: 고치기 전 한 번 찾는 데 1초 넘게 걸렸다
        for text in ("어느주차장" + "가" * 195, "어떤 주차장" + "가" * 97 + " " + "가" * 96, ("어느주차장" * 40)[:200],
                     "무슨 주차장 " + "가 " * 94):
            self.assertLess(seconds(parser._RANK.search, text), 0.2, text[:12])

    def test_one_request_does_not_stall(self):
        # 입력은 200자까지 받는다. 고치기 전: 첫 문장은 끝나지 않았고, 둘째 문장은 한 요청에 7.9초
        for text in ("오" * 190 + " 내 차량 어디야", "어느주차장" + "가" * 195, "이" * 185 + " 차 번호 몇 번이야"):
            self.assertLess(seconds(parser.parse_rules, text), 0.5, text[:12])
            e.reset_all()
            self.assertLess(seconds(lambda: e.handle("slow", text=text)), 2.0, text[:12])
        e.reset_all()


class SameMeaningTests(unittest.TestCase):
    def test_vehicle_range_accepts_the_same_strings(self):
        parts = ["일", "이", "십", "3", "12", " ", "번", "대", "~", "부터", "-", ".", "가"]
        for n in range(1, 5):
            for combo in itertools.product(parts, repeat=n):
                text = "".join(combo)
                self.assertEqual(bool(OLD_RANGE.search(text)), bool(parser._VEHICLE_RANGE.search(text)), text)

    def test_rank_accepts_the_same_strings(self):
        self.assertIn(NEW_RANK_TAIL.pattern, parser._RANK.pattern)
        parts = ["어느", "주차장", " ", "가", "이", "좋아", "추천", "제일", "나", "  "]
        for n in range(1, 6):
            for combo in itertools.product(parts, repeat=n):
                text = "".join(combo)
                self.assertEqual(bool(OLD_RANK_TAIL.search(text)), bool(NEW_RANK_TAIL.search(text)), text)

    def test_real_questions_are_read_as_before(self):
        for text in ("30번대 차량 있어?", "10번부터 20번까지 차 있어?", "십 번 대 차량 알려줘", "이십번부터 삼십번 차량"):
            self.assertEqual(parser._vehicle_query_scope(text), "range", text)
        for text in ("23번 차 어디야?", "오 번 칸 비었어?", "차량 번호 23"):
            self.assertIsNone(parser._vehicle_query_scope(text), text)
        for text in ("어느 주차장이 좋을까?", "어떤 주차장이 제일 괜찮아?", "무슨 주차장 가는 게 나을까", "어느주차장추천"):
            self.assertTrue(parser._RANK.search(text), text)
        # 추천을 묻는 말이 '주차장' 뒤 다섯 번째 덩어리부터 나오면 전처럼 아니다
        self.assertFalse(parser._RANK.search("어느 주차장이 오늘 저녁 일곱 시쯤 가면 좋을까"))
        self.assertFalse(parser._RANK.search("어느 주차장이야?"))


if __name__ == "__main__":
    unittest.main()
