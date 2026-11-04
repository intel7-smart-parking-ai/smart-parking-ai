# 통합 가이드 — 김채린 통합 담당용 (2026-11-04)

> **목적:** 김채린이 다른 팀원의 코드를 받아 하나로 통합할 때, **반드시 지켜야 할 팀 결정·데이터 규약·연결 방법**을 한 곳에 모았다.
> **기준:** main `683f0f2` + 열린 PR #45·#46·#47, `docs/DECISIONS.md` D-01~D-40, `docs/API_CONTRACT.md` v1.
> **표시:** ✅ main에 있음 · ◐ PR/브랜치 · ☐ 아직 없음 · ⚠️ 주의

## 0. 역할

| 역할 | 담당 | 비고 |
| --- | --- | --- |
| **UI 통합** (사용자 웹·모바일·관리자 화면, 다른 팀원 코드 연결) — **챗봇 제외** | **김채린** | D-40·D-41 (16:08 정정) |
| **챗봇·음성인식** | **서정범·윤명수** 공동 | D-41 |
| 백엔드 파일 (`main.py` 통합·LED→경보 연결·칸 `entry_at` 등) | **김채린** (통합 담당) — 기존 코드를 최대한 따르고 팀 규정에 문제없는 범위에서 수정 | D-42 |
| 카메라·ROI 지원 | **박해준** → 우지원 지원 | D-42 |
| 인수인계 제공 | 박해준(백엔드) · 윤명수(요금) · 우지원(카메라·ROI) | 각자 문서로 전달 (아래 1장) |
| 결정·일정·승인 | 김한별(PM) | main 병합은 PM 승인 |

⚠️ **백엔드 파일 수정 기준 (D-42)**: 기존 구조·함수·이름을 최대한 따르고, CLAUDE.md 절대 규칙·데이터 규약·DECISIONS와 충돌하지 않을 때만 수정한다. 애매하면 PM에게 먼저 묻는다.

⚠️ 통합 담당이어도 **각 모듈의 내부 로직은 원 담당자 코드를 그대로 재사용**한다. 고쳐야 하면 원 담당자에게 알리고 같이 확인한다 (요금 계산·견적·경보 판단 등). 통합은 **연결·실행 구성·화면**을 맡는다.

## 1. 인수인계 문서 위치

| 모듈 | 문서 | 상태 |
| --- | --- | --- |
| 백엔드 (이벤트·점유·세션·시연 서버) | `docs/KAN17_BACKEND_HANDOVER_2026-11-04.md` | ◐ PR #47 (Draft) |
| 요금·결제·매출 | `backend/app/billing/HANDOVER_KAN-28.md` | ◐ `docs/KAN-28-handover` (PR 예정) |
| 경보 | `backend/app/alerts/router.py` 문서 주석 | ◐ PR #46 (Ready) |
| 챗봇 | `backend/app/chatbot/parking-chatbot/docs/chat_response_fields.md`, `voice_input_guide_for_frontend.md`, `v213_change_summary.md` | ✅ main |
| 카메라·ROI | 우지원 계획서 2개 (디스코드 11/04 11:30) | ☐ 저장소에 코드 없음 |
| 전체 규격 | `docs/API_CONTRACT.md` (v1), `docs/DECISIONS.md` | ✅ main (DECISIONS D-25 이후는 `chore/progress-status`) |

## 2. 목표 구조 — 반드시 이 모양 (D-28)

```text
[카메라 PC] 우지원 카메라 모듈 ─┐            [가상 C/D 시뮬레이터 UI]
  (호스트 실행, 결과만 전송)      │  vision-event   │  vision-event (source=simulation)
                                  ▼                 ▼
         POST /api/v1/internal/vision-events  (Bearer 토큰)
                                  │
                       점유 엔진 (empty/occupied/uncertain)
                                  │ 점유 확정
                       parking_sessions (active/closed)  ← SQLite 한 파일
                                  │
     ┌──────────────┬─────────────┼─────────────┬──────────────┐
  조회 API      세션 API     요금·견적·결제·매출   경보 API      챗봇 /chat
  (박해준)       (박해준)        (윤명수)        (윤명수·서정범)   (서정범)
     └──────────────┴──────┬──────┴─────────────┴──────────────┘
                     api (FastAPI, main.py 하나)
                            │  /api/...
                     web (nginx + React)  ←  사용자 웹 / 모바일 / 관리자
                     admin-auth (관리자 로그인)
```

