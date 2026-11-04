"""
매출 집계 및 통계 서비스 (Revenue Aggregation Service)
담당자: 윤명수
"""

from __future__ import annotations
import sqlite3
from typing import Any

def generate_revenue_report(
    conn: sqlite3.Connection, month_filter: str = "ALL", lot_filter: str | None = None
) -> tuple[int, dict[str, Any]]:
  cur = conn.cursor()
  cur.execute("SELECT * FROM parking_lots ORDER BY lot_id")
  lots = [dict(r) for r in cur.fetchall()]
  # 주차장 필터: 지정하면 그 주차장 결제만 집계한다
  if lot_filter is not None:
    if lot_filter not in {lot["lot_id"] for lot in lots}:
      return 404, {"error_code": "PARKING_LOT_NOT_FOUND", "message": f"주차장({lot_filter})을 찾을 수 없습니다."}
    lots = [lot for lot in lots if lot["lot_id"] == lot_filter]

  cur.execute("SELECT * FROM rate_policies")
  policies = {r["lot_id"]: dict(r) for r in cur.fetchall()}

  by_lot: dict[str, dict[str, Any]] = {}
  for lot in lots:
    lid = lot["lot_id"]
    pol = policies.get(lid, {"base_minutes": 30, "base_fee": 1000, "unit_minutes": 10, "extra_fee": 500, "daily_cap": 10000})
    by_lot[lid] = {
        "lot_id": lid,
        "name": lot["name"],
        "type": lot["type"],
        "total_spaces": lot.get("total_spaces", 10),
        "policy_summary": f"기본 {pol['base_minutes']}분 {pol['base_fee']:,}원 / {pol['unit_minutes']}분당 +{pol['extra_fee']:,}원 / 상한 {pol['daily_cap']:,}원",
        "gross_amount_krw": 0,
        "canceled_amount_krw": 0,
        "net_revenue_krw": 0,
        "completed_count": 0,
        "canceled_count": 0,
    }

  cur.execute("SELECT * FROM mock_payments ORDER BY created_at DESC")
  payments = [dict(r) for r in cur.fetchall()]

  by_day: dict[str, dict[str, Any]] = {}
  by_month: dict[str, dict[str, Any]] = {}

  total_gross = 0
  total_canceled = 0
  total_net = 0
  total_completed_count = 0
  total_canceled_count = 0

  for p in payments:
    if lot_filter is not None and p["lot_id"] != lot_filter:
      continue
    ym = p["created_at"][:7]
    ymd = p["created_at"][:10]
    amt = int(p["amount_krw"])

    if ym not in by_month:
      by_month[ym] = {"month": ym, "gross_krw": 0, "canceled_krw": 0, "net_krw": 0, "completed_count": 0, "canceled_count": 0}
    if p["status"] == "completed":
      by_month[ym]["gross_krw"] += amt
      by_month[ym]["net_krw"] += amt
      by_month[ym]["completed_count"] += 1
    elif p["status"] == "canceled":
      by_month[ym]["gross_krw"] += amt
      by_month[ym]["canceled_krw"] += amt
      by_month[ym]["canceled_count"] += 1

    if month_filter != "ALL" and ym != month_filter:
      continue

    if ymd not in by_day:
      by_day[ymd] = {"date": ymd, "gross_krw": 0, "canceled_krw": 0, "net_krw": 0, "completed_count": 0, "canceled_count": 0}

    lot_bucket = by_lot.get(p["lot_id"])
    if p["status"] == "completed":
      if lot_bucket:
        lot_bucket["gross_amount_krw"] += amt
        lot_bucket["net_revenue_krw"] += amt
        lot_bucket["completed_count"] += 1
      by_day[ymd]["gross_krw"] += amt
      by_day[ymd]["net_krw"] += amt
      by_day[ymd]["completed_count"] += 1
      total_gross += amt
      total_net += amt
      total_completed_count += 1
    elif p["status"] == "canceled":
      if lot_bucket:
        lot_bucket["gross_amount_krw"] += amt
        lot_bucket["canceled_amount_krw"] += amt
        lot_bucket["canceled_count"] += 1
      by_day[ymd]["gross_krw"] += amt
      by_day[ymd]["canceled_krw"] += amt
      by_day[ymd]["canceled_count"] += 1
      total_gross += amt
      total_canceled += amt
      total_canceled_count += 1

  return 200, {
      "filter_month": month_filter,
      "lot_id": lot_filter,
      "aggregated_basis": "mock_payments.created_at",
      "summary": {
          "gross_amount_krw": total_gross,
          "canceled_amount_krw": total_canceled,
          "net_revenue_krw": total_net,
          "completed_count": total_completed_count,
          "canceled_count": total_canceled_count,
          "by_type": {
              "real_net_krw": (by_lot.get("A", {}).get("net_revenue_krw", 0) + by_lot.get("B", {}).get("net_revenue_krw", 0)),
              "virtual_net_krw": (by_lot.get("C", {}).get("net_revenue_krw", 0) + by_lot.get("D", {}).get("net_revenue_krw", 0)),
          },
      },
      "lots": list(by_lot.values()),
      "daily_revenue": sorted(by_day.values(), key=lambda x: x["date"], reverse=True),
      "monthly_revenue": sorted(by_month.values(), key=lambda x: x["month"], reverse=True),
  }
