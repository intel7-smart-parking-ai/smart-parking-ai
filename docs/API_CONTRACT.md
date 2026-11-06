# API 계약 v1 (2026-11-05, D-49 통합 확장 포함)

## D-49 승인된 통합 확장 (PR #53)

승인: PM 2026-11-05 10:01, 윤명수 10:05 (김채린 전달 승인 기록).
이 절은 #53의 `main.py` demo/real 통합 모드에 적용한다. 아래 11/04 기록과
충돌하면 이 절이 우선한다. 아직 main 병합 완료를 뜻하지 않는다.
`demo_main` 8002는 기존 시연 앱이며 동일 기능 전체를 제공한다고 가정하지 않는다.

### 주차칸 GET `/api/v1/parking-lots/{lot_id}/spaces`

| 필드 | 타입 / 의미 |
| --- | --- |
| `entry_at` | ISO 8601 타임존 포함 문자열 또는 null. 활성 입차 시각. uncertain에서도 활성 세션 입차 시각 보존 |
| `zone` | 문자열 또는 null. space_id의 `-` 앞부분. 주차장 ID와 별개 |
| `plate_number` | 문자열 또는 null. 가상 C/D 전체 번호판. 실물 car_id와 별개 |
| `vehicle_number` | 문자열 또는 null. 가상 번호판 뒤 4자리, 선행 0 보존 |
| `version` | 정수. 엔진에서 채택한 새 점유 관측의 버전. 초기 0, 새 관측 반영 시 증가. 관측 없이 시간 경과만으로 stale이 된 경우 증가를 보장하지 않음 |

필드 추가는 하위호환이며 미관측 값은 null 가능. 실물 스티커 car_id는 기존 10~99 유지.
`demo_main`에서는 가상 번호판/zone이 null일 수 있다.

### 요금 GET `/api/v1/sessions/{session_id}/fee`

- `payment_blocked`: boolean. 같은 주차장의 level>=2 미해제 경보가 있으면 true.
- `payment`: 완료 결제 영수증 객체 또는 null. payment_id/session_id/amount_krw/status/
  payment_method/idempotency_key/created_at 등 기존 결제 행 필드를 반환한다.
- **D-49 채택: 완료 결제가 있으면 최상위 `amount_krw`는 완료 결제 금액이다.**
  `payment.amount_krw`와 같다. 미결제는 기존 서버 견적 금액이며 계산은 billing이 수행한다.
  fee_breakdown은 기존 요금 계산 결과이므로 완료 영수증 금액 표시에는 사용하지 않는다.
- UI는 완료 결제 영수증이 있으면 이를 표시하고 재결제하지 않는다.

### 모의 결제 POST `/api/v1/mock-payments`

미해제 level>=2 경보 중 결제 요청은 409:

```json
{"error":{"code":"EVACUATION_ACTIVE","message":"대피 단계의 모의 화재 경보 중에는 결제할 수 없습니다.","details":[]}}
```

자동 결제 재시도 금지. 경보 검사와 결제 호출은 같은 동기화 잠금 안에서 처리한다.
챗봇의 409 처리/안내는 서정범·윤명수 담당이며 UI 통합자가 변경하지 않는다(D-50).

### 가상 차량 GET `/api/v1/vehicle-search?vehicle_number=1724`

C/D occupied 칸에서 뒤 4자리 문자열로 단일 차량 조회. 반환은 칸 정보에
lot_id/session_id/entry_at/plate_number/vehicle_number/lookup_type=plate를 포함한다.
요금은 별도 fee API를 조회한다. 형식 오류 422, 단일 일치 없음(없음/중복) 404.
기존 수신기 HTTP_ERROR가 구체적 문구를 일반화할 수 있어 UI는 상태별 안내를 제공한다.

### 가상 제어 `/api/v1/simulation/{lot}` (demo 모드, C/D만)

- GET: lot_id/running/interval_seconds/next_tick/last_error/events(최근 최대30개).
- POST `/{action}`: entry/exit/start/stop/clear. body는 선택적으로 space_id,
  vehicle_number(4자리 문자열), interval_seconds(3~60, 기본8),
  vehicle_type(general/ev/accessible). 종류 생략 시 일반차(등록 번호 9001=ev, 9002=accessible).
- D-17 보완: 수동 입차는 차량 종류와 같은 space_type의 빈칸만 선택하며,
  맞는 칸 없음·등록 번호의 종류 충돌·중복 차량번호는 409. 자동 입차는 종류별
  점유율이 낮은 가용 종류를 선택해 전용칸도 채운다. 차종은 내부 target에 저장해
  재시작 후에도 관측 vehicle_type을 유지한다. 기존 DB target은 general로 이관한다.
