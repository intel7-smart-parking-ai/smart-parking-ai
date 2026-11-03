"""요금 계산 및 결제 서비스 단위 테스트 (담당자: 윤명수)"""
import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from backend.app.billing.calculator import calculate_parking_fee_pure

def test_billing():
  policy_a = {"base_minutes": 30, "base_fee": 1000, "unit_minutes": 10, "extra_fee": 500, "daily_cap": 10000}
  res = calculate_parking_fee_pure("2026-11-02T09:05:00+09:00", "2026-11-02T10:00:00+09:00", policy_a)
  assert res["amount_krw"] == 2500
  print("[PASS] test_billing: 55분 요금 2,500원 일치")

if __name__ == "__main__":
  test_billing()
