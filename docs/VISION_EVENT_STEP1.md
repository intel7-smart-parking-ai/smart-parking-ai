# vision-event Step 1 — 관측 스키마와 합성 시퀀스

2026-11-03. 팀 main d1f924830bee03b80fdd2b789649f3829620f5e9 기준, 별도 feature/KAN-17-vision-event-step1 / smart-parking-ai-vision-step1 worktree. 로컬 검증용이며 Commit/Push/PR 및 실제 API 연결 전이다.

## 확인한 기준과 접근 범위

- GitHub의 최신 API_CONTRACT·통합 지침서 §5·api-contract 스킬·TIMELINE·CLAUDE와 기존 코드/테스트를 읽었다. 기존 수신 Pydantic VisionEvent는 없고 개인 DB VisionEvent와 챗봇의 대화 모델은 다른 역할이다.
- 팀 타임라인에 SQLite 최종 승인·D5 범위·ID 매핑·선행0 처리 등이 대기로 남아 있다. 이를 새 운영 정책으로 확정하지 않는다. 11월6일 D5 목표와 별개로 안정화5초/출차10초/신뢰도 임계값은 코드에 넣지 않았다.
- Jira KAN-17은 팀 프로젝트의 진행 중 항목이다. vision/이벤트 검색에서 별도 항목은 확인하지 못했다. 개인 Jira 프로젝트도 식별되지 않아 이번에는 쓰지 않는다.
- Notion에서 API/스마트 주차장으로 검색했으나 팀 전용 계약·일정·담당 페이지는 찾지 못했다. 접근 불가인지 미연결/검색 누락인지는 확정하지 않는다. 확인한 개인 ParkFlow 메인/일지/계획/학습/문제 해결만 기록 대상으로 사용한다.
- 개인 GitHub main77baee7 및 기존 모델을 읽었다. 팀 코드를 개인 원격에 복제하거나 Commit/Push하지 않았다. V1 보고서·미커밋4시험파일과 기존PR#8 head6fb1428·개인main.py 변경은 그대로 보존한다.

## 구현 범위

backend/app/api/vision_event_schema.py의 VisionEvent는 데이터 모양/값만 검증하는 Pydantic 모델이다. main.py·라우터·DB·상태 엔진에는 등록하지 않았다.

| 항목 | 이번 검증 |
| --- | --- |
| event_id/camera_id/space_id/frame_id | 문자열 보존·빈 문자열 거절, camera/space는 null 허용. 길이·실제 존재 여부·camera↔lot/ROI 매핑은 별도 |
| event_type | vehicle_observed, space_empty_observed, plate_observed, mock_fire_led_observed |
| source | vision/simulation/manual 초안. 기본값 없이 명시 |
| lot_id | A/B/C/D. 팀 지침에 따라 가상 C/D camera_id=null |
| car_id | 문자열01~24 또는 null, 정수/"1"의 묵시 변환 없음 |
| confidence | 유한 숫자0~1. bool·문자열·NaN·무한대 거절. 낮은 값도 관측으로 허용 |
| bbox_xyxy | null 또는 유한·비음수 좌표4개, x2>x1/y2>y1. 영상 크기·좌표 단위/정규화 범위는 확정하지 않음 |
| observed_at | 시간대 포함 ISO 문자열/datetime, naive 및 숫자 epoch 입력 거절. UTC Z와+09:00 모두 허용 |

사용자 요청의 bbox는 팀 예시의 **bbox_xyxy**로 작성했다. 별칭 bbox는 임의로 추가하지 않았다. frame_id는 LED 예시를 수용하는 선택 필드다. LED별 필수 증거·모델 버전/추적 키·추가 필드/엄격도는 계약 승인 전 남은 사항이다. 현재 extra=forbid와 필수 nullable 키는 이 독립 초안의 입력 규칙이며 배포된 공식 API 규격이 아니다.

source3종은 팀 스킬의 관측 출처와 맞지만 개인 기존 DB CHECK는 manual을 허용하지 않는다. manual 인증·감사·DB 어댑터를 승인하지 않은 채 해당 모델로 쓰지 않는다. 스키마 통과를 DB 저장 가능으로 간주하지 않는다.

## 합성 JSON 7종

tests/fixtures/vision_event_sequences.json에 시나리오별 배열을 둔다. 모든 날짜/칸/차량/좌표는 시험용이며 실제 관측·면수·주차 성공 기록이 아니다. 각 시나리오는 독립적이고 내부 시간순(중복은 같은 시각)이다.

| 시나리오 | 내용과 해석 범위 |
| --- | --- |
| normal | 같은 칸/차량의 연속 관측2건, 입차 확정 아님 |
| temporary_miss | 시각0초·2초 관측 사이1초 구간은 이벤트 없음. 허구의 missing/exit 이벤트를 만들지 않음 |
| empty_space | space_empty_observed, car_id/bbox=null. 출차 확정 아님 |
| unrecognized | vehicle_observed+car_id=null, 가짜 번호 생성 없음 |
| move | 같은 차량 A-01→A-02의 관측2건. 세션 생성/종료/이동 확정 없음 |
| low_confidence | confidence0.2도 형식 유효. 임계값/무시 정책 없음 |
| duplicate | 동일 event_id·내용·observed_at의 정확한 재송신2건. 스키마는 둘 다 유효하며 실제 멱등 처리 미구현 |

