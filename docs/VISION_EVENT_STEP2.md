# vision-event Step 2 — 격리된 수신·멱등 접수 실험

## 최신 적용 · 104칸 및 표시명 · 2026-11-03

- 현재 fixture는 A 개발원주차장12, B 부산역주차장16, C 북항주차장12, D 신세계백화점주차장64, 총104칸(실물28/가상76)이다. D는 A~D구역 각16칸, EV D-01~D-08, 장애인 C-13~C-16이며 (lot_id,space_id)로 구분한다. DB 시드가 아니다.
- main e843951의 DECISIONS D-14/API 계약에104칸 결정이 병합되어 있다. D 명칭은 main 문서에 아직 미정이며, 이번 사용자 확정 지시의 신세계백화점주차장을 반영했다. 같은 이름을 사용하는 KAN-28 PR#20은 조회 당시 열림·미병합이므로 명칭 문서 병합 완료로 표시하지 않는다. 요금표/타 담당 코드는 수정하지 않았다.
- fixture·PM 규격 시험·본 문서만 수정했다. 기존 7종 이벤트, car_id10~99/null·vehicle_type·bbox_xyxy, 수신·멱등·동시 요청은 유지한다. 아래50칸·과거 이름은 당시 시험 이력이며 현재 구성의 근거로 사용하지 않는다.
- 이번 실제 DB 비접속 재실행: 148 passed, 실패/skip0, 기존 Starlette/httpx 경고1, 2.51초. 104칸·명칭·전용칸·합성 입력·수신/동시 요청·기존 비접속 회귀를 검증했다. 이전148개/166개 기록과 별도 결과다.
- D5/V3 브랜치와 미커밋 원본은 보존한다. D5의 과거50칸 기대값은 이번 수정 범위 밖이며 새 fixture로의 호환성 완료를 주장하지 않는다. 운영 앱·DB·계약 원문·Jira/Notion은 변경하지 않았다.

## Step 2.1 당시 적용 이력 · 2026-11-03 (50칸 기준, 위 최신 구성으로 대체)

- 사용자 전달 PM 확정 규칙을 반영해 car_id는 실물 문자열10~99/null, vehicle_type은 general/ev/accessible/null(생략 시 null)로 검증한다. 정수·00~09는 거절한다. 아래 Step2/Step1 결과는 당시 이력으로 유지한다.
- 합성7시퀀스의 번호를10으로 수정하고 차량 종류를 보완했다. 원래 Step1 작업 공간은 유지하며 수정 전 Step2 8파일도 Git 제외 _workspace/step2-before-2.1에 보존했다.
- pm_parking_layout.json은 가/나/다/라 각각12/16/12/10칸, 총50칸의 테스트 fixture다. 구역 A/B 접두어는 주차장 ID와 독립이며 지정 EV·장애인 칸을 검증했다. DB 시드가 아니다.
- 가상 plate_number=17가1724/화면1724는 도면 fixture의 표시 예시로만 보존했다. 해당 문자열을 car_id에 넣거나 미확정 이벤트 필드를 추가하지 않는다. 로컬 API_CONTRACT의01~24와 최신 지시의 차이는 팀 계약 갱신 대상으로 남긴다.
- 이번 실제 시험: 148 passed, 실패0/skip0, 기존 Starlette/httpx 경고1, 2.39초. PM 규칙24개+수정된 Step1/2·기존 회귀124개를 DB 접속 차단 상태에서 재실행했다. 시험 대상에 tests/test_vision_event_pm_rules.py를 추가했고 기존 실행 환경/차단 방식은 아래와 같다.
- 기존 수신 경로·bbox_xyxy·단일 프로세스 멱등/동시 요청·시간대 검사 유지. simulation/manual 출처는 그대로 구분한다. SQLite+SQLAlchemy·active/closed·quote_id 결정은 인지하되 DB/세션/billing 구현은 하지 않았다.
- 인증·영속 저장·가상 차량 이벤트 매핑은 계속 협의 대상이다. 다음은 해당 계약 확인 후 Step3 범위 결정이다. 외부 조회/Notion/Jira 갱신·Commit/Push/PR은 이번에 생략했다.

