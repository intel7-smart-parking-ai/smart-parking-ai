# 챗봇 API 연결표

재구성 문서. 기준 코드 `86c3069`, 기본 경로 `/api/v1`.

| 사용자 질문/기능 | 연결 | 제약 |
|---|---|---|
| 주차장 목록·잔여 칸 | GET /parking-lots | 목록 as_of와 서버 집계 사용 |
| 특정 주차장·EV 빈자리 | GET /parking-lots/{lot_id}/spaces | empty만 집계, 충전기 작동 여부 아님 |
| 실물 차량 위치 | GET /vehicles/{car_id}/location | 10~99 문자열 스티커 |
| 가상 차량 위치 | GET /vehicle-search?vehicle_number=1724 | ASCII 4자리 문자열, C/D 런타임 |
| 차량 요금 | 위치 응답 session_id → GET /sessions/{session_id}/fee | 견적 DB 저장, 재시도 금지 |
| 요금 정책 | GET /rate-policies | 서버 정책, 챗봇 재계산 금지 |
| 추천 전 경보 확인 | GET /alerts?lot_id=A&limit=500 | 목록 완전성 확인, 500건이면 보수적으로 불완전 |
| 시설·엘리베이터·영화관 인접 칸 | 로컬 parking_knowledge.v0.1.json | 가상 시나리오임을 명시 |
| 목적지 추천 | 설정 후보 + spaces + alerts | 예약 아님, 실제 거리/도보시간 없음 |

## 차량 조회
두 식별자의 의미와 조회 API가 다르므로 내부 도구에서 분기한다.
404는 차량 미관측과 여러 칸 일치 모두 가능하다. '단일 위치를 특정하지 못했습니다'로 처리한다.
FastAPI 일반 Not Found는 API 미지원과 구분한다. 422는 입력 형식 오류다.
서버가 요청 차량과 다른 식별자를 반환하면 응답을 사용하지 않는다.

## 요금 조회
동일 요청 ID/세션 조합을 메모리에 캐시하고 실패도 재시도하지 않는다.
같은 요청 ID를 다른 세션에 사용하면 충돌 처리한다. 프로세스 재시작 후 보장은 없다.
완료 결제의 amount_krw는 payment.amount_krw와 일치해야 하며 fee_breakdown은
재계산 시점의 내역일 수 있어 동일합으로 강제하지 않는다. payment_blocked를 전달한다.
결제·취소 POST와 관리자 API는 새 챗봇의 도구에 포함하지 않는다.

## 실행 모드·UI
통합 main의 mock은 고정 응답이며 demo/real은 runtime 앱이다.
통합 runtime은 public_reads=True. 독립 occupancy_query_demo의 기본 인증과 혼동하지 않는다.
DB는 SQLite다. PGHOST 등 환경 변수 존재가 PostgreSQL 사용을 뜻하지 않는다.
현재 v3는 Python 서비스/콘솔까지만 연결한다. 기존 /chat 화면의 card/chips 및
runtime_id/state_version/alert_version 계약을 새 응답에 자동 호환한다고 가정하지 않는다.
새 HTTP 경로·UI 어댑터는 다음 단계이며 기존 공개 API를 수정하지 않았다.
