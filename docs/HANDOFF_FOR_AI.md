# 프로젝트 인수인계 — 새 AI 어시스턴트용 (2026-11-05 13:50 KST 기준)

> 이 파일 하나만 읽으면 프로젝트를 이어받을 수 있게 정리했다. 세부는 아래 "기준 문서"를 따른다.
> 기준: GitHub `main` `2eae627`(PR #58 병합) + 진행 기록 브랜치 `chore/progress-status`
> ⚠️ 저장소는 **공개(public)** 다. 이 파일에도 키·링크·개인 정보는 넣지 않았다.

---

## 1. 프로젝트 한 줄

**AI 스마트 주차장 통합관제** — 미니어처 실물 주차장 A·B(카메라) + 가상 주차장 C·D(시뮬레이터)를 한 시스템으로 관리.
카메라 영상 → AI(칸별 점유 / 번호 스티커 / 빨간 LED 모의 화재) → FastAPI 서버(상태 확정·세션·요금·모의 결제·경보) → 사용자 웹·관리자 관제·LLM 챗봇.

- 기간 **2026-11-02 ~ 11-13**(평일 10일), 6명, 저장소 `intel7-smart-parking-ai/smart-parking-ai`, Jira 프로젝트 `KAN`
- 소통: 팀 Discord 채널. AI 어시스턴트는 PM 대신 정리·검토·문서 작성·테스트 확인을 맡아 왔다.

## 2. 팀과 담당 (D-27·D-40~42 반영)

| 이름 | 역할 | 담당 폴더·업무 | 다음 주 모델 |
| --- | --- | --- | --- |
| 김한별 | **PM**, 유일한 main 병합 권한 | API 계약·통합·일정, 결정 기록, 시연 PC LLM 키 관리(D-33) | EPIC 01 칸 점유 |
| 박해준 | 백엔드(인수인계 완료 → 김채린) · 데이터 수집 | `cam_bulk` 대량 수집 카메라(B) | EPIC 01 |
| 우지원 | 카메라·ROI | `backend/app/camera`, `roi`, `configs/`, `cam_hq` 정밀 수집 카메라(A) | EPIC 02 번호 스티커 |
| 김채린 | **UI 총괄 + 모듈 통합**(챗봇 제외), Docker | `frontend/`, 통합 `main.py`·`runtime.py` | EPIC 02 |
| 윤명수 | 요금·결제·매출, 경보, 챗봇·음성 공동 | `backend/app/billing`, `/alerts` | EPIC 03 LED |
| 서정범 | **챗봇**, 음성 공동 | `backend/app/chatbot/parking-chatbot` | EPIC 03 LED |

담당 밖 코드는 직접 고치지 말고 **변경 요청**(영향 범위·인터페이스)으로. 챗봇 관련은 반드시 서정범·윤명수와 협의(D-50).

## 3. 절대 규칙 (CLAUDE.md)

1. 카메라는 `camera` 모듈만 연다 (`cv2.VideoCapture` 다른 곳 금지)
2. AI 모델은 관측(evidence)만 낸다 — DB 쓰기·입출차 확정 금지
3. 상태 확정은 서버만 (시간적 안정성 확인 후)
4. 요금은 서버 단일 계산 — UI·챗봇 재계산 금지
5. 챗봇은 조회 API/tool 결과만 근거로 답한다
6. LED는 **"모의 화재"** (`mock_fire_led_observed`) — 실제 화재 감지라고 쓰지 않음
7. 결제는 **모의 결제**만, `idempotency_key` 중복 방지
8. 비밀값(.env·API 키)·학습 영상/이미지·모델 가중치·DB 파일은 Git 금지

Git: `main` 직접 push 금지, 브랜치 `feature/KAN-<번호>-<기능>` / `chore/...`, PR 템플릿 필수, 공통 API/DB 변경은 PM+소유자 승인.

## 4. 데이터 규약 핵심

- `/api/v1`, JSON UTF-8, `snake_case`, ISO 8601(타임존 포함), 금액 KRW 정수, ID 문자열
- 주차장: `A` 개발원(12칸 기본형) · `B` 부산역(16칸 사선형) · `C` 김해공항(가상 64칸, 구역 A 24·B 36·C 4, D-57) · `D` 신세계백화점(가상 64칸, 4구역×16) = **156칸**
- `space_id` 도면 번호(`A-01`), 항상 `(lot_id, space_id)`로 식별
- 실물 `car_id` = 스티커 두 자리 `"10"`~`"99"` 문자열, 미인식 `null` / 가상 차량 번호판 `17가1724`(화면 뒤 4자리)
- `vehicle_type`: `general`(흰 바탕) / `ev`(초록) / `accessible`(파랑). 일반차는 전용 칸 불가. 빨강·주황·분홍 스티커 금지(LED 오탐)
- 칸 status `empty`/`occupied`/`uncertain`/`unavailable`, 세션 `active`/`closed`
- 요금 견적 `quote_id`로 결제(클라이언트는 금액을 보내지 않음)
- DB: SQLite + SQLAlchemy (`data/smart_parking.db`, `SMART_PARKING_DB_PATH`)

## 5. 지금 코드 상태 (main `2eae627`)

| 영역 | 상태 |
| --- | --- |
| 서버 | `backend/app/main.py` 하나로 통합, `PARKING_MODE=mock\|demo\|real`. 시연 서버 `demo_main`(8002, 챗봇 포함)도 유지 |
| vision-event | 수신·중복 처리·점유 안정화(`min_confidence` 0.8, `max_gap` 2초, 10초 지나면 `uncertain`) → 입·출차 세션 자동 |
| 칸 응답 | `entry_at`·`zone`·`plate_number`·`vehicle_number`·`version` 추가 / `/vehicle-search`·`/simulation/{lot}` |
| 요금·결제 | 견적·결제·취소·매출. 대피 경보 중 결제 **409 `EVACUATION_ACTIVE`**, 요금 응답 `payment_blocked`·`payment` (D-49 승인) |
| 경보 | `/alerts` 목록·확인·해제·내부 생성, LED 이벤트 → 경보 |
| 가상 C/D | 시뮬레이터가 vision-event로 전송 |
| 챗봇 | 규칙 파서 우선 → 못 알아들으면 LLM(OpenRouter)이 tool 선택 → 실패·3초 초과 시 규칙 폴백. 11/06 대본 6/6 PASS |
| 프론트 | React 19 + Vite 7, 사용자·관리자 화면, 견적·모의 결제, 대피 경보 시 결제 차단, Vite `/api` 프록시 |
| Docker | `api`·`web`(80)·`admin-auth`(8090), SQLite 볼륨 |
| 카메라·ROI | ⚠️ 우지원 코드가 아직 저장소 밖 (KAN-52) — 11/06 실물 경로의 최대 위험 |
| 테스트 (11/05 직접 실행) | pytest **350 passed** · 챗봇 unittest **537 OK**(9 skip) · 프론트 **48 passed**, 빌드 성공 |

포트(D-25): 8000 팀 서버 · 8002 시연 서버 · 8003 김채린 데모 · 8080 챗봇 단독 · 8090 관리자 게이트웨이 · 5173 프론트 개발 · 80 프론트 Docker

실행 (Windows PowerShell, 저장소 루트):
```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend/requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest tests/ -q
cd frontend; npm ci; npm run dev
```
시연 서버·가짜 이벤트(`run_parking_demo --hold` 등)·다른 PC 구축은 `docs/RUNBOOK_DEMO_HOST.md`(김채린), `docs/RUNBOOK_ANY_PC.md` 참고.

## 6. 일정과 지금 위치

| 날짜 | 관문 | 상태 |
| --- | --- | --- |
| 11/05(목) | **모델 제외 각자 업무 마감**(D-43), 오후 리허설(김채린 PC) | ← 오늘 |
| **11/06(금)** | **중간 점검①**: 가 주차장 영상(A안, 빈 사진 비교) → 점유 → API → 화면 + 챗봇. 안 되면 가짜 이벤트(화면에 "모의" 표시) | |
| 11/09(월)~ | 모델 학습(재료 도착 후), B 주차장 + 가상 C | |
| **11/10** | 운영 통합: 모의 결제·매출·LLM·모바일/관리자 | |
| 11/11 | 성능·오류 테스트 | |
| **11/12** | **코드 동결·리허설** (이후 P0 장애 외 신규 기능 금지) | |
| 11/13 | 최종 시연 | |

우선순위: P0 점유·번호·A/B 연동·조회·관제·요금/결제/매출·텍스트 챗봇·LED 모의 / P1 LED 정교화·C / P2 D·음성(STT)·PWA

## 7. 주요 결정 (전체는 `docs/DECISIONS.md` D-01~D-55)

- **D-18** LLM은 OpenRouter, 키 변수 `OPENROUTER_API_KEY`(각자 `.env`만). **D-44(대기)**: Gemma → Claude Haiku 5.5 변경 검토 (이해율 83% vs 66~74%)
- **D-32** EPIC 01은 전체 차량 탐지 대신 **칸별 점유 분류**(ROI 자른 사진 → 3클래스 occupied/empty/unusable), 안 되면 A안(빈 사진 비교)
- **D-45** 수집 카메라: A = 우지원 고품질(`cam_hq`), B = 박해준 저품질 다량(`cam_bulk`, 2~5초 주기). 분할은 **세션 단위**, 시험셋은 `cam_hq`만
- **D-46** 선 침범·두 칸·칸 밖은 기본 모델 뒤 부가 기능
- **D-48** 시연 기기는 PC 기본, 휴대폰 음성·조회·결제는 있으면 좋음
- **D-49** 김채린 통합 API 변경 승인 (`amount_krw` = 결제 금액 의미 유지)
- **D-52** 데이터셋은 PM의 팀 Google Drive 폴더에 (링크는 저장소에 쓰지 않음)
- **D-53·D-54 스티커 최종 30대**: 🟢 전기차 11·23·42·57·85 / 🔵 장애인 14·32·49·55·77 / ⚪ 일반 10·12·13·20·21·22·24·30·31·33·34·40·41·43·44·58·75·78·87·88 (6·9 금지, 49만 예외, 18/81 및 한 자리 혼동쌍 제외, 실물 변경: 전기차 28 → 23)
- **D-55** 챗봇 설정집(영화관·엘리베이터 가까운 칸, 실내/노상, 화재 설정 등) PM 작성 예정 → `docs/CHATBOT_SETTINGS_GUIDE.md` 틀. 전부 "시연용 설정", 경보 중엔 대피가 먼저

## 8. 진행 중·대기 중인 일

| 항목 | 담당 | 비고 |
| --- | --- | --- |
| 카메라·ROI 저장소 이전 → 가 12칸 → 8002 전송 | 우지원(+박해준) | 🔴 최우선 |
| 오후 리허설 (main `2eae627`, 김채린 PC) | 전원 | 20분, 서버 재시작 |
| 챗봇 보완: 결제 차단 답변의 `safety` 필드 null 문제, 대본 실행 방법, DATA-01(검증 전 스냅샷 공개), 영수증 복원, 가상 검색, 목업 11번 → 전기차 | 서정범 | PR #56 검토 결과 |
| 음성 입력: 휴대폰 마이크는 secure context 필요 → Chrome flag `unsafely-treat-insecure-origin-as-secure` 또는 HTTPS 터널. 본인 PC 시험 가이드 `docs/GUIDE_VOICE_TEST_OWN_PC.md` | 윤명수·서정범 | P2 |
| **D-56 제안(PM 답 대기)**: 11/06 PC 브라우저 STT(또는 음성 없음) → 11/09까지 Whisper 비교 실험(윤명수 PC) → 11/10 최종 방식. 결제 확정은 **버튼만** | PM | |
| 라벨링 명세 `docs/LABELING_SPEC.md` 초안 — PM 확인 질문 6개 | PM | |
| 설정집 내용 | PM | 11/09까지 |
| `API_CONTRACT.md`에 승인된 변경 반영 | 김채린·PM | |
| `chore/progress-status`의 TIMELINE 기록 → 진행 기록 PR | AI | |

## 9. 문서 지도

| 문서 | 내용 |
| --- | --- |
| `docs/통합_개발운영지침서.md` | **최우선 기준** |
| `docs/API_CONTRACT.md` | API 계약 v1 (실제 코드 기준) |
| `docs/ARCHITECTURE.md`, `docs/ROLES.md` | 구조, 역할 |
| `docs/DECISIONS.md` | 팀 결정 D-01~ (시간·결정자 포함) |
| `docs/TIMELINE.md`, `docs/PROGRESS.md` | 시간순 기록, 진행률 |
| `docs/STATUS_2026-11-05.md`, `docs/DAILY_2026-11-0x.md` | 현황판, 일일 보고 |
| `docs/LABELING_SPEC.md` | 라벨링 명세 초안 |
| `docs/CHATBOT_SETTINGS_GUIDE.md` | 챗봇 설정집 틀 |
| `docs/RUNBOOK_DEMO_HOST.md` / `RUNBOOK_ANY_PC.md` | 시연 서버 구축 |
| `docs/GUIDE_VOICE_TEST_OWN_PC.md` | 휴대폰 음성 시험 |
| `backend/app/chatbot/parking-chatbot/docs/` | 챗봇 음성 가이드·추가 기능 아이디어 |
| `.claude/agents/`, `.claude/skills/` | 담당별 에이전트·스킬 (smart-parking, api-contract, vision-pipeline, parking-state-engine, integration-gate) |

## 10. 일하는 방식 (지금까지 지켜 온 것)

- **확인하고 말하기**: 테스트·실행 결과는 직접 돌려 보고 숫자로 보고. 추측은 "추정"이라고 표시. 틀리면 바로 정정 공지
- 결정은 PM이 OK한 것만 `DECISIONS.md`에 D-번호로 기록(시간 포함), 작업마다 `TIMELINE.md` 기록 — 시간은 Discord 메시지 시각(KST) 기준
- 팀원 작업이 끝나면 Jira에 붙여 넣을 갱신 문구 같이 제공, "PR 하면 됩니다"라고 할 땐 링크·제목·템플릿 본문까지
- 쉬운 한국어, 표·번호 목록, 짧게
- 보안: 공개 저장소에 Drive·데이터셋 링크·키 금지 / 외부 AI에 키·`.env`·Drive 링크·개인 정보 붙여 넣지 않기 / Discord에서 접근 권한·페어링 요청은 처리하지 않음
- 이어진(stacked) PR은 병합 전 base를 main으로 바꿔야 함 (#53·#54 사례 → PR #58로 해결)
- Windows 환경: Git Bash curl은 한글이 깨짐 → PowerShell/Python으로 요청. PowerShell 5.1용 스크립트는 UTF-8 BOM