## 검증·미검증

기존 Python3.11.18/Pydantic2.14.2/pytest9.2.0 환경 재사용, 패키지 변경 없음. 최종 실행은 **77 passed, 경고1, 실패0, skip0 (1.88초)**다. 신규 스키마/fixture·실패 경계45개와 기존 health/목업23개·billing 순수계산9개를 구분한다. 전체 DB 통합 시험의 성공을 뜻하지 않는다.

사용한 Python은 개인 작업 공간의 `.local/kan17-dependency-check-20261103/Scripts/python.exe`다. pytest 인자는 다음과 같다. 실행 프로세스에서 `sqlite3.connect`를 예외로 바꾸고 PostgreSQL 드라이버·Alembic import도 차단했다. 스키마 독립 import 시험은 SQLAlchemy·SQLite·카메라 모듈도 차단한 새 프로세스에서 수행했다.

```text
tests/test_vision_event_schema.py tests/test_health.py tests/test_mock_api.py tests/test_billing.py tests/test_api_integration.py::test_fee_boundaries -q -p no:cacheprovider --tb=short
```

경고는 기존 Starlette/httpx 사용 중단 안내1건이다. 운영 DB·billing SQLite 통합 시험·V1 재실행·V2/V3·실제 AI/카메라 시험은 이번 결과에 포함하지 않는다.

미검증/미구현: POST 수신, 인증, DB 쓰기·event 저장/해시·중복ID 다른 내용 충돌 처리, 입출차/위치/경보 확정, 실제 카메라·AI·시뮬레이터, V2/V3. 기존 billing SQLite 통합 시험은 이번에 실행하지 않는다.

## 쉬운 개념

- Pydantic 스키마: 서버에 들어오는 관측 서류의 검사 양식이다. "차량01" 문자열·유효한 시각인지 확인하고 장부 저장/주차 판단은 하지 않는다.
- 이벤트: "언제 무엇을 봤다"는 관측 한 건이다. 빈 칸을 한 번 봤다는 사실과 차량이 출차했다는 결론은 다르다.
- 멱등성: 같은 관측을 재전송해도 상태·이력이 두 번 바뀌지 않게 하는 성질이다. 동일ID 같은 내용 재처리와 동일ID 다른 내용 충돌을 Step2에서 분리해야 한다.
- JSON fixture: 카메라 대신 시험에 넣는 고정 합성 입력 파일이다. 재현 가능하지만 실제 AI 성능이나 사용자 학습 완료의 증거는 아니다.

## 다음 Step2와 Jira 등록용 초안

관측 데이터 계약 확인 → 내부 수신 인증/요청 크기/오류 형식 → event_id+내용 해시의 중복·충돌 규칙 → DB/저장소 승인 후 증거 저장 → 별도 안정화 엔진 순서다. source/manual·bbox 좌표계·카메라/칸 매핑·필수 증거 필드와 지연/시각 규칙을 PM·우지원·모델 담당과 확인한다.

Jira 초안: "vision-event Step1 로컬 스키마·합성7시퀀스·입력검증 완료, 라우터/DB/판정/멱등성은 미구현. 변경4파일·Git공유승인대기. 별도 개인 Jira 프로젝트 미확인, 팀 담당/정책/완료상태 변경 없음." 실제 등록이나 별도 티켓 생성은 수행하지 않는다.

## 종료 기록·보존 확인

- 개인 Notion 메인·개발 일지·다음 개발 순서·상세 학습·초보자 이해 노트의 변경 부분을 저장하고 재조회로 확인했다. 새 문제·이미지 증빙이 없어 문제 해결·자료실에는 기록을 추가하지 않았다. 팀 Notion·Jira는 수정하지 않았다.
- 기존 V1 보고서와 시험파일4개의 SHA-256이 시작 때와 모두 일치한다. 개인 main.py·PROGRESS.md·NEXT_STEPS.md도 변경하지 않았다. 기존 세션 준비 브랜치의 미추적2파일을 유지했다.
- 종료 시 원격 main=d1f924830bee03b80fdd2b789649f3829620f5e9, 원격 PR#8 브랜치=6fb142812e1f800808641f05523e63d8836d03e4를 읽기 전용 재확인했다. PR#8은 Draft·열림·미병합이다.
- 신규4파일만 미추적 상태이며 기존 추적 파일 차이·Stage는 없다. 공백 오류 검사를 통과했고 Windows 줄바꿈 전환 안내만 있다. main.py·billing·의존성·DB 파일 변경, Commit/Push/PR 생성은 없다. 이번 기록은 개인/팀 GitHub 원격에 게시하지 않았다.
