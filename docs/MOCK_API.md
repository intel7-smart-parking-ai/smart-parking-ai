# 팀 조회 목업 API — 계약 v0.1 초안 기준

개인 `huks13/ParkFlow`의 `0d4ff4a`에서 조회 3개를 팀 앱으로 이전했다.
팀 기준 main은 `85839e8`이며 작업 브랜치는 `feature/KAN-16-mock-query-api`다.
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
2. `GET /api/v1/parking-lots`를 실행해 A/B/C를 확인한다.
3. `GET /api/v1/parking-lots/{lot_id}/spaces`에 `A`, `B`, `C` 중 하나를 입력한다.
4. `GET /api/v1/vehicles/{car_id}/location`에 문자열 `01`, `02`, `03`을 입력한다.
5. `D` 또는 없는 차량을 입력하면 리소스 404를 반환한다.

각 항목에서 `Try it out` → 값 입력 → `Execute` 순서로 실행한다.
서버를 종료할 때는 실행한 터미널에서 `Ctrl+C`를 누른다.

## 합성 데이터와 응답

- A/B는 실물 주차장의 합성 예시, C는 가상 주차장이다. D는 제외한다.
- 각 4칸은 실제 면수가 아니다. occupied/empty/uncertain/unavailable이 각각 1칸이다.
- 빈자리는 empty만 포함한다. 목록의 items는 lot_id/name/type/total_spaces/empty_spaces/uncertain_spaces/unavailable_spaces를 담는다.
- 칸 items는 space_id/status/car_id/observed_at/confidence/version을 담는다.
- 목록과 칸 목록의 바깥 객체는 items/next_cursor/as_of다. 페이지 처리는 구현하지 않아 next_cursor는 null이다.
- `01`은 A-01, `02`는 B-01, `03`은 C-01에 있다. 실제 번호판·개인정보는 사용하지 않는다.
- 목업 조회 정상·리소스 404에만 `X-ParkFlow-Data-Source: mock`을 붙인다.
- 기존 health·Swagger·OpenAPI·미등록 경로에는 목업 헤더를 붙이지 않는다.
- 고정 시각·confidence·version=0은 실시간 관측·실제 상태 변경을 뜻하지 않는다.

차량 `01` 예상 응답:

```json
{
  "car_id": "01",
  "lot_id": "A",
  "space_id": "A-01",
  "session_id": null,
  "status": "occupied",
  "observed_at": "2026-11-02T10:00:00+09:00",
  "confidence": 0.95
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
main.py에는 라우터 import와 등록만 추가하며 제목·health 응답을 보존한다.

## 팀 합의 대기

주차장 status 의미, 응답 바깥 객체·페이지·필터, 필드 필수/선택 여부,
session_id=null, confidence 정의, 관측 시각·version 의미, 공통 오류 코드,
인증·접근 범위는 최종 합의가 필요하다. 불명확한 필드를 추가하지 않았다.
개인 /health와 팀 /api/v1/health는 별도이며 개인 OpenAPI 제목 기대값 실패는 이 통합에서 수정하지 않는다.

Jira 작업은 [KAN-16](https://rgq548.atlassian.net/browse/KAN-16)이다.
사용자가 해당 feature 브랜치의 Commit·Push를 승인했다. main에는 직접 Push하지 않는다.
PR 생성은 별도 승인 대상이며 병합 전 PM/타인 리뷰 절차를 따른다.