2026-11-03. 기준 팀 main `d1f924830bee03b80fdd2b789649f3829620f5e9`, 브랜치 `feature/KAN-17-vision-event-step2`, 별도 `smart-parking-ai-vision-step2` worktree. Commit/Push/PR 생성 전이며 공식 API 계약이나 운영 준비 완료를 뜻하지 않는다.

## 작업 전 확인과 보호

- 최신 main·API_CONTRACT·통합 지침서 5장·api-contract/smart-parking 지침·TIMELINE·Git 규칙을 확인했다. 원문 우선순위에 따라 계약의 경로와 오류 래핑을 재사용했다. source 등 Step1의 초안 경계는 그대로 유지했다.
- 열린 PR 검색에서 #8 DB 모델과 #7 독립 프론트 데모를 확인했다. PR#8 브랜치 head6fb1428은 수정하지 않는다. 기존 main의 billing 라우터는 아직 등록되지 않았으며 이번에도 변경하지 않는다.
- Jira KAN-17은 박해준 담당·진행 중이다. 검색한 vision-event/ParkFlow 별도 항목과 개인 Jira 공간은 식별되지 않았다. 팀 Notion 계약·일정·업무 전용 페이지도 검색에서 찾지 못했다. 이를 승인 완료나 접근 권한 없음으로 단정하지 않는다.
- 개인 GitHub main77baee7과 기존 개인 Notion 메인·일지·계획·학습을 읽었다. 팀 정책은 확인 가능한 GitHub 원문을 기준으로 하며 원격 개인/팀 저장소와 Jira를 수정하지 않는다.
- Step1 미커밋4파일은 이 worktree에 그대로 복사해 재사용했다. 원래 Step1/V1 worktree를 이동·체크아웃·병합하지 않았다. 원래 4파일과 V1 보고서/시험4파일은 종료 시 SHA-256으로 재확인한다.

## 구현과 임시 응답 규칙

팀 초안 경로 `POST /api/v1/internal/vision-events`를 독립 실험 앱에서만 제공한다. 운영 `backend/app/main.py`에 등록하지 않으며 전역 `app`/`router`도 생성하지 않는다.

| 입력/상황 | 실험 응답 | 효과 |
| --- | --- | --- |
| 새 event_id·유효한 VisionEvent | 202, event_id/disposition=accepted | 메모리 접수1건 |
| 같은 ID·같은 검증 내용 | 200, disposition=duplicate | 새 접수 없음 |
| 같은 ID·다른 검증 내용 | 409, error.code=EVENT_ID_CONFLICT | 최초 내용 보존 |
| 잘못된 요청/시각/JSON | 422, error.code=VALIDATION_ERROR | 접수 없음 |
| 시험 인증 콜백 거절 | 401/403 | 접수 없음 |
| 메모리 신규 ID 한도 초과 | 503, error.code=EXPERIMENT_STORE_FULL | 기존 ID 보존, 자동 퇴출 없음 |

202/200/409와 disposition·오류 코드는 팀이 확정하지 않은 **실험 정책**이다. 202는 메모리 수신 확인이며 DB 영속 저장·큐 등록·입출차 처리 예약 완료를 뜻하지 않는다. 오류는 팀 초안의 error/code/message/details 형태이며 422 응답은 입력 원문과 ctx를 재노출하지 않는다.

예시:

```json
{"event_id":"fixture-001","disposition":"accepted"}
```

```json
{"event_id":"fixture-001","disposition":"duplicate"}
```

```json
{"error":{"code":"EVENT_ID_CONFLICT","message":"같은 event_id의 내용이 다릅니다.","details":[]}}
```

