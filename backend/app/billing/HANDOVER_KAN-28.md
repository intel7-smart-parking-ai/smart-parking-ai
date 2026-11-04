# KAN-28 인수인계 — 요금·모의 결제·매출 (EPIC 05)

> 작성 기준: 2026-11-04, main `683f0f2`. 담당 윤명수 (GitHub `m258k2002-lgtm` / `Jamesyyoon`).
> 이 문서만 읽고 바로 이어서 작업할 수 있도록 **구현 내용 · 코드 위치 · 실행 방법 · 테스트 결과 · 미완료 항목**을 정리한다.
> 팀 규칙은 `CLAUDE.md`, 결정은 `docs/DECISIONS.md`, 전체 규격은 `docs/API_CONTRACT.md` 6장(요금·결제·매출)이 기준이다.

## 1. 한눈에 보기

| 항목 | 상태 |
| --- | --- |
| 요금 계산 (주차장별 요금표, 일 최대요금) | ✅ 완료 |
| 요금 견적 `quote_id` (5분 유효, 견적 금액으로 결제) | ✅ 완료 (D-09) |
| 모의 결제 (멱등키로 중복 방지) · 취소·모의 환불 | ✅ 완료 |
| 결제 목록 · 요금표 조회 · 일별/월별/주차장별 매출 | ✅ 완료 (매출 `lot_id` 필터 포함) |
| 세션 상태 `status` 칸 (`active`/`closed`) | ✅ 완료 (D-02) |
| 시연 서버(`demo_main`)에서 동작 | ✅ 확인 (요금 → 견적 결제 → 매출) |
| 팀 서버 `main.py` 등록 | ⏳ 박해준 담당, 11/09 통합 예정 (D-20) |
| 진행률 | **약 97%** |

## 2. 브랜치·PR 이력 (모두 main 병합 완료)

| PR | 브랜치 | 내용 |
| --- | --- | --- |
| #5 | `feature/KAN-28-billing` | 요금 계산·결제·취소·매출 API, 팀 공통 에러·목록 형식, 멱등키 409, 동시 요청 SQLite 스레드 오류 수정 |
| #15 | `feature/KAN-28-fee-quote` | 견적 `quote_id`·`QUOTE_*` 오류, 세션 상태 `active/closed`, car_id 두 자리 |
| #20 | `feature/KAN-28-rate-policies` | 주차장 4곳 요금표 기본 데이터, 라 주차장 이름 |
| #35 | `fix/KAN-28-fee-schema` | `FeeResponse` 스키마를 실제 요금 응답 필드에 맞춤 |
| #41 | `feature/KAN-28-session-status` | `parking_sessions.status` 칸, 매출 리포트 `lot_id` 필터 |

새 작업은 **최신 main에서 새 브랜치**로 시작한다 (`git switch main` → `git pull` → `git switch -c feature/KAN-28-<기능명>`). 위 브랜치들은 이미 병합돼 다시 쓰지 않는다.

## 3. 코드 위치

| 파일 | 역할 | 주요 함수·내용 |
| --- | --- | --- |
| `backend/app/billing/calculator.py` | **요금 계산 순수 함수** (DB 없음) | `calculate_parking_fee_pure(entry_iso, target_iso, policy)` → `duration_minutes`, `fee_breakdown`, `amount_krw`, `is_daily_capped` |
| `backend/app/billing/db.py` | SQLite 스키마·연결·기본 데이터 | `SCHEMA`(테이블 6개), `init_schema()`(예전 DB에 `status` 칸 자동 추가), `seed_defaults()`(빈 DB에만 주차장·요금표), `get_db_conn()`(요청마다 새 연결, FastAPI 의존성) |
| `backend/app/billing/payment_service.py` | 견적·결제·취소 | `create_fee_quote()`, `_check_quote()`, `process_mock_payment()`, `cancel_mock_payment()`, `list_mock_payments()`, `quote_ttl_seconds()` |
| `backend/app/billing/revenue_service.py` | 매출 집계 | `generate_revenue_report(conn, month_filter, lot_filter)` |
| `backend/app/billing/schemas.py` | Pydantic 모델 | `PaymentCreateRequest`(결제 요청 검증, `extra=forbid`), `FeeResponse` 등(문서용) |
| `backend/app/api/billing_router.py` | **HTTP 라우터** (`/api/v1`) | `_calculate_fee_helper()`(세션 → 요금), `_respond()`(에러를 팀 형식으로 변환), 엔드포인트 6개 |
| `clients/chatbot_billing_tools.py`, `clients/parking_billing_client.js` | 초기 연동 예시 클라이언트 | ⚠️ 옛 규격 (아래 7장) |
| `tests/test_api_integration.py` | API 통합 테스트 (대부분 billing) | 요금 경계값, 견적, 결제·재전송·충돌, 취소, 매출, 에러 형식, 필터 |
| `tests/test_billing.py` | 요금 계산 단위 테스트 | 55분 → 2,500원 |