- clear는 자동 실행 정지 후에만 허용(실행 중 409), 대상 C/D 전체 차량에 empty 관측을
  보내 정상 출차·세션 종료를 기다린다. 이력/결제는 삭제하지 않고 성공 시 GET과 같은
  제어 상태를 반환한다. 처리 중 추가 제어는 409, 15초 내 안정화 미완료는 503.
  배치 변경 후 운영 순서: stop → clear → start. API/내부 DB 추가 사항은 D-17 리뷰 대상.
- entry/exit 응답은 event_id/lot_id/space_id/action/vehicle_number/session_id/occurred_at
  (안정화 대기 시 일부 식별자는 없을 수 있음), start/stop은 GET과 같은 제어 상태.
- 사용할 수 없는 lot/real 모드는 403. 안정화 시간 초과는 503(같은 상태 재조회).
- Docker는 인증된 관리자 `/admin-api/team/v1/simulation/...` 경유만 허용.
  내부 시뮬레이터는 vision-events 관측을 보내며 점유·세션 직접 기록 금지.

### D-50·D-51 리허설 / 네트워크

김채린 PC의 `demo_main:8002`, DATA_SOURCE=api, BACKEND_BASE_URL=http://127.0.0.1:8002,
LLM_PROVIDER=none. 원격 카메라를 받을 때 API_HOST=0.0.0.0, 같은 VISION_TOKEN과 시각 동기화.
다른 PC는 **서버의 LAN IP**로 접속한다. 127.0.0.1은 각 PC 자신이다.
웹 Docker는 80, 관리자 /admin/login, 8090 게이트웨이는 서버 로컬 전용.
자세한 절차/확인 기록은 [RUNBOOK_DEMO_HOST.md](RUNBOOK_DEMO_HOST.md).

> 이 문서는 **새 규칙을 만들지 않는다.** 11/04 main(`1ca963e`) 코드와 결정(D-01~D-38, [DECISIONS.md](DECISIONS.md))을 받아써서 한 곳에 모은 것이다 (D-26).
> 코드와 이 문서가 다르면 **코드가 기준**이고, 다른 점은 담당자가 고친 뒤 이 문서를 맞춘다. "확인 필요"는 담당자 검토 항목이다.
> API 계약 변경은 PM 김한별 + 해당 담당자 승인.

## 검토 담당

| 장 | 내용 | 검토 |
| --- | --- | --- |
| 1~2 | 식별자·주차장·공통 형식 | 김한별 |
| 3 | vision-event 수신 | 박해준 (전송 조건 우지원) |
| 4 | 조회 (주차장·칸·차량 위치) | 박해준 |
| 5 | 세션 | 박해준 |
| 6 | 요금·결제·매출 | 윤명수 |
| 7 | 경보 (초안, 미병합) | 윤명수·서정범 |
| 8 | 챗봇 | 서정범 |
| 4·6·7·8 소비 필드 | 화면이 읽는 필드 | 김채린 |
| 9 | 실행·포트·환경 | 박해준·김채린·우지원 |

## 구현 위치 요약 (11/04)

| 기능 | 코드 | 실행 서버 |
| --- | --- | --- |
| health, 조회 목업 | `backend/app/main.py`, `api/mock_queries.py` | 팀 서버 `main:app` (8000) |
| vision-event 수신·점유·세션·조회·요금·결제·매출 | `demo_main.py`, `api/occupancy_query_demo.py`, `api/billing_router.py`, `database/parking_sessions.py` | 시연 서버 `python -m backend.app.demo_main` (8002) |
| 세션 목록 `GET /sessions` | `api/session_queries.py` | PR #43 (미병합) |
| 경보 `/alerts` | 3팀 초안 `backend/app/alerts/router.py` | 미병합 |
| 챗봇 `POST /chat` | `chatbot/router.py` → `parking-chatbot/chatbot/http_router.py` | 등록 대기 (`demo_main`에 `include_router`) |

- 11/09까지 `main.py` 하나로 통합한다 (D-20, 박해준 확인 필요).

---

## 1. 식별자·주차장 (결정 사항)