## 인증·저장소 경계

- create_experiment_app/create_router는 저장소와 인증 콜백을 명시적으로 주입해야 한다. 인증 콜백 반환값이 정확히 True인 경우만 허용하며 False/None/숫자/문자열은 거절한다. 예외로 반환한 401/403도 보존한다.
- 테스트의 X-Test-Caller=fixture는 시험 호출 구분자일 뿐 실제 비밀키나 운영 인증이 아니다. 실제 토큰·권한·manual 감사 정책이 정해지기 전 uvicorn 실행/운영 등록/외부 공개를 하지 않는다.
- InMemoryEventStore는 잠금 하나 안에서 ID 확인·비교·등록한다. 검증된 전체 payload를 정렬 JSON bytes로 보관하며 float 해시 충돌이나 원본 dict 변경에 의존하지 않는다.
- 동일 내용은 Pydantic 검증 뒤 비교한다. JSON 키 순서, 정수/실수 좌표 표기, 같은 순간의 시간대 표기, 선택 frame_id 생략/null은 같다. 관측 시각은 aware epoch와의 차이를 정수 마이크로초로 계산해 연도 경계의 UTC 변환 overflow도 피한다.
- event_id 범위는 단일 저장소 인스턴스 전체다. lot/camera 별 범위, 보존 기간·재시작 후 복원·분산 원자성·재처리 정책은 미확정이다. 서로 다른 인스턴스는 중복을 공유하지 않으며 재시작 후 동일 이벤트를 새 접수할 수 있다.
- 기본 신규 ID 한도1000은 실험 메모리 제한이며 운영 용량이나 계약 수치가 아니다. 요청 크기 제한·만료·영속 큐·후속 엔진 부작용과 접수의 원자성은 이번 구현에 없다.

## 실제 검증

기존 Git 제외 Python3.11.18/Pydantic2.14.2/FastAPI0.162.0/pytest9.2.0 환경을 재사용했고 설치·requirements 변경은 없다.

최종 결과: **124 passed, 경고1, 실패0, skip0, 2.17초**.

| 집합 | 실제 범위 | 결과 |
| --- | --- | --- |
| Step2 신규 | 7종 API 시퀀스·422/인증·중복/충돌·정규화·극단 시각·동시 요청·용량·운영 경로 미노출 | 47 passed |
| Step1 재실행 | 입력 스키마·fixture·경계·DB/카메라 없는 새 프로세스 import | 45 passed |
| 기존 회귀 | health/조회23개·billing 순수계산9개 | 32 passed |

실행 대상:

```text
tests/test_vision_event_receiver.py tests/test_vision_event_schema.py tests/test_health.py tests/test_mock_api.py tests/test_billing.py tests/test_api_integration.py::test_fee_boundaries -q -p no:cacheprovider --tb=short
```

Python은 `.local/kan17-dependency-check-20261103/Scripts/python.exe`를 사용했다. 실행 프로세스에서 sqlite3.connect를 예외 함수로 교체하고 psycopg/psycopg2/asyncpg/Alembic import를 차단한 뒤 pytest.main을 호출했다. 최초 명령 전달 두 번은 결과 없이 대기해 중단했으며 시험 성공으로 세지 않는다. 최종 실행만 위 통계로 기록한다.

| 동시 요청 조건 | 결과 |
| --- | --- |
| 같은 ID/내용20개 | 202 1개, 200 19개, 접수1건 |
| 같은 ID에 내용2종 각10개 | 202 1개, 200 9개, 409 10개, 접수1건. 먼저 등록된 내용은 순서 비결정적 |
| 서로 다른 ID20개 | 202 20개, 접수20건 |
| 용량1에 서로 다른 ID20개 | 202 1개, 503 19개, 접수1건 |

