"""요금·결제 공식 라우터 모듈 (FastAPI 통합용)."""

from __future__ import annotations
import sqlite3
from typing import Any, Optional
from fastapi import APIRouter, Body, Depends, Path, Query
from fastapi.responses import JSONResponse

from backend.app.billing.calculator import calculate_parking_fee_pure
from backend.app.billing.payment_service import process_mock_payment, cancel_mock_payment
from backend.app.billing.revenue_service import generate_revenue_report

billing_router = APIRouter(prefix="/api/v1", tags=["4. 윤명수 — 요금·결제·매출"])

# 팀 공용 DB 세션 의존성 (박해준 님 DB 연결 함수가 없을 경우 기본 sqlite3 연결 사용)
def get_db_conn():
    conn = sqlite3.connect("parking_billing.db")
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def _calculate_fee_helper(conn: sqlite3.Connection, session_id: str, simulated_now: Optional[str] = None) -> tuple[int, dict[str, Any]]:
    cur = conn.cursor()
    cur.execute("SELECT * FROM parking_sessions WHERE session_id = ?", (session_id,))
    session = cur.fetchone()
    if not session:
        return 404, {"error_code": "SESSION_NOT_FOUND", "message": f"세션({session_id})을 찾을 수 없습니다."}

    cur.execute("SELECT * FROM rate_policies WHERE lot_id = ?", (session["lot_id"],))
    policy_row = cur.fetchone()
    policy = dict(policy_row) if policy_row else {
        "base_minutes": 30, "base_fee": 1000, "unit_minutes": 10, "extra_fee": 500, "daily_cap": 10000
    }

    target_iso = session["exit_at"] or simulated_now or session["entry_at"]
    calc = calculate_parking_fee_pure(session["entry_at"], target_iso, policy)

    cur.execute(
        "SELECT status FROM mock_payments WHERE session_id = ? ORDER BY created_at DESC LIMIT 1",
        (session_id,),
    )
    pay_row = cur.fetchone()
    payment_status = pay_row["status"] if pay_row else "unpaid"

    return 200, {
        "session_id": session["session_id"],
        "lot_id": session["lot_id"],
        "space_id": session["space_id"],
        "car_id": session.get("car_id", "01"),
        "entry_at": session["entry_at"],
        "exit_at": session["exit_at"],
        "target_at": target_iso,
        "duration_minutes": calc["duration_minutes"],
        "amount_krw": calc["amount_krw"],
        "payment_status": payment_status,
        "rate_policy": policy,
    }


# [API 1] GET /api/v1/sessions/{session_id}/fee
@billing_router.get("/sessions/{session_id}/fee", summary="차량별 주차요금 실시간 계산 및 조회")
async def get_session_fee(
    session_id: str = Path(..., description="주차 세션 ID"),
    now: Optional[str] = Query(None, description="요금 산출 기준 시각"),
    conn: sqlite3.Connection = Depends(get_db_conn),
) -> JSONResponse:
    code, data = _calculate_fee_helper(conn, session_id, now)
    return JSONResponse(status_code=code, content=data)


# [API 2] POST /api/v1/mock-payments
@billing_router.post("/mock-payments", summary="모의 결제 처리 및 DB 저장")
async def create_mock_payment(
    payload: dict[str, Any] = Body(...),
    conn: sqlite3.Connection = Depends(get_db_conn),
) -> JSONResponse:
    fee_calc_fn = lambda s_id, t_iso: _calculate_fee_helper(conn, s_id, t_iso)
    code, data = process_mock_payment(conn, payload, fee_calc_fn)
    return JSONResponse(status_code=code, content=data)


# [API 3] POST /api/v1/mock-payments/{id}/cancel
@billing_router.post("/mock-payments/{id}/cancel", summary="결제 취소 및 모의 환불")
async def cancel_payment(
    id: str = Path(..., description="취소할 결제 ID"),
    payload: dict[str, Any] = Body(default={}),
    conn: sqlite3.Connection = Depends(get_db_conn),
) -> JSONResponse:
    code, data = cancel_mock_payment(conn, id, payload)
    return JSONResponse(status_code=code, content=data)


# [API 4] GET /api/v1/reports/revenue
@billing_router.get("/reports/revenue", summary="일별·월별·주차장별 매출 집계 리포트")
async def get_revenue_report(
    month: str = Query("ALL", description="조회할 월 필터"),
    conn: sqlite3.Connection = Depends(get_db_conn),
) -> JSONResponse:
    code, data = generate_revenue_report(conn, month)
    return JSONResponse(status_code=code, content=data)
