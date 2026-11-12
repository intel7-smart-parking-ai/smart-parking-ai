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


POLICY = {"base_minutes": 30, "base_fee": 1000, "unit_minutes": 10, "extra_fee": 500, "daily_cap": 10000}


def test_free_turnaround_boundaries():
  from backend.app.billing.calculator import free_minutes_for
  assert [free_minutes_for(lot) for lot in "ABCD"] == [5, 5, 5, 5]
  assert free_minutes_for("Z") == 0 and free_minutes_for(None) == 0
  policy = dict(POLICY, free_minutes=5)
  entry = "2026-11-12T09:00:00+09:00"
  free = calculate_parking_fee_pure(entry, "2026-11-12T09:04:59+09:00", policy)
  assert free["amount_krw"] == 0 and free["free_applied"] is True and free["fee_breakdown"]["free_minutes"] == 5
  assert calculate_parking_fee_pure(entry, "2026-11-12T09:05:00+09:00", policy)["amount_krw"] == 0
  over = calculate_parking_fee_pure(entry, "2026-11-12T09:05:01+09:00", policy)
  assert over["amount_krw"] == 1000 and over["free_applied"] is False
  # 무료 시간을 넘기면 무료분을 빼 주지 않고 원래 요금 그대로
  assert calculate_parking_fee_pure(entry, "2026-11-12T09:55:00+09:00", policy)["amount_krw"] == 2500


def test_no_free_turnaround_without_policy():
  short = calculate_parking_fee_pure("2026-11-12T09:00:00+09:00", "2026-11-12T09:02:00+09:00", POLICY)
  assert short["amount_krw"] == 1000
  # 하루 넘게 주차하면 무료 아님
  day = calculate_parking_fee_pure("2026-11-12T09:00:00+09:00", "2026-11-13T09:03:00+09:00", dict(POLICY, free_minutes=5))
  assert day["amount_krw"] > 0


def test_free_turnaround_env_switch(monkeypatch):
  from backend.app.billing.calculator import free_minutes_for
  monkeypatch.setenv("FREE_TURNAROUND_MINUTES", "0")
  assert [free_minutes_for(lot) for lot in "ABCD"] == [0, 0, 0, 0]
  monkeypatch.setenv("FREE_TURNAROUND_MINUTES", "10")
  assert free_minutes_for("A") == 10 and free_minutes_for("Z") == 0
  monkeypatch.setenv("FREE_TURNAROUND_MINUTES", "abc")
  assert free_minutes_for("A") == 5
