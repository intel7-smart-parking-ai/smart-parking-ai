# 팀 API 요청·확인 목록 (v2.9, 2026-11-03) — 전송은 사람이 한다

대부분 이미 팀원 할 일(11/03 결정·규격 통일표)에 들어 있다. 신규 요청과 확인 항목은 아래 표에 표시했다.

| # | 항목 | 담당 | 구분 | 챗봇 쪽 상태 |
|---|---|---|---|---|
| 1 | `/alerts` 응답에 `level`(1·2), `space_id` null 허용, `occurred_at`, `source` 포함 | 박해준님 | **신규 요청** | 응답 형식이 오면 바로 사용. `level`이 없으면 Lv1로 두고 추측하지 않음 |
| 2 | 목록 응답 `as_of`의 의미(응답 생성 시각/마지막 관측 시각) | 박해준님 | **신규 확인** | 오래된 '경보 없음' 거절 기준에 사용 |
| 3 | 조회 목업: D 추가, 칸 수 12·16·12·64, 도면 번호, 차량 `01~03` → 두 자리 10 이상 | 박해준님 | 이미 할 일에 있음 | 반영 전까지 규칙 밖 차량은 번호 미인식으로 처리 |
| 4 | `/parking-lots` 주차장 이름, 칸 `space_type` | 박해준님 | 이미 할 일에 있음 | 들어오면 서버 값 우선 |
| 5 | 가상 차량 검색 경로·필드명 | 박해준님 | 11/04 확정 예정 | 희망안: `GET /api/v1/vehicles/search?plate_suffix=1724` → location 형식 + `plate_number`, 여러 대면 `items`. 확정되면 `api_paths.json`의 `virtual_search`만 채움 |
| 6 | `quote_id`·`quote_expires_at`, 견적 오류 코드 | 윤명수님 | 반영됨(PR #15) | 반영 완료. main.py 등록 후 실서버 확인 |
| 7 | billing 라우터 `main.py` 등록 | 박해준님·윤명수님 | DB 통합 후 | 등록 전 실서버 요금·결제는 404 → "기능이 아직 연결되지 않았어요" |
| 8 | `/sessions?car_id=&status=active`, `location.session_id` 실제 연결 | 박해준님 | 기존 요청 유지 | null이면 "아직 요금 정보를 확인할 수 없어요" |
| 9 | 정보 공유: billing `schemas.py`의 FeeResponse 설명(`calculated_at`·`applied_policy_id`·`session_status`)과 실제 응답(`target_at`·`rate_policy`, session_status 없음)이 다름 | 윤명수님 | 정보 공유 | 챗봇은 실제 응답 기준으로 파싱 |
| 10 | `/spaces` 칸 항목에 `entry_at`(시간대 포함 ISO 8601, 차량이 없으면 null) 추가 | 박해준님 | **신규 요청** | 번호 미인식 후보 안내에 사용. 받을 준비 완료, 없으면 기존 동작 |
