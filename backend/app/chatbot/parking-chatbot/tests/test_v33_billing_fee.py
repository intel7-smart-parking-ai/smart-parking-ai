"""규칙 4(요금은 서버 단일 계산)와 PR 전 점검(챗봇 폴더에 요금 계산식 금지)을 지키는지 확인한다. 11/11.

- 챗봇 안에는 요금 계산식이 없다. 금액은 billing의 calculate_parking_fee_pure 결과 그대로다.
- 내 차 예상 요금은 서버가 준 지금 금액이 billing 결과와 같을 때만 같은 함수로 계산한다.
- 미래 시각 서버 조회(fee?now=)는 쓰지 않는다: 시연 서버가 now를 무시하고 견적만 남긴다(봇 점검 11/10).
"""
import re
import unittest
from pathlib import Path
from unittest.mock import patch

from chatbot import adapters, config, engine as e
from chatbot.adapters import ApiAdapter, FeeCalculatorUnavailable, MockAdapter, fee_details
from chatbot.tools.read import ReadTools, future_fee

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


class FutureFeeTests(unittest.TestCase):
    """내 차 예상 요금: 서버 지금 금액과 billing 결과가 같을 때만, 같은 함수로 미래 금액."""
    POLICY = POLICIES[1]

    def test_future_is_billing_result_after_consistency_check(self):
        now_fee = fee_details(95, self.POLICY)[0]
        self.assertEqual(future_fee(now_fee, 95, self.POLICY, 60), (fee_details(155, self.POLICY)[0], 155))
        self.assertGreaterEqual(future_fee(now_fee, 95, self.POLICY, 60)[0], now_fee)
        # 시각이 있으면 실제 경과 초 기준(94분 30초 + 60분 → 155분)
        self.assertEqual(future_fee(now_fee, 95, self.POLICY, 60, "2026-11-11T09:00:00+09:00", "2026-11-11T10:34:30+09:00")[1], 155)

    def test_refuses_when_server_amount_differs_or_inputs_bad(self):
        now_fee = fee_details(95, self.POLICY)[0]
        self.assertIsNone(future_fee(now_fee + 500, 95, self.POLICY, 60))   # 할인·감면·영수증 금액 등
        self.assertIsNone(future_fee(now_fee, 95, {"base_fee": 1000}, 60))
        self.assertIsNone(future_fee(now_fee, None, self.POLICY, 60))
        with patch("chatbot.adapters.billing_calculator", side_effect=FeeCalculatorUnavailable("없음")):
            self.assertIsNone(future_fee(now_fee, 95, self.POLICY, 60))

    def test_no_future_time_server_call(self):
        # 시연 서버(runtime)는 fee?now=를 무시하고 견적만 남긴다 → 챗봇은 미래 시각 조회를 하지 않는다.
        source = (ROOT / "chatbot" / "adapters.py").read_text(encoding="utf-8")
        self.assertNotIn("?now=", source)
        self.assertFalse(hasattr(ApiAdapter, "fee_at"))


def _runtime():
    import sys
    repo = str(ROOT.parents[3])   # 저장소 루트(backend 패키지)
    if (ROOT.parents[3] / "backend" / "app" / "runtime.py").is_file() and repo not in sys.path:
        sys.path.append(repo)
    try:
        from fastapi.testclient import TestClient
        from backend.app.runtime import create_runtime_app
        return TestClient, create_runtime_app
    except Exception:
        return None


@unittest.skipUnless(_runtime(), "저장소 루트(backend 패키지)가 없으면 건너뜀")
class RuntimeAppTests(unittest.TestCase):
    """봇 점검 11/10: 실제 시연 앱(create_runtime_app, mode=demo)의 요금 응답으로 확인한다."""
    TOKEN = "chatbot-fee-test-token-123456"

    def setUp(self):
        import tempfile
        TestClient, create_runtime_app = _runtime()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.app = create_runtime_app(token=self.TOKEN, database=Path(self.tmp.name) / "fee.db", mode="demo")
        self.client = TestClient(self.app)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        r = self.client.post("/api/v1/simulation/D/entry",
                             json={"space_id": "A-04", "vehicle_number": "0765", "vehicle_type": "general"})
        self.assertEqual(r.status_code, 200, r.text)
        self.session_id = self.client.get("/api/v1/vehicle-search?vehicle_number=0765").json()["session_id"]

    def api(self, tamper=None):
        client, token, session_id = self.client, self.TOKEN, self.session_id

        class Runtime(ApiAdapter):
            def find_car(self, car_id):
                return {"car_id": None, "lot_id": "D", "session_id": session_id}

            def _request(self, method, path, body=None):
                r = client.request(method, path, json=body, headers={"Authorization": "Bearer " + token})
                value = r.json()
                if tamper and r.status_code == 200 and "rate_policy" in value:
                    tamper(value)
                return r.status_code, value, dict(r.headers)
        return Runtime("http://runtime.test")

    def quotes(self):
        from contextlib import closing
        with closing(self.app.state.pipeline.connection()) as conn:
            return conn.execute("SELECT count(*) FROM fee_quotes WHERE session_id=?", (self.session_id,)).fetchone()[0]

    def test_future_fee_matches_billing_and_adds_no_quote(self):
        raw = self.api().get_fee("x")
        before = self.quotes()
        for ahead in (60, 3000):
            with self.subTest(ahead=ahead):
                future = future_fee(raw["fee"], raw["minutes"], raw["rate_policy"], ahead, raw["entry_at"], raw["updated_at"])
                self.assertIsNotNone(future, raw)
                amount = future[0]
                self.assertGreaterEqual(amount, raw["fee"])
                from datetime import datetime, timedelta
                target = datetime.fromisoformat(raw["updated_at"]) + timedelta(minutes=ahead)
                expected = adapters.billing_calculator()(raw["entry_at"], target.isoformat(), raw["rate_policy"])
                self.assertEqual(amount, expected["amount_krw"])
        self.assertEqual(self.quotes(), before)   # 예상 금액 계산은 서버에 견적을 남기지 않는다

    def test_paid_car_gets_no_estimate(self):
        raw = self.api().get_fee("x")
        r = self.client.post("/api/v1/mock-payments", json={"session_id": self.session_id, "quote_id": raw["quote_id"],
                                                            "idempotency_key": "fee-test-1", "payment_method": "MOCK_CARD"})
        self.assertIn(r.status_code, (200, 201), r.text)
        paid = self.api().get_fee("x")
        self.assertTrue(paid["paid"])   # read.estimate_fee는 결제된 기록이면 계산하지 않는다

    @patch.dict("os.environ", {"FREE_TURNAROUND_MINUTES": "0"})  # 무료 회차 중엔 0원끼리 같아 규칙 차이를 못 본다
    def test_server_rule_differs_gives_no_estimate(self):
        def discount(value):
            value["rate_policy"] = dict(value["rate_policy"], base_fee=value["rate_policy"]["base_fee"] + 1000)
        raw = self.api(tamper=discount).get_fee("x")
        self.assertIsNone(future_fee(raw["fee"], raw["minutes"], raw["rate_policy"], 60))


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
