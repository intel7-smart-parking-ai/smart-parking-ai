"""규칙 4(요금은 서버 단일 계산)와 PR 전 점검(챗봇 폴더에 요금 계산식 금지)을 지키는지 확인한다. 11/11.

- 챗봇 안에는 요금 계산식이 없다. 금액은 billing의 calculate_parking_fee_pure 결과 그대로다.
- 내 차 예상 요금은 서버가 계산한다(GET /sessions/{id}/fee?now=미래 시각).
- 서버가 그 조회에도 견적을 만들지만, 미래 금액이므로 견적 번호를 받지 않는다(결제에 쓰이지 않게).
"""
import re
import unittest
from pathlib import Path
from unittest.mock import patch

from chatbot import adapters, config, engine as e
from chatbot.adapters import ApiAdapter, FeeCalculatorUnavailable, MockAdapter, fee_details
from chatbot.tools.read import ReadTools

ROOT = Path(__file__).resolve().parents[1]
POLICIES = [
    {"base_minutes": 30, "base_fee": 3000, "unit_minutes": 10, "extra_fee": 500, "daily_cap": 40000},
    {"base_minutes": 60, "base_fee": 1000, "unit_minutes": 30, "extra_fee": 1000, "daily_cap": 10000},
]


class BillingSourceTests(unittest.TestCase):
    def test_same_result_as_billing_function(self):
        calc = adapters.billing_calculator()
        self.assertEqual(calc.__name__, "calculate_parking_fee_pure")
        for policy in POLICIES:
            for minutes in (1, 30, 31, 95, 1439, 1440, 1441, 4320, 4330):
                with self.subTest(policy=policy["base_fee"], minutes=minutes):
                    entry = "2026-11-10T10:00:00+09:00"
                    from datetime import datetime, timedelta
                    target = (datetime.fromisoformat(entry) + timedelta(minutes=minutes)).isoformat()
                    expected = calc(entry, target, policy)
                    self.assertEqual(fee_details(minutes, policy), (expected["amount_krw"], expected["is_daily_capped"]))

    def test_no_fee_formula_in_chatbot_code(self):
        # PR 전 점검: backend/app/chatbot/** 에서 extra_fee·unit_minutes 연산이 나오면 위반
        arithmetic = re.compile(r"""(extra_fee|unit_minutes)["']?\]?\s*[-+*/]|[-+*/]\s*\w*\[["'](extra_fee|unit_minutes)""")
        hits = []
        for path in sorted((ROOT / "chatbot").rglob("*.py")):
            for no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if arithmetic.search(line.split("#", 1)[0]):
                    hits.append(f"{path.relative_to(ROOT)}:{no}: {line.strip()}")
        self.assertEqual(hits, [])


class FakeApi(ApiAdapter):
    def __init__(self, value, status=200):
        super().__init__("http://backend.test")
        self.value, self.status, self.paths = value, status, []

    def find_car(self, car_id):
        return {"car_id": car_id, "lot_id": "A", "session_id": "ses_1", "entry_at": "2026-11-11T09:00:00+09:00"}

    def _request(self, method, path, body=None):
        self.paths.append((method, path))
        return self.status, dict(self.value), {}


class ServerEstimateTests(unittest.TestCase):
    SERVER = {"session_id": "ses_1", "lot_id": "A", "car_id": "23", "entry_at": "2026-11-11T09:00:00+09:00",
              "exit_at": None, "amount_krw": 4500, "duration_minutes": 95, "payment_status": "unpaid",
              "quote_id": "quote_future", "quote_expires_at": "2026-11-11T11:00:00+09:00"}

    def test_asks_server_with_future_time_and_drops_quote(self):
        api = FakeApi(self.SERVER)
        result = api.fee_at("23", 60)
        self.assertEqual((result["fee"], result["minutes"]), (4500, 95))
        self.assertNotIn("quote_id", result)            # 미래 금액 견적은 결제에 쓰지 않는다
        method, path = api.paths[0]
        self.assertEqual(method, "GET")
        self.assertTrue(path.startswith("/api/v1/sessions/ses_1/fee?now="), path)
        self.assertEqual(ApiAdapter._request_group(path), "billing")

    def test_closed_or_failed_returns_nothing(self):
        self.assertIsNone(FakeApi({**self.SERVER, "exit_at": "2026-11-11T10:00:00+09:00"}).fee_at("23", 60))
        self.assertIsNone(FakeApi({}, status=503).fee_at("23", 60))

    def test_car_estimate_uses_server_value_not_local_math(self):
        e.reset_all()
        self.addCleanup(e.reset_all)
        tools = ReadTools(e.data, e.current_alert, allowed_car_id="23")
        # 모의 서버가 현재 요금을 만드는 건 서버 역할이다. 예상 금액은 서버(fee_at) 값 그대로여야 한다.
        with patch.object(MockAdapter, "fee_at", return_value={"fee": 98765, "minutes": 999}) as asked:
            r = tools.estimate_fee(60, car_id="23")
        asked.assert_called_once_with("23", 60)
        self.assertTrue(r["ok"], r)
        self.assertEqual((r["data"]["estimated_fee"], r["data"]["total_minutes"]), (98765, 999))

    def test_mock_future_fee_creates_no_quote(self):
        mock = MockAdapter()
        before = len(mock.quotes)
        result = mock.fee_at("23", 60)
        self.assertIsInstance(result["fee"], int)
        self.assertEqual(len(mock.quotes), before)


class LotEstimateTests(unittest.TestCase):
    def setUp(self):
        e.reset_all()
        self.addCleanup(e.reset_all)

    def test_lot_estimate_is_billing_result(self):
        r = ReadTools(e.data, e.current_alert).estimate_fee(4320, lot_id="C")
        policy = config.catalog.rate_policy("C")
        self.assertEqual(r["data"]["estimated_fee"], fee_details(4320, policy)[0])

    def test_without_billing_only_policy_is_given(self):
        with patch("chatbot.adapters.billing_calculator", side_effect=FeeCalculatorUnavailable("없음")):
            r = ReadTools(e.data, e.current_alert).estimate_fee(4320, lot_id="C")
        self.assertFalse(r["ok"])
        self.assertNotIn("estimated_fee", r["data"])
        self.assertTrue(r["data"]["policy_text"])


if __name__ == "__main__":
    unittest.main()
