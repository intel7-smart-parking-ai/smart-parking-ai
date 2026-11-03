# [AI 스마트 주차장] 윤명수 담당 공식 API v1 규격서

**담당자: 윤명수 (요금 계산 · 모의 결제 · 매출 집계 단일 소유자)**  
**제공 대상: 박해준 님(FastAPI/DB), 김채린 님(프론트엔드/관제), 서정범 님(LLM 챗봇), 김한별 님(PM)**

---

## 📌 박해준 님 확정 공식 4대 API 요약

| # | HTTP 메서드 & 경로 | 기능 설명 | 주요 소비자 |
| :-: | :--- | :--- | :--- |
| **1** | `GET /api/v1/sessions/{session_id}/fee` | **요금 조회** (기본/추가/최대요금 산출) | 사용자 웹, 관리자 화면, LLM 챗봇 |
| **2** | `POST /api/v1/mock-payments` | **모의 결제** (멱등키 중복방지 & DB 저장) | 사용자 모바일 웹 |
| **3** | `POST /api/v1/mock-payments/{id}/cancel` | **결제 취소** (모의 환불 & 매출 차감 정정) | 관리자 통합관제 화면 |
| **4** | `GET /api/v1/reports/revenue` | **매출 조회** (일별·월별·주차장별 순매출) | 관리자 관제 화면, LLM 챗봇 |

---

## 1. GET `/api/v1/sessions/{session_id}/fee` — 요금 조회

주차장별(실물 가(A) 12면·나(B) 16면, 가상 다(C) 12면·라(D) 10면 — 이름은 임시) 요금 정책과 입출차 시간을 바탕으로 실시간 주차요금을 계산하여 반환합니다.

조회할 때마다 **요금 견적(`quote_id`)** 을 발급한다. 결제는 이 견적 금액(요금 화면에 보인 금액) 그대로 처리된다. 견적 유효 시간은 환경변수 `FEE_QUOTE_TTL_SECONDS`(기본 300초).

### 요청 (Request)
* **Path Parameter**: `session_id` (string, 필수) — 예: `ses_0001`
* **Query Parameter**: `now` (string, 선택) — 요금 계산 기준 시각 (생략 시 현재 시각 기준 KST)

### 응답 (Response — 200 OK)
```json
{
  "session_id": "ses_0001",
  "lot_id": "A",
  "space_id": "A-01",
  "car_id": "27",
  "entry_at": "2026-11-02T09:05:00+09:00",
  "exit_at": null,
  "target_at": "2026-11-02T10:00:00+09:00",
  "duration_minutes": 55,
  "fee_breakdown": {
    "base_fee_krw": 1000,
    "extra_units": 3,
    "extra_fee_krw": 1500,
    "daily_cap_krw": 10000,
    "cap_discount_krw": 0
  },
  "amount_krw": 2500,
  "is_daily_capped": false,
  "payment_status": "unpaid",
  "rate_policy": {"base_minutes": 30, "base_fee": 1000, "unit_minutes": 10, "extra_fee": 500, "daily_cap": 10000},
  "quote_id": "quote_3f9a1c2b7d10",
  "quote_expires_at": "2026-11-02T10:05:00+09:00"
}
```

---

## 2. POST `/api/v1/mock-payments` — 모의 결제

실제 금전 거래 없이 가상 결제를 처리하고 `mock_payments` DB에 저장하며, 관리자 매출에 즉시 반영합니다.

### 요청 (Request)
* **Headers**: `Content-Type: application/json`
* **Body (JSON)**:
```json
{
  "session_id": "ses_0001",
  "quote_id": "quote_3f9a1c2b7d10",
  "idempotency_key": "idem_ses_0001_unique_12345",
  "payment_method": "MOCK_CARD"
}
```

