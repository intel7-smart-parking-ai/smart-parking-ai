# AI 스마트 주차장 통합관제 — Claude Code 작업 지침

미니어처 실물 주차장 A·B(카메라 각 1대)와 가상 주차장 C(필수)·D(선택)를 하나의 시스템으로 관리한다.
카메라 영상 → AI(차량 탐지 / 번호 스티커 OCR / LED 모의 화재) → ROI 매칭 → FastAPI 상태 엔진·DB → 사용자 웹 / 관리자 관제 / LLM 챗봇.

- 기간: 2026-11-02 ~ 2026-11-13 (평일 10일), 6명, PM 김한별
- 기준 문서: `docs/통합_개발운영지침서.md` (최우선), `docs/API_CONTRACT.md`, `docs/ARCHITECTURE.md`, `AI데이터셋_수집계획.md`
- 문서와 이 파일이 충돌하면 **docs 문서가 우선**이다. 이 파일은 요약이다.

## 하네스 사용법

| 상황 | 사용 |
| --- | --- |
| 기능 개발 전반 (예: "A 주차장 점유 판정 붙여줘") | `/smart-parking` 오케스트레이터 스킬 |
| API·이벤트·DB 스키마를 만들거나 쓸 때 | `api-contract` 스킬 |
| 데이터셋·라벨링·학습·평가·모델 어댑터 | `vision-pipeline` 스킬 |
| 점유 안정화·입출차 세션·요금·모의 결제 | `parking-state-engine` 스킬 |
| 11/06·11/10·11/12 통합 점검, PR 전 검증 | `integration-gate` 스킬 |

에이전트(`.claude/agents/`)는 팀 소유권과 1:1로 대응한다. 담당 영역 밖 수정이 필요하면 직접 고치지 말고 **변경 요청(영향 범위·인터페이스)** 을 작성한다.

| 에이전트 | 담당 영역(폴더) | 소유자 |
| --- | --- | --- |
| `integration-pm` | API 계약, 통합, 일정·범위 | 김한별 |
| `vision-engineer` | `backend/app/vision/{vehicle,plate,fire}` | 1팀 김한별·박해준 / 2팀 우지원·김채린 / 3팀 윤명수·서정범 |
| `camera-roi-engineer` | `backend/app/camera`, `backend/app/roi`, `configs/` | 우지원 |
| `backend-engineer` | `backend/app/{api,parking,database,simulation}`, `main.py` | 박해준 |
| `billing-engineer` | `backend/app/billing` | 윤명수 |
| `frontend-engineer` | `frontend/` | 김채린 |
| `chatbot-engineer` | `backend/app/chatbot` | 서정범 |
| `qa-reviewer` | `tests/`, 리뷰 전반 | 전원 |

## 절대 규칙 (경계)

1. **카메라는 `camera` 모듈만 연다.** 다른 모듈에서 `cv2.VideoCapture` 금지 — 공유 프레임 또는 테스트 파일 사용.
2. **AI 모델은 관측(evidence)만 낸다.** bbox/confidence/car_id/LED 위치만 출력. DB 직접 쓰기·입출차 확정 금지.
3. **상태 확정은 서버만.** 점유·입출차·세션은 시간적 안정성 확인 후 `parking`이 확정. 단일 프레임 미검출로 출차 처리 금지.
4. **요금은 서버 단일 계산.** UI·챗봇은 요금을 재계산하지 않는다.
5. **챗봇은 정의된 조회 API/tool 결과만 근거로 답한다.** 추측·허구 데이터 금지.
6. **LED는 "모의 화재"** — 실제 화재 감지라고 표기하지 않는다. `event_type=mock_fire_led_observed`.
7. **결제는 모의 결제만.** `idempotency_key`로 중복 방지.
8. 비밀값(.env, API 키), 학습 원본 영상·이미지, 모델 가중치, DB 파일은 Git에 올리지 않는다.

## 데이터 규약 (요약 — 상세는 `api-contract` 스킬)