- 기본 경로 `/api/v1`, JSON UTF-8, 필드 `snake_case`
- 시각: ISO 8601, **시간대 필수** (시간대 없는 입력은 422)
- 금액: 원(KRW) 정수. 요금은 **서버만 계산**, 클라이언트는 금액을 보내지 않는다
- ID는 모두 문자열
- `car_id`(실물 미니카 스티커): `"10"`~`"99"` 두 자리 숫자 문자열 (`^[1-9][0-9]$`), `00`~`09` 미사용, 미인식 `null`, `int` 변환 금지 (D-06)
- 스티커 번호·색 배정은 D-31·D-39 (헷갈리는 번호 제외)
- 가상 C/D 차량: 번호판(예: `17가1724`)이 기준 값, 화면은 뒤 4자리. **필드명 미정**(`plate_number` 제안, 박해준). `car_id`에 번호판을 넣지 않는다
- `lot_id`: `A`/`B`(실물), `C`/`D`(가상). 화면 표시 이름은 아래 표
- `space_id`: 주차장별 도면 칸 번호(`A-01`, `B-06` …). 항상 `(lot_id, space_id)`로 식별, 표시는 "개발원주차장 B-06"
- `camera_id`: `camera_a`/`camera_b`, 가상 C/D는 `null`

### 주차장·칸 구성 (D-03·D-14·D-15·D-16·**D-57**)

| 순서 | 주차장 이름 | `lot_id` | 종류 | 도면 | 칸 수 | 칸 번호 | 전기차 전용 | 장애인 전용 |
| --- | --- | --- | --- | --- | :-: | --- | --- | --- |
| 가 | **개발원주차장** | `A` | 실물 | 기본형 | 12 | A-01~A-06, B-01~B-06 | A-05, A-06 | B-06 |
| 나 | **부산역주차장** | `B` | 실물 | 사선형 | 16 | A-01~A-08, B-01~B-08 | A-07, A-08 | B-07, B-08 |
| 다 | **김해공항 주차장** | `C` | 가상 | 공항형 3구역 | **64** | A-01~A-24, B-01~B-36, C-01~C-04 | A-11, A-12, B-11, B-12, B-31, B-32 | A-01~A-06, B-01~B-06, B-25~B-28 |
| 라 | **신세계백화점주차장** | `D` | 가상 | 대형 4구역 | **64** | A-01~A-16, B-01~B-16, C-01~C-16, D-01~D-16 | C-05~C-08, D-05~D-08 | A-05~A-08, B-05~B-08 |

- 합계 **156칸** (실물 28 / 가상 128). 미니카는 최대 30대 (D-54)
- D-57 (11/06 PM): C는 북항주차장 12칸 → **김해공항 주차장 64칸**(구역 A 24 · B 36 · C 4)으로 변경. 구역 C는 `lot_id`와 무관 (예: `lot_id=C, space_id=C-02`)
- 11/06 사용자 최종 배치 요청 반영: C 전기차 6·장애인 16, D 전기차 8·장애인 8. 기존 배포본과 일치시키며 공통 명부 변경은 담당자 리뷰 대상으로 남긴다. P1=A-01~24(24칸), P2=B-01~24(24칸), P3=B-25~36+C-01~04(16칸); 서버 space_id와 세션은 유지한다.
- 라 주차장의 구역 C·D는 `lot_id`와 무관 (예: `lot_id=D, space_id=C-13` = 라 주차장 C구역 13번)
- 전기차·장애인 전용 칸은 일반 차량 주차 불가 (D-05). 위반 처리 방식은 **미정**(박해준)

### 종류·상태 값

| 필드 | 값 | 근거 |
| --- | --- | --- |
| `space_type` (칸 종류) | `general` / `ev` / `accessible` | 조회 응답(코드) |
| `vehicle_type` (차량 종류, 스티커 색) | `general`(흰) / `ev`(초록) / `accessible`(파랑) / `null` | D-07 |
| 칸 `status` | `empty` / `occupied` / `uncertain` / `unavailable` | `simulation/mock_data.py` |
| 세션 `status` | `active`(주차 중) / `closed`(출차 완료) | D-02 |
| 결제 `status` | `completed` / `canceled` (요금 응답 `payment_status`는 `unpaid` 포함) | billing |
| 경보 `status` | `active` / `acknowledged` / `resolved` | 경보 초안 |
| `source` | `vision` / `simulation` / `manual` (`simulation`·`manual`이면 `is_mock: true`) | D-11 |

---

## 2. 공통 형식

### 목록

```json
{"items": [ ... ], "next_cursor": null, "as_of": "2026-11-04T14:00:00+00:00"}
```

- 페이지 나눔은 아직 없다 (`next_cursor`는 항상 `null`). 화면은 `next_cursor`가 있으면 오류로 본다

### 오류

```json
{"error": {"code": "SESSION_NOT_FOUND", "message": "세션(...)을 찾을 수 없습니다.", "details": []}}
```

- 요청 형식 오류는 422 `VALIDATION_ERROR`, `details`에 `[{loc, type, message}]` (시연 서버 처리기)
- 확인 필요: 팀 서버 `main:app`에는 아직 이 422 처리기가 없다 (FastAPI 기본 `{"detail": [...]}`) → `main.py` 통합 때 같이 맞춘다

