# API 계약 확인

기준 Git `86c30692379f474bf54947efa0df22e9b96d7aa2`.
CLAUDE.md, API_CONTRACT.md, runtime.py, occupancy_query_demo.py, billing 및 alerts
구현과 실제 FastAPI TestClient 결과를 기준으로 확인했다.

| 항목 | 처리 |
|---|---|
| DB | SQLite + SQLAlchemy. PG 환경 변수로 자동 전환하지 않음 |
| 칸 식별 | lot_id A/B/C/D + space_id 조합, 156칸 |
| 실물 차량 | 10~99 문자열 스티커, `/vehicles/{car_id}/location` |
| 가상 차량 | ASCII 네 자리 문자열, `/vehicle-search`, 앞 0 보존 |
| 차량 404 | 미관측 또는 복수 일치, 등록 없음으로 단정 금지 |
| fee GET | 견적 DB 저장 있음. 동일 요청의 성공/실패 캐시, 자동 재시도 없음 |
| 결제 완료 | top-level amount와 receipt 금액/세션/상태 일치 검증 |
| 시각 | 시간대 포함, 추천 관측·스냅샷·경보 10초 내 확인 |
| 경보 | acknowledged도 미해제, 500건이나 cursor/합계 불일치 시 전체 확인 불가 |
| 시설 | 시연용 가상 설정, 실제 장소 검증 자료와 구분 |
| UI | 새 응답과 기존 card/chips 계약은 별도, 아직 연결 전 |

공통 서버 API·DB·기존 프런트엔드 파일은 수정하지 않았다.
단일 프로세스 메모리 캐시는 재시작/다중 워커 중복 실행을 막지 못한다.
통합 runtime의 public_reads=True와 독립 occupancy_query_demo 기본 인증을 혼동하지 않는다.

후속 검토에서 발견·수정한 오류와 HTTP/UI 규약 차이는 [상세 검토](REVIEW_2026-11-07.md)에 기록했다.
