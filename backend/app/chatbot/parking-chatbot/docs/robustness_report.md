# 다각도 안정성·최적화 검증 — v2.6

2026-11-03. 기준: API 정렬 v2.5(1946b72). 수정은 parking-chatbot 내부에 한정했다. 실행 의존성은 늘리지 않았다.

## 1. 요약

실행으로 재현한 문제를 8개 원인 그룹으로 묶었다. S1 1개, S2 1개, S3 4개, S4 1개, S5 1개다. **7개 수정, 1개 보관 정책 결정 필요**다. 재현하지 않은 위험은 별도 의심 목록으로 분류했다.

기존 테스트 파일 변경은 0개다. 기존 질문 242/242 및 텍스트·카드·칩 등 응답 스냅샷의 동일성을 확인했다(날짜·토큰·버전 같은 변동 필드 제외). 잠금·캐시 세대·알림 커서·결제 직렬화 정책을 새로 설계하지 않았다.

## 2. 문제 목록

| ID | 관점 | 등급 | 재현 | 기대 | 수정 전 실제 | 원인/수정 위치 | 수정 | 추가 검사 | 상태 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| R01 | C | S2 | 상태에 []·{} / alerts.items에 비객체 주입 | 확인 불가 | TypeError·AttributeError로 요청 예외 | chatbot/contracts.py:124 | 타입 검사 | test_unhashable_status_and_malformed_alert_items_never_escape | 수정 |
| R02 | C | S3 | 위치 parked, 관측 시각 null, 신뢰도 문자열 | 확인 불가 | 위치 확정 표현 | chatbot/adapters.py:426 | 상태·관측 시각·신뢰도 검사 | test_location_unknown_state_and_missing_time_not_certainty | 수정 |
| R03 | C | S3 | 중복 lot ID·누락/중복/잘못된 칸 목록 | 구역 미확인 | ID 덮어쓰기·구역 0 집계 | chatbot/adapters.py:411 | 중복 거절·완전한 집계만 구역 수 제공 | test_duplicate_lots_and_incomplete_spaces_not_invented | 수정 |
| R04 | C | S3 | is_daily_capped 문자열/정수 | 확인 불가 | 하루 최대 요금 적용 문구 | chatbot/contracts.py:49 | boolean 확인 | test_numeric_and_flag_types_rejected | 수정 |
| R05 | D/G | S1 | 오지 말아 주세요·나중에 호출·납부 안 할래요 등 | 실행/확인 대기 없음 | 일부 관리자 접수/결제 확인 대기 | chatbot/parser.py:129 | 부정·미루기 규칙 보완 | test_fifty_negative_utterances_never_create_action | 수정 |
| R06 | D | S3 | 07 선택 뒤 001번·99999번·-1번 위치 | 잘못된 번호 안내 | 07 위치 대체/01 확인 질문 | chatbot/parser.py:47 | 잘못된 명시 번호 거절 | test_numbers_are_not_negative_or_long_suffixes | 수정 |
| R07 | I | S5 | 360px에 공백 없는 영문 2,000자 | 영역 안 표시 | 대화 로그 가로 넘침 | static/index.html:40 | 줄바꿈·최소 너비 보완 | browser_robustness.cjs | 수정 |
| R08 | A/F | S4 | 합치기 기간 이후 접수 800회 (가상 시간) | 보관 정책 필요 | 기록 200→400→800 누적 | chatbot/engine.py:610 | 임의 삭제 미적용·DB 이관/보관 기간 결정 필요 | 보관 진단 | 남음 |

수정 전: 새 서버 검사 9개에서 하위 사례 실패 9건·예외 7건, 새 입력 검사 5개에서 하위 사례 실패 20건·장기 1개 제외를 관찰했다. 긴 답변 브라우저 검사는 가로 넘침 단언에 실패했다. 요청 예외를 서버 전체 프로세스 종료로 표현하지 않는다.

R08은 접수 이력을 임의 삭제하면 기능이 달라져 미수정이다. 아래 메모리는 진단 전체의 Python 추적 현재값이며 접수 목록만의 크기는 아니다.

| 접수 수 | 보관 기록 | 추적 bytes |
| ---: | ---: | ---: |
| 200 | 200 | 276399 |
| 400 | 400 | 524699 |
| 800 | 800 | 1023459 |

## 3. 의심 목록 — 재현 결함으로 세지 않음