### 응답 (Response — 200 OK 신규 결제)
```json
{
  "payment_id": "pay_0005",
  "session_id": "ses_0001",
  "lot_id": "A",
  "car_id": "27",
  "amount_krw": 2500,
  "payment_method": "MOCK_CARD",
  "status": "completed",
  "idempotency_key": "idem_ses_0001_unique_12345",
  "created_at": "2026-11-02T10:05:00+09:00",
  "canceled_at": null,
  "refund_reason": null
}
```

### 중복 결제 방지 동작 (Idempotent Replay)
* 동일한 `idempotency_key`로 같은 세션 재요청 시: **200 OK**와 함께 기존 결제 레코드 반환 (`"idempotent_replayed": true`)
* 같은 `idempotency_key`를 **다른 세션**에 사용 시: **409** `IDEMPOTENCY_CONFLICT`
* 이미 결제 완료된 세션에 다른 키로 요청 시: **409** `ALREADY_PAID` (`details`에 기존 `existing_payment_id`)
* 없는 견적: **404** `QUOTE_NOT_FOUND` / 다른 세션의 견적: **409** `QUOTE_MISMATCH` / 만료된 견적: **409** `QUOTE_EXPIRED` → 요금을 다시 조회 (결제를 만들지 않음)
* 결제 금액은 **견적 금액** (결제 시점에 다시 계산하지 않음). 같은 멱등키 재요청은 견적이 만료됐어도 기존 결제를 반환
* `quote_id`가 없으면 **422**
* `amount_krw` 등 정의되지 않은 필드를 보내면 **422** (요금은 서버만 계산)

### 에러 응답 형식 (모든 billing API 공통, 팀 계약 형식)
```json
{"error": {"code": "ALREADY_PAID", "message": "이미 결제 완료된 주차 세션(ses_0001)입니다.",
           "details": [{"existing_payment_id": "pay_3f9a1c2b7d10"}]}}
```

---

## 2-1. GET `/api/v1/mock-payments` — 결제 목록

* **Query Parameter (모두 선택)**: `lot_id`, `status` (`completed` / `canceled`), `car_id` (실물 스티커 두 자리, 예: `27`)
* **응답**: `{"items": [결제 객체, ...], "next_cursor": null, "as_of": "<조회 시각>"}` — 최신순

## 2-2. GET `/api/v1/rate-policies` — 요금 정책

* **응답**: `{"items": [{"lot_id": "A", "base_minutes": 30, "base_fee": 1000, "unit_minutes": 10, "extra_fee": 500, "daily_cap": 10000}, ...], "next_cursor": null, "as_of": "..."}`

### 주차장별 요금표 (2026-11-03, 윤명수 작성)

| 주차장 | `lot_id` | 기본 시간 | 기본 요금 | 추가 단위 | 추가 요금 | 일 최대 |
| --- | --- | --- | --- | --- | --- | --- |
| 개발원주차장 | `A` | 30분 | 1,000원 | 10분 | 500원 | 10,000원 |
| 부산역주차장 | `B` | 30분 | 2,000원 | 10분 | 700원 | 20,000원 |
| 북항주차장 | `C` | 30분 | 3,000원 | 10분 | 800원 | 40,000원 |
| 신세계백화점주차장 | `D` | 30분 | 4,000원 | 10분 | 1,000원 | 50,000원 |

* 빈 DB로 서버를 처음 실행하면 위 요금표와 주차장 4곳이 기본 데이터로 들어간다 (`backend/app/billing/db.py`의 `seed_defaults`). 이미 데이터가 있으면 바꾸지 않는다
* 요금 기준은 billing 한 곳이다. 화면·챗봇·데모 서버는 이 표를 다시 정의하지 않고 `GET /rate-policies` 또는 요금 조회 결과를 사용한다
* 계산 예: 부산역 41분 = 2,000 + 700 × 2 = 3,400원 / 라 55분 = 4,000 + 1,000 × 3 = 7,000원

---