> `billing_router.py`는 `backend/app/api/` 폴더에 있지만 **billing 담당 파일**이다 (KAN-28 PR로 관리).

### DB 테이블 (`db.py` `SCHEMA`)

| 테이블 | 용도 |
| --- | --- |
| `parking_lots` | 주차장 4곳 (이름·종류·칸 수) |
| `rate_policies` | 주차장별 요금표 |
| `parking_sessions` | 입출차 세션 (`session_id`, `lot_id`, `space_id`, `car_id`, `entry_at`, `exit_at`, `status`) — **박해준 세션 서비스와 같은 표를 공유** |
| `mock_payments` | 모의 결제 (`completed`/`canceled`, 멱등키, 금액) |
| `fee_quotes` | 요금 견적 (`quote_id`, 금액, 만료 시각) |
| `audit_logs` | 결제·환불 기록 |

- DB 파일: 기본 `data/smart_parking.db` (Git 제외), 환경변수 **`SMART_PARKING_DB_PATH`**로 변경
- 시연 서버(`demo_main`)는 임시 폴더 DB를 씀 → 재시작마다 초기화
- 최종 시연은 한 파일을 Docker 볼륨에 둠 (D-38)

## 4. 요금 규칙

- 기본 시간까지 기본요금, 초과분은 **추가 단위(분)마다** 추가요금, **하루 최대요금** 상한, 24시간 넘으면 하루 단위로 상한 반복
- 이용 시간은 **분 올림**(최소 1분), 출차 세션은 출차 시각, 주차 중이면 조회 시각(`?now=`로 시험 가능) 기준
- **요금은 서버만 계산한다** (CLAUDE.md 규칙 4). 화면·챗봇은 응답 금액을 그대로 표시

| 주차장 | 기본 30분 | 추가 10분당 | 하루 최대 |
| --- | ---: | ---: | ---: |
| A 개발원주차장 | 1,000원 | 500원 | 10,000원 |
| B 부산역주차장 | 2,000원 | 700원 | 20,000원 |
| C 북항주차장 | 3,000원 | 800원 | 40,000원 |
| D 신세계백화점주차장 | 4,000원 | 1,000원 | 50,000원 |

## 5. API 요약 (상세는 `docs/API_CONTRACT.md` 6장)

| 메서드 | 경로 | 설명 |
| --- | --- | --- |
| GET | `/api/v1/sessions/{session_id}/fee` | 요금 조회 + **견적 발급**(`quote_id`, `quote_expires_at`). `?now=`로 기준 시각 지정 |
| POST | `/api/v1/mock-payments` | 결제. body `{session_id, quote_id, idempotency_key, payment_method}` — **금액은 받지 않음** |
| GET | `/api/v1/mock-payments` | 결제 목록 (`lot_id`·`status`·`car_id` 필터) |
| POST | `/api/v1/mock-payments/{id}/cancel` | 취소·모의 환불 (매출에서 차감) |
| GET | `/api/v1/rate-policies` | 주차장별 요금표 |
| GET | `/api/v1/reports/revenue` | 매출 (`month`=YYYY-MM 또는 ALL, `lot_id`) |

- 결제 수단 값: `MOCK_CARD`·`MOCK_EASY_PAY`·`MOCK_ACCOUNT` (화면 표시는 변형 이름, D-10)
- 견적 유효 시간: 기본 300초, 환경변수 **`FEE_QUOTE_TTL_SECONDS`** (화면은 2분 뒤 초기화)
- 목록 형식 `{items, next_cursor, as_of}`, 에러 형식 `{"error": {code, message, details}}`

### 에러 코드

| 코드 | 언제 |
| --- | --- |
| `SESSION_NOT_FOUND` | 없는 세션 요금·결제 |
| `QUOTE_NOT_FOUND` / `QUOTE_MISMATCH` / `QUOTE_EXPIRED` | 없는 견적 / 다른 세션 견적 / 만료 → 요금 재조회 |
| `ALREADY_PAID` | 이미 결제된 세션 |
| `IDEMPOTENCY_CONFLICT` | 같은 멱등키로 다른 요청 |
| `PAYMENT_CONFLICT` | 동시 결제 충돌 |
| `PAYMENT_NOT_FOUND` / `INVALID_PAYMENT_STATE` | 없는 결제 취소 / 완료가 아닌 결제 취소 |
| `PARKING_LOT_NOT_FOUND` | 매출 `lot_id`가 없는 주차장 |
| `INVALID_INPUT` | 필수값 누락 |