- 서버가 큰 잘못된 칸 ID·상태값을 주면 진단 로그가 길어질 수 있다. 전체 응답 수신 한도는 1MiB지만 로그 때문에 정지하는 상황은 재현하지 않았다. 로그 필드 길이 상한 검토 대상이다.
- incidents.observe는 보고서 잠금 안에서 차량 목록을 읽는다. 현재 Mock은 로컬 조회, API는 즉시 확인 불가다. 향후 실제 세션 HTTP 연결 시 잠금 범위를 재검토해야 한다.
- 일반 읽기의 메모리 평탄화가 모든 접수·경보 이력의 보관 상한을 뜻하지 않는다. 접수는 R08로 재현했고, 상황 보고서 보관도 정책 검토가 필요하다.

## 4. 정책·팀 결정

| 항목 | 확인된 동작/남은 결정 |
| --- | --- |
| 번호 두 개 | 대상 하나를 다시 묻는다. |
| 번호 변형 | 음수·세 자리 이상 명시 번호는 거절. 1·01·전각 １·일번은 01로 처리. |
| Lv2 결제 | 확인 대기 취소, 새 결제 중지, 사람 먼저 안전 카드 유지. |
| 관리자 반복 | 같은 세션·차량·사건은 CALL_MERGE_SEC 안에서 합침. 이후 보관 기간은 팀 결정. |
| 결제 키 | 취소→새 확인은 새 키. 같은 request_id는 응답 재사용. 불확실한 결과는 자동 새 결제로 재시도하지 않음. |
| 미래 호출 | 예약 기능이 없어 지금 즉시 접수하지 않음. |
| DATA_SOURCE 오류 | 시작 때 명확한 ValueError. 목업으로 조용히 전환하지 않음. |
| LLM 목업 예외 | v2.5의 기존 기대값과 충돌했던 번호 추정 허용은 목업에 남고 API에서는 차단. 기대값 변경은 이번 범위에서 하지 않음. |
| 보관 | 접수·보고서 삭제 시점과 DB 이관·조회 범위를 팀이 결정해야 함. |

## 5. A 정적 점검

Ruff 설치는 다운로드 프록시 오류로 실패해 대안인 Pyflakes를 테스트 환경에 설치했다. 런타임·tools·이번 새 Python 검사 코드 경고는 0건이다. 기존 검사 파일의 미사용 바인딩 12건은 실행 결함/정의되지 않은 이름이 아니며 기존 검사 원형을 유지했다. 런타임/도구의 미사용 import 2개와 불필요 대입 1개는 제거했고 함수 호출은 유지했다.

| 패턴 | 판정 |
| --- | --- |
| bare except / except Exception: pass | 런타임 0. 구체 예외 pass 2곳은 아래 이유 참조. |
| timeout 없는 urlopen | 런타임 0. transport는 본문 수신까지 전체 기한·1MiB 상한 적용. |
| encoding 없는 텍스트 I/O | 런타임 0. HTTP 본문은 bytes 수신 후 UTF-8 디코딩. |
| timeout 없는 subprocess | 런타임 0. 테스트 Popen은 외부 기한과 finally 종료/kill/wait를 둠. |
| while True | 런타임 0. 테스트 대기는 기한·해제 이벤트 사용. |
| async 함수 속 동기 I/O | async endpoint 0. def 엔드포인트의 실제 FastAPI 50동시 HTTP 통과. |
| 전역 저장소 | 세션 200, 로그 2,000, 대화 100, 요청 100/600초, 알림 100/TTL 등 기존 상한. 접수/보고서 이력은 R08·의심 목록. |
| int 차량번호 저장 | 임시 비교·정규화 뒤 NN 문자열 사용. 선행 0 URL·본문 검사 통과. |
| 잠금/I/O/순서 | 아래 전 위치 검토. 의도된 세션 순서·결제 직렬화는 예산 제한. 이번 경합에서 교착 미재현. |

AST의 모든 런타임 패턴 일치 위치를 아래에 판정했다. “문제 없음”은 검토·실행 범위의 판단이며 모든 스케줄의 무교착 증명은 아니다.