### 오류 코드 표

| 코드 | HTTP | 발생 위치 |
| --- | :-: | --- |
| `VALIDATION_ERROR` | 422 | 시연 서버 요청 형식 오류 (메시지가 "관측 요청 형식…"으로 나옴, 확인 필요) |
| `UNAUTHORIZED` / `FORBIDDEN` / `HTTP_ERROR` | 401 / 403 / 기타 | 시연 서버 인증·HTTP 예외 |
| `EVENT_ID_CONFLICT` | 409 | vision-event: 같은 `event_id`, 다른 내용 |
| `EXPERIMENT_STORE_FULL` | 503 | vision-event: 접수함 한도 (시연 서버 100,000건) |
| `PARKING_LOT_NOT_FOUND` | 404 | 조회: 없는 주차장 / 매출: 없는 `lot_id` |
| `VEHICLE_NOT_FOUND` | 404 | 차량 위치: 없음 또는 한 칸으로 특정 불가 |
| `SESSION_NOT_FOUND` | 404 | 요금 조회·결제 |
| `INVALID_INPUT` | 400 | 결제: 필수값 없음 |
| `QUOTE_NOT_FOUND` | 404 | 결제: 견적 없음 |
| `QUOTE_MISMATCH` | 409 | 결제: 다른 세션의 견적 |
| `QUOTE_EXPIRED` | 409 | 결제: 견적 만료 (기본 300초) |
| `ALREADY_PAID` | 409 | 결제: 이미 완료된 세션 (`details`에 `existing_payment_id`) |
| `IDEMPOTENCY_CONFLICT` | 409 | 결제: 같은 멱등키를 다른 세션에 사용 |
| `PAYMENT_CONFLICT` | 409 | 결제: 동시 처리 충돌 |
| `PAYMENT_NOT_FOUND` | 404 | 결제 취소 |
| `INVALID_PAYMENT_STATE` | 409 | 결제 취소: `completed`가 아님 |
| `SESSION_WRITE_BUSY` | 503 | 세션 서비스: DB 잠김 |
| `INVALID_SESSION_ID` / `INVALID_LOT_ID` / `INVALID_CAR_ID` / `INVALID_SESSION_TIME` | 422 | 세션 서비스 입력 |
| `SESSION_ID_CONFLICT` / `ACTIVE_SESSION_EXISTS` / `SESSION_ALREADY_CLOSED` | 409 | 세션 서비스 상태 충돌 |
| `SESSION_STATE_UNAVAILABLE` | 503 | 세션 서비스 상태 확인 불가 |
| `ALERT_NOT_FOUND` / `INVALID_ALERT_TRANSITION` / `INVALID_ALERT_EVENT` | 404 / 409 / 422 | 경보 초안 |

- 확인 필요: 시연 서버에서 세션 서비스 오류(`ACTIVE_SESSION_EXISTS` 등)가 vision-event 응답으로 나갈 때 코드가 `HTTP_ERROR`로 바뀐다 (11/04 실측, 박해준)

---

## 3. vision-event 수신 (카메라·AI·시뮬레이터 → 서버)

`POST /api/v1/internal/vision-events` · 헤더 `Authorization: Bearer <토큰>` · 코드 `api/vision_event_schema.py`, `api/vision_event_receiver.py`

### 요청 필드 (정의되지 않은 필드는 422)

| 필드 | 형식 | 규칙 |
| --- | --- | --- |
| `event_id` | 문자열 | 필수, 빈 문자열 불가. 재전송 시 같은 값 |
| `event_type` | `vehicle_observed` / `space_empty_observed` / `plate_observed` / `mock_fire_led_observed` | 필수 |
| `source` | `vision` / `simulation` / `manual` | 필수 |
| `lot_id` | `A`~`D` | 필수 |
| `camera_id` | 문자열 / `null` | 필수 키. C·D는 `null` |
| `space_id` | 문자열 / `null` | 필수 키, 도면 칸 번호 |
| `car_id` | `"10"`~`"99"` / `null` | 필수 키 |
| `vehicle_type` | `general` / `ev` / `accessible` / `null` | 선택 |
| `confidence` | 0~1 실수 | 필수 |
| `bbox_xyxy` | `[x1, y1, x2, y2]` / `null` | 필수 키, 양의 너비·높이 |
| `observed_at` | ISO 8601 | 필수, 시간대 필수 |
| `frame_id` | 문자열 / `null` | 선택 |

### 응답

