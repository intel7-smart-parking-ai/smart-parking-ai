# KAN-17 — SQLite 공통 기반 Step 0~1 검증 결과

확인일: 2026-11-03 (Asia/Seoul). **SQLite+SQLAlchemy는 DB 담당의 우선 개발·검증 방향이다. PM 김한별·billing 윤명수의 최종 합의 전이며 실제 공통 DB 전환은 미실행이다.**

Jira: [KAN-17](https://rgq548.atlassian.net/browse/KAN-17). 기존 [통합 설계](DB_INTEGRATION_PLAN.md)는 PostgreSQL 우선 제안의 당시 이력으로 보존한다.

## 1. 기준·범위와 작업 공간

| 항목 | 실제 확인 |
| --- | --- |
| 팀 최신 main | 종료 시 `cb764937b465293d6cfb16914440b720a0ec5a7e`. 시작/회귀 기준은 `922809f68e1ed576809bea7c4fec66fbc559b5f3`, KAN-28 PR #5 병합 완료 |
| PR #8 | head `6fb142812e1f800808641f05523e63d8836d03e4`, Draft·열림·미병합 |
| 이번 브랜치 | `feature/KAN-17-db-stage1-verify`, **PR #8 HEAD에서 분기** |
| 별도 worktree | 개발원 PC의 `.local/team-integration/smart-parking-ai-stage1` |
| 기존 작업 폴더 | `.local/team-integration/smart-parking-ai`, 세션 준비 브랜치의 기존 미추적 문서·테스트 2개 유지 |
| 검증 환경 | 기존 격리 Python 3.11.18 / SQLAlchemy 2.1.6 / pytest 9.2.0 / SQLite 3.53.1, 패키지 변경 없음 |
| 이번 제외 | V1~V3 본검증, 실제 데이터 접근/백업/이관, 새 업무 모델/시드/API, billing 이전·main 등록, Alembic, Commit/Push/PR 생성 |

바탕화면의 `DB_통합방안.md`, `DB_단계적통합계획.md`, `DB_1단계_검증계획.md`와 팀 DB 통합 계획을 읽었다. 참고 문서의 'PR 전', billing 실행 중, 기본 실사용 URL·파일 삭제/재생성·잠금 동등 보장 등의 표현은 현재 사실·승인으로 취급하지 않는다.
이번 사용자 지시가 우선이므로 기본 URL·실사용 DB 경로·기존 파일 재생성은 적용하지 않고, 두 모델과 새 임시 검증 환경만 준비했다. SQLite 우선 방향으로 바뀐 이유는 설치/팀 연결 부담과 기존 billing 호환성을 먼저 검증하려는 것이며, 최종 엔진/세션 정책 승인은 별도다.

종료 확인 중 main에 PR #9/#10의 `docs/PROGRESS.md`, `docs/TIMELINE.md`가 추가됐다. 두 main 커밋의 backend/tests Git tree 해시가 각각 동일함을 확인했다. 따라서 코드 차이는 없지만 32개 회귀의 **실제 실행 커밋은 922809f**로 기록하고, cb76493에서 재실행했다고 표시하지 않는다. 최신 문서도 읽었으며 기존 진행률/일지 파일을 덮어쓰지 않고 이번 worktree에 병합하지 않았다.

## 2. 실제 변경 파일

| 파일 | 변경 |
| --- | --- |
| backend/app/database/models.py | 두 모델의 trim 및 엔진별 JSON 타입 조정 |
| backend/app/database/settings.py | 기본값 없는 명시 설정, 검증 경로 검사, 비밀값 없는 오류 |
| backend/app/database/session.py | 새 파일만 확보하는 검증 엔진, FK 활성화, 세션 팩터리/get_db |
| tests/test_db_model_prototype.py | 기존 8개 시험의 trim/JSON 기대값만 호환형으로 보완 |
| tests/db_stage1/test_sqlite_foundation.py | 보호/실제 SQLite 제약·JSON·관계·세션·PG 컴파일 등 42개 시험 |
| docs/DB_STAGE1_REPORT.md | 이 결과·미검증·승인 대기 사항 |

Base/명명 규칙, 기존 main.py·조회·API 계약·요구사항, 개인 원본 모델/0001 및 기록 파일은 변경하지 않았다. 최신 main의 billing 코드는 Git에서 참조했으며 이번 worktree에 병합/복사하지 않았다. `_workspace/00_request.md`와 시험 DB는 Git 제외 로컬 자료다.

## 3. 모델 호환성

- `ParkingLot`: lot_id PK, name/type/active/created_at, real/virtual CHECK와 공백 이름 거절 유지.
- `ParkingSpace`: (lot_id,space_id) 복합 PK, lot FK의 ondelete/onupdate RESTRICT, roi_version>=0·enabled·NULL/기본값 유지. 원래 추가 UNIQUE/보조 인덱스는 없으며 새 제약을 임의로 넣지 않았다.
- `btrim(column) <> ''` → `trim(column) <> ''`. 기본 공백(U+0020)을 제거하는 기존 취지를 유지하며 모든 공백 문자를 새로 금지하는 정책을 추가하지 않았다. 제약 이름은 동일하다.
- `JSON(none_as_null=True).with_variant(JSONB(none_as_null=True), "postgresql")`: SQLite JSON / PostgreSQL JSONB. Python None은 JSON null 문자열이 아닌 SQL NULL로 유지한다. dict/list/None 저장·조회 검증.
- `ParkingLot.spaces`의 viewonly 관계 유지. 미이전 Camera/ParkingSession/SpaceState 참조 관계 3개는 계속 보류했다. 가짜 모델·임의 상태·실제 면수/시드는 추가하지 않았다.

SQLite는 VARCHAR(n) 길이를 PostgreSQL처럼 강제하지 않으며, DateTime(timezone=True)도 읽기 때 시간대 정보를 동일하게 복원하지 않는다. 선언/키/검증된 제약을 유지했다는 뜻이며 **전체 데이터 의미·시간대·잠금의 DB 간 동등성을 입증한 것은 아니다**. 입력 길이/UTC 정규화/시각 직렬화 정책은 후속 합의 대상이다.

## 4. 테스트 DB 보호와 설정

`DatabaseSettings.from_env(test_root=...)`는 DATABASE_URL만 읽고 기본값·.env 로딩·billing 설정 대체가 없다. 테스트는 설정 mapping 또는 monkeypatch로 검증 URL을 제공하고 환경을 복원한다. URL 원문/인증정보는 오류에 출력하지 않는다.

허용 조건은 모두 충족해야 한다.

1. sqlite 또는 sqlite+pysqlite 파일 URL, 호스트/인증/query 옵션 없음. PostgreSQL URL은 이번 실행에서 거절한다.
2. 호출자가 명시한 **실제로 존재하는 pytest tmp_path**의 직접 자식·절대 경로. 상대 경로는 모호성을 없애기 위해 모두 거절한다.
3. 파일명 `kan17-verify-<UUID hex 32자리>.sqlite3`. `smart_parking.db`는 대소문자와 허용 루트 위치에 관계없이 거절한다.
4. 점/상위 경로·끝 공백/점·8.3(~)·환경변수/홈 확장·인코딩·UNC/장치/ADS·URI 옵션·루트 이탈을 거절한다. 외부 파일 경로는 resolve/stat 전에 거절한다.
5. 허용 디렉터리/조상의 symlink 및 Windows junction/reparse를 검사한다. 기존 파일은 `O_CREAT|O_EXCL`로 거절하며 내용을 열거나 초기화하지 않는다.
6. 새로 확보한 파일의 inode/device·일반 파일·단일 링크를 연결 때 재확인한다. 내부 SQLite URI의 mode=rw는 파일이 사라졌을 때 자동 새 파일 생성을 막는다.

허용 tmp_path는 테스트 호출자가 제공하는 신뢰 경계다. 이 코드가 임의 디렉터리를 pytest 소유라고 인증하는 것은 아니다. 승인한 임시 폴더를 다른 프로세스가 조작하지 않는 조건이 필요하며 파일 검사와 연결 사이의 경쟁까지 막는 OS 보안 격리 장치는 아니다. 경로 문자열 검사만으로 실사용 데이터 안전을 보장한다고 주장하지 않는다.

실제 실사용 `data/smart_parking.db`를 읽기/쓰기/해시/백업 목적으로 열지 않았다. 파일 보호 시험의 기존 내용/별칭/교체 대상은 모두 테스트가 직접 만든 임시 파일이다. junction 검사는 실제 Windows junction을 생성한 시험이 아니라 reparse 메타데이터 주입 시험이며, 하드링크·파일 교체는 자신이 만든 파일로 실제 검증했다.

현재 연결은 **검증 전용**이다. 운영/개발 공통 DB를 열도록 보호를 해제하거나 기존 파일을 재사용하는 경로는 만들지 않았다. .env와 서버 설정도 변경하지 않는다.

## 5. 세션·연결 생명주기

- settings/session/models import는 파일 확보·엔진 생성·접속·DDL을 수행하지 않는다. 별도 프로세스에서 해당 동작을 차단한 채 import/mapper 성공을 확인했다.
- 명시적인 `create_test_engine(settings)` 호출에서만 새 빈 검증 파일을 배타적으로 확보한다. 스키마는 엔진 생성 시 자동으로 만들지 않는다.
- **tests fixture가** 해당 새 tmp_path DB에만 `Base.metadata.create_all(engine)`을 명시 실행했다. 실사용 DB/일반 CLI 초기화/마이그레이션이 아니다.
- 모든 새 연결에서 PRAGMA foreign_keys=ON 및 실제 값1을 확인한다. NullPool로 연결을 보관하지 않고 사용 후 닫는다. WAL·busy_timeout 정책은 이번에 설정/비교하지 않았다.
- `create_session_factory(engine)`와 `get_db(factory)`는 호출자가 commit을 결정한다. 예외 시 rollback, 모든 종료 경로에서 close. 정상 종료한 미커밋 쓰기도 close로 rollback된다. 닫힌 Session은 재사용하지 않도록 설정했다.
- FastAPI main/get_db 의존성 등록 없음, billing sqlite3 연결 함수 변경 없음, 자동 commit 없음.

## 6. 실제 시험 결과

| 실행 | 결과·해석 |
| --- | --- |
| 첫 실행 | 32 passed / 37 setup errors / 경고1. 지정 basetemp 상위 폴더 미생성이 원인, SQLite fixture가 시작되지 못함. 성공으로 처리하지 않음 |
| 상위 폴더 준비 후 재실행 | 69 passed, 경고1 (4.01초) |
| 보호·복합키/FK 경계 보완 후 최종 worktree 전체 tests | **73 passed, 1 warning in4.00s**, 실패/오류/skip0 |
| 구성 | 신규42 + 기존 모델8 + 기존 health/목업23 = 73. 이전 실행 수를 합산하지 않음 |
| 최신 main922809f 별도 회귀 | **32 passed, 1 warning in1.45s**: health/목업23 + 기존 순수요금9, DB 접속 차단. 위73과 중복되는 API가 있어 고유105개로 보고하지 않음 |
| 경고 | 기존 Starlette TestClient/httpx 사용 중단 예정 경고. 패키지 임의 변경 없음 |

실제로 확인한 것: import/mapper/metadata 두 표, PK/복합 PK·이름 유지, FK 활성화·없는 부모/부모 삭제·키 갱신 거절, 같은 space_id의 서로 다른 lot 허용 및 같은 복합키 중복 거절, 5CHECK, 기본값·JSON/SQL NULL·viewonly 조회, 명시 commit/예외 rollback/미커밋 종료 rollback/닫힌 Session, URL 미설정·실사용/상대/절대/별칭 경로 차단·기존 파일 보존, PG 타입·DDL 컴파일.

worktree 루트에서 재현(실제 Python은 기존 `.local/kan17-dependency-check-20261103/Scripts/python.exe`):

```powershell
$python = '..\..\kan17-dependency-check-20261103\Scripts\python.exe'
$testParent = '..\..\kan17-stage1-tests'
[IO.Directory]::CreateDirectory((Join-Path (Get-Location) $testParent)) | Out-Null
$testRoot = Join-Path $testParent ('run-' + [guid]::NewGuid().ToString('N'))
if (Test-Path -LiteralPath $testRoot) { throw '기존 경로 재사용 금지' }
& $python -B -X utf8 -m pytest tests/ -q -p no:cacheprovider --basetemp $testRoot
```

시험 DB는 Git 제외된 고유 실행 폴더의 pytest tmp_path에만 있다. 재실행 때 기존 basetemp를 재사용/삭제하지 않는다. 위 전체 tests 명령은 **billing이 없는 PR #8 기반 worktree 전용**이다. 최신 main에서는 billing 파일 DB fixture·동시 결제 시험을 선택하지 않고 health/mock/billing 순수 계산 및 test_fee_boundaries만 DB 접속 차단 프로세스에서 실행했다.

미실행: PostgreSQL 접속/DDL·실제 billing DB·main의 SQLite 통합21개/동시 결제·프론트/챗봇 전체 시험·실서버·백업/이관·V1~V3·새 세션/입출차/주차칸 배정·API 실제 DB 연결. 과거 '53개'·실서버20건 기록을 이번 재실행 결과로 취급하지 않는다.

## 7. PostgreSQL 검토와 V1~V3 준비

| 대상 | 이번 결과 | 후속 조건 |
| --- | --- | --- |
| PostgreSQL | 엔진/드라이버 없이 JSONB·복합키·FK·trim·timestamptz DDL 컴파일 확인 | 실제 제약·시각·동시성 시험은 별도 DB/권한 승인 후 |
| V1 동시 결제 | main의 기존 UNIQUE·멱등성·시험 구조 읽음, 실행 안 함 | 윤명수와 기준 부하·새 전용 DB·연결/트랜잭션/재시도·WAL/busy_timeout 비교 계획 합의 |
| V1 칸 배정/동시 입차 | 해당 서비스/모델은 아직 없음 | 정책 결정 후 **별도 검증용 로직·제약** 필요. PK만 시험하고 이중 배정 방지 성공으로 보고할 수 없음 |
| V2 백업/이관 | 원본 파일/복사본에 접근하거나 스크립트 실행 안 함 | 원본 소유자 승인·SQLite 안전 백업 방식(WAL 포함 일관성)·보존 범위·새 목적지·건수/금액/순매출/NULL/중복 검증·복구 절차 합의 |
| V3 session_id | 기존 문자열 소비와 UUID 제안 자료만 검토, 새 모델/ID 검증 안 함 | String(36)·새 UUID 문자열·기존 ses_ 보존·36자 초과 이력·API 접두어 의존·FK 매핑을 별도 합성 시험 |

SQLite 쓰기 직렬화나 `with_for_update()` 무시를 PostgreSQL 행 잠금과 동등한 보장으로 간주하지 않는다. V1 성능/경합 합격 여부·V2 원본 보존/복원·V3 형식 호환은 모두 아직 미검증이다. 단순 파일 복사를 WAL 사용 중 안전한 백업이라고 자동 판단하지 않는다.

## 8. PM·billing 결정과 다음 작업

1. SQLite 우선 **검증**과 팀 공식 운영 DB 선택을 구분하여 최종 엔진·서버/파일 소유권·시험 권한을 합의한다.
2. PR #8을 그대로 리뷰/병합할지 이 검증 결과와 통합할지 결정한다. 이번 브랜치는 PR #8을 수정/대체/병합하지 않는다.
3. Vehicle/ParkingSession/SpaceState는 기존 계획의 내부 UUID·외부01~24·필수 차량 FK·현재 위치/세션 복합 참조를 재사용 후보로만 둔다. 미식별 방문 허용, 차량 번호의 실물/가상 중복 범위, active/closed·active/exited·parked/exited, 시간대/길이 검증·이동/종료 정책을 먼저 결정한다.
4. billing이 기대하는 car_id/space_id/entry_at/exit_at/session_id 투영, parking_lots.total_spaces, 내부 commit과 상위 트랜잭션 책임, 과거 결제/세션 ID 보존 및 데이터 이관을 윤명수와 합의한다. 담당 코드/테이블/연결은 이번에 수정하지 않는다.
5. V1~V3의 별도 검증 로직·실행 범위 승인 → 결과/결정 회의 → 승인된 모델/어댑터 이전 → 안전한 실제 통합 순서로 진행한다. 새 API·시드·일반 DB 초기화·Alembic은 별도 단계다.

현재 완료는 Step 0 작업 공간·검증 DB 보호와 Step 1 두 모델 호환/세션 기반 시험이다. 실제 공통 DB 전환·추가 모델·V1~V3 합격·최종 팀 정책 확정은 완료가 아니다. Commit/Push/PR 생성 없이 변경 결과를 보고하고 승인 대기한다.

## 9. 기록·종료 확인

- Jira KAN-17과 기존 Notion 메인·개발 일지·다음 개발 순서에 이번 검증 단계만 보완하고 재조회했다. 기존 설명/일지/계획 본문과 메인 지정행 밖 내용은 보존, KAN-17은 진행 중이다. 원격 공유 전 로컬 산출물임을 명시했다.
- 종료 시 maincb76493 문서 추가와 실제 시험 기준922809f의 차이도 기록했다. PR #8·기존 세션 준비 미추적2파일과 개인 main.py/PROGRESS/NEXT_STEPS 해시 보존을 확인했다. 개인 기록 파일을 수정하지 않았다.
- 별도 읽기 전용 QA 검토에서 필수 수정 결함은 확인되지 않았고, 경로 경쟁·SQLite 길이/시간대·junction 검증 한계는 본 보고서에 명시했다.
- 최종 변경 대상은 위6파일뿐이며 미Stage·미Commit·미Push 상태다. 환경·검증 DB·_workspace는 Git 제외이고 팀 main/PR 병합은 수행하지 않는다.

## 10. V1 SQLite 동시성 검증 — 2026-11-03 후속 로컬 작업

이 절만 후속으로 추가하며 위 Step 0~1·미실행 당시 기록은 보존한다. 기준 브랜치 HEAD는 6999b2348c257e90927c953970cea3f90e4cda9f, 별도 main worktree는 3a4d759276488fe20e95b8006a48fda1e71da302다. PR #8 head6fb1428 보존. 브랜치 병합·billing/main.py 변경·Commit/Push/새 PR 없음.

### 실행 구분과 보호

- **기존 KAN-28 실제 코드:** 별도 detached main worktree에서 원본 billing_router/get_db_conn/계산/결제/취소/매출을 사용했다. 테스트 전용 FastAPI 앱에 원래 라우터만 등록하고 TestClient로 호출했다. 운영 main 등록·TCP/uvicorn 공개는 하지 않았다.
- **테스트 전용 실험:** 칸/입차 서비스가 없으므로 별도 임시 experiment 테이블의 resource PK 및 request_id UNIQUE와 명시 commit/rollback을 시험했다. 차량당 현재 실험행1개를 표현하며 active/closed 정책, 운영 Session/Vehicle/SpaceState 모델을 구현하지 않았다.
- **미검증:** 실제 공통DB 통합, 관측 안정화→입출차→위치→결제 전체 흐름, 이동/미식별/지연, 운영 부하/다중 프로세스/실서버 네트워크는 별도 검증 필요. V2·V3 미실행.

모든 V1 파일은 pytest tmp_path의 새 UUID SQLite 파일이다. 기존 DATABASE_URL 설정 가드를 변경하지 않고 v1_support가 같은 가드를 파일 경로에서 로드한다. O_EXCL 예약·소유 inode/device·단일 링크 재검사·mode=rw로 자신이 새로 만든 파일만 연결한다. main의 sqlite3.connect는 시험 프로세스에서만 이 연결기로 제한했다. 실사용 DATABASE_URL/.env·data/smart_parking.db·billing 기존 데이터는 읽거나 쓰지 않았다. 프로파일 PRAGMA는 임시 연결에만 적용했다.
기존 main53개 시험의 test.db/t.db 요청도 승인 임시 범위인지 검사한 뒤 새 UUID 파일로 매핑했다. 테스트 원본·응답·비즈니스 로직은 변경하지 않았다.

### A. 동시 결제 — 실제 billing 코드

프로파일마다 서로 다른 멱등키20개를 장벽으로 동시에 출발시켰다. 네 조건 모두 **HTTP200 1건, HTTP409 ALREADY_PAID 19건**, 새 결제1행이었다. 성공 키 재요청은 같은 payment_id·idempotent_replayed=true, 취소200·재취소409, 순매출2500→0을 확인했다. 취소 후 완료결제0·전체결제행1을 확인했다. HTTP500·관측된 SQLite 잠금 오류0, 응용 자동재시도0이다. SQLite busy 핸들러 내부 대기/재시도 횟수는 계측하지 않았다.

### B/C. 칸 배정·차량 입차 — 실험 구현

각 조건에서 각각10개 쓰기를 동시에 출발시켜 **삽입1·충돌9·최종1행**, 성공 요청의 동일 키 재요청은 replayed였다. IntegrityError 뒤 rollback 및 현재 행 재검사를 사용했다. 실패 요청에 임의 자동 재시도는 하지 않았다.
UNIQUE 없는 대조 테이블에서는 같은 resource에10개 모두 저장됐다. 따라서 SQLite 쓰기 직렬화/트랜잭션만으로 업무 중복이 방지되는 것은 아니다. 이 결과는 운영 칸 배정/입차 서비스 합격을 뜻하지 않는다.

### D. 설정 및 평균/p95 (밀리초)

각 셀은 평균 / p95다. 표본은 결제20개, 칸/입차 각각10개로 작으며 프로파일별1회 측정이다.

| 프로파일 | 결제 평균/p95 | 칸 실험 평균/p95 | 입차 실험 평균/p95 |
| --- | --- | --- | --- |
| 기본 DELETE | 61.219 / 70.859 | 16.438 / 40.509 | 22.081 / 33.064 |
| WAL | 36.586 / 57.902 | 21.705 / 50.234 | 31.584 / 73.011 |
| DELETE+busy_timeout10000 | 81.027 / 130.317 | 12.768 / 22.450 | 28.594 / 50.923 |
| WAL+busy_timeout10000 | 56.293 / 103.557 | 19.789 / 56.047 | 17.603 / 43.565 |

주의: 설정 탐침 연결의 기본 timeout은5000ms지만 **원본 billing 요청 연결은 기존 코드가 timeout=10을 지정해 기본/WAL도10000ms**다. 그러므로 결제에서 timeout 증가 효과를 분리한 비교가 아니며, 명시10000 설정은 기존값을 재확인하는 조건이다. 칸/입차 실험은 실제 기본5000 대10000 비교다. WAL은 파일 단위, timeout은 매 연결에 설정했다. 이 표로 WAL이 항상 더 빠르다거나 timeout을 늘리면 문제가 해결된다고 결론내리지 않는다.

p95는 정렬 후 ceil(0.95*N)번째 값(nearest-rank)이다. 결제는 장벽 해제 뒤 TestClient 호출부터 응답까지(ASGI 내부, 실제 TCP 아님), 칸/입차는 연결 생성·장벽 대기 뒤 SQL→commit/충돌 처리·close까지 측정한다. 시작 전 큐 대기/네트워크 지연은 제외한다. 제약 충돌은 의도된 정상 거절이고 database is locked 오류와 구분한다.

### 60초 혼합 부하

WAL+10000 조건만 실행했다. 합성 관측 INSERT10회/초(카메라2대×5에 대응하는 저장 부하)와 **기존 billing 결제2회/초**를60초 동안 예약했다. 사전 합성 세션120개는 시험용이며 실제 입차 서비스로 생성한 것이 아니다.
결과: 60.011초, 합성관측600행·결제120행, 처리720건 모두 성공·500/잠금오류0. 전체720작업 평균17.540ms/p95 30.985ms. 이 값은 관측 SQL과 결제 HTTP 내부 호출이 섞인 작업시간이며 결제 전용 SLA가 아니다. 스레드20개, 실행자 큐 대기·스케줄 지연은 측정에서 제외했다.
비전 추론·POST vision-events·점유 안정화·LLM·웹·실제 카메라/운영 네트워크 부하는 포함하지 않았다. 다른 설정의60초 시험·반복/장시간/다중프로세스 부하는 미실행이다.

### 회귀·실행 이력

| 대상 | 실제 결과 |
| --- | --- |
| stage1 HEAD6999b23의 기존73+신규 실험9 | **82 passed,1 warning,4.86초**, 실패/skip0 |
| main HEAD3a4d759의 기존53개 | **53 passed,1 warning,2.76초**, 실패/skip0 |
| main 원본 billing 신규 동시성4+60초부하1 | **5 passed,1 warning,61.41초**, 실패/skip0 |

이 세 집합은 별도 프로세스/작업 공간이며 API 중복도 있다. 전체 통합140개 성공으로 합산하지 않는다. 기존 Starlette/httpx 사용 중단 예정 경고는 그대로다.
최초 V1 실행은 stage81개·billing4개 통과였고, 이후 연결 문맥의 명시 close·UNIQUE 없는 대조 및60초 시험을 보완하여 위 최종 집합으로 검증했다. 저장한 main 회귀 실행기는 최초 직접 실행 시 cwd가 sys.path에 없어 수집1오류가 발생했다. 시험 실행기만 수정해 main cwd 검증/sys.path를 추가한 뒤53개 통과했다. billing 원본 문제나 DB 통합 실패로 분류하지 않는다.

신규 파일은 tests/v1_support.py, tests/test_v1_sqlite.py, tests/v1_billing_probe.py, tests/v1_main_regression.py다. 결과 JSON은 Git 제외 로컬 실행 폴더에 저장했다. 원본 billing·main.py·기존73개시험·연결 가드는 수정하지 않았다.

재현은 기존 Python3.11 환경에서 새 결과 폴더를 만들고 KAN17_V1_RESULTS를 지정한다. stage worktree에서 pytest tests/를 실행하고, 별도 main worktree에서 stage의 tests/v1_billing_probe.py를 외부 시험 경로로 지정한다. 두 프로세스 모두 -B -X utf8, -p no:cacheprovider 및 서로 다른 새 --basetemp를 사용한다.
기존 main53개는 main cwd에서 KAN17_V1_REGRESSION_ROOT를 새 경로로 지정하고 stage의 tests/v1_main_regression.py를 실행한다. 기존 경로 재사용은 거절한다. 결과 폴더와 명령 대상 커밋을 기록해야 하며 일반 운영 DB에서 재현하지 않는다.

### 후속 권고

V1의 이번 제한된 합성 부하에서는 중복 방지와 응답이 유지됐다. SQLite와 PostgreSQL 잠금이 동등하다는 근거가 아니며 운영 승인은 보류한다. 실제 서비스의 트랜잭션 경계·복합 제약·키 정렬/재검사·busy 예외→HTTP 정책·재시도/재진입 멱등성·백프레셔를 소유자와 합의해야 한다.
V2는 데이터 소유자 승인과 WAL 일관성을 포함한 안전 백업·이관 목적지/건수·금액/복구 검증 계획부터 진행한다. V3는 기존 ses_·장문ID·UUID 문자열 및 소비자 형식 의존을 별도로 검증한다. 이번에는 둘 다 실행하지 않았다.