ThreadPoolExecutor의20개 호출을 Barrier로 동시에 시작하고 같은 TestClient·메모리 접수함을 공유했다. 단일 프로세스의 요청 경합 시험이며 실제 네트워크·다중 워커·성능 부하 시험은 아니다. 경고1개는 기존 Starlette/httpx 사용 중단 안내다.

읽기 전용 코드 검토에서 인증 반환값과 극단 시각의 두 경계 문제를 찾아 보완했고 최종 시험에 포함했다. 실제 DB·기존 SQLite 파일·입출차/결제 상태는 사용하지 않았다. V1은 재실행하지 않았고 V2/V3·AI·카메라·DB 통합·운영 인증 시험도 미실행이다.

## Step 3 전에 결정할 사항

1. PM/모델·카메라 담당: 필수 증거 필드, bbox 좌표계·ID 매핑, manual 허용·감사.
2. 내부 API 인증·접근 범위·요청 크기와 속도 제한.
3. event_id 발급 주체/전역 범위, 동일/충돌 내용 정의, 202/200/409와 오류 코드 확정.
4. 승인된 영속 저장소·고유 제약·트랜잭션·실패 후 재시도·재시작/다중 워커 시험.
5. 접수 후 후속 엔진 연결과 별도 안정화/입출차 정책. 단일 관측이나 가림을 출차로 처리하지 않는다.

지금은 합성 입력·수신 경계·단일 프로세스 멱등 접수 실험까지 완료다. Step3의 실제 공통 DB 저장·운영 등록·상태 판정은 위 승인 전 시작하지 않는다.

## 학습 메모

- FastAPI 라우터: 요청 주소를 처리 함수에 연결하는 접수 창구 묶음이다. 이번 창구는 운영 본관에 연결하지 않은 시험 창구다.
- 멱등키: 재송신이 같은 작업인지 알아보는 표식이다. 이번에는 event_id가 그 역할을 한다. 새 관측에는 새 ID가 필요하다.
- 409 Conflict: ID는 같지만 내용이 달라 기존 접수와 충돌한다는 응답이다. 이번 사용은 승인 전 실험 정책이다.
- 422 Validation Error: 차량 ID·시각·좌표 등 요청 양식이 잘못됐다는 응답이다. 양식을 고친 뒤 다시 요청해야 한다.

복습: 같은 event_id로 관측 시각만 바꿔 보내면 어떤 응답인가? 테스트 앱을 새로 만들면 이전 접수가 유지될까? 사용자 이해/직접 실습 완료로 표시하지 않는다.

## 기록·종료 확인

- 개인 Notion 메인·개발 일지·다음 개발 순서·상세 학습·초보자 이해 노트5페이지를 갱신하고 재조회했다. 기존 Step1 및 과거 본문을 유지했고 메인 지정 상태행만 최신화했다. 새로운 문제/이미지 증빙·담당 변경이 없어 나머지 기록은 유지했다.
- 개인 PROGRESS.md에는 이번 실제 결과와 기록 성공만 추가했다. 개인 GitHub 원격 반영은 아직 없으며 기존 main.py 사용자 변경은 해시 동일이다. 개인 Jira 공간 미식별, KAN-17 댓글0·진행 중을 읽었고 팀 Jira/Notion은 수정하지 않았다.
- 원래 Step1 신규4파일·V1 보고서/시험4파일의 SHA-256이 작업 전과 일치한다. Step2에 복사한 Step1 4파일도 원본과 동일하다. PR#8은 종료 재조회에서 Draft/open/미병합·head6fb1428이다.
- 최종 팀 worktree는 기존 추적 파일 변경·Stage 없이 미추적8파일이다. Step1 재사용4파일+이번 신규4파일이며 Git 제외 _workspace/00_request.md는 공유 대상이 아니다. 공백 오류 없음, Windows 줄바꿈 전환 안내만 확인했다. 팀 main.py·billing·계약·requirements·실제 DB는 변경하지 않았다. Commit/Push/PR 생성 없음.
