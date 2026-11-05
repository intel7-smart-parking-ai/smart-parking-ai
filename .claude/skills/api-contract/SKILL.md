---
name: api-contract
description: "스마트 주차장 공통 데이터 계약 레퍼런스: /api/v1 엔드포인트 목록, vision-event 페이로드, 주차칸/세션/결제/경보 객체, 식별자 규칙(lot_id, space_id, car_id 두 자리 스티커), DB 최소 스키마, 에러 형식. API·Pydantic 모델·DB 테이블·프론트 목업·챗봇 tool·모델 어댑터를 작성하거나 검증할 때 사용한다."
---

# API Contract v1 (요약)

2026-11-05 D-49 승인 확장: PM 10:01 + 윤명수 10:05(사용자 전달).
`docs/API_CONTRACT.md` 첫 절이 기존 미정 항목보다 우선한다. 통합 main demo/real에서
칸 entry_at/zone/plate_number/vehicle_number/version, fee payment_blocked/payment,
완료 결제 후 amount_krw=payment.amount_krw, 결제409 EVACUATION_ACTIVE,
가상 GET vehicle-search 및 GET/POST simulation API를 제공한다.
시연 demo_main과 통합 main의 제공 범위는 구분한다. 챗봇 내부는 해당 담당자 영역(D-50).

원문: `docs/통합_개발운영지침서.md` 5~6장, `docs/API_CONTRACT.md`. **원문이 바뀌면 원문 우선**이며 이 스킬도 갱신한다. 계약 변경은 PM(김한별) + 박해준 협의.

## 공통 규칙

- Base `/api/v1`, JSON UTF-8, 필드 `snake_case`
- 시각: ISO 8601 + 타임존 (`2026-11-02T10:00:00+09:00`) — Python은 timezone-aware datetime만
- 금액: KRW 정수 (`amount_krw`)
- ID는 모두 문자열
- 에러: HTTP 상태코드 + `{"error": {"code": "SPACE_NOT_FOUND", "message": "...", "details": []}}`

## 식별자

| 필드 | 값 | 규칙 |
| --- | --- | --- |
| `lot_id` | `A`,`B` (real) / `C`,`D` (virtual) | 화면 이름 개발원·부산역·북항·신세계백화점. 칸 수 12·16·12·64 (`docs/API_CONTRACT.md`) |
| `camera_id` | `camera_a`, `camera_b` / 가상은 `null` | |
| `space_id` | `A-01`, `B-03` … | 주차장별 도면 번호(A·B = 주차장 안 구역, `lot_id`와 무관). 고유키는 `(lot_id, space_id)` |
| `car_id` | `"10"`~`"99"` | 실물 미니카 스티커, **두 자리 숫자 문자열**, `00`~`09` 미사용, 미인식 `null`. `int` 변환 금지 |
| `session_id` | 문자열 | 입차~출차 1회. 칸 이동은 세션 유지 |
| 주차칸 `status` | `empty` / `occupied` / `uncertain` / `unavailable` | |
| `source` | `vision` / `simulation` / `manual` | |

## 엔드포인트

| 메서드 | 경로 | 응답 핵심 | 소비자 |
| --- | --- | --- | --- |
| GET | `/health` | status, camera/model/db 상태, 마지막 갱신 | 관리자 |
| GET | `/parking-lots` | lot_id, name, type, total_spaces, empty_spaces, status | 웹, 챗봇 |
| GET | `/parking-lots/{lot_id}/spaces` | space_id, status, car_id, confidence, observed_at | 웹, 관제, 챗봇 |
| GET | `/vehicles/{car_id}/location` | lot_id, space_id, observed_at, confidence | 웹, 챗봇 |
| GET | `/sessions?lot_id=A` | session 목록 | 관리자 |
| GET | `/sessions/{session_id}/fee` | 서버 계산 요금 내역 | 사용자 |
| POST | `/mock-payments` | body: session_id, idempotency_key → payment | 사용자 |
| POST | `/mock-payments/{payment_id}/cancel` | 취소된 payment | 관리자 |
| GET | `/reports/revenue?month=2026-11` | 주차장별 일·월 (결제−취소) | 관리자 |
| GET | `/alerts` | LED 모의 화재 등 경보, 확인 상태 | 관리자 |
| POST | `/internal/vision-events` | 모델/ROI 관측 수집 (내부 전용) | 서버 |

## 핵심 객체

주차칸 상태
```json
{"lot_id":"A","camera_id":"camera_a","space_id":"A-03","status":"occupied",
 "car_id":"12","confidence":0.94,"observed_at":"2026-11-02T10:00:00+09:00","source":"vision"}
```

vision-event (모델 원시 출력은 어댑터에서 이 형식으로 변환)
```json
{"event_id":"evt_0001","lot_id":"A","camera_id":"camera_a",
 "event_type":"vehicle_observed","space_id":"A-03","car_id":"12",
 "bbox_xyxy":[100,140,180,260],"confidence":0.92,"observed_at":"2026-11-02T10:00:00+09:00"}
```
- `event_type`: `vehicle_observed`, `space_empty_observed`, `plate_observed`, `mock_fire_led_observed`
- LED 이벤트는 위치·신뢰도·증거 프레임 ID(`frame_id`) 포함. "실제 화재" 표기 금지
- 같은 car_id가 여러 칸에서 관측되면 중복으로 표시 → 관리자 확인

## DB 최소 스키마

| 테이블 | 주요 필드 |
| --- | --- |
| parking_lots | lot_id, name, type(real/virtual), active |
| cameras | camera_id, lot_id, source_config, last_seen_at, status |
| parking_spaces | lot_id, space_id, roi_polygon_json, enabled |
| space_states | lot_id, space_id, status, car_id, observed_at, confidence |
| parking_sessions | session_id, lot_id, car_id, entry_at, exit_at, status |
| vision_events | event_id, model_type, lot_id, space_id, car_id, confidence, observed_at |
| rate_policies | policy_id, lot_id, effective_at, base_fee, unit_minutes, extra_fee, daily_cap |
| mock_payments | payment_id, session_id, amount_krw, status, idempotency_key, created_at |
| alerts | alert_id, lot_id, space_id, type, status, detected_at, acknowledged_at |
| audit_logs | log_id, actor, action, target, created_at |

## 변경 절차

1. `_workspace/change_request_<주제>.md`: 변경 내용 / 이유 / 영향 소유자 / 하위호환 / 적용일
2. PM + 소유자 승인 → `docs/API_CONTRACT.md` PR → 이 스킬 갱신
3. 필드는 추가(optional) 우선, 이름·의미 변경은 지양