| 위치 | 패턴 | 판정·이유 |
| --- | --- | --- |
| chatbot/adapters.py:105 | lock_scope / self.lock | 문제 없음: 메모리 읽기·복사·상태 보호. 이 범위의 무기한 I/O는 확인되지 않음 |
| chatbot/adapters.py:118 | lock_scope / self.lock | 문제 없음: 메모리 읽기·복사·상태 보호. 이 범위의 무기한 I/O는 확인되지 않음 |
| chatbot/adapters.py:128 | lock_scope / self.lock | 문제 없음: 메모리 읽기·복사·상태 보호. 이 범위의 무기한 I/O는 확인되지 않음 |
| chatbot/adapters.py:146 | lock_scope / self.lock | 문제 없음: 메모리 읽기·복사·상태 보호. 이 범위의 무기한 I/O는 확인되지 않음 |
| chatbot/adapters.py:168 | lock_scope / self.lock | 문제 없음: 메모리 읽기·복사·상태 보호. 이 범위의 무기한 I/O는 확인되지 않음 |
| chatbot/adapters.py:173 | lock_scope / self.lock | 문제 없음: 메모리 읽기·복사·상태 보호. 이 범위의 무기한 I/O는 확인되지 않음 |
| chatbot/adapters.py:190 | lock_scope / self.lock | 문제 없음: 메모리 읽기·복사·상태 보호. 이 범위의 무기한 I/O는 확인되지 않음 |
| chatbot/adapters.py:206 | lock_scope / self.lock | 문제 없음: 메모리 읽기·복사·상태 보호. 이 범위의 무기한 I/O는 확인되지 않음 |
| chatbot/adapters.py:213 | lock_scope / self.lock | 문제 없음: 메모리 읽기·복사·상태 보호. 이 범위의 무기한 I/O는 확인되지 않음 |
| chatbot/adapters.py:228 | lock_scope / self.lock | 문제 없음: 메모리 읽기·복사·상태 보호. 이 범위의 무기한 I/O는 확인되지 않음 |
| chatbot/adapters.py:237 | lock_scope / self.lock | 문제 없음: 메모리 읽기·복사·상태 보호. 이 범위의 무기한 I/O는 확인되지 않음 |
| chatbot/adapters.py:248 | lock_scope / self.lock | 문제 없음: 메모리 읽기·복사·상태 보호. 이 범위의 무기한 I/O는 확인되지 않음 |
| chatbot/adapters.py:258 | lock_scope / self.lock | 문제 없음: 메모리 읽기·복사·상태 보호. 이 범위의 무기한 I/O는 확인되지 않음 |
| chatbot/adapters.py:265 | lock_scope / self.lock | 문제 없음: 메모리 읽기·복사·상태 보호. 이 범위의 무기한 I/O는 확인되지 않음 |
| chatbot/adapters.py:281 | lock_scope / self.lock | 문제 없음: 메모리 읽기·복사·상태 보호. 이 범위의 무기한 I/O는 확인되지 않음 |
| chatbot/adapters.py:308 | lock_scope / self.lock | 문제 없음: 메모리 읽기·복사·상태 보호. 이 범위의 무기한 I/O는 확인되지 않음 |
| chatbot/adapters.py:498 | lock_scope / self.lock | 문제 없음: 메모리 읽기·복사·상태 보호. 이 범위의 무기한 I/O는 확인되지 않음 |
| chatbot/adapters.py:542 | lock_scope / self.lock | 문제 없음: 메모리 읽기·복사·상태 보호. 이 범위의 무기한 I/O는 확인되지 않음 |
| chatbot/adapters.py:641 | lock_scope / self.lock, self.inner.lock | 문제 없음: 메모리 읽기·복사·상태 보호. 이 범위의 무기한 I/O는 확인되지 않음 |
| chatbot/adapters.py:654 | lock_scope / self.lock | 문제 없음: 메모리 읽기·복사·상태 보호. 이 범위의 무기한 I/O는 확인되지 않음 |
| chatbot/adapters.py:662 | lock_scope / self.lock | 문제 없음: 메모리 읽기·복사·상태 보호. 이 범위의 무기한 I/O는 확인되지 않음 |
| chatbot/adapters.py:557 | lock_scope / self.lock | 문제 없음: 메모리 읽기·복사·상태 보호. 이 범위의 무기한 I/O는 확인되지 않음 |
| chatbot/adapters.py:569 | lock_scope / self.lock | 문제 없음: 메모리 읽기·복사·상태 보호. 이 범위의 무기한 I/O는 확인되지 않음 |
| chatbot/adapters.py:586 | lock_scope / self.lock | 문제 없음: 메모리 읽기·복사·상태 보호. 이 범위의 무기한 I/O는 확인되지 않음 |
| chatbot/adapters.py:636 | lock_scope / self.lock | 문제 없음: 메모리 읽기·복사·상태 보호. 이 범위의 무기한 I/O는 확인되지 않음 |
| chatbot/adapters.py:603 | lock_scope / self.lock | 문제 없음: 메모리 읽기·복사·상태 보호. 이 범위의 무기한 I/O는 확인되지 않음 |
| chatbot/catalog.py:63 | lock_scope / self.lock | 문제 없음: 메모리 읽기·복사·상태 보호. 이 범위의 무기한 I/O는 확인되지 않음 |
| chatbot/catalog.py:68 | lock_scope / self.lock | 문제 없음: 메모리 읽기·복사·상태 보호. 이 범위의 무기한 I/O는 확인되지 않음 |
| chatbot/catalog.py:73 | lock_scope / self.lock | 문제 없음: 메모리 읽기·복사·상태 보호. 이 범위의 무기한 I/O는 확인되지 않음 |
| chatbot/catalog.py:76 | lock_scope / self.lock | 문제 없음: 메모리 읽기·복사·상태 보호. 이 범위의 무기한 I/O는 확인되지 않음 |
| chatbot/catalog.py:86 | lock_scope / self.lock | 문제 없음: 메모리 읽기·복사·상태 보호. 이 범위의 무기한 I/O는 확인되지 않음 |
| chatbot/catalog.py:89 | lock_scope / self.lock | 문제 없음: 메모리 읽기·복사·상태 보호. 이 범위의 무기한 I/O는 확인되지 않음 |
| chatbot/catalog.py:116 | lock_scope / self.lock | 문제 없음: 메모리 읽기·복사·상태 보호. 이 범위의 무기한 I/O는 확인되지 않음 |
| chatbot/catalog.py:129 | lock_scope / self.lock | 문제 없음: 메모리 읽기·복사·상태 보호. 이 범위의 무기한 I/O는 확인되지 않음 |
| chatbot/catalog.py:138 | lock_scope / self.lock | 문제 없음: 메모리 읽기·복사·상태 보호. 이 범위의 무기한 I/O는 확인되지 않음 |
| chatbot/engine.py:68 | lock_scope / _lock | 문제 없음: 메모리 읽기·복사·상태 보호. 이 범위의 무기한 I/O는 확인되지 않음 |
| chatbot/engine.py:89 | lock_scope / _lock | 문제 없음: 메모리 읽기·복사·상태 보호. 이 범위의 무기한 I/O는 확인되지 않음 |
| chatbot/engine.py:107 | lock_scope / _alert_condition | 문제 없음: 메모리 읽기·복사·상태 보호. 이 범위의 무기한 I/O는 확인되지 않음 |
| chatbot/engine.py:434 | lock_scope / _payment_lock | 유지: 결제 직렬화·요청 예산·실행 직전 경보 확인 |
| chatbot/engine.py:592 | lock_scope / _lock | 문제 없음: 메모리 읽기·복사·상태 보호. 이 범위의 무기한 I/O는 확인되지 않음 |
| chatbot/engine.py:602 | lock_scope / _lock | 문제 없음: 메모리 읽기·복사·상태 보호. 이 범위의 무기한 I/O는 확인되지 않음 |
| chatbot/engine.py:618 | lock_scope / _lock | 문제 없음: 메모리 읽기·복사·상태 보호. 이 범위의 무기한 I/O는 확인되지 않음 |
| chatbot/engine.py:952 | lock_scope / _alert_lock | 문제 없음: 메모리 읽기·복사·상태 보호. 이 범위의 무기한 I/O는 확인되지 않음 |
| chatbot/engine.py:1027 | lock_scope / _lock | 문제 없음: 메모리 읽기·복사·상태 보호. 이 범위의 무기한 I/O는 확인되지 않음 |
| chatbot/engine.py:1043 | lock_scope / _lock | 문제 없음: 메모리 읽기·복사·상태 보호. 이 범위의 무기한 I/O는 확인되지 않음 |
| chatbot/engine.py:1050 | lock_scope / _lock | 문제 없음: 메모리 읽기·복사·상태 보호. 이 범위의 무기한 I/O는 확인되지 않음 |
| chatbot/engine.py:95 | lock_scope / _lock | 문제 없음: 메모리 읽기·복사·상태 보호. 이 범위의 무기한 I/O는 확인되지 않음 |
| chatbot/engine.py:142 | lock_scope / _alert_condition | 문제 없음: 메모리 읽기·복사·상태 보호. 이 범위의 무기한 I/O는 확인되지 않음 |
| chatbot/engine.py:170 | lock_scope / _reserved_session(session_id), _locked_session(s) | 유지: 동일 세션 순서 보장, 잠금 대기도 요청 예산 포함 |
| chatbot/engine.py:1058 | lock_scope / _alert_lock | 문제 없음: 메모리 읽기·복사·상태 보호. 이 범위의 무기한 I/O는 확인되지 않음 |
| chatbot/engine.py:138 | lock_scope / _alert_lock | 문제 없음: 메모리 읽기·복사·상태 보호. 이 범위의 무기한 I/O는 확인되지 않음 |
| chatbot/engine.py:183 | lock_scope / _lock | 문제 없음: 메모리 읽기·복사·상태 보호. 이 범위의 무기한 I/O는 확인되지 않음 |
| chatbot/engine.py:193 | lock_scope / _lock | 문제 없음: 메모리 읽기·복사·상태 보호. 이 범위의 무기한 I/O는 확인되지 않음 |
| chatbot/http_api.py:8 | lock_scope / engine._lock | 문제 없음: 메모리 읽기·복사·상태 보호. 이 범위의 무기한 I/O는 확인되지 않음 |
| chatbot/incidents.py:49 | lock_scope / _lock | 현재 범위 유지: 보고서 원자성, 실제 목록 HTTP 연결 시 재검토 |
| chatbot/incidents.py:277 | lock_scope / _lock | 현재 범위 유지: 보고서 원자성, 실제 목록 HTTP 연결 시 재검토 |
| chatbot/incidents.py:282 | lock_scope / _lock | 현재 범위 유지: 보고서 원자성, 실제 목록 HTTP 연결 시 재검토 |
| chatbot/incidents.py:33 | lock_scope / _lock | 현재 범위 유지: 보고서 원자성, 실제 목록 HTTP 연결 시 재검토 |
| chatbot/incidents.py:103 | except_pass / except DataUnavailable: | 문제 없음: 영향 차량 미확인 상태 유지, 복구 실패를 정상 값으로 바꾸지 않음 |
| chatbot/llm_router.py:107 | lock_scope / _model_lock | 문제 없음: 메모리 읽기·복사·상태 보호. 이 범위의 무기한 I/O는 확인되지 않음 |
| chatbot/llm_router.py:129 | lock_scope / _stats_lock | 문제 없음: 메모리 읽기·복사·상태 보호. 이 범위의 무기한 I/O는 확인되지 않음 |
| chatbot/llm_router.py:140 | lock_scope / _stats_lock | 문제 없음: 메모리 읽기·복사·상태 보호. 이 범위의 무기한 I/O는 확인되지 않음 |
| chatbot/llm_router.py:143 | lock_scope / _model_lock | 문제 없음: 메모리 읽기·복사·상태 보호. 이 범위의 무기한 I/O는 확인되지 않음 |
| chatbot/llm_router.py:182 | lock_scope / _model_lock | 문제 없음: 메모리 읽기·복사·상태 보호. 이 범위의 무기한 I/O는 확인되지 않음 |
| chatbot/llm_router.py:195 | lock_scope / _stats_lock | 문제 없음: 메모리 읽기·복사·상태 보호. 이 범위의 무기한 I/O는 확인되지 않음 |
| chatbot/llm_router.py:120 | lock_scope / _model_lock | 문제 없음: 메모리 읽기·복사·상태 보호. 이 범위의 무기한 I/O는 확인되지 않음 |
| chatbot/llm_router.py:210 | lock_scope / _stats_lock | 문제 없음: 메모리 읽기·복사·상태 보호. 이 범위의 무기한 I/O는 확인되지 않음 |
| chatbot/notifications.py:18 | lock_scope / self.lock | 문제 없음: 메모리 읽기·복사·상태 보호. 이 범위의 무기한 I/O는 확인되지 않음 |
| chatbot/notifications.py:33 | lock_scope / self.lock | 문제 없음: 메모리 읽기·복사·상태 보호. 이 범위의 무기한 I/O는 확인되지 않음 |
| chatbot/notifications.py:51 | lock_scope / self.lock | 문제 없음: 메모리 읽기·복사·상태 보호. 이 범위의 무기한 I/O는 확인되지 않음 |
| chatbot/notifications.py:56 | lock_scope / self.lock | 문제 없음: 메모리 읽기·복사·상태 보호. 이 범위의 무기한 I/O는 확인되지 않음 |
| chatbot/notifications.py:61 | lock_scope / self.lock | 문제 없음: 메모리 읽기·복사·상태 보호. 이 범위의 무기한 I/O는 확인되지 않음 |
| chatbot/notifications.py:65 | lock_scope / self.lock | 문제 없음: 메모리 읽기·복사·상태 보호. 이 범위의 무기한 I/O는 확인되지 않음 |
| chatbot/transport.py:38 | except_pass / except OSError: | 문제 없음: 이미 닫힌 소켓의 종료 OSError 정리 |