### 꼭 지킬 원칙

1. **서버 1개, DB 1개**: `api` 하나, SQLite `data/smart_parking.db` 한 파일 (D-01·D-20·D-38)
2. **카메라와 시뮬레이터는 같은 경로**: 둘 다 vision-event로 보내고 점유 엔진이 확정 (D-28)
   - 시뮬레이터가 DB에 직접 쓰지 않는다
3. **AI·카메라·시뮬레이터는 관측만, 확정은 서버** (CLAUDE.md 규칙 2·3)
4. **요금은 서버만 계산**: 화면·챗봇은 응답 금액만 표시, 결제 요청에 금액을 넣지 않음 (규칙 4)
5. **결제는 모의 결제만**, 결제 수단 실제 회사명 금지 (규칙 7, D-10)
6. **LED는 "모의 화재"**: 실제 화재라고 표시하지 않음 (규칙 6)
7. **가짜 데이터는 표시**: `is_mock`·`source=simulation`이면 "모의 이벤트" (D-11)
8. 비밀값(토큰·비밀번호·API 키)·DB 파일은 Git에 올리지 않음 (규칙 8)

## 3. 팀 결정 — 통합에 영향 주는 것만

| 분야 | 결정 | 통합 시 의미 |
| --- | --- | --- |
| 주차장 | A 개발원 12 · B 부산역 16 · C 북항 12 · D 신세계백화점 64(4구역 A~D 각 16) = 104칸 (D-03·D-14~16) | 지도·목록·필터 4곳 |
| 칸 번호 | 주차장마다 도면 번호 `A-01`…, 항상 `(lot_id, space_id)`로 식별, 표시는 "개발원주차장 B-06" (D-04) | 라 주차장 `C-13`은 구역 번호 (`D-45` 같은 연속 번호 금지) |
| 전용 칸 | 전기차·장애인 칸 별도, 일반차 불가 (D-05) | 라: 전기차 `D-01~08`, 장애인 `C-13~16` |
| 실물 차량 번호 | `"10"`~`"99"` 문자열, 00~09 미사용, 미인식 `null`, int 변환 금지 (D-06) | 입력 검증·표시 |
| 스티커 | 색 = 차량 종류(흰 일반·초록 전기차·파랑 장애인) (D-07), 배정표 D-39 | `vehicle_type` 표시 |
| 가상 차량 | 번호판 `17가1724` 기준, 화면 뒤 4자리 (D-08), 필드명 미정 | `car_id`에 넣지 않음 |
| 세션 상태 | `active`/`closed` (D-02) | 현재 입차 = `?status=active` |
| 요금 견적 | `quote_id`로 결제, 서버 5분·화면 2분 초기화 (D-09) | 결제 직전 재조회 |
| 결제 수단 | `MOCK_CARD`·`MOCK_EASY_PAY`·`MOCK_ACCOUNT`, 표시명 변형(피넛페이) (D-10) | |
| 11/06 | 가짜 이벤트 허용·표시 (D-11), 실물 A는 빈 주차장 비교(D-36), 챗봇 포함(D-22) | |
| 카메라 | 2대 (D-19), 칸 위 판독 기본 (D-30), 결과만 전송 (D-23), 우지원 노트북 (D-34) | |
| 경보 | 윤명수·서정범 공동 (D-29) | |
| LLM | OpenRouter, 키 `OPENROUTER_API_KEY`, 시연 PC 키 PM (D-18·D-33) | |
| 통합 서버 | 11/06 `demo_main`, **11/09까지 `main.py` 하나** (D-20) | |
| 포트 | 아래 5장 (D-25) | |
| Docker | 서비스 `api`·`web`·`admin-auth`, DB 볼륨 (D-38), 데모 서버 PostgreSQL 제외 (D-37) | |
| 리허설 | 11/05 오후 + 11/06 오전, 회차당 20분, 회차마다 재시작 (D-35) | |

## 4. 모듈별 연결 정보

