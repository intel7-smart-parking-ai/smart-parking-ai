# 챗봇의 팀 API 소비 계약 (v2.9, 2026-11-03)

기준: 팀 레포 `docs/API_CONTRACT.md`·`docs/DECISIONS.md`(main `3c20761`)와 billing 실제 코드(`backend/app/api/billing_router.py`).
챗봇은 DB를 직접 읽지 않고 아래 API만 쓴다. 다르면 팀 레포 문서가 기준이다.

## 경로 (`chatbot/api_paths.json`)
| 키 | 경로 | 사용 | 서버 상태(11/03) |
| --- | --- | --- | --- |
| lots | /api/v1/parking-lots | items의 total·empty·uncertain·unavailable_spaces, name | 목업 |
| spaces | /api/v1/parking-lots/{lot_id}/spaces | space_id(도면 번호)·status·car_id·space_type | 목업 |
| location | /api/v1/vehicles/{car_id}/location | 위치·session_id·vehicle_type | 목업(session_id null) |
| fee | /api/v1/sessions/{session_id}/fee | amount_krw·target_at·rate_policy·quote_id·quote_expires_at·payment_status | 코드 있음, main.py 미등록 |
| payment | /api/v1/mock-payments | 견적 결제 | 코드 있음, main.py 미등록 |
| rate_policies | /api/v1/rate-policies | 주차장별 요금 정책 안내 | 코드 있음, main.py 미등록 |
| alerts | /api/v1/alerts | 모의 화재·전용 칸 위반 경보 | 미구현 |
| sessions | /api/v1/sessions | (후속) 진행 중 세션 | 미구현 |
| virtual_search | null | 가상 차량 검색(경로 확정 전 호출 안 함) | 미확정 |

경로 변수는 UTF-8 URL quote. 세션 ID·견적 ID는 형식을 가정하지 않는 불투명 문자열.

## 식별자
- `lot_id`: A 개발원주차장, B 부산역주차장, C 북항주차장(가상), D 신세계백화점주차장(가상). 화면·답변은 이름으로 표시.
- `space_id`: 주차장별 도면 번호(`A-01`, `B-06`…). 글자는 주차장 안 구역이며 `lot_id`와 무관 → 항상 `(lot_id, space_id)`로 식별. 답변 표기 "개발원주차장 B-06".
  - 챗봇 내부는 주차장 칸 목록의 순번을 쓰고, API·답변·화면에서는 도면 번호로 바꾼다.
  - 서버 칸 목록이 명부와 다르면 서버를 따른다(명부에 없는 칸 이름은 추가, 구역은 "미지정").
- `car_id`(실물): `"10"`~`"99"` 두 자리 문자열(D-06). 한 자리·0 시작 입력은 조회하지 않고 "두 자리로 말씀해 주세요". 서버가 규칙 밖 값(`"01"` 등)을 주면 그 칸만 번호 미인식(null)으로 본다.
- 가상 차량: 번호판 `17가1724`, 화면 `1724`(D-08). 검색 경로 확정 전에는 "가상 주차장 차량 검색은 준비 중이에요".
- `space_type`·`vehicle_type`: `general`/`ev`/`accessible`. 들어오면 서버 값 우선, 없으면 `lots.json` 구역.

## 요금·결제 (D-09 견적)
- 요금 응답의 실제 키(billing_router): `session_id, lot_id, space_id, car_id, entry_at, exit_at, target_at, duration_minutes, fee_breakdown, amount_krw, is_daily_capped, payment_status, rate_policy, quote_id, quote_expires_at`.
  - `schemas.py` 설명의 `calculated_at`·`applied_policy_id`·`session_status`는 실제 응답에 없다 → 요구하지 않는다(있으면 사용).
- 금액은 `amount_krw` 그대로. 챗봇은 재계산하지 않는다(ApiAdapter에서 calc_fee 사용 0).
- 결제 body는 `build_payment_body` 한 곳: `{session_id, quote_id, idempotency_key, payment_method:"MOCK_CARD"}`.
  - 요금 응답에 `quote_id`가 없으면 결제 요청을 보내지 않는다.
  - 견적 만료(`quote_expires_at`, 시계 차 5초) 또는 `QUOTE_EXPIRED`·`QUOTE_MISMATCH`·`QUOTE_NOT_FOUND` → 자동 결제 없이 요금 재조회 후 새 금액으로 재확인(새 견적·새 멱등키).
  - 같은 확인의 재시도는 같은 멱등키.
- 세션 상태: `active` 주차 중 / `closed` 출차 완료. 응답에 없으면 `exit_at` 유무로 판단. 전환기 별칭 `exited`→closed, `parked`→active.

## 에러 코드 → 처리 (`contracts.PAYMENT_ERROR_STATUS` 외)
| 코드 | 처리 |
| --- | --- |
| VEHICLE_NOT_FOUND / PARKING_LOT_NOT_FOUND | 찾을 수 없음(지어내지 않음) |
| SESSION_NOT_FOUND | 아직 요금 정보를 확인할 수 없어요 |
| 경로 404(`{"detail":...}`) | 요금 조회 기능이 아직 연결되지 않았어요 |
| ALREADY_PAID | 이미 결제 |
| QUOTE_EXPIRED / QUOTE_MISMATCH / QUOTE_NOT_FOUND | 재조회 후 재확인 |
| PAYMENT_CONFLICT | 다른 결제가 처리 중, 자동 재시도 금지 |
| INVALID_PAYMENT_STATE | 이미 처리된 결제 |
| IDEMPOTENCY_CONFLICT | 결제 요청이 겹쳤어요, 새 키 재시도 금지 |
| 400·422 | 결제를 처리하지 못했어요 |
| 연결 실패·5xx | 지금은 확인할 수 없어요(목업으로 대체하지 않음) |

`error.message` 원문은 사용자에게 보여주지 않는다. 실패 응답은 캐시하지 않는다.

## 경보
- 화재 경보 type: `mock_fire_led`, `mock_fire_led_observed`. 상태 `active/acknowledged/resolved`, `level` 1·2(없으면 Lv1로 두고 추측하지 않음).
- `space_id` null 허용("개발원주차장(정확한 칸 확인 중)"), `lot_id` null 허용("주차장 위치 확인 중").
- `source`가 `simulation`/`manual`이면 "(모의 이벤트)".
- `restricted_space_violation`(전용 칸 위반)은 화재 레벨과 섞지 않고 "위반 차량 있어?"에만 사용.
- 오래되었거나 미래 시각인 "경보 없음"은 확정하지 않는다. 마지막 성공 Lv2는 조회 실패·신뢰할 수 없는 해제로 지우지 않는다.

## 시연 표시
- 응답 헤더 `X-ParkFlow-Data-Source: mock` 또는 `is_mock: true`면 그 답 끝에 "※ 시연용 데이터예요" 한 번.
- 응답 기준 시각 표기("N분 전 기준")는 v2.9에서 제거했다.
