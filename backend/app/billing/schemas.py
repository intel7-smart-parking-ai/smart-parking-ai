"""Pydantic v2 데이터 모델 스키마 (EPIC 05: 요금·결제·매출).

작성자: 윤명수
연동 대상: 박해준 님 메인 FastAPI 프로젝트 (`schemas/` 폴더)
"""

from __future__ import annotations
from typing import Any, Optional
from pydantic import BaseModel, ConfigDict, Field


# 1. 요금 조회 스키마 (GET /api/v1/sessions/{session_id}/fee)
class FeeBreakdown(BaseModel):
  base_fee_krw: int = Field(..., description="기본 요금 (KRW)")
  extra_units: int = Field(..., description="추가 요금 단위 수")
  extra_fee_krw: int = Field(..., description="추가 요금 합계 (KRW)")
  daily_cap_krw: int = Field(..., description="일일 최대요금 상한 (KRW)")
  cap_discount_krw: int = Field(..., description="최대요금 상한 적용 감면액 (KRW)")


class FeeResponse(BaseModel):
  session_id: str = Field(..., description="주차 세션 ID")
  lot_id: str = Field(..., description="주차장 ID (A, B, C, D)")
  space_id: Optional[str] = Field(None, description="주차면 ID (예: A-01), 미배정 null")
  car_id: Optional[str] = Field(None, description="실물 차량 스티커 번호 (두 자리 '10'~'99', 미인식 null)")
  entry_at: str = Field(..., description="입차 일시 (ISO 8601)")
  exit_at: Optional[str] = Field(None, description="출차 일시 (주차 중이면 null)")
  target_at: str = Field(..., description="요금 계산 기준 시각 (출차 시각 또는 조회 시각)")
  duration_minutes: int = Field(..., description="총 주차 시간 (분)")
  fee_breakdown: FeeBreakdown = Field(..., description="요금 세부 산출 내역")
  amount_krw: int = Field(..., description="최종 계산된 주차 요금 (KRW 정수)")
  is_daily_capped: bool = Field(..., description="일일 최대요금 상한 적용 여부")
  payment_status: str = Field(..., description="결제 상태 ('unpaid', 'completed', 'canceled')")
  rate_policy: dict[str, int] = Field(..., description="적용 요금표 (base_minutes, base_fee, unit_minutes, extra_fee, daily_cap)")
  quote_id: str = Field(..., description="요금 견적 ID — 결제 요청에 그대로 보낸다")
  quote_expires_at: str = Field(..., description="견적 만료 시각 (ISO 8601). 지나면 요금을 다시 조회")


# 2. 모의 결제 스키마 (POST /api/v1/mock-payments)
class PaymentCreateRequest(BaseModel):
  # 금액·시각은 받지 않는다(요금은 서버 단일 계산). 정의되지 않은 필드는 422.
  model_config = ConfigDict(extra="forbid")

  session_id: str = Field(..., min_length=1, description="결제할 주차 세션 ID (예: 'ses_0001')")
  quote_id: str = Field(..., min_length=1, description="요금 조회 응답의 견적 ID (이 금액으로 결제)")
  idempotency_key: str = Field(..., min_length=1, description="중복 결제 방지용 고유 멱등키 (UUID 또는 난수)")
  payment_method: str = Field("MOCK_CARD", description="모의 결제 수단 ('MOCK_CARD', 'MOCK_EASY_PAY', 'MOCK_ACCOUNT')")


class PaymentResponse(BaseModel):
  payment_id: str = Field(..., description="발급된 결제 고유 ID (예: 'pay_0001')")
  session_id: str = Field(..., description="주차 세션 ID")
  lot_id: str = Field(..., description="주차장 ID")
  car_id: Optional[str] = Field(None, description="차량 번호 (미인식 null)")
  amount_krw: int = Field(..., description="결제 승인 금액 (KRW)")
  payment_method: str = Field(..., description="결제 수단")
  status: str = Field(..., description="결제 상태 ('completed' or 'failed')")
  idempotency_key: str = Field(..., description="요청에 사용된 멱등키")
  created_at: str = Field(..., description="결제 승인 일시 (ISO 8601)")
  canceled_at: Optional[str] = Field(None, description="취소 일시")
  refund_reason: Optional[str] = Field(None, description="환불 사유")
  idempotent_replayed: Optional[bool] = Field(None, description="중복 요청 재반환 여부")
  notice: Optional[str] = Field(None, description="안내 메시지")


# 3. 결제 취소 스키마 (POST /api/v1/mock-payments/{id}/cancel)
class PaymentCancelRequest(BaseModel):
  refund_reason: Optional[str] = Field("고객 모의 환불 요청", description="결제 취소 및 모의 환불 사유")


class PaymentCancelResponse(BaseModel):
  payment_id: str = Field(..., description="취소된 결제 ID")
  session_id: str = Field(..., description="주차 세션 ID")
  lot_id: str = Field(..., description="주차장 ID")
  car_id: Optional[str] = Field(None, description="차량 번호 (미인식 null)")
  refunded_amount_krw: int = Field(..., description="환불 차감 금액 (KRW)")
  status: str = Field("canceled", description="변경된 결제 상태")
  canceled_at: str = Field(..., description="취소 일시 (ISO 8601)")
  refund_reason: Optional[str] = Field(None, description="환불 사유")
  revenue_adjusted: bool = Field(True, description="매출 차감 정정 완료 여부")


# 4. 매출 집계 스키마 (GET /api/v1/reports/revenue)
class LotRevenueItem(BaseModel):
  lot_id: str
  name: str
  type: str
  total_spaces: int
  policy_summary: str
  gross_amount_krw: int
  canceled_amount_krw: int
  net_revenue_krw: int
  completed_count: int
  canceled_count: int


class RevenueSummary(BaseModel):
  gross_amount_krw: int
  canceled_amount_krw: int
  net_revenue_krw: int
  completed_count: int
  canceled_count: int
  by_type: dict[str, int]


class DailyRevenueItem(BaseModel):
  date: str
  gross_krw: int
  canceled_krw: int
  net_krw: int
  completed_count: int
  canceled_count: int


class MonthlyRevenueItem(BaseModel):
  month: str
  gross_krw: int
  canceled_krw: int
  net_krw: int
  completed_count: int
  canceled_count: int


class RevenueReportResponse(BaseModel):
  filter_month: str
  aggregated_basis: str
  summary: RevenueSummary
  lots: list[LotRevenueItem]
  daily_revenue: list[DailyRevenueItem]
  monthly_revenue: list[MonthlyRevenueItem]