| 모듈 | 코드 | 연결 방법 | 상태 |
| --- | --- | --- | --- |
| vision-event 수신 | `api/vision_event_receiver.py`, `vision_event_store.py`, `vision_event_schema.py` | `create_router(store=, authorize=)` | ✅ |
| 점유 엔진 | `api/occupancy_prototype.py` (`OccupancyExperiment`, `on_confirmed` 콜백) | 2초 점유·3초 빈칸·2초 간격·10초 무관측 → uncertain | ✅ |
| 조회 (점유 엔진 기반) | `api/occupancy_query_demo.py` (`create_demo_app`, `public_reads`) | 칸에 `source`·`is_mock`·`session_id` | ✅ |
| 세션 | `database/parking_sessions.py` (`enter`·`exit_session`), `api/session_queries.py` (`GET /sessions`) | 같은 SQLite `parking_sessions` | ✅ |
| 시연 서버 | `demo_main.py` (`create_app(token=)`) — 위 전부 + billing + 세션 조회 | 지금 유일하게 전체 흐름이 연결된 앱 | ✅ |
| 합성 이벤트 | `simulation/run_parking_demo.py` (`--hold`·`--depart --keep-empty`·`--short`·`--base-url`) | A 주차장만 | ✅ |
| 요금·결제·매출 | `api/billing_router.py` + `billing/*` | `include_router(billing_router)`, `get_db_conn` 의존성 | ✅ |
| 경보 | `alerts/router.py` (`router`, `internal_router`, `ingest_led_fire_event()`) | billing과 같은 DB, 내부 생성은 `ALERTS_INTERNAL_TOKEN` | ◐ PR #46 |
| LED → 경보 | 수신기가 `mock_fire_led_observed`를 받으면 `ingest_led_fire_event()` 호출 | | ☐ 아직 연결 안 됨 |
| 챗봇 | `backend.app.chatbot.router` (`router`) | `include_router`, `DATA_SOURCE=api`, `BACKEND_BASE_URL=<같은 서버>` | ✅ 코드 / ◐ 시연 서버 등록 PR #45 |
| 카메라·ROI | `camera/`·`roi/`·`configs/` | vision-event로 전송 | ☐ 비어 있음 |
| 프론트 | `frontend/` (`VITE_DATA_SOURCE=team`, `API_PROXY_TARGET`) | `/api` 프록시 | ✅ |

## 5. 포트·환경변수 (D-25·D-28·D-38)

| 포트 | 용도 |
| --- | --- |
| 8000 | 팀 서버 `api` (11/09 이후 `main.py`) |
| 8002 | 시연 서버 `demo_main` (챗봇 포함) — 11/06 |
| 8003 | 김채린 데모 서버 (보관용, D-37) |
| 8080 | 챗봇 단독 실행 (개발용) |
| 8090 | 관리자 로그인 게이트웨이 `admin-auth` |
| 5173 | 프론트 개발 서버 |
| 80 | 프론트 Docker `web` |

⚠️ **카메라가 붙으려면 `api`의 vision-event 경로가 밖에서 닿아야 한다** (D-28). nginx 뒤에만 두면 카메라 PC가 못 보낸다. 방법은 둘 중 하나:
- `api` 8000을 열기
- nginx에 `/api/v1/internal/vision-events`를 토큰 그대로 통과시키기

| 환경변수 | 쓰는 곳 | 의미 |
| --- | --- | --- |
| `SMART_PARKING_DB_PATH` | billing·세션·경보 | SQLite 파일 경로 (Docker 볼륨) |
| `FEE_QUOTE_TTL_SECONDS` | billing | 견적 유효 시간 (기본 300) |
| `ALERTS_INTERNAL_TOKEN` | 경보 | 내부 경보 생성 토큰 (16자 이상) |
| `DATA_SOURCE`, `BACKEND_BASE_URL`, `BACKEND_TOKEN` | 챗봇 | `api`·같은 서버 주소, 조회 토큰 불필요. ⚠️ **시연 서버(8002)에 붙일 때 `BACKEND_BASE_URL=http://127.0.0.1:8002` 필수** — 없으면 기본 8000을 찾아가 조회 실패 (11/04 실측) |
| `LLM_PROVIDER`, `LLM_MODEL`, `OPENROUTER_API_KEY` | 챗봇 | 기본 `none`(규칙만) |
| `API_BASE_URL`, `VISION_TOKEN` | 카메라 모듈 | ☐ 예정 (D-28), 시연 서버는 지금 실행 시 토큰을 키보드로 입력 |
| `VITE_DATA_SOURCE`, `API_PROXY_TARGET` | 프론트 | `team`, 서버 주소 |