## 6. 성능 이전/이후

242문장을 직접 엔진에 넣어 3회 반복했다. p50/p95는 회차별 값의 중앙값, 최대는 세 회차의 최댓값이다. 로컬 환경의 간섭이 있어 운영 성능을 보장하지 않는다.

| 항목 | v2.5 | 최종 |
| --- | ---: | ---: |
| 기본 전체 검사 | 33.311s / 344개 | 56.950s / 374개 (앞선 실행 42.862s) |
| p50_ms | 0.611 | 0.446 |
| p95_ms | 1.645 | 1.380 |
| 최대 ms | 9.748 | 5.719 |
| dev 첫 응답 ms | 165.0 | 130.4 |
| fastapi 첫 응답 ms | 692.7 | 618.3 |
| 1,000문장 전후 추적 bytes | 0→2539615 | 0→2540916 |


최종 기본 검사 증가는 약 23.6초로 30초 이내 조건을 충족했다. 같은 변경본의 앞선 실행은 42.862초였으며 호스트 부하에 따른 변동이 있었다. 시작 시간은 각 1회라 개선 주장에 쓰지 않는다. 메모리 절감도 주장하지 않는다. tracemalloc은 Python 할당이고 전체 RSS·SDK 네이티브 메모리는 아니다.

## 7. 채택한 최적화