| HTTP | 뜻 |
| :-: | --- |
| 202 | 접수 `{"event_id", "disposition": "accepted"}` |
| 200 | 같은 `event_id`·같은 내용 재전송 (중복) |
| 409 | `EVENT_ID_CONFLICT` |
| 422 | 형식 오류, 도면에 없는 칸 |
| 403 | 토큰 없음·불일치 |
| 503 | `EXPERIMENT_STORE_FULL` |

### 전송 조건 (점유 엔진 설정: 시연 서버)

| 설정 | 값 | 카메라 쪽 의미 |
| --- | --- | --- |
| `occupy_seconds` | 2 | 같은 관측이 2초 이어지면 점유 확정 |
| `empty_seconds` | 3 | 3초 이어지면 빈칸 확정 → 세션 종료 |
| `max_gap_seconds` | 2 | **칸마다 2초 이내 간격으로 계속 보내야 한다.** 넘으면 확정된 칸도 `uncertain` (11/04 실측) → **1초 간격 권장** |
| `stale_seconds` | 10 | 10초 이상 관측이 없으면 `uncertain` (빈칸 아님) |
| `min_confidence` | 0.8 | 미만이면 `uncertain` |

- **기본 입차 방식 (D-30):** 칸 위에서 번호를 읽어 `vehicle_observed` 하나에 `space_id`+`car_id`(+`vehicle_type`)를 함께 보낸다. 번호를 못 읽으면 `car_id: null`로 보내고 점유는 유지된다
- **결과만 전송 (D-23):** 사진·영상은 보내지 않는다
- 가림·끊김 중에는 `space_empty_observed`를 보내지 않는다 (가짜 출차 방지)
- 카메라 PC와 서버의 시각 차이는 10초 이내 (D-28)
- 같은 칸에 `vision`과 `simulation`을 섞어 보내지 않는다 (안정화가 계속 초기화됨)
- `plate_observed`(`space_id: null`)는 현재 **422로 거절**된다. 근접 판독 예비안(D-19 원안)으로 바꿀 때 입차 매칭과 함께 처리 (미정)
- `mock_fire_led_observed`는 접수만 되고 경보 생성과는 아직 연결되지 않았다 (7장)

---

## 4. 조회 (화면·챗봇)

### `GET /api/v1/parking-lots`

목록 형식. 항목:

| 필드 | 형식 |
| --- | --- |
| `lot_id` | 문자열 |
| `name` | 주차장 이름 |
| `type` | `real` / `virtual` |
| `total_spaces` · `empty_spaces` · `uncertain_spaces` · `unavailable_spaces` | 정수 |

- 화면 지도는 layout_type이 없을 때 lot_id A=basic, B=angled, C=loop, D=mall로 매핑한다. 서버 필드 추가는 별도 협의한다.

### `GET /api/v1/parking-lots/{lot_id}/spaces`

목록 형식. 항목:

| 필드 | 형식 |
| --- | --- |
| `space_id` | 도면 칸 번호 |
| `status` | 칸 상태 |
| `car_id` | `"10"`~`"99"` / `null` |
| `observed_at` | 시각 / `null` |
| `confidence` | 실수 / `null` (시연 서버는 `null`) |
| `version` | 정수 (관측마다 증가) |
| `space_type` | `general` / `ev` / `accessible` |
| `vehicle_type` | 차량 종류 / `null` |
| `source` | `vision`·`simulation`·`manual` / `null` |
| `is_mock` | 참·거짓 / `null` |
| `entry_at` | **예정 (D-21)**: 시간대 포함 / `null` |

- 없는 주차장 → 404 `PARKING_LOT_NOT_FOUND`

### `GET /api/v1/vehicles/{car_id}/location`

| 필드 | 형식 |
| --- | --- |
| `car_id` · `lot_id` · `space_id` | 문자열 |
| `session_id` | 문자열 / `null` (시연 서버: 점유 확정 시 세션 ID) |
| `status` | `occupied` / `uncertain` |
| `observed_at` | 시각 |
| `confidence` | 실수 / `null` |
| `vehicle_type` | 차량 종류 / `null` |
| `source` · `is_mock` | 출처·모의 여부 |

- 없음 또는 여러 칸 → 404 `VEHICLE_NOT_FOUND`
- 팀 서버 목업 응답에는 헤더 `X-ParkFlow-Data-Source: mock`

---

## 5. 세션