## 6. 데이터 규약 핵심 (상세 `docs/API_CONTRACT.md`)

- 경로 `/api/v1`, JSON, `snake_case`, 시각 ISO 8601 **시간대 포함**, 금액 원 정수, ID 문자열
- 목록 `{items, next_cursor, as_of}` · 에러 `{"error": {code, message, details}}` → 화면은 `error.code`로 분기
- 칸 상태 `empty`/`occupied`/`uncertain`/`unavailable`
  - ⚠️ `uncertain` = 카메라가 10초 못 본 칸 → **빈칸으로 표시하지 말 것**
- **vision-event 전송 조건**: 칸마다 **1초 간격(최대 2초)**, 10초 끊기면 `uncertain`, 카메라 PC 시각 차이 10초 이내, 같은 칸에 vision·simulation 섞지 않기
- ⚠️ 시간대가 섞여 있음: 요금·견적 `+09:00`, 점유·조회 `+00:00` → 화면은 **KST로 변환**
- 주차장 목록에 `layout_type` 없음 → 지도 모양은 `lot_id`로 매핑, 구역은 `space_id` 앞글자

## 7. 11/04 통합본(`INTEGRATION_RESULT.md`) 대비 맞출 것

| # | 항목 | 왜 | 추천 |
| --- | --- | --- | --- |
| 1 | C/D 시뮬레이터가 SQLite에 직접 기록 | 원칙 2 위반, 서버가 둘로 갈라짐 | 시뮬레이터 UI → vision-event 전송 |
| 2 | 통합 앱이 점유 엔진·수신기 없이 동작 | 11/06 실물 A 카메라(D-36)가 못 붙음 | `demo_main` 구성(수신기·점유 엔진·billing·세션) 위에 통합 |
| 3 | API 외부 포트 없음 | 카메라 PC 전송 불가 | 5장 ⚠️ 참고 |
| 4 | 사용자 웹 8090 | D-25에서 8090은 관리자 게이트웨이 | 웹은 80 (또는 PM께 변경 요청) |
| 5 | 챗봇 | UI 통합 범위 아님 (D-41) | 챗봇 연결은 서정범·윤명수. 화면 ChatPanel은 그대로 두고 연결 확인만 같이 |
| 6 | 프론트·Docker·서버 변경이 한 번에 섞임 | D-37 순서 | 프론트 PR 먼저 → Docker → 서버 연결 |
| 7 | 코드 미push | 다른 사람이 검증 불가 | 브랜치로 push 후 검토 |

## 8. 일정·체크리스트

| 날짜 | 해야 할 것 |
| --- | --- |
| **11/05 오후 리허설** | 시연 서버(8002) + 합성 스크립트 + 화면 team 모드(`?status=active`·라 4구역·모의 표시) + 챗봇 + 요금 → 결제 → 취소 → 매출 |
| **11/06 중간 점검** | A 주차장: 실물 카메라(빈 주차장 비교) 또는 가짜 이벤트 → 점유 → API → **화면** |
| 11/09 | B 주차장 + 가상 C, `main.py` 하나로 통합, Docker `api`·`web`·`admin-auth` + SQLite 볼륨 |
| **11/10 운영 통합** | 모의 결제·매출·LLM 챗봇·모바일·관리자·경보 |
| 11/11 | 성능·오류 테스트 (가림·오인식·재연결·중복 결제·경보 해제) |
| **11/12 코드 동결** | 이후 P0 장애 외 수정 금지 |

## 9. 아직 정해지지 않은 것 (통합 전에 PM 확인)

- 시간대 통일 여부 (KST/UTC)
- 가상 번호판 필드명(`plate_number`)·검색 API
- 전용 칸 위반 처리 방식
- 입차 매칭(D-30 예비안, 근접 판독 전환 시에만)
- 시연 장소 네트워크 (카메라를 다른 PC에서 보낼 경우)
