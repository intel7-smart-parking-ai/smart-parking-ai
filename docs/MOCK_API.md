# 팀 조회 목업 API — 계약 v0.1 초안 기준

개인 `huks13/ParkFlow`의 `0d4ff4a`에서 조회 3개를 팀 앱으로 이전했다.
초기 이전 기준은 main85839e8이다. Step5는 main25134e8 기반 feature/KAN-16-query-104-spaces에서 PM104칸 규격으로 정렬했다.
최종 계약 승인·실제 DB 연동·팀 배포 완료를 뜻하지 않는다.

## 실행과 직접 확인

저장소 루트에서 팀 Python 3.11 가상환경과 기존 requirements를 사용한다.
이미 설치한 환경을 덮어쓰지 않는다. 사용 중인 서버·포트는 변경하지 않는다.
다음 예시는 비어 있는 8002번 포트를 사용한다.

```powershell
.\.venv\Scripts\python.exe -m pip install -r backend/requirements-dev.txt
.\.venv\Scripts\python.exe -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8002
```

uv로 생성해 pip가 없는 가상환경은 기존 uv의
`uv pip install --python .venv/Scripts/python.exe -r backend/requirements-dev.txt`를 사용할 수 있다.

Swagger: http://127.0.0.1:8002/docs

1. 기존 `GET /api/v1/health`를 실행한다. 기존 status/service/timestamp 응답을 유지한다.
2. `GET /api/v1/parking-lots`를 실행해 개발원(A)·부산역(B)·김해공항(C)·신세계백화점(D)을 확인한다.
3. `GET /api/v1/parking-lots/{lot_id}/spaces`에 `A`, `B`, `C`, `D` 중 하나를 입력한다.
4. `GET /api/v1/vehicles/{car_id}/location`에 합성 차량 문자열 `10`(A), `20`(B)을 입력한다.
5. 없는 주차장 `Z` 또는 없는 차량은 공통 형식404다. 구형01~09를 새 차량으로 변환하지 않는다.

각 항목에서 `Try it out` → 값 입력 → `Execute` 순서로 실행한다.
서버를 종료할 때는 실행한 터미널에서 `Ctrl+C`를 누른다.

## 합성 데이터와 응답

- PM 도면은 A 개발원주차장12/B 부산역주차장16/C 김해공항 주차장64(D-57, 11/06 북항12에서 변경)/D 신세계백화점주차장64, 총156칸이다. 상태·차량은 모두 고정 합성 예시이며 실제 빈자리 정보나 DB 시드가 아니다.
- 주차장마다 occupied/uncertain/unavailable 각1칸, 나머지는empty다. 빈자리 A9/B13/C9/D61은 실험값이다. D는 A~D구역 각16칸, EV D-01~D-08·장애인 C-13~C-16이다. 항상 (lot_id,space_id)로 식별한다.
- 빈자리는 empty만 포함한다. 목록의 items는 lot_id/name/type/total_spaces/empty_spaces/uncertain_spaces/unavailable_spaces를 담는다.
- 칸 items는 기존 space_id/status/car_id/observed_at/confidence/version과 추가 space_type/vehicle_type/source/is_mock를 담는다. source=simulation·is_mock=true이며 칸 종류로 차량 종류를 추정하지 않는다.
- 목록과 칸 목록의 바깥 객체는 items/next_cursor/as_of다. 페이지 처리는 구현하지 않아 next_cursor는 null이다.
- 차량10은 (A,A-01), 차량20은 (B,A-01)에 있다. C/D 점유 예시는 car_id/vehicle_type=null이다. 가상 plate_number 필드/검색은 미확정이므로 가짜 실물번호나 번호판 검색 규격을 만들지 않는다. car_id=null은 빈칸을 뜻하지 않는다.
- 목업 조회 정상·리소스 404에만 `X-ParkFlow-Data-Source: mock`을 붙인다.
- 기존 health·Swagger·OpenAPI·미등록 경로에는 목업 헤더를 붙이지 않는다.
- 고정 시각·confidence·version=0은 실시간 관측·실제 상태 변경을 뜻하지 않는다.

차량 `10` 예상 응답:

```json
{
  "car_id": "10",
  "lot_id": "A",
  "space_id": "A-01",
  "session_id": null,
  "status": "occupied",
  "observed_at": "2026-11-02T10:00:00+09:00",
  "confidence": 0.95,
  "vehicle_type": "general",
  "source": "simulation",
  "is_mock": true
}
```

없는 차량의 404:

```json
{"error":{"code":"VEHICLE_NOT_FOUND","message":"목업 차량이 없습니다.","details":[]}}
```

없는 주차장은 `PARKING_LOT_NOT_FOUND`를 사용한다. 코드명·문구는 임시값이다.

## 테스트와 통합 경계

```powershell
.\.venv\Scripts\python.exe -B -m pytest tests/ -q -p no:cacheprovider
```

기존 tests/test_health.py는 수정하지 않는다. tests/test_mock_api.py는 DB 드라이버를
요구하지 않고 실제 소켓 접속을 차단한다. 별도 프로세스에서 DB 모듈 import도 차단한 채
health와 조회를 실행한다. 이는 실제 DB 동작 검증이 아니다.
Step5에서는 main.py·health·billing·프론트·챗봇을 변경하지 않았다. 기존 경로/기존 응답 필드를 유지하고 새 칸 종류/출처 필드만 추가했다. 추가 필드를 거절하는 엄격한 소비자는 담당자와 조율해야 한다.

## Step5 검증 및 D5 운영 연결 제안

이번 실제 DB 비접속 선택 회귀는145 passed, 실패/skip0, 기존 Starlette/httpx 경고1, 2.39초다. 조회/health29개+Vision 스키마/수신/PM116개다. tests/test_mock_api.py·test_health.py·test_vision_event_schema.py·test_vision_event_receiver.py·test_vision_event_pm_rules.py를 -q -p no:cacheprovider --tb=short로 실행했다. 실행 프로세스의 sqlite3.connect와 PostgreSQL 드라이버/Alembic도 차단했다. D5/V3 전체 시험은 이번 범위가 아니다.

PR#23/#24는 확인 당시 미병합이며 이 변경의4파일과 직접 겹치지 않는다. D5 실험 GET 칸 목록과 운영 목업의 URL이 같아 둘을 같은 앱에 등록하면 경로 선점·중복 OpenAPI가 생길 수 있다. 향후 한 조회 라우터·공통 응답 모델에 목업/상태 엔진 제공자 중 하나만 주입하고, 주차장 집계·칸 목록·차량 위치가 같은 스냅샷/as_of를 사용하도록 연결하는 방안을 제안한다. 이 작업에서는 제공자 전환·수신 등록·실제 세션 연결을 구현하지 않았다. D5는 독립 앱으로 유지하며 운영 라우터를 중복 등록하지 않는다.

## 팀 합의 대기

주차장 status 의미, 응답 바깥 객체·페이지·필터, 필드 필수/선택 여부,
session_id=null, confidence 정의, 관측 시각·version 의미, 공통 오류 코드,
인증·접근 범위는 최종 합의가 필요하다. 불명확한 필드를 추가하지 않았다.
개인 /health와 팀 /api/v1/health는 별도이며 개인 OpenAPI 제목 기대값 실패는 이 통합에서 수정하지 않는다.

Jira 작업은 [KAN-16](https://rgq548.atlassian.net/browse/KAN-16)이다.
사용자가 해당 feature 브랜치의 Commit·Push를 승인했다. main에는 직접 Push하지 않는다.
PR 생성은 별도 승인 대상이며 병합 전 PM/타인 리뷰 절차를 따른다.
