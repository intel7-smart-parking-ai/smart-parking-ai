"""
매출 집계 및 통계 서비스 (Revenue Aggregation Service)
담당자: 윤명수
"""

from __future__ import annotations
import sqlite3
from typing import Any

from .calculator import free_minutes_for
from .db import KST_PAYMENT_HOUR_SQL, demo_history_cutoff
from backend.app.simulation.demo_history import metadata, monthly_usage

def _stored_demo_usage(conn, month, lot, cutoff):
  if not conn.execute(
      "SELECT 1 FROM sqlite_schema WHERE type='table' AND name='demo_hourly_usage'"
  ).fetchone():
    return [], None

  params = (month, lot, lot, cutoff, cutoff)
  rows = conn.execute(
      """SELECT u.lot_id, c.day, substr(u.hour_kst, 12, 2) AS hour,
                c.weekday, c.is_weekend, c.is_holiday, u.entries,
                u.occupied_seconds AS occupied, u.capacity_seconds AS capacity
         FROM demo_hourly_usage u JOIN demo_calendar c ON c.day = substr(u.hour_kst, 1, 10)
         WHERE substr(u.hour_kst, 1, 7) = ? AND (? IS NULL OR u.lot_id = ?)
           AND (? IS NULL OR datetime(u.hour_kst, '+1 hour') <= datetime(?))
         ORDER BY c.day, hour""",
      params,
  ).fetchall()
  if not rows:
    return [], None

  stays = conn.execute(
      """WITH stays AS (
           SELECT round((julianday(s.exit_at) - julianday(s.entry_at)) * 86400) AS seconds
           FROM parking_sessions s JOIN demo_session_details d USING(session_id)
           WHERE substr(s.entry_at, 1, 7) = ? AND (? IS NULL OR s.lot_id = ?)
             AND s.status='closed' AND s.exit_at > s.entry_at
             AND (? IS NULL OR julianday(s.exit_at) <= julianday(?))
         )
         SELECT avg(seconds)/60 AS average,
                count(CASE WHEN seconds < 3600 THEN 1 END) AS short,
                count(CASE WHEN seconds >= 3600 AND seconds < 10800 THEN 1 END) AS medium,
                count(CASE WHEN seconds >= 10800 AND seconds < 86400 THEN 1 END) AS long,
                count(CASE WHEN seconds >= 86400 THEN 1 END) AS overnight
         FROM stays""",
      params,
  ).fetchone()
  return rows, stays


def _demo_usage(conn, month, lot, revenue_by_day, cutoff, heartbeat):
  if month == "ALL":
    return None
  rows, stays = (monthly_usage(conn, month, lot, cutoff, heartbeat) if heartbeat
                 else _stored_demo_usage(conn, month, lot, cutoff))
  if not rows:
    return None

  def bucket(groups, key, row):
    totals = groups.setdefault(key, {"entries": 0, "occupied": 0, "capacity": 0})
    for name in totals:
      totals[name] += row[name]
    return totals

  def occupancy(totals):
    return round(100 * totals["occupied"] / totals["capacity"], 1)

  day_groups, hour_groups, type_groups, heatmaps = {}, {}, {}, {}
  day_types = {}
  for row in rows:
    day = row["day"]
    kind = "holiday" if row["is_holiday"] else "weekend" if row["is_weekend"] else "weekday"
    day_types[day] = kind
    bucket(day_groups, day, row)
    bucket(hour_groups, row["hour"], row)
    bucket(type_groups, kind, row)
    bucket(heatmaps.setdefault(row["lot_id"], {}), (row["weekday"], row["hour"]), row)

  daily = [{"day": day, "entries": totals["entries"], "occupancy_pct": occupancy(totals)}
           for day, totals in day_groups.items()]
  comparisons = []
  for kind in ("weekday", "weekend", "holiday"):
    dates = [day for day, day_type in day_types.items() if day_type == kind]
    if dates:
      totals = type_groups[kind]
      comparisons.append({"kind": kind, "days": len(dates),
                          "daily_entries": round(totals["entries"] / len(dates), 1),
                          "daily_net_krw": round(sum(revenue_by_day.get(day, {}).get("net_krw", 0)
                                                     for day in dates) / len(dates)),
                          "occupancy_pct": occupancy(totals)})
  return {
      "source": "demo_and_observed_history" if heartbeat else "synthetic_history",
      "first_day": daily[0]["day"],
      "last_day": daily[-1]["day"],
      "days_covered": len(daily),
      "entries": sum(row["entries"] for row in rows),
      "avg_stay_minutes": round(stays["average"], 1) if stays["average"] is not None else None,
      "avg_occupancy_pct": round(100 * sum(row["occupied"] for row in rows) /
                                sum(row["capacity"] for row in rows), 1),
      "daily": daily,
      "hourly": [{"hour": hour, "entries": totals["entries"], "occupancy_pct": occupancy(totals)}
                 for hour, totals in sorted(hour_groups.items())],
      "day_type_comparison": comparisons,
      "stay_distribution": {name: stays[name] for name in ("short", "medium", "long", "overnight")},
      "heatmaps": {lot_id: [{"weekday": weekday, "hour": hour, "occupancy_pct": occupancy(totals)}
                           for (weekday, hour), totals in sorted(cells.items())]
                   for lot_id, cells in sorted(heatmaps.items())},
  }

