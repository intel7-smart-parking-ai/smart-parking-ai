# KAN-17 백엔드 후임자 기술 인수인계 (2026-11-04)

**기존 담당:** 박해준 / **후임:** PM 지정 대기 / **팀 DB:** SQLite+SQLAlchemy / **11/06 시연:** `demo_main`(8002) / **11/09까지:** `main.py` 통합·Docker·영속 SQLite.  
**관련:** [완료 작업 현황](KAN17_WORK_SUMMARY_2026-11-04.md) · [Jira KAN-17](https://rgq548.atlassian.net/browse/KAN-17) · [API 계약](API_CONTRACT.md).

> 이 문서는 담당 전환을 위한 운영 절차와 미완료 개발 순서다. 11/04 GitHub 확인 기준 PR #45 Draft/Open, PR #46 Ready/Open이며, 상태가 바뀌면 GitHub가 우선. **팀 main은 PM 승인 없이 병합하거나 직접 push하지 않는다.**

## 1. 첫 작업: 실제 브랜치·PR 확인
- [#43](https://github.com/intel7-smart-parking-ai/smart-parking-ai/pull/43) GET sessions는 **main 병합 완료**.
- [#45](https://github.com/intel7-smart-parking-ai/smart-parking-ai/pull/45) 챗봇 시연 등록: 브랜치 `feature/KAN-17-demo-chatbot-register`, **Draft**, 변경 파일 `backend/app/demo_main.py` + `tests/test_demo_chatbot_registration.py`. Codex 보고 **17 passed/0 failed/경고1**, `DATA_SOURCE=api`·`BACKEND_BASE_URL=http://127.0.0.1:8002`에서 실제 채팅·A 빈칸 12→11·차량 10번 위치 확인. **PR 본문 '미실행' 표기 갱신이 필요**. Ready/병합은 PM 승인 절차 따름.
- [#46](https://github.com/intel7-smart-parking-ai/smart-parking-ai/pull/46) 경보: 윤명수·서정범 소유, **Ready지만 아직 미병합**. `backend/app/alerts/router.py`의 `GET /api/v1/alerts`, 상세·확인·해제·내부 경보 생성 API와 `ingest_led_fire_event()` 사용. PR 본문 작성자 보고 341 passed. 수신기→경보 생성 연결은 별도.
- 팀 공식 규격/결정: `docs/API_CONTRACT.md`, `docs/DECISIONS.md`. API 계약 v1 작성 당시의 PR 병합 상태는 오래됐을 수 있다.

## 2. 흐름과 소유권

```text
카메라·ROI/합성 → POST /api/v1/internal/vision-events [Bearer]
   → D5 점유 empty/occupied/uncertain
   → parking_sessions active/closed (같은 SQLite)
   → /api/v1/sessions, 주차칸/차량 위치 조회
   → 윤명수 요금·견적·모의 결제 / 김채린 화면 / 서정범 챗봇
LED 모의 이벤트 → [아직 경보 수신기 연결 필요] → PR #46 alerts
```

| 코드 | 책임 |
| --- | --- |
| `backend/app/api/vision_event_store.py`, `api/occupancy_prototype.py`, `api/occupancy_query_demo.py` | 백엔드 이벤트 접수·점유·조회 |
| `backend/app/database/parking_sessions.py`, `api/session_queries.py` | 세션 생성/종료·목록(GET, lot_id/car_id/status 필터) |
| `backend/app/demo_main.py`, `simulation/run_parking_demo.py` | 백엔드 11/06 시연 서버·합성 스크립트 |
| `backend/app/billing/*` | **윤명수 담당**. 백엔드는 라우터·세션 연계만 |
| `backend/app/chatbot/*` | **서정범 담당**. 백엔드는 시연 서버 router 등록만 |
| `backend/app/alerts/*` | **윤명수·서정범 담당**. 백엔드는 서버 라우터/LED 이벤트 adapter 연결 |
| 프론트·관리자, 카메라·ROI | **김채린**, **우지원** 각각 담당. PM 김한별 승인·일정 관리 |

## 3. 11/06 시연: 2개 터미널

**서버:** 팀 main 승인 기준 최신 clone, 의존성 설치. 토큰은 ASCII 16자 이상이며 아래 두 실행에서 **같은 값**을 입력한다. 실제 토큰은 Jira·GitHub에 기록 금지.

```powershell
# 터미널 A: 서버 (기본 127.0.0.1:8002)
python -m backend.app.demo_main
# Swagger: http://127.0.0.1:8002/docs
```

```powershell
# 터미널 B: A-01 ~ A-03 3칸 점유 지속
python -m backend.app.simulation.run_parking_demo --short --hold
# 입차·요금·현재 세션 확인 → Ctrl+C

# 출차 후 빈칸 유지
python -m backend.app.simulation.run_parking_demo --short --depart --keep-empty
# closed 세션·빈칸 확인 → Ctrl+C

# 10초 이내 다음 회차
python -m backend.app.simulation.run_parking_demo --short --hold
```

- 12칸 시연은 위 명령의 `--short`를 생략. `--hold`와 `--depart` 동시 사용 불가, `--keep-empty`는 `--depart`와 함께 사용.
- 같은 칸에 **vision과 simulation 이벤트를 섞어 송신하지 않는다**. 서로 다른 PC/포트면 `--base-url http://127.0.0.1:<포트>` 사용.
- 점유 후보 약 2초, 출차 빈칸 후보 약 3초, 연속 간격 약 2초 이내, **관측 중단 10초 후 uncertain**. `--keep-empty` 종료 후 10초 넘기면 재입차 시작 검사에서 막힐 수 있으니 필요 시 `--depart`로 빈칸을 재확정.
- demo_main의 **임시 SQLite(TemporaryDirectory)**는 **재시작 시 세션·결제 기록 초기화**. 초기 `ses_demo` closed fixture 1개는 정상. Docker 영속성은 별도 과제.
- 시연 전용 이벤트 저장소 한도 100,000건, 4조BOT 실측 25,000건 HTTP202·503 0, 12칸 --hold 6분40초 안정. 장기 무제한 운영 의미는 아님.

### HTTP 조회 체크
```text
GET http://127.0.0.1:8002/api/v1/parking-lots
GET http://127.0.0.1:8002/api/v1/parking-lots/A/spaces
GET http://127.0.0.1:8002/api/v1/sessions?status=active
GET http://127.0.0.1:8002/api/v1/sessions?status=closed
GET http://127.0.0.1:8002/api/v1/vehicles/10/location
GET http://127.0.0.1:8002/api/v1/sessions/<실제ID>/fee
```

기대: `--short --hold` → occupied 3칸·빈칸 9/12·active 3개. `--depart` → active 0개, closed 증가. 초기 closed `ses_demo`가 있으므로 closed 전체 수는 0으로 시작하지 않는다. 관리자 ‘현재 입차 세션’은 `GET /sessions?status=active` 필터가 **필수**([KAN-39](https://rgq548.atlassian.net/browse/KAN-39)). 응답 공통 형식 `items,next_cursor,as_of`, `car_id:null` 지원.

## 4. 후임자가 이어받을 순서

### 시연 전 필수 (11/05 오후·11/06 오전)
1. PR #45 검증 결과가 이미 **Codex 실행 보고**임을 PR 설명에 반영하고, 리뷰·PM 승인 후 등록 완료 여부 확인. 테스트를 새로 돌린 것처럼 쓰지 않는다.
2. PR #46의 최종 병합 상태 확인. 병합 후 `demo_main`에 경보 라우터 등록 → 기존 vision-event의 `mock_fire_led_observed`를 경보 수신 함수 `ingest_led_fire_event()`에 연결 → 경보 목록·acknowledge·resolve·챗봇 대피·모의 표시 E2E. **PR #46 자체는 담당자 코드이므로 무단 수정 금지**.
3. 김채린 [KAN-36](https://rgq548.atlassian.net/browse/KAN-36) 프론트 team 모드 프록시와 [KAN-39](https://rgq548.atlassian.net/browse/KAN-39) active 조회, [KAN-40](https://rgq548.atlassian.net/browse/KAN-40) `is_mock` 표기 확인.
4. 우지원 [KAN-52](https://rgq548.atlassian.net/browse/KAN-52) A 12칸 카메라·ROI 이벤트 실측. 안 되면 D-11에 따라 합성 이벤트로 시연하고 **‘모의 이벤트’ 표시**. 실제/합성 동시에 보내지 않기.
5. 김한별 [KAN-53](https://rgq548.atlassian.net/browse/KAN-53) 리허설 두 회차에 참여해 입차→위치→요금/챗봇→출차·경보 시연 성공 증거와 포트/토큰 문제를 기록.

### 11/09 통합 (기능별 작은 PR)
- [KAN-46](https://rgq548.atlassian.net/browse/KAN-46): 시연 로직을 `backend.app.main:app` 하나로 통합(현재는 목업 위주). `backend/Dockerfile`, `0.0.0.0`(컨테이너), port/host/`VISION_TOKEN` 환경변수. Docker `api`·`web`·`admin-auth` 서비스/프록시는 김채린 [KAN-41](https://rgq548.atlassian.net/browse/KAN-41)과 정렬.
- SQLite는 `SMART_PARKING_DB_PATH=data/smart_parking.db` 등 경로·볼륨으로 **영속 보존**. 기존 PostgreSQL로 변경하지 않음.
- [KAN-45](https://rgq548.atlassian.net/browse/KAN-45): 주차칸 조회 항목의 `entry_at` 값(세션 시작 시각 또는 null)을 구현·테스트.
- 통합 이후 `main.py`의 공통 422 오류 포맷·권한·행수/재시작 안전성을 별도 검증.

### 후순위
- [KAN-47](https://rgq548.atlassian.net/browse/KAN-47) 입차 매칭은 **D-30 예비안**. 팀 API_CONTRACT는 우선 `vehicle_observed` 단일 이벤트에 `space_id`+`car_id` 직접판독을 명시. 이전 DECISIONS의 D-19 근접판독 방식과 차이가 있으므로 PM 최신 결정 확인. `plate_observed(space_id:null)`는 현재 422. 근접 방식 재도입 시 대기시간·`entry_at`·동시 입차 정책이 먼저.
- 장기 이벤트 자동 정리, 차량 칸 이동, 전용 칸 위반 경보·지도 `layout_type`, 가상 번호판 `plate_number`는 정책 합의 뒤.

## 5. 인계 안전 규칙 및 수락 기준
- **작업은 Codex가 소규모 브랜치에서 구현·테스트·Commit/Push·Draft PR. ChatGPT는 설계·검토/보고.** 기존 worktree·미커밋 보존, force push/reset --hard 금지. 팀 main 병합은 PM 승인 후.
- 코드·Jira·Notion에 비밀값/토큰/`.env`/개인 PC 절대경로/개인 GitHub 포트폴리오 링크를 포함하지 않는다.
- **후임자 미지정:** Jira KAN-7·17·45·46·47의 Assignee와 업무 소유권은 PM 확정 후만 변경.
- [ ] PM이 후임자와 담당 범위를 확정 · [ ] 후임이 시연 직접 재현 · [ ] PR #45/#46 병합 및 이벤트·경보 E2E 결과 기록 · [ ] 11/09 main/Docker/SQLite 볼륨 미완료 항목 관리 · [ ] Jira/인계 수락 확인.

**본 문서의 상태는 '인수 준비'. 역할 이전 완료 또는 기능 전부 완료를 의미하지 않는다.**
