# D5 점유 프로토타입 · 2026-11-03

- 별도 메모리 실험이며 운영 main·DB·billing·기존 수신기를 수정하지 않았다. 세션 입출차·차량 위치 확정 엔진이 아니다.
- 신규: api/occupancy_prototype.py, api/occupancy_experiment_app.py, tests/test_occupancy_prototype.py와 본 문서.
- 기존 create_experiment_app 수신기와 PM 50칸 fixture를 명시적으로 주입해 재사용한다. 전역 app·자동 도면 로딩·서버 기동은 없다. 인증 콜백의 정확한 True가 읽기/쓰기 모두 필요하며 실제 인증은 미확정이다.
- 경로: POST /api/v1/internal/vision-events → GET /api/v1/parking-lots/{lot_id}/spaces. 기존 운영 조회/목업과는 별도 앱이다.
- 조회에는 data_source=memory-prototype·policy_status=experiment-only와 칸별 source/is_mock를 제공한다. simulation/manual은 is_mock=true, vision은false이며 미관측은null이다. 모든 이번 데이터는 합성이다.
- 필수 실험 설정: occupy_seconds/empty_seconds/max_gap_seconds/stale_seconds/min_confidence/initial_status. 기본 운영 정책은 제공하지 않는다.
- 시험 가정: 점유2초·해제3초·관측간격 최대2초·오래됨10초·최소신뢰도0.8·초기empty. 초기empty는 합성 초기값일 뿐 실제50칸이 비었다는 증거가 아니다.
- 연속 차량 관측 t=0/1/2에서 uncertain→occupied, 빈칸 관측 t=3/4/5/6에서 uncertain→empty. 관측 한 건으로 확정하지 않는다. 모의 입출차는 위 두 관측 종류의 점유 변화만 뜻한다.
- 낮은 신뢰도·상충하는 빈칸/차량 근거는uncertain. OCR null만으로 점유를 부정하지 않는다. 차량/출처가 바뀌거나 관측 간격을 넘으면 후보를 다시 시작한다. LED/번호 관측만으로 점유를 갱신하지 않는다.
- 같은 lot/space의 늦은·동시각 신규 이벤트는 접수만 하고 상태를 덮어쓰지 않는다. 조회 시 오래된 관측은uncertain으로 표시하며 empty/출차로 바꾸지 않는다. 미래 시각·동시각 다중 증거의 본격적 조정은 별도 정책이다.
- (lot_id,space_id)별 상태와 후보를 분리하며 같은 car_id의 여러 칸 점유·이동·중복 차량 경보는 이번 범위에 포함하지 않는다.
- 접수와 상태 갱신/조회는 단일 상태 잠금으로 보호한다. 기존 ledger의 동일ID/내용 중복200·다른내용409·한도503에는 새 상태 효과가 없다. 도면에 없는 칸은 접수 전422, 없는 주차장 조회는404다. 오류코드/응답은 실험 계약이다.
- 최종 실행: 기존 Python3.11.18 환경에서 신규18+기존 수신/스키마/PM/비접속 API 회귀148=166 passed, 실패/skip0, 기존 Starlette/httpx 경고1, 2.51초. sqlite3.connect와 PostgreSQL 드라이버/Alembic을 차단한 프로세스로 실행했다.
- 실행 대상: tests/test_occupancy_prototype.py, test_vision_event_pm_rules.py, test_vision_event_receiver.py, test_vision_event_schema.py, test_health.py, test_mock_api.py, test_billing.py, test_api_integration.py::test_fee_boundaries. pytest 옵션 -q -p no:cacheprovider --tb=short.
- 동일 이벤트20개 동시 수신:202 1개/200 19개·상태version1. 충돌 후 상태 동일, A/C의 동일 A-01 독립, 50칸/전용칸·출처·불확실·가림 간격·지연·인증 거절을 시험했다.
- 실제 네트워크 서버·AI/카메라·영속 DB·세션/결제·V1/V3 재실행은 미검증이다. 재시작/다중 워커의 멱등성과 상태 공유를 보장하지 않는다.
- 다음: PM 안정화/출처 혼합/시각·카메라/ROI 기준·인증·영속 저장 합의 → 승인된 연결/실서버 시연 검증. 공식 정책으로 위 시험 수치를 채택하지 않는다.
- 외부 기록·Git Stage/Commit/Push/PR 없음. 기존21 미커밋파일은 작업 전후 해시 비교로 보존을 확인한다.
