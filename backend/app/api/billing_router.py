"""요금·결제 공식 라우터 모듈 (FastAPI 통합용)."""

from __future__ import annotations

import sqlite3
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, Path, Query
from fastapi.responses import JSONResponse

from backend.app.billing.calculator import calculate_parking_fee_pure
from backend.app.billing.db import get_db_conn
from backend.app.billing.payment_service import (
    cancel_mock_payment,
    list_mock_payments,
    now_kst_iso,
    process_mock_payment,
)
from backend.app.billing.revenue_service import generate_revenue_report
from backend.app.billing.schemas import PaymentCancelRequest, PaymentCreateRequest

billing_router = APIRouter(prefix="/api/v1", tags=["4. 윤명수 — 요금·결제·매출"])

DEFAULT_POLICY = {
    "base_minutes": 30, "base_fee": 1000, "unit_minutes": 10, "extra_fee": 500, "daily_cap": 10000,
}


def _calculate_fee_helper(
    conn: sqlite3.Connection, session_id: str, simulated_now: Optional[str] = None
) -> tuple[int, dict[str, Any]]:
    cur = conn.cursor()
    cur.execute("SELECT * FROM parking_sessions WHERE session_id = ?", (session_id,))
    session = cur.fetchone()
    if not session:
        return 404, {"error_code": "SESSION_NOT_FOUND", "message": f"세션({session_id})을 찾을 수 없습니다."}

    cur.execute(
        "SELECT base_minutes, base_fee, unit_minutes, extra_fee, daily_cap FROM rate_policies WHERE lot_id = ?",
        (session["lot_id"],),
    )
    policy_row = cur.fetchone()
    policy = dict(policy_row) if policy_row else dict(DEFAULT_POLICY)

    # 출차 세션은 출차 시각, 주차 중이면 요청 기준 시각(없으면 현재)으로 계산
    target_iso = session["exit_at"] or simulated_now or now_kst_iso()
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
        "car_id": session["car_id"],  # 미인식이면 null 그대로
        "entry_at": session["entry_at"],
        "exit_at": session["exit_at"],
        "target_at": target_iso,
        "duration_minutes": calc["duration_minutes"],
        "fee_breakdown": calc["fee_breakdown"],
        "amount_krw": calc["amount_krw"],
        "is_daily_capped": calc["is_daily_capped"],
        "payment_status": payment_status,
        "rate_policy": policy,
    }


def _respond(code: int, data: dict[str, Any]) -> JSONResponse:
    """성공은 그대로, 오류는 팀 공통 형식 {"error": {"code", "message", "details"}}으로 변환."""
    if code < 400:
        return JSONResponse(status_code=code, content=data)
    extra = {k: v for k, v in data.items() if k not in ("error_code", "message")}
    return JSONResponse(
        status_code=code,
        content={"error": {
            "code": data.get("error_code", "ERROR"),
            "message": data.get("message", ""),
            "details": [extra] if extra else [],
        }},
    )


def _list_response(items: list[dict[str, Any]]) -> JSONResponse:
    """팀 공통 목록 형식 {"items", "next_cursor", "as_of"}."""
    return JSONResponse(content={"items": items, "next_cursor": None, "as_of": now_kst_iso()})


# [API 1] GET /api/v1/sessions/{session_id}/fee
@billing_router.get("/sessions/{session_id}/fee", summary="차량별 주차요금 실시간 계산 및 조회")
def get_session_fee(
    session_id: str = Path(..., description="주차 세션 ID"),
    now: Optional[str] = Query(None, description="요금 산출 기준 시각 (ISO 8601, 생략 시 현재)"),
    conn: sqlite3.Connection = Depends(get_db_conn),
) -> JSONResponse:
    code, data = _calculate_fee_helper(conn, session_id, now)
    return _respond(code, data)


# [API 2] POST /api/v1/mock-payments
@billing_router.post("/mock-payments", summary="모의 결제 처리 및 DB 저장")
def create_mock_payment(
    req: PaymentCreateRequest,
    conn: sqlite3.Connection = Depends(get_db_conn),
) -> JSONResponse:
    def fee_calc_fn(s_id: str, t_iso: str) -> tuple[int, dict[str, Any]]:
        return _calculate_fee_helper(conn, s_id, t_iso)

    code, data = process_mock_payment(conn, req.model_dump(), fee_calc_fn)
    return _respond(code, data)


# [API 2-1] GET /api/v1/mock-payments
@billing_router.get("/mock-payments", summary="모의 결제 목록 조회")
def get_mock_payments(
    lot_id: Optional[str] = Query(None, description="주차장 ID 필터"),
    status: Optional[Literal["completed", "canceled"]] = Query(None, description="결제 상태 필터"),
    car_id: Optional[str] = Query(None, description="차량 번호 필터 (예: '01')"),
    conn: sqlite3.Connection = Depends(get_db_conn),
) -> JSONResponse:
    return _list_response(list_mock_payments(conn, lot_id, status, car_id))


# [API 2-2] GET /api/v1/rate-policies
@billing_router.get("/rate-policies", summary="주차장별 요금 정책 조회")
def get_rate_policies(conn: sqlite3.Connection = Depends(get_db_conn)) -> JSONResponse:
    cur = conn.cursor()
    cur.execute(
        "SELECT lot_id, base_minutes, base_fee, unit_minutes, extra_fee, daily_cap FROM rate_policies ORDER BY lot_id"
    )
    return _list_response([dict(r) for r in cur.fetchall()])


# [API 3] POST /api/v1/mock-payments/{id}/cancel
@billing_router.post("/mock-payments/{id}/cancel", summary="결제 취소 및 모의 환불")
def cancel_payment(
    id: str = Path(..., description="취소할 결제 ID"),
    req: Optional[PaymentCancelRequest] = None,
    conn: sqlite3.Connection = Depends(get_db_conn),
) -> JSONResponse:
    payload = req.model_dump() if req else {}
    code, data = cancel_mock_payment(conn, id, payload)
    return _respond(code, data)


# [API 4] GET /api/v1/reports/revenue
@billing_router.get("/reports/revenue", summary="일별·월별·주차장별 매출 집계 리포트")
def get_revenue_report(
    month: str = Query("ALL", description="조회할 월 필터 (YYYY-MM 또는 ALL)"),
    conn: sqlite3.Connection = Depends(get_db_conn),
) -> JSONResponse:
    code, data = generate_revenue_report(conn, month)
    return _respond(code, data)