- 저장: `parking_sessions(session_id, lot_id, space_id, car_id, entry_at, exit_at, status)` — billing과 같은 표 (PR #30·#41)
- 세션 ID는 문자열 그대로 (`ses_…`·UUID), 접두어로 판단하지 않는다
- 시연 서버: 점유 확정 → `active` 세션 생성, 빈칸 확정 → `closed` (PR #36)
- 식별 차량은 주차장과 관계없이 활성 세션 1개

### `GET /api/v1/sessions` (PR #43, 미병합)

| 쿼리 | 값 |
| --- | --- |
| `lot_id` | `A`~`D` |
| `car_id` | `^[1-9][0-9]$` |
| `status` | `active` / `closed` |

- 목록 형식. 항목: `session_id`, `lot_id`, `space_id`, `car_id`, `entry_at`, `exit_at`, `status`
- 정렬: 입차 시각 최신순
- 관리자 "현재 입차 세션"은 `?status=active`로 부른다

---

## 6. 요금·결제·매출 (`api/billing_router.py`)

### `GET /api/v1/sessions/{session_id}/fee`

쿼리 `now`(선택, 계산 기준 시각). 호출할 때마다 **견적**이 새로 만들어진다.

| 필드 | 형식 |
| --- | --- |
| `session_id` · `lot_id` · `space_id` · `car_id` | 문자열 / `null` |
| `entry_at` · `exit_at` | 시각 / `null` |
| `target_at` | 계산 기준 시각 (출차 시각 또는 조회 시각) |
| `duration_minutes` | 정수 |
| `fee_breakdown` | `{base_fee_krw, extra_units, extra_fee_krw, daily_cap_krw, cap_discount_krw}` |
| `amount_krw` | 정수 |
| `is_daily_capped` | 참·거짓 |
| `payment_status` | `unpaid` / `completed` / `canceled` |
| `rate_policy` | `{base_minutes, base_fee, unit_minutes, extra_fee, daily_cap}` |
| `quote_id` · `quote_expires_at` | 견적 ID·만료 시각 (기본 300초, `FEE_QUOTE_TTL_SECONDS`) |

- 화면은 견적 2분 뒤 초기화, 서버 견적은 5분 (11/04 확인)
- 확인 필요: 이 시각들은 `+09:00`(KST), 점유·조회 시각은 `+00:00`(UTC)이다. 둘 다 규칙에 맞지만 화면은 KST로 바꿔 표시한다

### `POST /api/v1/mock-payments`

```json
{"session_id": "ses_0001", "quote_id": "quote_…", "idempotency_key": "<요청마다 고유>", "payment_method": "MOCK_CARD"}
```

- 정의되지 않은 필드는 422 (금액·시각을 보내면 거절)
- `payment_method`: `MOCK_CARD`(기본) / `MOCK_EASY_PAY` / `MOCK_ACCOUNT`. 화면은 변형 이름 표시 (D-10)
- 성공 200: `payment_id`, `session_id`, `lot_id`, `car_id`, `amount_krw`, `payment_method`, `status`(`completed`), `idempotency_key`, `created_at`, `canceled_at`, `refund_reason`
- 같은 멱등키·같은 세션 재요청 → 200 기존 결과 + `idempotent_replayed: true`, `notice`
- 오류: `INVALID_INPUT`, `SESSION_NOT_FOUND`, `QUOTE_*`, `ALREADY_PAID`, `IDEMPOTENCY_CONFLICT`, `PAYMENT_CONFLICT`

### 그 밖

| 메서드·경로 | 내용 |
| --- | --- |
| `GET /api/v1/mock-payments` | 목록 형식. 필터 `lot_id`, `status`(`completed`/`canceled`), `car_id`. 최신순 |
| `POST /api/v1/mock-payments/{id}/cancel` | 본문 선택 `{"refund_reason"}`. 200: `payment_id`, `session_id`, `lot_id`, `car_id`, `refunded_amount_krw`, `status`(`canceled`), `canceled_at`, `refund_reason`, `revenue_adjusted` |
| `GET /api/v1/rate-policies` | 목록 형식. `lot_id`, `base_minutes`, `base_fee`, `unit_minutes`, `extra_fee`, `daily_cap` |
| `GET /api/v1/reports/revenue` | 쿼리 `month`(`YYYY-MM` 또는 `ALL`, 기본 `ALL`), `lot_id`(선택, PR #41). 응답: `filter_month`, `lot_id`, `aggregated_basis`, `summary{gross_amount_krw, canceled_amount_krw, net_revenue_krw, completed_count, canceled_count, by_type}`, `lots[]`, `daily_revenue[]`, `monthly_revenue[]` |

### 요금표 (D-03, PR #20)

| 주차장 | 기본 30분 | 10분당 | 일 최대 |
| --- | ---: | ---: | ---: |
| A 개발원 | 1,000원 | 500원 | 10,000원 |
| B 부산역 | 2,000원 | 700원 | 20,000원 |
| C 김해공항 | 3,000원 | 800원 | 40,000원 |
| D 신세계백화점 | 4,000원 | 1,000원 | 50,000원 |

---

## 7. 경보 — 초안 (3팀 윤명수·서정범, D-29, 미병합)

> 검토 중이며 필드가 바뀔 수 있다. 확정되면 담당자가 이 장을 고친다.

| 메서드·경로 | 내용 |
| --- | --- |
| `GET /api/v1/alerts` | 목록 형식 + `total`, `active_count`, `acknowledged_count`, `resolved_count`. 필터 `lot_id`, `status`, `type`, `limit`(1~500) |
| `GET /api/v1/alerts/{alert_id}` | 상세 |
| `POST`·`PATCH /api/v1/alerts/{alert_id}/acknowledge` | `active` → `acknowledged`. 해제된 경보는 409 |
| `POST`·`PATCH /api/v1/alerts/{alert_id}/resolve` | → `resolved` |
| `POST /api/v1/internal/alerts` | 내부 생성 (Bearer `ALERTS_INTERNAL_TOKEN`), 본문은 vision-event `mock_fire_led_observed`와 같은 필드. 공개 생성 API 없음 |

경보 항목:

| 필드 | 형식 |
| --- | --- |
| `alert_id` | `alt_…` |
| `lot_id` · `camera_id` · `space_id` | 문자열 / `null` |
| `type` | `mock_fire_led` |
| `level` | `1`(주의) / `2`(대피 안내·결제 중단). LED 모의 화재 기본 2 (서정범 확인 필요) |
| `status` | `active` / `acknowledged` / `resolved` |
| `source` · `is_mock` | 출처·모의 여부 |
| `message` | "[모의 화재] 개발원주차장 A-03 빨간 LED 점등 (실제 화재 아님)" |
| `confidence` · `frame_id` | 실수·문자열 / `null` |
| `detected_at` · `acknowledged_at` · `resolved_at` | 시각 / `null` |
| `actor` | 처리자 |

- 같은 칸 미해제 경보가 있으면 새로 만들지 않고 갱신한다. 같은 `event_id` 재전송은 같은 경보
- **"모의 화재"로만 표기** (CLAUDE.md 규칙 6)
- 미정: 수신기에서 LED 이벤트를 받으면 경보를 만드는 연결(박해준 변경 요청), 화면 "모의 경보 발생" 버튼 경로(`POST /demo/alerts` 사용 중, 김채린), 전용 칸 위반 경보(`restricted_space_violation`)

---

## 8. 챗봇 (`chatbot/http_router.py`, 서정범)

### `POST /api/v1/chat`

| 요청 필드 | 형식 | 비고 |
| --- | --- | --- |
| `session_id` | 1~128자 | 필수, 화면 하나당 고정 |
| `text` | 최대 200자 | `chip_id`와 둘 중 하나 |
| `chip_id` | 최대 128자 | 버튼을 눌렀을 때 |
| `event` | 최대 32자 | 선택 |
| `request_id` | 1~128자 | 권장. 같은 값 재전송 시 결제가 두 번 되지 않음 |
| `confirmation_token` | 최대 128자 | 확인 버튼(차량 확인·결제 확인) |
| `runtime_id` | 최대 128자 | 선택 |

| 응답 필드 | 형식 | 화면 |
| --- | --- | --- |
| `text` | 문자열 (`\n` 포함) | **답변 기준 필드** |
| `messages` | `[{text}]` | 팀 앱 호환 (`messages[0].text == text`, 11/04 실측) |
| `chips` | `[{id, label, confirmation_token?}]` | 버튼 |
| `confirmation_token` | 문자열 / `null` | 확인 단계 |
| `flow` | `confirm_vehicle` / `confirm_action` / `idle` 등 | 현재 단계 |
| `card` | `{type, data}` / `null` | 선택 표시 |
| `banner` · `safety` | 객체 / `null` | 모의 화재 배너·대피 안내 |
| `vehicle_context` | `{car_id, state, …}` | 대화 중인 차량 |

- 그 밖(`meta`, `runtime_id`, `state_version`, `alert_version`, `updated_at`, `destination`, `catalog`)은 화면에서 무시해도 된다
- 오류: 400 형식, 409 같은 세션 동시 요청, 503 팀 서버 응답 없음
- 그 밖 경로: `GET /api/v1/chat/config`·`/suggest`·`/updates`, 관리자용 `/admin/*`·`/incidents/*`·`/demo/*`. 프론트 nginx는 지금 `POST /api/v1/chat`만 연다
- 챗봇은 조회 API만 근거로 답한다 (규칙 5). 서버 실행 시 `DATA_SOURCE=api`, `BACKEND_BASE_URL=<서버 주소>`
- LLM: OpenRouter + `google/gemma-4-26b-a4b-it`, 키 `OPENROUTER_API_KEY`, 기본 `LLM_PROVIDER=none` (D-18·D-33)

---

## 9. 실행·포트·환경

### 포트 (D-25)

| 포트 | 용도 |
| :-: | --- |
| 8000 | 팀 서버 `main:app` |
| 8002 | 시연 서버 `demo_main` (챗봇 포함 예정) |
| 8003 | 김채린 데모 서버 (프론트 `/demo-api`) |
| 8080 | 챗봇 단독 실행 (개발용) |
| 8090 | 관리자 로그인 게이트웨이 `admin-auth` |
| 5173 | 프론트 개발 서버 |
| 80 | 프론트 Docker (nginx) |

- 프론트 vite.config.js 기본값은 /api → 8000, /demo-api → 8003, 관리자 프록시 → 8090이다. API_PROXY_TARGET·DEMO_API_PROXY_TARGET·ADMIN_PROXY_TARGET으로 변경할 수 있다.

### 서버·Docker (D-20·D-28·D-38)

- 11/06은 `demo_main`, 11/09까지 `main.py` 하나로 통합
- Docker 서비스 이름: `api`(팀 서버) · `web`(화면, nginx `/api` → `api:8000`) · `admin-auth`
- DB: `data/smart_parking.db` 한 파일 (`SMART_PARKING_DB_PATH`), Docker에서는 볼륨. 시연 서버는 현재 임시 폴더(재시작 시 초기화)

### 카메라 연결 (D-28·D-34)

| 모드 | 카메라 모듈 설정 | 서버 |
| --- | --- | --- |
| L. 시연 PC에 직접 연결 | `API_BASE_URL=http://127.0.0.1:8000` | 그대로 |
| R. 다른 PC에서 전송 | `API_BASE_URL=http://<시연 PC IP>:8000` | 같은 네트워크에 공개, 방화벽 8000 |

- 토큰은 환경변수 `VISION_TOKEN` (예정, 지금 시연 서버는 실행 시 키보드 입력)
- 카메라·모델 실행은 우지원 노트북

---

## 10. 미정

| 항목 | 담당 |
| --- | --- |
| 입차 매칭 (근접 판독 예비안 전환 시: 대기 시간·`entry_at`·동시 입차) | 박해준 |
| 가상 번호판 필드명(`plate_number`)·4자리 검색 API | 박해준 |
| 전용 칸 위반 판정·경보 | 박해준·3팀 |
| 칸 응답 `entry_at` 구현 (D-21) | 박해준 |
| 경보 필드 확정·수신기 연결 | 윤명수·서정범·박해준 |
| 지도 `layout_type` (서버 추가 또는 화면 매핑) | 김채린·박해준 |
| 팀 서버 422 오류 형식 통일 | 박해준 |

### 챗봇 응답 언어 선택 (화면 통합 보완)

- POST `/api/v1/chat`의 선택 필드 `language`: `ko`, `en`, `zh-CN`, `ja`.
- 생략하면 기존 한국어 응답/필드 구성을 유지한다. `ko`로 요청했지만 질문이 영어·중국어·일본어인 경우 입력 문자로 해당 언어를 선택한다. 숫자만 입력하거나 한국어를 포함하면 한국어를 사용한다.
- 언어를 명시한 POST 응답에는 선택된 `language`를 포함한다. 프론트는 다음 추천 버튼 요청과 GET `/api/v1/chat/updates`의 선택 query `language`에 대화 언어를 전달한다. 새 텍스트 질문은 화면 선택 언어/입력 언어로 다시 판별한다.
- 비한국어 응답은 상태 엔진이 확정한 text, chips.label, banner/safety 안내를 번역하고 `translation_status=translated`를 추가한다. request_id, 확인 토큰, 카드 수치/ID, 결제·경보 레벨은 그대로 유지한다. 주차장명·차량번호·칸 번호·숫자·원화 단위는 보호 토큰으로 검증한다.
- 번역 실패는 확정된 원본 정보(특히 안전/영수증)를 보존하고 선택 언어의 실패 안내 및 `translation_status=unavailable`를 반환한다. 번역을 위한 업무 재실행은 하지 않는다.
- 외부 번역은 설정된 OpenRouter를 사용하며 최대 8초, 동시 처리 2개, 메모리 캐시 128개로 제한한다. UI의 챗봇 응답 제한은 20초다. 한국어와 변경 없는 빈 polling에는 외부 번역을 호출하지 않는다.