## 3. POST `/api/v1/mock-payments/{id}/cancel` — 결제 취소 (모의 환불)

관리자 관제 화면에서 결제를 취소/환불 처리하며, 결제 상태를 `canceled`로 변경하고 순매출에서 즉시 차감 정정합니다.

### 요청 (Request)
* **Path Parameter**: `id` (string, 필수) — 취소할 결제 ID (예: `pay_0005`)
* **Body (JSON, 선택)**:
```json
{
  "refund_reason": "고객 요청 모의 환불"
}
```

* 이미 취소된 결제를 다시 취소: **409** `INVALID_PAYMENT_STATE` / 없는 결제: **404** `PAYMENT_NOT_FOUND`

### 응답 (Response — 200 OK)
```json
{
  "payment_id": "pay_0005",
  "session_id": "ses_0001",
  "lot_id": "A",
  "car_id": "27",
  "refunded_amount_krw": 2500,
  "status": "canceled",
  "canceled_at": "2026-11-02T10:10:00+09:00",
  "refund_reason": "고객 요청 모의 환불",
  "revenue_adjusted": true
}
```

---

## 4. GET `/api/v1/reports/revenue` — 매출 조회

결제 시점(`mock_payments.created_at`)을 기준으로 일별·월별 및 주차장별(가 12·나 16·다 12·라 10면) 순매출을 집계합니다.

### 요청 (Request)
* **Query Parameter**: `month` (string, 선택) — `'ALL'` 또는 `'2026-11'` (기본값: `'ALL'`)

### 응답 (Response — 200 OK)
```json
{
  "filter_month": "2026-11",
  "aggregated_basis": "mock_payments.created_at",
  "summary": {
    "gross_amount_krw": 13200,
    "canceled_amount_krw": 5900,
    "net_revenue_krw": 7300,
    "completed_count": 3,
    "canceled_count": 2,
    "by_type": {
      "real_net_krw": 7300,
      "virtual_net_krw": 0
    }
  },
  "lots": [
    {
      "lot_id": "A",
      "name": "가",
      "type": "real",
      "total_spaces": 12,
      "gross_amount_krw": 5500,
      "canceled_amount_krw": 3000,
      "net_revenue_krw": 2500,
      "completed_count": 1,
      "canceled_count": 1
    }
  ],
  "daily_revenue": [ ... ],
  "monthly_revenue": [ ... ]
}
```

---

## 5. 팀원별 프로젝트 코드 연동 방법

### ⚙️ 박해준 님 (FastAPI 메인 서버)
라우터는 `backend/app/api/billing_router.py`에 있습니다. **아직 `main.py`에 등록하지 않았습니다** (main은 현재 DB 미사용 목업만 허용).
DB 방식(KAN-17) 확정 후 `backend/app/billing/db.py`의 `get_db_conn`을 공용 DB 연결로 바꾸고 아래 한 줄로 등록합니다.
```python
from backend.app.api.billing_router import billing_router
app.include_router(billing_router)
```

### 📱 김채린 님 (프론트엔드 / 관리자 UI)
패키지 내 `parking_billing_client.js`를 임포트하여 호출:
```javascript
import { ParkingBillingClient } from './parking_billing_client.js';
const billing = new ParkingBillingClient("http://<서버PC_IP>:8000");

const fee = await billing.getFee("ses_0001");
const pay = await billing.pay("ses_0001", "MOCK_CARD");
await billing.cancel(pay.payment_id, "환불 요청");
const rev = await billing.getRevenue("2026-11");
```

### 🤖 서정범 님 (LLM 챗봇)
패키지 내 `chatbot_billing_tools.py`를 챗봇 툴로 등록하여 사용:
```python
from chatbot_billing_tools import ChatbotBillingTools
tools = ChatbotBillingTools("http://<서버PC_IP>:8000")
fee = tools.get_vehicle_fee("ses_0001")
rev = tools.get_monthly_revenue("2026-11")
```
