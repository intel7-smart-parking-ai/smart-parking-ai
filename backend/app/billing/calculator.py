"""
요금 계산 순수 함수 모듈 (Billing Pure Calculator)
담당자: 윤명수

주차장별 요금정책(기본시간/기본료/추가단위/추가요금/일일최대요금)을 적용하여
입·출차 시간 기반 주차요금을 1원의 오차 없이 정수 KRW로 산출합니다.
"""

from __future__ import annotations
import datetime
import math
import os
from typing import Any

# 무료 회차(분): 입차 후 이 시간 이내에 나가면 0원. A·B·C·D 모두 5분(11/12 윤명수 결정).
# DB 스키마는 바꾸지 않고 코드 상수로 둔다. 목록에 없는 주차장은 0분(무료 없음).
# 과거 매출 이력 생성(simulation/demo_history)은 DB 정책만 써서 영향 없음.
FREE_TURNAROUND_MINUTES: dict[str, int] = {"A": 5, "B": 5, "C": 5, "D": 5}


def free_minutes_for(lot_id: str | None) -> int:
  """주차장의 무료 회차 분. 환경변수 FREE_TURNAROUND_MINUTES(정수 0 이상)가 있으면 그 값으로 덮는다.
  0이면 무료 회차를 끈다(시연 중 바로 끌 수 있는 스위치). 잘못된 값은 무시하고 기본값을 쓴다."""
  default = FREE_TURNAROUND_MINUTES.get(lot_id or "", 0)
  raw = os.environ.get("FREE_TURNAROUND_MINUTES", "").strip()
  if default and raw.isdigit():
    return int(raw)
  return default


def parse_iso_timestamp(ts: str) -> datetime.datetime:
  if ts.endswith("Z"):
    ts = ts[:-1] + "+00:00"
  return datetime.datetime.fromisoformat(ts)

def calculate_parking_fee_pure(
    entry_iso: str, target_iso: str, policy: dict[str, Any]
) -> dict[str, Any]:
  entry_dt = parse_iso_timestamp(entry_iso)
  target_dt = parse_iso_timestamp(target_iso)
  diff_seconds = max(0.0, (target_dt - entry_dt).total_seconds())
  duration_minutes = max(1, math.ceil(diff_seconds / 60.0))

  base_minutes = int(policy.get("base_minutes", 30))
  base_fee = int(policy.get("base_fee", 1000))
  unit_minutes = max(1, int(policy.get("unit_minutes", 10)))
  extra_fee = int(policy.get("extra_fee", 500))
  daily_cap = int(policy.get("daily_cap", 10000))
  free_minutes = max(0, int(policy.get("free_minutes", 0) or 0))

  if free_minutes and duration_minutes <= free_minutes:
    # 무료 회차: 요금 0원. 결제 상태는 그대로(새 상태를 만들지 않는다).
    return {
        "duration_minutes": duration_minutes,
        "fee_breakdown": {
            "base_fee_krw": 0,
            "extra_units": 0,
            "extra_fee_krw": 0,
            "daily_cap_krw": daily_cap,
            "cap_discount_krw": 0,
            "free_minutes": free_minutes,
        },
        "amount_krw": 0,
        "is_daily_capped": False,
        "free_applied": True,
    }

  minutes_per_day = 1440
  full_days = duration_minutes // minutes_per_day
  rem_minutes = duration_minutes % minutes_per_day

  extra_units = 0
  extra_fee_total = 0
  uncapped_today_fee = 0
  rem_fee = 0

  if rem_minutes > 0 or full_days == 0:
    if rem_minutes <= base_minutes:
      uncapped_today_fee = base_fee
    else:
      extra_units = math.ceil((rem_minutes - base_minutes) / unit_minutes)
      extra_fee_total = extra_units * extra_fee
      uncapped_today_fee = base_fee + extra_fee_total
    rem_fee = min(uncapped_today_fee, daily_cap)

  raw_uncapped_total = (full_days * max(daily_cap, base_fee)) + uncapped_today_fee
  total_fee = (full_days * daily_cap) + rem_fee
  discount_by_cap = max(0, raw_uncapped_total - total_fee)

  return {
      "duration_minutes": duration_minutes,
      "fee_breakdown": {
          "base_fee_krw": base_fee,
          "extra_units": extra_units,
          "extra_fee_krw": extra_fee_total,
          "daily_cap_krw": daily_cap,
          "cap_discount_krw": discount_by_cap,
      },
      "amount_krw": total_fee,
      "is_daily_capped": (rem_fee >= daily_cap) or (full_days > 0),
      "free_applied": False,
  }