cProfile 1,000문장: deepcopy 누적 3.55s, Catalog.lots 2.11s, chips 1.07s, public 0.81s가 상위 20개였다(포함 시간이라 합산 금지). 공개 정보·칩 생성에서 불필요한 목업 배치·별칭까지 복사하던 부분을 필요한 필드 투영 후 복사/새 객체 생성으로 바꿨다. 캐시·TTL·세대 로직은 추가하지 않았다.

첫 분리 측정은 호스트 간섭으로 p95가 악화해 그 결과만으로 채택하지 않았다. 원래/변경 메서드를 같은 프로세스에서 번갈아 각각 3회·968문장씩 비교한 아래 측정이 채택 근거다.

| 통제 비교 | 원래 ms | 변경 ms | 개선 |
| --- | ---: | ---: | ---: |
| p50_ms | 0.592 | 0.356 | 39.9% |
| p95_ms | 1.816 | 1.339 | 26.3% |


242개 응답 동일성, 반환 dict·칩 params 변경이 원본 설정에 영향을 주지 않는 검사, 전체 회귀를 함께 통과했다.

## 8. 최종 결과

| 실행 | 전체 | 통과 | skip | 실패 |
| --- | ---: | ---: | ---: | ---: |
| LangChain 설치 기본 | 374 | 372 | 2 (장기) | 0 |
| LangChain 미설치 기본 | 374 | 370 | 4 (장기 2+기존 패키지 2) | 0 |
| cp949 콘솔·설치 환경 | 374 | 372 | 2 (장기) | 0 |
| RUN_SLOW=1 새 검증 묶음 | 30 | 30 | 0 | 0 |

