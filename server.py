#!/usr/bin/env python3
"""AI 스마트 주차장 통합관제 시스템 — 윤명수 님 요금·결제·매출 로컬 서버 (FastAPI + SQLite).

[팀원 연동 지원]
  - 같은 Wi-Fi/네트워크 내 다른 팀원(김채린 님 모바일 웹, 박해준 님 서버, 서정범 님 챗봇) 접속 지원 (host="0.0.0.0")
  - 내 PC IP 자동 감지 및 팀원 공유용 URL 콘솔 출력
  - CORS 전면 허용 (다른 포트 및 모바일 기기에서의 호출 완벽 지원)

실행 방법:
  python server.py --port 8080
  (지정한 포트가 사용 중이면 8081, 8082 등 빈 포트로 자동 전환)
"""

from __future__ import annotations

import argparse
import datetime
import json
import math
import os
import socket
import sqlite3
import sys
import tempfile
from typing import Any

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "parking_billing.db")
HTML_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ai_led.html")
KST = datetime.timezone(datetime.timedelta(hours=9))
_ACTIVE_DB_PATH = DB_PATH


def now_kst_iso() -> str:
  return datetime.datetime.now(KST).strftime("%Y-%m-%dT%H:%M:%S+09:00")


def get_local_ip() -> str:
  """내 PC의 로컬 네트워크 IP(LAN IPv4)를 감지합니다."""
  s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
  try:
    s.connect(("8.8.8.8", 80))
    ip = s.getsockname()[0]
  except Exception:
    ip = "127.0.0.1"
  finally:
    s.close()
  return ip


def is_port_in_use(port: int, host: str = "127.0.0.1") -> bool:
  with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
    s.settimeout(0.5)
    return s.connect_ex((host, port)) == 0


def find_available_port(start_port: int, host: str = "127.0.0.1", max_attempts: int = 50) -> int:
  port = start_port
  for _ in range(max_attempts):
    if not is_port_in_use(port, host):
      return port
    port += 1
  return start_port


# ==============================================================================
# 1. SQLite 데이터베이스 초기화 및 시드 데이터 (A: 12면, B: 12면, C: 10면, D: 10면)
# ==============================================================================
def get_db() -> sqlite3.Connection:
  global _ACTIVE_DB_PATH
  try:
    conn = sqlite3.connect(_ACTIVE_DB_PATH)
    conn.execute("PRAGMA journal_mode=MEMORY;")
    conn.execute("PRAGMA synchronous=OFF;")
    conn.execute("SELECT 1;")
  except sqlite3.OperationalError:
    _ACTIVE_DB_PATH = os.path.join(tempfile.gettempdir(), "parking_billing.db")
    conn = sqlite3.connect(_ACTIVE_DB_PATH)
    conn.execute("PRAGMA journal_mode=MEMORY;")
    conn.execute("PRAGMA synchronous=OFF;")
  conn.row_factory = sqlite3.Row
  return conn


