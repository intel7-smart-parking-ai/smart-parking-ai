# 통합 API 승인 요청 및 팀 실행 공지 — 2026-11-05

상태: **D-49 승인 완료** (사용자 전달: PM 2026-11-05 10:01, 윤명수 10:05).
승인 계약은 docs/API_CONTRACT.md 첫 절에 반영했다. 아래는 요청 당시 기록이며
금액 의미는 완료 결제 후 amount_krw=payment.amount_krw를 유지한다.
리허설 서버는 D-50에 따라 김채린 PC demo_main:8002로 확정, 절차는 RUNBOOK_DEMO_HOST.md.
검토 PR: #52 → #53 → #54. main 병합은 PM 리뷰 후 진행한다.

## 1. 윤명수님·PM: 요금/결제 어댑터 승인 요청

`runtime.py`가 GET `/api/v1/sessions/{session_id}/fee` 및 POST
`/api/v1/mock-payments`를 등록한다. 해당 두 경로의 billing 라우트는 복사한 라우터에서
제외하므로 어댑터가 처리한다. billing 소유 모듈의 계산/결제 구현은 그대로 호출한다.

| 변경 | 소비자 영향 및 확인 요청 |
| --- | --- |
| 요금 응답 `payment_blocked`: boolean | 동일 주차장에 미해제 level>=2 경보가 있으면 true |
| 요금 응답 `payment`: 결제 행 객체 또는 null | 완료 결제 영수증을 화면에 복원. 공개 필드 범위 검토 필요 |
| 완료 결제 후 `amount_krw` | 현재 재계산액 대신 완료 결제 금액을 반환. 기존 의미와의 호환성 승인 필요 |
| POST 결제 409 `EVACUATION_ACTIVE` | `{error:{code,message,details:[]}}`. 경보 검사와 결제 호출은 같은 잠금 안에서 실행 |

윤명수님은 금액 의미·중복 결제·영수증 필드, PM은 공통 계약 영향을 승인/수정 요청해주세요.
승인 전 신규 동작을 공식 계약으로 확정하지 않는다.

## 2. 서정범님·윤명수님: 챗봇 영향 확인

409 `EVACUATION_ACTIVE`를 수신하면 자동 재결제하지 않고 대피 안내로 전환해야 한다.
이는 소비자에게 요청하는 동작이며 챗봇 내부 구현 완료를 뜻하지 않는다.
경보 조회 실패/시간 초과에서도 기존 안전 상태를 잃지 않는지 담당자 검수가 필요하다.
챗봇 소유 폴더는 이번 후속 수정에서 변경하지 않는다.

## 3. PM·백엔드: 칸 응답 및 가상 API 승인 요청

| 항목 | 구현 관찰 / 제안 |
| --- | --- |
| `entry_at` | 활성 세션의 ISO 8601 입차 시각 또는 null. uncertain에서도 활성 세션 시각 보존 |
| `zone` | `space_id`의 `-` 앞부분. lot_id와 별개 |
| `plate_number`, `vehicle_number` | C/D 시뮬레이터의 전체 가상 번호판 및 뒤 4자리 문자열. 미존재/null 허용. 실물 car_id 대체 금지 |
| `version` | 점유 엔진의 버전 필드 사용처·조회 범위를 PM/백엔드가 확인하여 계약 범위를 확정 |
| GET `/api/v1/vehicle-search?vehicle_number=1724` | C/D occupied 칸의 뒤 4자리로 단일 차량 조회. 형식 오류 422, 단일 일치 없음 404 |
| GET `/api/v1/simulation/{lot}` | C/D 제어 상태와 최근 이벤트. demo 모드만 허용 |
| POST `/api/v1/simulation/{lot}/{action}` | action=entry/exit/start/stop, 선택 body space_id·vehicle_number·interval_seconds(3~60, 기본8) |