기존 테스트의 기대값 변경·skip 추가는 0건이다. 새 장기 시험 2개만 RUN_SLOW로 분리했다. 기본은 테스트마다 20초 faulthandler watchdog, 20,000문장 시험은 작업 자체가 약 1분이므로 별도 180초 watchdog을 사용한다.

| 질문 세트 | 정답/전체 |
| --- | ---: |
| questions.csv | 36/36 |
| questions_extended.csv | 50/50 |
| questions_holdout.csv | 20/20 |
| questions_navigation_dev.csv | 20/20 |
| questions_navigation_holdout.csv | 12/12 |
| questions_realistic.csv | 69/69 |
| questions_rehearsal.csv | 5/5 |
| questions_stability.csv | 30/30 |
| 합계 | 242/242 |

| 관점 | 실제 실행·결과 |
| --- | --- |
| B | 무응답 헤더/느린 본문: 0.15초 외부·0.4초 요청 예산에서 1.4초 이내. 닫힌 포트 정상 오류 안내. DNS는 gaierror 주입. 10초 가짜 모델은 3초 경계에서 포기, 5동시 분류 최대 2슬롯. 느린 준비·늦은 결과·Lv2 전환 통과. 시험 소유 HTTP·라우터 스레드 종료 확인. |
| C | 6 API×8 상태/본문 48조합과 타입·숫자·상태·중복/누락 검사. 10MB·큰 목록은 1MiB 한도/계약 검사로 거절. 정상→장애→정상, 헤더 대소문자 통과. |
| D | 시드 1103, 5000입력, 예외 0, 최대 4.84ms. 부정문 50개 실행·확인 대기 0. |
| E | 취소/새 키, 성공/해제, 실패 응답 재전송, Lv2 확인 취소, 늦은 결과 무시, 세션·확인 만료 통과. |
| F | 20명×50문장, 결제 10동시→1건, 호출 10동시→1건, 경보 40전환/20리더, FastAPI 실제 HTTP 50동시 전부 응답. |
| F 장기 | 20,000문장, 현재 메모리 867715→875452 bytes, 마지막/첫 p95 1.00배. 접수 보관 R08을 해결한 결과는 아님. |
| G | assert_safe: Lv2 안전 카드, 화재 부정 표기, 오류 원문/세션 ID의 답변 미노출, 카드·명시적 문맥의 금액/자리/칸 비교. API 시험은 로컬 요금 호출을 AssertionError로 막음. 근거 없는 일반 정책 문구는 기존 명세 검사로 확인하며 모든 가능한 문장을 형식 증명한 것은 아님. |
| H | Python 3.12, cp949 전체 검사, 잘못된 설정 거절, 키 없음/가짜 SDK 오류, URL 슬래시, 점유 포트, 실제 한글·공백 복사 경로에서 질문 도구 실행 통과. |
| I | 기존 30+새 UI 6개 통과. XSS 글자 표시, 5초 지연 10연타=1요청, 360px 긴 답변/칩, 오프라인 안내/입력 해제, 단기 폴링 수, JS 런타임 예외 0. 의도적 네트워크 장애 콘솔 메시지는 별도. |
| I 모의 장기 | 시계로 30분 상당 900주기/900요청. GC 후 JS heap 1925912→2040716 bytes. 실제 30분 대기는 아님. |