def init_db() -> None:
  conn = get_db()
  cur = conn.cursor()

  cur.executescript("""
    CREATE TABLE IF NOT EXISTS parking_lots (
      lot_id TEXT PRIMARY KEY,
      name TEXT NOT NULL,
      type TEXT NOT NULL,
      total_spaces INTEGER NOT NULL DEFAULT 10,
      active INTEGER NOT NULL DEFAULT 1
    );

    CREATE TABLE IF NOT EXISTS rate_policies (
      policy_id TEXT PRIMARY KEY,
      lot_id TEXT NOT NULL UNIQUE,
      effective_at TEXT NOT NULL,
      base_minutes INTEGER NOT NULL,
      base_fee INTEGER NOT NULL,
      unit_minutes INTEGER NOT NULL,
      extra_fee INTEGER NOT NULL,
      daily_cap INTEGER NOT NULL
    );

    CREATE TABLE IF NOT EXISTS parking_sessions (
      session_id TEXT PRIMARY KEY,
      lot_id TEXT NOT NULL,
      space_id TEXT NOT NULL,
      car_id TEXT NOT NULL,
      entry_at TEXT NOT NULL,
      exit_at TEXT,
      status TEXT NOT NULL DEFAULT 'active'
    );

    CREATE TABLE IF NOT EXISTS mock_payments (
      payment_id TEXT PRIMARY KEY,
      session_id TEXT NOT NULL,
      lot_id TEXT NOT NULL,
      car_id TEXT NOT NULL,
      amount_krw INTEGER NOT NULL,
      payment_method TEXT NOT NULL DEFAULT 'MOCK_CARD',
      status TEXT NOT NULL,
      idempotency_key TEXT NOT NULL UNIQUE,
      created_at TEXT NOT NULL,
      canceled_at TEXT,
      refund_reason TEXT
    );

    CREATE TABLE IF NOT EXISTS audit_logs (
      log_id TEXT PRIMARY KEY,
      actor TEXT NOT NULL,
      action TEXT NOT NULL,
      target TEXT NOT NULL,
      created_at TEXT NOT NULL
    );
  """)

  cur.execute("SELECT COUNT(*) FROM parking_lots")
  if cur.fetchone()[0] == 0:
    cur.executemany(
        "INSERT INTO parking_lots (lot_id, name, type, total_spaces, active) VALUES (?, ?, ?, ?, ?)",
        [
            ("A", "실물 A 주차장 (12면)", "real", 12, 1),
            ("B", "실물 B 주차장 (12면)", "real", 12, 1),
            ("C", "가상 C 주차장 (10면)", "virtual", 10, 1),
            ("D", "가상 D 주차장 (10면)", "virtual", 10, 1),
        ],
    )

  cur.execute("SELECT COUNT(*) FROM rate_policies")
  if cur.fetchone()[0] == 0:
    cur.executemany(
        """INSERT INTO rate_policies
           (policy_id, lot_id, effective_at, base_minutes, base_fee, unit_minutes, extra_fee, daily_cap)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        [
            ("POL-LOT-A", "A", "2026-11-01T00:00:00+09:00", 30, 1000, 10, 500, 10000),
            ("POL-LOT-B", "B", "2026-11-01T00:00:00+09:00", 30, 1200, 10, 600, 12000),
            ("POL-LOT-C", "C", "2026-11-01T00:00:00+09:00", 60, 1000, 15, 400, 8000),
            ("POL-LOT-D", "D", "2026-11-01T00:00:00+09:00", 30, 800, 10, 300, 7000),
        ],
    )

  cur.execute("SELECT COUNT(*) FROM parking_sessions")
  if cur.fetchone()[0] == 0:
    cur.executemany(
        """INSERT INTO parking_sessions
           (session_id, lot_id, space_id, car_id, entry_at, exit_at, status)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        [
            ("ses_0001", "A", "A-01", "01", "2026-11-02T09:05:00+09:00", None, "active"),
            ("ses_0002", "A", "A-02", "12", "2026-11-02T09:40:00+09:00", None, "active"),
            ("ses_0003", "B", "B-01", "07", "2026-11-02T08:25:00+09:00", None, "active"),
            ("ses_0004", "C", "C-01", "21", "2026-11-02T05:00:00+09:00", None, "active"),
            ("ses_0005", "B", "B-05", "16", "2026-11-02T09:20:00+09:00", None, "active"),
            ("ses_0006", "D", "D-01", "31", "2026-11-02T08:45:00+09:00", None, "active"),
        ],
    )

  cur.execute("SELECT COUNT(*) FROM mock_payments")
  if cur.fetchone()[0] == 0:
    cur.executemany(
        """INSERT INTO mock_payments
           (payment_id, session_id, lot_id, car_id, amount_krw, payment_method, status, idempotency_key, created_at, canceled_at, refund_reason)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        [
            ("pay_0001", "ses_oct_01", "A", "02", 6000, "MOCK_CARD", "completed", "idem_oct_a02_001", "2026-10-31T15:20:00+09:00", None, None),
            ("pay_0002", "ses_nov_01", "A", "03", 2500, "MOCK_CARD", "completed", "idem_nov_a03_002", "2026-11-01T14:15:00+09:00", None, None),
            ("pay_0003", "ses_nov_02", "B", "09", 4800, "MOCK_EASY_PAY", "completed", "idem_nov_b09_003", "2026-11-02T09:15:00+09:00", None, None),
            ("pay_0004", "ses_nov_03", "C", "19", 3400, "MOCK_ACCOUNT", "canceled", "idem_nov_c19_004", "2026-11-02T09:40:00+09:00", "2026-11-02T09:50:00+09:00", "고객 요청 모의 환불 처리"),
        ],
    )

  conn.commit()
  conn.close()


# ==============================================================================
# 2. 요금 계산 순수 함수
# ==============================================================================
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
  }


# ==============================================================================
# 3. 비즈니스 서비스 레이어
# ==============================================================================
def service_get_session_fee(session_id: str, simulated_now: str | None = None) -> tuple[int, dict[str, Any]]:
  conn = get_db()
  cur = conn.cursor()
  cur.execute("SELECT * FROM parking_sessions WHERE session_id = ?", (session_id,))
  session = cur.fetchone()
  if not session:
    conn.close()
    return 404, {"error_code": "SESSION_NOT_FOUND", "message": f"세션({session_id})을 찾을 수 없습니다."}

  cur.execute("SELECT * FROM rate_policies WHERE lot_id = ?", (session["lot_id"],))
  policy_row = cur.fetchone()
  policy = dict(policy_row) if policy_row else {
      "policy_id": "POL-DEFAULT",
      "base_minutes": 30,
      "base_fee": 1000,
      "unit_minutes": 10,
      "extra_fee": 500,
      "daily_cap": 10000,
  }

  target_iso = session["exit_at"] or simulated_now or now_kst_iso()
  calc = calculate_parking_fee_pure(session["entry_at"], target_iso, policy)

  cur.execute(
      "SELECT status FROM mock_payments WHERE session_id = ? ORDER BY created_at DESC LIMIT 1",
      (session_id,),
  )
  pay_row = cur.fetchone()
  payment_status = pay_row["status"] if pay_row else "unpaid"
  conn.close()

  return 200, {
      "session_id": session["session_id"],
      "lot_id": session["lot_id"],
      "space_id": session["space_id"],
      "car_id": session["car_id"],
      "session_status": session["status"],
      "entry_at": session["entry_at"],
      "exit_at": session["exit_at"],
      "calculated_at": target_iso,
      "duration_minutes": calc["duration_minutes"],
      "fee_breakdown": calc["fee_breakdown"],
      "amount_krw": calc["amount_krw"],
      "is_daily_capped": calc["is_daily_capped"],
      "applied_policy_id": policy["policy_id"],
      "payment_status": payment_status,
  }


def service_post_mock_payment(payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
  session_id = str(payload.get("session_id", "")).strip()
  idempotency_key = str(payload.get("idempotency_key", "")).strip()
  payment_method = str(payload.get("payment_method", "MOCK_CARD")).strip()
  override_amount = payload.get("amount_krw")
  created_at = str(payload.get("created_at") or now_kst_iso())

  if not session_id or not idempotency_key:
    return 400, {"error_code": "INVALID_INPUT", "message": "session_id와 idempotency_key는 필수입니다."}

  conn = get_db()
  cur = conn.cursor()

  cur.execute("SELECT * FROM mock_payments WHERE idempotency_key = ?", (idempotency_key,))
  existing_by_key = cur.fetchone()
  if existing_by_key:
    res = dict(existing_by_key)
    res["idempotent_replayed"] = True
    res["notice"] = "중복 결제 방지 작동: 동일 idempotency_key 요청이므로 기존 결제 레코드를 반환합니다."
    conn.close()
    return 200, res

  cur.execute(
      "SELECT * FROM mock_payments WHERE session_id = ? AND status = 'completed'",
      (session_id,),
  )
  already_paid = cur.fetchone()
  if already_paid:
    conn.close()
    return 409, {
        "error_code": "ALREADY_PAID",
        "message": f"이미 결제가 완료된 차량 세션({session_id})입니다.",
        "existing_payment_id": already_paid["payment_id"],
    }

  status_code, fee_info = service_get_session_fee(session_id, created_at)
  if status_code != 200:
    lot_id = str(payload.get("lot_id", "A"))
    car_id = str(payload.get("car_id", "01"))
    amount_krw = int(override_amount or 1000)
  else:
    lot_id = fee_info["lot_id"]
    car_id = fee_info["car_id"]
    amount_krw = int(override_amount if override_amount is not None else fee_info["amount_krw"])

  cur.execute("SELECT COUNT(*) FROM mock_payments")
  next_seq = cur.fetchone()[0] + 1
  payment_id = f"pay_{next_seq:04d}"

  cur.execute(
      """INSERT INTO mock_payments
         (payment_id, session_id, lot_id, car_id, amount_krw, payment_method, status, idempotency_key, created_at, canceled_at, refund_reason)
         VALUES (?, ?, ?, ?, ?, ?, 'completed', ?, ?, NULL, NULL)""",
      (payment_id, session_id, lot_id, car_id, amount_krw, payment_method, idempotency_key, created_at),
  )

  log_id = f"log_{int(datetime.datetime.now().timestamp() * 1000) % 100000:05d}"
  cur.execute(
      "INSERT OR REPLACE INTO audit_logs (log_id, actor, action, target, created_at) VALUES (?, ?, ?, ?, ?)",
      (log_id, f"user:car_{car_id}", "MOCK_PAYMENT_SAVED", f"{payment_id} ({lot_id}주차장 {amount_krw}원)", created_at),
  )
  conn.commit()

  cur.execute("SELECT * FROM mock_payments WHERE payment_id = ?", (payment_id,))
  saved = dict(cur.fetchone())
  conn.close()
  return 200, saved


def service_cancel_mock_payment(payment_id: str, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
  reason = str(payload.get("refund_reason") or payload.get("reason") or "관리자 모의 환불 및 매출 정정")
  canceled_at = str(payload.get("canceled_at") or now_kst_iso())

  conn = get_db()
  cur = conn.cursor()
  cur.execute("SELECT * FROM mock_payments WHERE payment_id = ?", (payment_id,))
  payment = cur.fetchone()
  if not payment:
    conn.close()
    return 404, {"error_code": "PAYMENT_NOT_FOUND", "message": "결제 내역을 찾을 수 없습니다."}

  if payment["status"] != "completed":
    conn.close()
    return 400, {
        "error_code": "INVALID_PAYMENT_STATE",
        "message": f"현재 상태가 '{payment['status']}'이므로 취소/환불할 수 없습니다.",
    }

  cur.execute(
      "UPDATE mock_payments SET status = 'canceled', canceled_at = ?, refund_reason = ? WHERE payment_id = ?",
      (canceled_at, reason, payment_id),
  )
  log_id = f"log_{int(datetime.datetime.now().timestamp() * 1000) % 100000:05d}"
  cur.execute(
      "INSERT OR REPLACE INTO audit_logs (log_id, actor, action, target, created_at) VALUES (?, ?, ?, ?, ?)",
      (log_id, "admin:operator", "MOCK_REFUND_REVENUE_ADJUSTED", f"{payment_id} (-{payment['amount_krw']}원 매출 정정)", canceled_at),
  )
  conn.commit()
  conn.close()

  return 200, {
      "payment_id": payment_id,
      "session_id": payment["session_id"],
      "lot_id": payment["lot_id"],
      "car_id": payment["car_id"],
      "refunded_amount_krw": payment["amount_krw"],
      "status": "canceled",
      "canceled_at": canceled_at,
      "refund_reason": reason,
      "revenue_adjusted": True,
  }


def service_get_revenue_report(month_filter: str = "ALL") -> tuple[int, dict[str, Any]]:
  conn = get_db()
  cur = conn.cursor()

  cur.execute("SELECT * FROM parking_lots ORDER BY lot_id")
  lots = [dict(r) for r in cur.fetchall()]

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
  conn.close()

  by_day: dict[str, dict[str, Any]] = {}
  by_month: dict[str, dict[str, Any]] = {}

  total_gross = 0
  total_canceled = 0
  total_net = 0
  total_completed_count = 0
  total_canceled_count = 0

  for p in payments:
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


def service_update_rate_policy(lot_id: str, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
  conn = get_db()
  cur = conn.cursor()
  base_minutes = max(1, int(payload.get("base_minutes", 30)))
  base_fee = max(0, int(payload.get("base_fee", 1000)))
  unit_minutes = max(1, int(payload.get("unit_minutes", 10)))
  extra_fee = max(0, int(payload.get("extra_fee", 500)))
  daily_cap = max(1000, int(payload.get("daily_cap", 10000)))

  target_lots = ["A", "B", "C", "D"] if lot_id == "ALL" else [lot_id]
  for lid in target_lots:
    cur.execute(
        """UPDATE rate_policies
           SET base_minutes = ?, base_fee = ?, unit_minutes = ?, extra_fee = ?, daily_cap = ?
           WHERE lot_id = ?""",
        (base_minutes, base_fee, unit_minutes, extra_fee, daily_cap, lid),
    )
  conn.commit()
  cur.execute("SELECT * FROM rate_policies")
  updated = {r["lot_id"]: dict(r) for r in cur.fetchall()}
  conn.close()
  return 200, {"status": "updated", "rate_policies": updated}


# ==============================================================================
# 4. FastAPI 앱 구성 (팀원 연동: CORS 전면 개방)
# ==============================================================================
try:
  from fastapi import Body, FastAPI, Query, Request
  from fastapi.middleware.cors import CORSMiddleware
  from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

  init_db()
  app = FastAPI(
      title="AI 스마트 주차장 — 요금·모의결제·매출 API 서버 (EPIC 05)",
      version="1.1.0",
      description="윤명수 님 담당: 주차장별(A·B 12면 / C·D 10면) 요금 정책, 입출차 요금 계산, 모의 결제/환불, 매출 집계 API",
  )
  app.add_middleware(
      CORSMiddleware,
      allow_origins=["*"],
      allow_credentials=True,
      allow_methods=["*"],
      allow_headers=["*"],
  )

  @app.get("/", response_class=HTMLResponse)
  def serve_index() -> Any:
    if os.path.exists(HTML_PATH):
      return FileResponse(HTML_PATH, media_type="text/html; charset=utf-8")
    return HTMLResponse("<h1>ai_led.html 파일을 찾을 수 없습니다.</h1>", status_code=404)

  @app.get("/api/v1/health")
  def api_health() -> dict[str, Any]:
    return {
        "status": "ok",
        "server": "FastAPI + SQLite3",
        "db_path": os.path.basename(DB_PATH),
        "local_ip": get_local_ip(),
        "timestamp": now_kst_iso(),
    }

  @app.get("/api/v1/sessions/{session_id}/fee")
  def api_session_fee(session_id: str, now: str | None = Query(default=None)) -> JSONResponse:
    code, data = service_get_session_fee(session_id, now)
    return JSONResponse(status_code=code, content=data)

  @app.post("/api/v1/mock-payments")
  def api_create_payment(payload: dict[str, Any] = Body(...)) -> JSONResponse:
    code, data = service_post_mock_payment(payload)
    return JSONResponse(status_code=code, content=data)

  @app.post("/api/v1/mock-payments/{id}/cancel")
  def api_cancel_payment(id: str, payload: dict[str, Any] = Body(default={})) -> JSONResponse:
    code, data = service_cancel_mock_payment(id, payload)
    return JSONResponse(status_code=code, content=data)

  @app.post("/api/v1/mock-payments/{payment_id}/cancel", include_in_schema=False)
  def api_cancel_payment_alias(payment_id: str, payload: dict[str, Any] = Body(default={})) -> JSONResponse:
    code, data = service_cancel_mock_payment(payment_id, payload)
    return JSONResponse(status_code=code, content=data)

  @app.get("/api/v1/reports/revenue")
  def api_revenue_report(month: str = Query(default="ALL")) -> JSONResponse:
    code, data = service_get_revenue_report(month)
    return JSONResponse(status_code=code, content=data)

  @app.put("/api/v1/rate-policies/{lot_id}")
  def api_update_policy(lot_id: str, payload: dict[str, Any] = Body(...)) -> JSONResponse:
    code, data = service_update_rate_policy(lot_id, payload)
    return JSONResponse(status_code=code, content=data)

  HAS_FASTAPI = True
except ImportError:
  HAS_FASTAPI = False


# ==============================================================================
# 5. 파이썬 표준 내장 http.server 폴백 (팀원 연동 CORS 헤더 포함)
# ==============================================================================
def run_builtin_http_server(host: str = "0.0.0.0", port: int = 8080) -> None:
  from http.server import BaseHTTPRequestHandler, HTTPServer
  from urllib.parse import parse_qs, urlparse

  init_db()

  class ParkingRequestHandler(BaseHTTPRequestHandler):
    def _send_json(self, code: int, payload: dict[str, Any]) -> None:
      body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
      self.send_response(code)
      self.send_header("Content-Type", "application/json; charset=utf-8")
      self.send_header("Access-Control-Allow-Origin", "*")
      self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, OPTIONS")
      self.send_header("Access-Control-Allow-Headers", "*")
      self.send_header("Content-Length", str(len(body)))
      self.end_headers()
      self.wfile.write(body)

    def do_OPTIONS(self) -> None:
      self.send_response(204)
      self.send_header("Access-Control-Allow-Origin", "*")
      self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, OPTIONS")
      self.send_header("Access-Control-Allow-Headers", "*")
      self.end_headers()

    def do_GET(self) -> None:
      parsed = urlparse(self.path)
      path = parsed.path
      qs = parse_qs(parsed.query)

      if path in ("/", "/ai_led.html", "/index.html"):
        if os.path.exists(HTML_PATH):
          with open(HTML_PATH, "rb") as f:
            content = f.read()
          self.send_response(200)
          self.send_header("Content-Type", "text/html; charset=utf-8")
          self.send_header("Access-Control-Allow-Origin", "*")
          self.send_header("Content-Length", str(len(content)))
          self.end_headers()
          self.wfile.write(content)
          return
      if path == "/api/v1/health":
        self._send_json(200, {
            "status": "ok",
            "server": "Python Builtin HTTP + SQLite3",
            "local_ip": get_local_ip(),
            "timestamp": now_kst_iso(),
        })
        return
      if path.startswith("/api/v1/sessions/") and path.endswith("/fee"):
        sid = path.split("/")[4]
        now_param = qs.get("now", [None])[0]
        code, data = service_get_session_fee(sid, now_param)
        self._send_json(code, data)
        return
      if path == "/api/v1/reports/revenue":
        month = qs.get("month", ["ALL"])[0]
        code, data = service_get_revenue_report(month)
        self._send_json(code, data)
        return
      self._send_json(404, {"error_code": "NOT_FOUND", "message": f"경로({path})를 찾을 수 없습니다."})

    def do_POST(self) -> None:
      parsed = urlparse(self.path)
      path = parsed.path
      length = int(self.headers.get("Content-Length", "0"))
      raw = self.rfile.read(length).decode("utf-8") if length > 0 else "{}"
      payload = json.loads(raw or "{}")

      if path == "/api/v1/mock-payments":
        code, data = service_post_mock_payment(payload)
        self._send_json(code, data)
        return
      if path.startswith("/api/v1/mock-payments/") and path.endswith("/cancel"):
        pid = path.split("/")[4]
        code, data = service_cancel_mock_payment(pid, payload)
        self._send_json(code, data)
        return
      self._send_json(404, {"error_code": "NOT_FOUND", "message": f"경로({path})를 찾을 수 없습니다."})

    def do_PUT(self) -> None:
      parsed = urlparse(self.path)
      path = parsed.path
      length = int(self.headers.get("Content-Length", "0"))
      raw = self.rfile.read(length).decode("utf-8") if length > 0 else "{}"
      payload = json.loads(raw or "{}")

      if path.startswith("/api/v1/rate-policies/"):
        lid = path.split("/")[4]
        code, data = service_update_rate_policy(lid, payload)
        self._send_json(code, data)
        return
      self._send_json(404, {"error_code": "NOT_FOUND", "message": f"경로({path})를 찾을 수 없습니다."})

  server = HTTPServer((host, port), ParkingRequestHandler)
  local_ip = get_local_ip()
  print(f"\n[내장 HTTP 서버 기동 완료 — 팀원 연동 모드]")
  print(f"• 내 PC(로컬) 접속:     http://localhost:{port}")
  print(f"• 다른 팀원 공유용 접속: http://{local_ip}:{port}")
  print(f"====================================================================\n")
  server.serve_forever()


def parse_arguments() -> tuple[int, bool]:
  parser = argparse.ArgumentParser(description="AI 스마트 주차장 로컬 서버 (팀원 연동 모드)")
  parser.add_argument("-p", "--port", type=int, default=None, help="실행할 포트 번호 (기본: 8080)")
  parser.add_argument("--builtin", action="store_true", help="FastAPI 대신 파이썬 내장 http.server 사용")
  parser.add_argument("positional_port", nargs="?", type=int, default=None, help="포트 번호 (선택 사항)")
  args, _ = parser.parse_known_args()

  port = args.port or args.positional_port
  if port is None:
    env_port = os.environ.get("PORT")
    port = int(env_port) if env_port and env_port.isdigit() else 8080

  use_builtin = args.builtin or ("--builtin" in sys.argv)
  return port, use_builtin


if __name__ == "__main__":
  desired_port, use_builtin = parse_arguments()
  bind_host = "0.0.0.0"  # 외부 팀원 접속 허용 바인딩
  check_host = "127.0.0.1"
  local_ip = get_local_ip()

  actual_port = find_available_port(desired_port, check_host)
  if actual_port != desired_port:
    print(f"\n[알림] 요청하신 {desired_port}번 포트가 이미 사용 중입니다.")
    print(f"       -> 가용한 빈 포트인 {actual_port}번으로 자동 전환하여 기동합니다.\n")

  if use_builtin or not HAS_FASTAPI:
    run_builtin_http_server(bind_host, actual_port)
  else:
    import uvicorn
    print(f"\n====================================================================")
    print(f"[AI 스마트 주차장 FastAPI 로컬 서버 기동 완료 — 팀원 연동 모드]")
    print(f"• 내 PC(로컬) 접속:     http://localhost:{actual_port}")
    print(f"• 다른 팀원 공유용 접속: http://{local_ip}:{actual_port}")
    print(f"• Swagger API 문서:    http://{local_ip}:{actual_port}/docs")
    print(f"• 서버 상태(Health):   http://{local_ip}:{actual_port}/api/v1/health")
    print(f"====================================================================")
    print(f"💡 같은 Wi-Fi/공유기에 연결된 김채린 님(모바일/웹 UI), 박해준 님(서버),")
    print(f"   서정범 님(LLM 챗봇)에게 위 '공유용 접속' IP 주소를 전달하세요!\n")
    uvicorn.run(app, host=bind_host, port=actual_port)
