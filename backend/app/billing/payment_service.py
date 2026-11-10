"""
모의 결제 처리 및 상태 머신 서비스 (Mock Payment Service)
담당자: 윤명수
"""

from __future__ import annotations
import datetime
import os
import sqlite3
import uuid
from typing import Any

KST = datetime.timezone(datetime.timedelta(hours=9))

from .db import KST_PAYMENT_HOUR_SQL, demo_history_cutoff

def now_kst_iso() -> str:
  return datetime.datetime.now(KST).strftime("%Y-%m-%dT%H:%M:%S+09:00")

def _new_id(prefix: str) -> str:
  return f"{prefix}_{uuid.uuid4().hex[:12]}"

DEFAULT_QUOTE_TTL_SECONDS = 300

def quote_ttl_seconds() -> int:
  """견적 유효 시간(초). 환경변수 FEE_QUOTE_TTL_SECONDS로 조정, 기본 300초."""
  try:
    return int(os.getenv("FEE_QUOTE_TTL_SECONDS", DEFAULT_QUOTE_TTL_SECONDS))
  except ValueError:
    return DEFAULT_QUOTE_TTL_SECONDS
REUSE_MIN_REMAINING_SECONDS = 150
def create_fee_quote(conn: sqlite3.Connection, session_id: str, amount_krw: int, calculated_at: str) -> dict[str, str]:
  """요금 조회 결과를 견적으로 저장한다. 결제는 이 금액으로만 처리된다."""
  now = datetime.datetime.now(KST)
  floor = (now + datetime.timedelta(seconds=REUSE_MIN_REMAINING_SECONDS)).isoformat(timespec="seconds")
  row = conn.execute(
      "SELECT quote_id, expires_at FROM fee_quotes WHERE session_id = ? AND amount_krw = ? AND expires_at > ? "
      "ORDER BY expires_at DESC LIMIT 1", (session_id, int(amount_krw), floor)).fetchone()
  if row:
    return {"quote_id": row[0], "quote_expires_at": row[1]}
  created_at = now.isoformat(timespec="seconds")
  expires_at = (now + datetime.timedelta(seconds=quote_ttl_seconds())).isoformat(timespec="seconds")
  quote_id = _new_id("quote")
  conn.execute(
      "INSERT INTO fee_quotes (quote_id, session_id, amount_krw, calculated_at, created_at, expires_at) VALUES (?, ?, ?, ?, ?, ?)",
      (quote_id, session_id, int(amount_krw), calculated_at, created_at, expires_at),
  )
  conn.commit()
  return {"quote_id": quote_id, "quote_expires_at": expires_at}

def _check_quote(cur: sqlite3.Cursor, quote_id: str, session_id: str) -> tuple[tuple[int, dict[str, Any]] | None, dict[str, Any] | None]:
  """견적 검증. (오류, None) 또는 (None, 견적 행)."""
  cur.execute("SELECT * FROM fee_quotes WHERE quote_id = ?", (quote_id,))
  row = cur.fetchone()
  if not row:
    return (404, {"error_code": "QUOTE_NOT_FOUND", "message": "요금 견적을 찾을 수 없습니다. 요금을 다시 조회해 주세요."}), None
  if row["session_id"] != session_id:
    return (409, {"error_code": "QUOTE_MISMATCH", "message": "다른 주차 세션의 요금 견적입니다. 요금을 다시 조회해 주세요."}), None
  if datetime.datetime.now(KST) >= datetime.datetime.fromisoformat(row["expires_at"]):
    return (409, {"error_code": "QUOTE_EXPIRED", "message": "요금 견적이 만료되었습니다. 요금을 다시 조회해 주세요.",
                  "quote_expires_at": row["expires_at"]}), None
  return None, dict(row)

def _find_by_idempotency_key(cur: sqlite3.Cursor, idempotency_key: str, session_id: str) -> tuple[int, dict[str, Any]] | None:
  """같은 멱등키가 있으면 (같은 세션: 200 기존 결제 / 다른 세션: 409) 반환."""
  cur.execute("SELECT * FROM mock_payments WHERE idempotency_key = ?", (idempotency_key,))
  row = cur.fetchone()
  if not row:
    return None
  if row["session_id"] != session_id:
    return 409, {
        "error_code": "IDEMPOTENCY_CONFLICT",
        "message": "다른 주차 세션에 이미 사용된 결제 요청 키입니다.",
    }
  res = dict(row)
  res["idempotent_replayed"] = True
  res["notice"] = "중복 결제 방지 작동: 동일 멱등키 요청이므로 기존 결제 결과를 반환합니다."
  return 200, res


def list_mock_payments(
    conn: sqlite3.Connection, lot_id: str | None = None, status: str | None = None, car_id: str | None = None,
    *, limit: int | None = None, payment_day: str | None = None,
) -> list[dict[str, Any]]:
  """결제 목록 (최신순). 필터는 모두 선택."""
  cutoff = demo_history_cutoff(conn)
  cur = conn.cursor()
  cur.execute(
      f"""SELECT payment.*, plate.plate_number
         FROM mock_payments AS payment
         LEFT JOIN session_vehicle_plates AS plate ON plate.session_id = payment.session_id
         WHERE (? IS NULL OR payment.lot_id = ?) AND (? IS NULL OR payment.status = ?)
           AND (? IS NULL OR payment.car_id = ?)
           AND (? IS NULL OR substr({KST_PAYMENT_HOUR_SQL}, 1, 10) = ?)
           AND (? IS NULL OR payment.session_id NOT GLOB 'demo-*'
                OR julianday(payment.created_at) <= julianday(?))
         ORDER BY julianday(payment.created_at) DESC, payment.payment_id LIMIT ?""",
      (lot_id, lot_id, status, status, car_id, car_id, payment_day, payment_day, cutoff, cutoff, -1 if limit is None else limit),
  )
  return [dict(r) for r in cur.fetchall()]

