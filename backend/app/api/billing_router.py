"""박해준 님 메인 FastAPI 프로젝트 통합용 공식 라우터 모듈.

[박해준 님 확정 API 규격 4종]:
  1. GET  /api/v1/sessions/{session_id}/fee        — 요금 조회
  2. POST /api/v1/mock-payments                    — 모의 결제
  3. POST /api/v1/mock-payments/{id}/cancel        — 결제 취소 (모의 환불)
  4. GET  /api/v1/reports/revenue                  — 매출 조회

[박해준 님 메인 서버 연동 방법 (단 2줄)]:
  from routers.billing_router import billing_router
  app.include_router(billing_router)
"""

from __future__ import annotations
from typing import Any, Optional
from fastapi import APIRouter, Body, HTTPException, Path, Query
from fastapi.responses import JSONResponse

try:
  from schemas import FeeResponse, PaymentCreateRequest, PaymentResponse, PaymentCancelRequest, PaymentCancelResponse, RevenueReportResponse
  HAS_SCHEMAS = True
except ImportError:
  HAS_SCHEMAS = False

billing_router = APIRouter(prefix="/api/v1", tags=["4. 윤명수 — 요금·결제·매출"])


# [API 1] GET /api/v1/sessions/{session_id}/fee — 요금 조회
@billing_router.get(
    "/sessions/{session_id}/fee",
    summary="차량별 주차요금 실시간 계산 및 조회",
    description="입출차 시간과 주차장별(A·B·C·D) 요금정책을 적용하여 기본요금·추가요금·일일최대요금을 계산합니다.",
)
async def get_session_fee(
    session_id: str = Path(..., description="주차 세션 ID (예: 'ses_0001')"),
    now: Optional[str] = Query(None, description="요금 산출 기준 시각 (생략 시 현재 시각 기준 KST)"),
) -> JSONResponse:
  from server import service_get_session_fee
  code, data = service_get_session_fee(session_id, now)
  return JSONResponse(status_code=code, content=data)


# [API 2] POST /api/v1/mock-payments — 모의 결제
@billing_router.post(
    "/mock-payments",
    summary="모의 결제 처리 및 DB 저장",
    description="idempotency_key를 통해 중복 결제를 차단하며, 결제 완료 시 mock_payments에 저장하고 관리자 매출에 즉시 반영합니다.",
)
async def create_mock_payment(
    payload: dict[str, Any] = Body(
        ...,
        example={
            "session_id": "ses_0001",
            "idempotency_key": "idem_ses_0001_unique_123",
            "payment_method": "MOCK_CARD"
        }
    )
) -> JSONResponse:
  from server import service_post_mock_payment
  code, data = service_post_mock_payment(payload)
  return JSONResponse(status_code=code, content=data)


# [API 3] POST /api/v1/mock-payments/{id}/cancel — 결제 취소
@billing_router.post(
    "/mock-payments/{id}/cancel",
    summary="결제 취소 및 모의 환불 (매출 차감 정정)",
    description="결제 완료(completed) 건에 대해 결제 취소/모의 환불을 수행하고, 결제 상태를 canceled로 변경하며 순매출에서 즉시 차감 정정합니다.",
)
async def cancel_mock_payment(
    id: str = Path(..., description="취소할 결제 ID (예: 'pay_0001')"),
    payload: dict[str, Any] = Body(
        default={},
        example={"refund_reason": "고객 모의 환불 요청"}
    )
) -> JSONResponse:
  from server import service_cancel_mock_payment
  code, data = service_cancel_mock_payment(id, payload)
  return JSONResponse(status_code=code, content=data)


# [API 4] GET /api/v1/reports/revenue — 매출 조회
@billing_router.get(
    "/reports/revenue",
    summary="일별·월별·주차장별 매출 집계 리포트",
    description="결제 일시(created_at) 기준으로 총 승인액에서 환불 차감액을 뺀 순매출(net_revenue_krw)을 일별·월별 및 주차장별(A·B 12면 / C·D 10면)로 집계합니다.",
)
async def get_revenue_report(
    month: str = Query("ALL", description="조회할 월 필터 ('ALL' 또는 '2026-11' 등)"),
) -> JSONResponse:
  from server import service_get_revenue_report
  code, data = service_get_revenue_report(month)
  return JSONResponse(status_code=code, content=data)