## 9. 미검증·제한

- 팀 실서버·DB·카메라·차단기 연결 미제공, 미검증. 가짜 HTTP 규격 검사와 실서버 확인 도구 유지.
- Windows 실기기·Python 3.11/3.13 실행 파일 없음. Linux cp949·한글 경로 검사가 Windows 전체 호환 보장은 아님.
- 실제 제공자에 잘못된 키를 보내는 외부/유료 호출은 하지 않음. 모델 지연·준비·오류는 가짜 주입.
- 실제 30분 브라우저 방치·OS 메모리 미측정. F는 대안 20,000문장, I는 실제 단기 관측+가상 900주기를 사용.
- DNS는 실제 임의 도메인 접속 대신 결정적인 실패를 주입. R08 보관 정책 미해결.
- 새 시험 개발 중 잘못 쓴 HTTP 경로와 기존 오류 문구를 좁게 가정한 검사는 실행 도구 오류로 수정했으며 제품 결함 수에 넣지 않음.

## 재현
```powershell
python tests/run_guarded.py
python tests/run_robust_benchmark.py .validation/robustness/recheck.json
$env:RUN_SLOW="1"
python tests/run_guarded.py "test_robust_*.py"
Remove-Item Env:RUN_SLOW
python tests/run_browser_checks.py browser_robustness.cjs
python tests/run_browser_checks.py browser_robust_poll.cjs
```

선택 검사 도구: pyflakes(테스트 전용), FastAPI/httpx/uvicorn, Playwright/Chromium, requirements-llm.txt. 기본 Mock 실행 의존성은 추가하지 않았다. 원시 덤프·가상환경은 ZIP에서 제외한다.

수정 커밋: d221e08(재현 오류·회귀 도구), f22e756(측정된 설정 투영 최적화), ce12f39(긴 답변 줄바꿈·검증 보완). 원격 push·PR 병합은 수행하지 않았다.