def _already_paid(cur: sqlite3.Cursor, session_id: str) -> tuple[int, dict[str, Any]] | None:
  cur.execute("SELECT payment_id FROM mock_payments WHERE session_id = ? AND status = 'completed'", (session_id,))
  row = cur.fetchone()
  if not row:
    return None
  return 409, {
      "error_code": "ALREADY_PAID",
      "message": f"이미 결제 완료된 주차 세션({session_id})입니다.",
      "existing_payment_id": row["payment_id"],
  }

def process_mock_payment(conn: sqlite3.Connection, payload: dict[str, Any], fee_calculator_fn) -> tuple[int, dict[str, Any]]:
  """모의 결제. 금액은 요금 조회 때 서버가 저장한 견적 금액만 사용한다(클라이언트 금액·시각 입력 없음)."""
  session_id = str(payload.get("session_id", "")).strip()
  quote_id = str(payload.get("quote_id", "")).strip()
  idempotency_key = str(payload.get("idempotency_key", "")).strip()
  payment_method = str(payload.get("payment_method", "MOCK_CARD")).strip()
  created_at = now_kst_iso()

  if not session_id or not idempotency_key or not quote_id:
    return 400, {"error_code": "INVALID_INPUT", "message": "session_id, quote_id, idempotency_key는 필수입니다."}

  cur = conn.cursor()
  # 멱등키가 먼저: 네트워크 재시도는 견적이 만료됐어도 기존 결제를 그대로 돌려준다.
  replay = _find_by_idempotency_key(cur, idempotency_key, session_id)
  if replay:
    return replay

  conflict = _already_paid(cur, session_id)
  if conflict:
    return conflict

  quote_error, quote = _check_quote(cur, quote_id, session_id)
  if quote_error:
    return quote_error

  status_code, fee_info = fee_calculator_fn(session_id, created_at)
  if status_code != 200:
    # 없는 세션 등: 결제를 만들지 않고 그대로 오류 반환
    return status_code, fee_info

  lot_id = fee_info["lot_id"]
  car_id = fee_info["car_id"]  # 미인식이면 None
  amount_krw = int(quote["amount_krw"])  # 요금 화면에 보인 견적 금액 (결제 시점 재계산 아님)
  payment_id = _new_id("pay")

  try:
    cur.execute(
        """INSERT INTO mock_payments
           (payment_id, session_id, lot_id, car_id, amount_krw, payment_method, status, idempotency_key, created_at, canceled_at, refund_reason)
           VALUES (?, ?, ?, ?, ?, ?, 'completed', ?, ?, NULL, NULL)""",
        (payment_id, session_id, lot_id, car_id, amount_krw, payment_method, idempotency_key, created_at),
    )
    cur.execute(
        "INSERT INTO audit_logs (log_id, actor, action, target, created_at) VALUES (?, ?, ?, ?, ?)",
        (_new_id("log"), f"user:car_{car_id or 'unknown'}", "MOCK_PAYMENT_SAVED",
         f"{payment_id} ({lot_id}주차장 {amount_krw}원)", created_at),
    )
    conn.commit()
  except sqlite3.IntegrityError:
    # 동시 요청 경합: 같은 멱등키 또는 같은 세션의 완료 결제가 먼저 들어간 경우
    conn.rollback()
    replay = _find_by_idempotency_key(cur, idempotency_key, session_id)
    if replay:
      return replay
    return _already_paid(cur, session_id) or (409, {"error_code": "PAYMENT_CONFLICT", "message": "결제 처리 중 충돌이 발생했습니다."})

  cur.execute("SELECT * FROM mock_payments WHERE payment_id = ?", (payment_id,))
  saved = dict(cur.fetchone())
  return 200, saved

def cancel_mock_payment(conn: sqlite3.Connection, payment_id: str, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
  reason = str(payload.get("refund_reason") or payload.get("reason") or "관리자 모의 환불")
  canceled_at = now_kst_iso()

  cur = conn.cursor()
  cur.execute("SELECT * FROM mock_payments WHERE payment_id = ?", (payment_id,))
  payment = cur.fetchone()
  if not payment:
    return 404, {"error_code": "PAYMENT_NOT_FOUND", "message": "결제 내역을 찾을 수 없습니다."}

  if payment["status"] != "completed":
    return 409, {
        "error_code": "INVALID_PAYMENT_STATE",
        "message": f"현재 상태가 '{payment['status']}'이므로 취소/환불할 수 없습니다.",
    }

  cur.execute(
      "UPDATE mock_payments SET status = 'canceled', canceled_at = ?, refund_reason = ? WHERE payment_id = ?",
      (canceled_at, reason, payment_id),
  )
  cur.execute(
      "INSERT INTO audit_logs (log_id, actor, action, target, created_at) VALUES (?, ?, ?, ?, ?)",
      (_new_id("log"), "admin:operator", "MOCK_REFUND_REVENUE_ADJUSTED", f"{payment_id} (-{payment['amount_krw']}원 매출 정정)", canceled_at),
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
