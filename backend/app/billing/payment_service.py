"""
모의 결제 처리 및 상태 머신 서비스 (Mock Payment Service)
담당자: 윤명수
"""

from __future__ import annotations
import datetime
import sqlite3
from typing import Any

KST = datetime.timezone(datetime.timedelta(hours=9))

def now_kst_iso() -> str:
  return datetime.datetime.now(KST).strftime("%Y-%m-%dT%H:%M:%S+09:00")

def process_mock_payment(conn: sqlite3.Connection, payload: dict[str, Any], fee_calculator_fn) -> tuple[int, dict[str, Any]]:
  session_id = str(payload.get("session_id", "")).strip()
  idempotency_key = str(payload.get("idempotency_key", "")).strip()
  payment_method = str(payload.get("payment_method", "MOCK_CARD")).strip()
  override_amount = payload.get("amount_krw")
  created_at = str(payload.get("created_at") or now_kst_iso())

  if not session_id or not idempotency_key:
    return 400, {"error_code": "INVALID_INPUT", "message": "session_id와 idempotency_key는 필수입니다."}

  cur = conn.cursor()
  cur.execute("SELECT * FROM mock_payments WHERE idempotency_key = ?", (idempotency_key,))
  row = cur.fetchone()
  if row:
    res = dict(row)
    res["idempotent_replayed"] = True
    res["notice"] = "중복 결제 방지 작동: 동일 멱등키 요청이므로 기존 결제 결과를 반환합니다."
    return 200, res

  cur.execute("SELECT * FROM mock_payments WHERE session_id = ? AND status = 'completed'", (session_id,))
  already_paid = cur.fetchone()
  if already_paid:
    return 409, {
        "error_code": "ALREADY_PAID",
        "message": f"이미 결제 완료된 주차 세션({session_id})입니다.",
        "existing_payment_id": already_paid["payment_id"],
    }

  status_code, fee_info = fee_calculator_fn(session_id, created_at)
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
  return 200, saved

def cancel_mock_payment(conn: sqlite3.Connection, payment_id: str, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
  reason = str(payload.get("refund_reason") or payload.get("reason") or "관리자 모의 환불")
  canceled_at = str(payload.get("canceled_at") or now_kst_iso())

  cur = conn.cursor()
  cur.execute("SELECT * FROM mock_payments WHERE payment_id = ?", (payment_id,))
  payment = cur.fetchone()
  if not payment:
    return 404, {"error_code": "PAYMENT_NOT_FOUND", "message": "결제 내역을 찾을 수 없습니다."}

  if payment["status"] != "completed":
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