Docker에서 시뮬레이터는 관리자 `/admin-api/team/v1/...`를 통해서만 접근한다.
시뮬레이터는 관측을 인증된 vision-events로 보내며 점유/세션을 직접 기록하지 않는다.
공통 수신기가 원래 HTTPException 문구를 일반화하는 문제는 프론트의 차량검색 상태별 안내로 보완했다.

PM·해당 소유자 승인 댓글의 링크와 날짜를 기록한 뒤 `docs/API_CONTRACT.md`와
`.claude/skills/api-contract/SKILL.md`를 함께 갱신한다. 승인 기록은 아직 없다.

## 4. PM 결정 요청: 11/05 오후 / 11/06 리허설 서버

제안: 오후는 기존 `demo_main` 8002, Docker 통합본은 별도 시험.
11/06 최종 사용할 서버는 PM이 결정해주세요. 아직 확정된 일정 변경이 아니다.

공통 준비:

```powershell
python -m pip install -r backend/requirements-dev.txt
```

`requirements.txt`에 httpx가 추가됐다. 기존 가상환경을 활성화한 터미널에서 위 명령을
다시 실행한다(SQLAlchemy 등 기존 의존성도 함께 설치).

기존 시연 서버:

```powershell
$env:DATA_SOURCE="api"
$env:BACKEND_BASE_URL="http://127.0.0.1:8002"
python -m backend.app.demo_main
# 별도 터미널: 같은 VISION_TOKEN 또는 같은 입력 토큰
python -m backend.app.simulation.run_parking_demo --short --hold
# 프론트 터미널
cd frontend
npm ci
$env:VITE_DATA_SOURCE="team"
$env:API_PROXY_TARGET="http://127.0.0.1:8002"
npm run dev
```

Docker 통합본(PR #54 포함): Docker Desktop 실행 후

```powershell
python deployment/setup_local.py
docker compose up --build -d --wait
docker compose ps
```

setup에서 관리자 계정과 비밀번호를 직접 입력한다. `.env`·`.local-access/` 업로드 금지.
사용자 http://127.0.0.1/ · 관리자 http://127.0.0.1/admin/ · 8090은 관리자 게이트웨이.
SQLite 볼륨 보존: 종료는 `docker compose down` (`-v` 사용하지 않음).
카메라는 웹80 `/api/v1/internal/vision-events`에 같은 VISION_TOKEN으로 전송한다.

Docker 없는 통합 main:

```powershell
$env:PARKING_MODE="demo"
# VISION_TOKEN은 각자의 비공개 환경변수로 ASCII 16자 이상 지정
$env:DATA_SOURCE="api"
$env:BACKEND_BASE_URL="http://127.0.0.1:8000"
$env:API_HOST="127.0.0.1"
$env:API_PORT="8000"
python -m backend.app.main
```

단일 프로세스/workers=1. demo는 C/D 모의 관측 허용, real은 자동 관측/모의 경보 버튼 비활성.
챗봇 기본은 LLM_PROVIDER=none. 실제 OpenRouter 키/카메라·음성 연결은 담당자 리허설 필요.

## 5. 병합 및 브랜치 정리

- #52 → #53 → #54 Ready 전환. 앞 PR 병합 전에는 다음 PR의 현재 base 유지.
- #52 병합 후 #53 base=main, 최신 main을 반영하고 pytest·프론트 테스트·team 빌드 재실행.
- #53 병합 후 #54도 같은 절차 및 Docker 기동/게이트웨이 확인 후 PM 병합.
- `codex/parkon-user-admin-revenue`: 과거 데모 보관 전용. 신규 통합 작업 금지.
- `codex/ui-sqlite-integration`: 과거 prototype의 고유 커밋이 있어 동일 내용 병합 완료로
  취급하지 않는다. 정리 전 archive 태그로 커밋을 보존하고 통합 PR로 대체됨을 기록한다.

실제 승인, PM 서버 선택, 선행 PR 병합 후 base 변경은 외부 결정/이벤트가 필요하므로
완료로 표시하지 않는다.