- 기본 경로 `/api/v1`, JSON UTF-8, 필드 `snake_case`, 시각 ISO 8601 타임존 포함, 금액 KRW 정수, ID는 문자열
- `lot_id`: `A`/`B`(실물), `C`/`D`(가상), 화면 이름 A 개발원주차장 · B 부산역주차장 · C 북항주차장 · D 신세계백화점주차장 · `camera_id`: `camera_a`/`camera_b`, 가상은 `null`
- 칸 수: A 12 · B 16 · C 12 · D 64(4구역 A~D 각 16) = 104칸, 전기차·장애인 전용 칸 포함 (상세 `docs/API_CONTRACT.md`)
- `space_id`: 주차장별 도면 번호 `A-01`·`B-06` (A·B는 주차장 안 구역, `lot_id`와 무관). 항상 `(lot_id, space_id)`로 식별, 표시는 "가 주차장 B-06"
- `car_id`(실물 미니카 스티커): `"10"`~`"99"` **두 자리 숫자 문자열**, `00`~`09` 미사용, 미인식 `null`, `int` 변환 금지
- 가상 C/D 차량: 번호판 `17가1724`가 기준, 화면은 뒤 4자리 `1724`, 상세·관리는 전체 번호판 — 필드명 확정 전 (`plate_number` 제안)
- 차량 종류 `vehicle_type`: `general`/`ev`/`accessible` — 실물은 스티커 색(흰 바탕=일반, 초록=전기차, 파랑=장애인)으로 인식, 일반차는 전용 칸 주차 불가
- 주차칸 status: `empty` / `occupied` / `uncertain` / `unavailable` · 세션 상태: `active`(주차 중) / `closed`(출차 완료)
- DB: **SQLite + SQLAlchemy** (PostgreSQL 전환 가능 구조만 준비, 11/03 PM 결정)
- 요금 견적 `quote_id`: 요금 화면에 표시된 견적 금액으로 결제, 만료 시 재조회 (클라이언트는 금액을 보내지 않음)

## 실행 명령 (Windows PowerShell, 저장소 루트)

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend/requirements-dev.txt
.\.venv\Scripts\python.exe -m uvicorn backend.app.main:app --reload   # http://127.0.0.1:8000/docs
.\.venv\Scripts\python.exe -m pytest tests/ -q
cd frontend; npm install; npm run dev                                   # http://127.0.0.1:5173
```

스택: Python 3.11 · FastAPI · Pydantic v2 · pytest/httpx | React 19 · Vite 7 | OpenCV(데이터 수집 `smart_parking_dataset_capture/`).
새 라이브러리 추가는 `requirements*.txt`/`package.json`과 README 실행 명령을 함께 갱신하고 팀에 공유한다.

## Git 규칙

- `main` 직접 수정·push 금지. 브랜치 `feature/KAN-<번호>-<기능명>` (초기/설정은 `chore/...`).
- PR: Jira 링크, 변경 이유, 수정 파일, API 변경 여부, 실행 방법, 테스트 결과, 롤백 방법 (`.github/PULL_REQUEST_TEMPLATE.md`).
- 공통 API/DB/설정 변경은 PM + 해당 소유자 승인. 리뷰 최소 1명.
- AI가 생성한 코드도 담당자가 직접 실행·검증한다. "실행 확인 없이 완료" 보고 금지.

## 일정 관문

| 날짜 | 관문 |
| --- | --- |
| 11/02~03 | API v1·폴더 경계 확정, 카메라 위치 후보 비교, 모형 실측 |
| 11/04~05 | 데이터 수집·라벨링, 모델 1차, 서버 목업 API, UI 초기 화면 |
| **11/06** | **중간 점검①: A 주차장 영상 → 점유 → API → 화면** |
| 11/09 | B 주차장 + 가상 C |
| **11/10** | **운영 기능 통합: 모의 결제·매출·LLM·모바일/관리자** |
| 11/11 | 성능·오류 테스트 |
| **11/12** | **코드 동결·리허설** (이후 P0 장애 외 신규 기능 금지) |
| 11/13 | 최종 시연 |

우선순위: **P0** 차량 탐지/ROI/번호 식별(또는 대체 마커), A·B 연동, 통합 API, 사용자 조회, 관리자 관제, 요금·모의결제·매출, 텍스트 챗봇, LED 모의 시연 · **P1** LED 정교화, C 시연 · **P2** D, STT, PWA 고도화.