def generate_revenue_report(
    conn: sqlite3.Connection, month_filter: str = "ALL", lot_filter: str | None = None
) -> tuple[int, dict[str, Any]]:
  cutoff = demo_history_cutoff(conn)
  demo_metadata_present = conn.execute(
      "SELECT 1 FROM sqlite_schema WHERE type='table' AND name='demo_metadata'"
  ).fetchone()
  heartbeat = metadata(conn, 'runtime_heartbeat_kst') if demo_metadata_present else None
  has_demo_history = bool(demo_metadata_present and metadata(conn, 'source'))
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
        "policy_summary": f"기본 {pol['base_minutes']}분 {pol['base_fee']:,}원 / {pol['unit_minutes']}분당 +{pol['extra_fee']:,}원 / 상한 {pol['daily_cap']:,}원"
                          + (f" / {free_minutes_for(lid)}분 이내 무료" if free_minutes_for(lid) else ""),
        "gross_amount_krw": 0,
        "canceled_amount_krw": 0,
        "net_revenue_krw": 0,
        "completed_count": 0,
        "canceled_count": 0,
    }

  cur.execute(
      f"""SELECT lot_id, {KST_PAYMENT_HOUR_SQL} AS payment_hour, status,
                sum(amount_krw) AS amount_krw, count(*) AS payment_count
         FROM mock_payments
         WHERE (? IS NULL OR lot_id = ?)
           AND amount_krw > 0  -- 무료 회차 0원 결제는 매출 건수에 넣지 않는다
           AND (? IS NULL OR session_id NOT GLOB 'demo-*' OR julianday(created_at) <= julianday(?))
         GROUP BY lot_id, payment_hour, status""",
      (lot_filter, lot_filter, cutoff, cutoff),
  )
  payments = cur.fetchall()

  by_day: dict[str, dict[str, Any]] = {}
  by_month: dict[str, dict[str, Any]] = {}
  by_hour: dict[str, dict[str, Any]] = {}

  total_gross = 0
  total_canceled = 0
  total_net = 0
  total_completed_count = 0
  total_canceled_count = 0

  for p in payments:
    hour = p["payment_hour"]
    if hour is None:
      continue
    ymd = hour[:10]
    ym = ymd[:7]
    amt = int(p["amount_krw"])
    count = p["payment_count"]

    if ym not in by_month:
      by_month[ym] = {"month": ym, "gross_krw": 0, "canceled_krw": 0, "net_krw": 0, "completed_count": 0, "canceled_count": 0}
    if p["status"] == "completed":
      by_month[ym]["gross_krw"] += amt
      by_month[ym]["net_krw"] += amt
      by_month[ym]["completed_count"] += count
    elif p["status"] == "canceled":
      by_month[ym]["gross_krw"] += amt
      by_month[ym]["canceled_krw"] += amt
      by_month[ym]["canceled_count"] += count

    if month_filter != "ALL" and ym != month_filter:
      continue

    if ymd not in by_day:
      by_day[ymd] = {"date": ymd, "gross_krw": 0, "canceled_krw": 0, "net_krw": 0, "completed_count": 0, "canceled_count": 0}

    if month_filter != "ALL":
      bucket = by_hour.setdefault(hour, {"hour": hour, "gross_krw": 0, "canceled_krw": 0,
                                         "net_krw": 0, "completed_count": 0, "canceled_count": 0})
      if p["status"] in ("completed", "canceled"):
        bucket["gross_krw"] += amt
        if p["status"] == "completed":
          bucket["net_krw"] += amt
          bucket["completed_count"] += count
        else:
          bucket["canceled_krw"] += amt
          bucket["canceled_count"] += count

    lot_bucket = by_lot.get(p["lot_id"])
    if p["status"] == "completed":
      if lot_bucket:
        lot_bucket["gross_amount_krw"] += amt
        lot_bucket["net_revenue_krw"] += amt
        lot_bucket["completed_count"] += count
      by_day[ymd]["gross_krw"] += amt
      by_day[ymd]["net_krw"] += amt
      by_day[ymd]["completed_count"] += count
      total_gross += amt
      total_net += amt
      total_completed_count += count
    elif p["status"] == "canceled":
      if lot_bucket:
        lot_bucket["gross_amount_krw"] += amt
        lot_bucket["canceled_amount_krw"] += amt
        lot_bucket["canceled_count"] += count
      by_day[ymd]["gross_krw"] += amt
      by_day[ymd]["canceled_krw"] += amt
      by_day[ymd]["canceled_count"] += count
      total_gross += amt
      total_canceled += amt
      total_canceled_count += count

  return 200, {
      "filter_month": month_filter,
      "usage": _demo_usage(conn, month_filter, lot_filter, by_day, cutoff, heartbeat),
      "demo_cutoff": heartbeat or cutoff,
      "has_demo_history": has_demo_history,
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
      "hourly_revenue": sorted(by_hour.values(), key=lambda x: x["hour"]),
      "monthly_revenue": sorted(by_month.values(), key=lambda x: x["month"], reverse=True),
  }