## 6. 실행·테스트 방법 (Windows, 저장소 루트)

```powershell
# 처음 한 번 (또는 git pull 뒤 라이브러리 갱신)
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend/requirements-dev.txt

# 테스트
.\.venv\Scripts\python.exe -m pytest tests/test_api_integration.py tests/test_billing.py -q   # billing만
.\.venv\Scripts\python.exe -m pytest tests/ -q                                                # 전체

# 시연 서버 (요금·결제·매출 + 가짜 이벤트·점유·세션이 연결된 서버)
.\.venv\Scripts\python.exe -m backend.app.demo_main        # 127.0.0.1:8002, 실행 시 토큰 입력
# 브라우저 http://127.0.0.1:8002/docs 에서 요금 조회 → 결제 → 취소 → 매출 눌러보기
```

- 팀 서버 `uvicorn backend.app.main:app`에는 **아직 billing이 등록돼 있지 않다** (11/09 통합 예정)
- 가짜 차량으로 세션을 만들려면 다른 터미널에서 `python -m backend.app.simulation.run_parking_demo --short --hold` (같은 토큰)

### 테스트 결과 (2026-11-04, main `683f0f2`)

- billing 테스트(`test_api_integration.py` + `test_billing.py`): **45 passed**
- 저장소 전체: **331 passed**
- 시연 서버 실측:
  - 입차 → 위치의 `session_id` → 요금 1,000원 → 견적 결제 `completed`
  - 같은 멱등키 재전송은 재반환, 다른 키는 `ALREADY_PAID`
  - 가짜·다른 세션·만료 견적 → `QUOTE_*`
  - 챗봇 결제도 서버에 기록됨
- 이전 실측: 동시 결제 20건 → 1건 성공, 서버 오류 0

## 7. 미완료 항목 (이어서 할 일)

| 우선 | 항목 | 내용 | 관련 |
| --- | --- | --- | --- |
| 🟠 | 리허설 확인 | 11/05 오후·11/06 오전 리허설에서 요금 → 결제 → 취소 → 매출 화면 흐름 확인 | D-35 |
| 🟠 | `main.py` 등록 후 재확인 | 박해준이 11/09 `main.py` 통합하면 팀 서버에서 같은 흐름 확인, DB 경로(`SMART_PARKING_DB_PATH`) 볼륨 연결 확인 | D-20·D-38 |
| 🟡 | **`clients/` 예시 클라이언트 갱신 또는 정리** | `parking_billing_client.js`의 `pay()`가 **`quote_id` 없이** 결제 요청 → 지금 서버는 거절(422). `chatbot_billing_tools.py`도 견적 흐름 없음. 실제 화면·챗봇은 자체 코드를 써서 영향은 없지만, 쓸 거면 견적 흐름으로 고치고 안 쓰면 삭제 | D-09 |
| 🟡 | 시간대 | 요금·견적 시각은 `+09:00`(KST), 점유·조회는 `+00:00`(UTC). 둘 다 규칙 위반은 아니지만 화면 변환 필요 — 통일할지 팀 결정 | API_CONTRACT "확인 필요" |
| 🟡 | `parking_lots` 정의 단일화 | billing `parking_lots`와 공용 SQLAlchemy 모델(`backend/app/database/models.py`)의 주차장 표 정의가 다름 → 통합 시 한쪽으로 | 박해준 협의 |
| ⚪ | 추가 기능 (아이디어 단계) | 30분 뒤 예상 요금, 구매 금액 할인·무료 주차 — **서버(billing)에서 계산**해야 함. PM 배정 후 | D-24 |
| ⚪ | 경보 API | 별도 작업 (`backend/app/alerts/`, PR #46, 윤명수·서정범 공동) — KAN-28 아님 | D-29 |

## 8. 주의 사항

- **요금은 서버만 계산**: 클라이언트가 금액을 보내지 않는다 (`PaymentCreateRequest`는 `extra=forbid`라 `amount_krw`를 보내면 422)
- **결제는 모의 결제만**: 실제 결제사 연동·실제 회사명 금지 (D-10)
- `car_id`는 `"10"`~`"99"` 문자열, 미인식 차량은 `null`이어도 요금·결제 가능
- `parking_sessions`는 박해준 세션 서비스와 **공용**이다. 칸 추가·변경은 박해준과 먼저 합의 (11/04 `status` 칸 때 테스트 19개가 깨질 뻔함)
- 비밀값·DB 파일은 Git에 올리지 않는다 (`data/*.db`는 `.gitignore`)
- 다른 사람 폴더는 직접 고치지 말고 변경 요청 (CLAUDE.md)
