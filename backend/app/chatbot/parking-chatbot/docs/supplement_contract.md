# 보완 응답·시연 계약

기존 API 경로·응답은 유지하며 다음 선택 필드를 추가한다. 실제 팀 API v1 변환은 별도 확정 대상이다.

- 점유 `lots.<id>.uncertain`, `zones.<key>.uncertain`: 0 이상 정수, 생략 시 0. `empty + uncertain <= total`. 구역 합계도 전체 이하. unavailable 칸은 total에 남고 empty에는 포함하지 않는다. 추천은 empty만 사용한다.
- 차량 목록 `vehicle_no`: 문자열 또는 null. null이면 entered_at(시간대 포함 ISO), lot_id, spot_no가 필요하다. 동일 위치 중복과 식별 차량 번호 중복은 거절한다. 번호 없는 만차 연출용 점유는 목록에 포함하지 않는다.
- 응답 `safety`: Lv2일 때 title/guidance/vehicle_help, 그 외 null. 알림에도 포함한다. 기존 card의 종류와 내용은 보존한다.
- 카드 `unrecognized_candidates`: cars 배열과 notice. 위치 참고용이며 차량번호와 연결하지 않는다. 후보 버튼은 세션별 토큰, 120초, 선택 시 목록 재검증을 적용한다.
- 보고서 `briefing`: 현장 정보 구조와 복사용 text. 차량 목록 실패 시 ev_count=null, 시설 null은 미확인. 보고서 확정 뒤에는 변경하지 않는다.
- 채팅 입력: text 최대 200자, chip_id/확인 토큰/runtime ID 최대 128자, event 최대 32자. 큰 요청은 거절하며 원문을 기록에 보관하지 않는다.

새 모의 시연 API는 두 서버 모두 기존 관리자 접근 키 및 DEMO_ENABLED 검사를 적용한다.

- POST `/api/v1/chat/demo/uncertain`: `{ "lot_id":"D", "spot_no":1, "uncertain":true }`. true는 확인 중, false는 해제. 이미 점유된 칸에는 설정하지 않는다.
- POST `/api/v1/chat/demo/unrecognized`: `{ "lot_id":"D" }`. 비어 있는 칸에 번호 null과 입차 시각을 가진 차량을 추가한다.

삭제된 문의 상태 변경 API는 제공하지 않는다. FastAPI의 정적 파일 mount는 해당 POST에 405, 개발 서버는 404를 반환할 수 있으며 둘 다 상태 변경을 수행하지 않는다.
