# EPIC06 기능 정리·보완 결과

2026-11-02. 기준: 첨부 `EPIC06_보완_작업묶음/parking-chatbot`. 작업 브랜치: `feature/KAN-9-chatbot-cleanup`.

## 결과

첨부 00 요청과 02 계획의 단계 1~5를 구현했다. 가이드라인은 `implementation_guidelines.md`, 추가 응답·시연 API 계약은 `supplement_contract.md`에 있다. LangChain 도입은 다음 작업이라는 첨부 지침을 적용했다. 원격 저장소 변경·PR·병합은 하지 않았다.

현재 기준본에 해당하는 이전 검수의 입력 보관량, ID 대소문자, 차량 변경 대상, 설정 검증을 보완했다. 이전 LangChain 모델 생성의 잠금 문제는 이 기준본에 해당 모듈이 없으므로 이번 코드에 추가하지 않았다. 후속 라우터 도입 시 통계 잠금과 초기화 잠금을 분리하고 준비 지연 중에도 3초 제한을 지키는 검사가 필요하다.

## 단계별 변경

1. **기능 삭제** — engine/parser/templates/catalog/navigation/chips, 두 서버, 두 화면, 설정·검사를 함께 정리했다. `chatbot/waitlist.py`를 삭제했다. 문의 상태 변경 API·피드백 전이를 삭제하고 10분 내 같은 세션·차량·사건 호출 통합과 고정 접수 확인은 유지했다. 독립 이용 현황 대신 위치 카드를 사용하고, 요금·영수증·출차 카드 안의 이용 단계는 유지했다. 빈자리 알림 요청에는 기능이 없다고 밝힌 뒤 현재 빈자리를 보여준다. 변경 질문은 아래 21행이며 원본 문장을 삭제하지 않았다.
2. **사람 우선 안내** — templates/engine/index/incidents에서 Lv2 안내·배너·알림·안전 카드를 보완했다. 안이면 지정 출구, 밖이면 재진입 금지, 차량 이동 금지를 안내한다. Lv2 칩에 위치·요금·결제를 넣지 않는다. 후보 위치 안내도 Lv2에서는 관리자 확인으로 전환한다. 모델이 실제 화재를 판정하거나 외부 신고를 수행하는 기능은 추가하지 않았다.
3. **현장 브리핑** — lots/catalog/briefing/incidents/admin에서 전기차 목록·구역 배치·출구·설비 검증, 해당·인접 차량, 전기차 수, 장비 참고·복사·보고서 포함을 구현했다. 초기 보고서와 브리핑은 같은 차량 조회를 재사용한다. 목록 실패는 0대가 아니라 확인 불가, 번호 미인식 포함 시 전체 전기차 수는 미확인이다. 시설 null은 미확인이다. 장비 필요성 판단은 소방에 둔다.
4. **확인 중 칸** — adapters/contracts/navigation/index와 두 서버에서 uncertain 선택 필드·집계 검사·표시·시연 변경을 구현했다. 확정 empty만 추천하고 점유·확인 중 칸이 겹치지 않게 한다. 새 변경에도 캐시 무효화를 적용한다.
5. **번호 미인식 후보** — adapters/contracts/state/engine/incidents와 두 화면·서버에서 null 번호와 입차 시각을 지원했다. 최근순 최대 3대의 세션별 위치 토큰을 만들고 4대 이상이면 관리자 확인으로 연결한다. 후보 선택 시 120초 만료·다른 세션·목록 변경을 검사한다. 번호와 자동 연결하지 않으며 요금·결제·요금을 이용한 출차 진단은 차단한다. 관리자 요약에 후보 위치가 포함된다. 여러 미인식 차량의 근접 강조는 번호 null이 아니라 칸 번호로 구분한다. 번호 없는 만차 연출 점유는 기존대로 후보에서 제외한다.
공통 **기존 검수 보완** — engine/server_fastapi에서 text 200자 등 입력 제한과 로그 제한, catalog/parser에서 원래 주차장 ID 보존, engine에서 번호가 있는 차량 변경을 확인 단계로 연결, config에서 NaN·무한대·음수·0 설정을 시작 시 검사했다. 담당자에게 보내지 말라는 표현도 신규 호출로 분류하지 않는다. 새 시연 API는 기존 접근 키/비활성 설정을 적용하며 DATA_SOURCE=api에서 실행할 수 없다.

단계별 로컬 커밋을 만들었다. 목록은 `commit_history.txt`에 있다. 전체 변경 파일은 `change_summary.txt`, 질문·테스트 원본 변경 목록은 `question_changes.json`, `test_changes.json`, `test_change_inventory.json`에 있다.

## 문구 지침의 조정

‘관리자가 확인해 연락드린다’, ‘차량 정보가 이미 등록돼 있다’는 문구는 실제 기능과 조건이 맞을 때만 써야 한다. 연락처 수집·자동 연락이 없으므로 ‘차량 피해 확인은 현장 관리자에게 요청’, ‘관리자 호출로 정보와 문의를 남길 수 있음’으로 구현했다. 실제 문의가 접수된 응답에는 요약이 관리자 화면에 등록됐다고 안내한다. 두 줄의 말풍선과 안전 카드로 상세 내용을 나눴다.

## 삭제·변경 검사

삭제 기능 전용 Python 검사 17개를 제거했다. 나머지 기존 기대값은 유지했으며, 삭제·대체된 기능의 검사는 새 명세에 맞게 변경했다. 의미가 바뀌어 이름이 바뀐 검사는 `test_changes.json`에 명시했다. 새 기능·경계·두 서버 검사는 `test_supplement.py`에 27개 추가했다.

구체적인 삭제 목록:

- `tests/test_regressions.py::test_one_vacancy_does_not_notify_two_people`
- `tests/test_regressions.py::test_parallel_waiters_receive_only_available_vacancies`
- `tests/test_regressions.py::test_stale_vacancy_does_not_notify`
- `tests/test_reliability.py::test_cancel_removes_undelivered_wait_notification`
- `tests/test_reliability.py::test_wait_status_reports_queue`
- `tests/test_reliability.py::test_expired_offer_does_not_block_next_waiter`
- `tests/test_stability.py::test_lv2_vacancy_is_deferred_until_clear`
- `tests/test_stability.py::test_previously_queued_vacancy_is_withdrawn_during_alarm`
- `tests/test_optimization.py::test_waiter_poll_does_not_calculate_queue_position_for_all_sessions`
- `tests/test_optimization.py::test_completed_call_query_can_reopen_same_call`
- `tests/test_ux.py::test_status_redelivery_and_reopen`
- `tests/test_ux.py::test_other_session_cannot_use_feedback`
- `tests/test_ux.py::test_old_manager_revision_and_runtime_rejected`
- `tests/test_ux.py::test_status_retry_emits_only_one_notification`
- `tests/test_ux.py::test_feedback_resolve_and_stale_button`
- `tests/test_navigation.py::test_zone_waiting_gets_only_requested_zone`
- `tests/test_navigation.py::test_shared_total_and_zone_waiters_do_not_receive_same_spot_twice`

공용 알림 재수신 검사는 대기 알림 대신 경보 알림으로 동일한 재전송·커서 단언을 유지했다. 세션 정리 검사는 삭제된 큐 대신 공용 알림 정리를 계속 검사한다. 목적지·경보·결제·캐시·보고서 충돌 검사는 보존했다.

추가 조정 이유:

- 용량을 5칸으로 바꾸는 기존 설정 테스트는 새로 추가된 8칸 전용 zone_layout도 제거하도록 입력 fixture를 조정했다. 기존 3개/4개 명부 실행 기대값은 유지했다. 잘못된 배치를 허용하도록 검증을 완화하지 않았다.
- 삭제 API의 FastAPI 응답은 정적 파일 mount 때문에 405가 될 수 있어 404/405 거절을 확인한다. 상태 변경은 수행되지 않음을 함께 검사한다.
- 브라우저의 삭제 기능 시나리오를 접수 확인·위치 조회로 대체하고 대기 전용 1개와 상태 재개 전용 1개를 제거했다. ‘모의 화재/안에 계시면’ 문구를 검사한다.
- busy 입력 보존의 350ms 고정 지연을 명시적 응답 대기로 바꿔 기기 속도에 따른 오검출을 줄였다. draft 보존 기대값은 유지했다.
- 과거 평가 결과 `tests/extended_before.json`은 현행 명세가 아니므로 `docs/history/`로 이동했다. 과거 기능 설명은 삭제됨/이력 문서임을 표시했다.

## 질문 정답 변경 21행

| 파일·행 | 기존 문장 | 의도 변경 |
|---|---|---|
| tests/questions_extended.csv:40 | 대기하지 않을게요 | waitlist_cancel → get_empty_spots |
| tests/questions_extended.csv:41 | 대기 그만할게요 | waitlist_cancel → get_empty_spots |
| tests/questions_extended.csv:42 | 자리 알림 끄고 싶어요 | waitlist_cancel → get_empty_spots |
| tests/questions.csv:32 | 내 이용 현황 보여줘 | my_status → find_my_car |
| tests/questions.csv:33 | 7번 차 주차 상태 | my_status → find_my_car |
| tests/questions.csv:34 | B 주차장 자리 나면 알려줘 | waitlist_join → get_empty_spots |
| tests/questions.csv:35 | 빈자리 생기면 알려줘 | waitlist_join → get_empty_spots |
| tests/questions.csv:36 | 대기 취소할게 | waitlist_cancel → get_empty_spots |
| tests/questions.csv:37 | 알림 꺼줘 | waitlist_cancel → get_empty_spots |
| tests/questions_realistic.csv:50 | B 자리 나면 알려주세요 | waitlist_join → get_empty_spots |
| tests/questions_realistic.csv:51 | 비 주차장 대기 등록할게 | waitlist_join → get_empty_spots |
| tests/questions_realistic.csv:52 | 빈자리 생기면 알려줘 | waitlist_join → get_empty_spots |
| tests/questions_realistic.csv:53 | 대기 신청할래요 | waitlist_join → get_empty_spots |
| tests/questions_realistic.csv:54 | 대기 취소해 주세요 | waitlist_cancel → get_empty_spots |
| tests/questions_realistic.csv:55 | 자리 알림 꺼주세요 | waitlist_cancel → get_empty_spots |
| tests/questions_realistic.csv:56 | 대기 안 할래요 | waitlist_cancel → get_empty_spots |
| tests/questions_realistic.csv:57 | 내 대기 순번 알려줘 | waitlist_status → get_empty_spots |
| tests/questions_realistic.csv:58 | 몇 번째예요? | waitlist_status → get_empty_spots |
| tests/questions_realistic.csv:59 | 대기 현황 | waitlist_status → get_empty_spots |
| tests/questions_holdout.csv:15 | B 주차장에 자리 생기면 알려주세요 | waitlist_join → get_empty_spots |
| tests/questions_holdout.csv:16 | 대기 취소 부탁해요 | waitlist_cancel → get_empty_spots |

## 실제 실행한 검증

Linux/Python 3.12.14, FastAPI/httpx 설치 환경, Node/Chromium에서 실행했다.

| 검사 | 결과 |
|---|---|
| 수정 전 Python 기준 | 245/245 통과 |
| 단계 1 삭제 후 | 228/228 통과, 질문 237/237 |
| 단계 2 안내 보완 후 | 228/228 통과 |
| 단계 3 브리핑·신규 경계 검사 | 235/235 통과 |
| 단계 4 확인 중 칸 | 238/238 통과 |
| 단계 5 후보 안내 | 246/246 통과 |
| 최종 설치 환경 Python | 255/255 통과 |
| 최종 질문 7세트 | 237/237 통과, 평균 약 0.31ms (로컬 규칙 파서) |
| 실제 Chromium | 기존 범위 조정 후 49개 + 새 화면 6개 = 55개 통과 |
| 동시 반복 | 30세션·2,400요청·경보 변경 40회, 불변 조건 전부 통과 |
| 정적 확인 | compileall, git diff --check, 현행 코드·검사·설정의 삭제 기능 참조 0 |

브라우저에서는 미인식 입차 버튼·후보 선택·결제 확인 미진입·관리자 요약·Lv2 칩·브리핑의 실제 클립보드 복사를 실행했다. 공용 조회·세션·일회성 확인·중복 결제·보고서 revision·알림 재수신은 기존 전체 검사와 동시 반복으로 확인했다. 원시 결과는 `validation_supplement/`에 있다. 새 API의 200자 초과 입력, boolean 형식, 범위, 관리자 접근 키·DEMO_ENABLED 거절을 두 서버에서 실제 호출했다.

선택 의존성 없는 환경 검사 결과는 `validation_supplement/without_optional.txt`와 아래 추가 실행 기록을 참조한다. 설치하지 못해 건너뛰는 FastAPI 검사는 설치 환경에서 별도로 전부 실행했다.

## 남은 한계와 팀 확인

- zone_layout의 충전 7·장애인 8, 전기차 03·11·18, 출구·설비는 실측 전 임시값이다. 기본 주차 배치 회귀를 피하도록 기존 자동 배정과 같은 칸을 명시했다. 실측 후 변경해야 한다.
- 실제 팀 API의 uncertain/null 차량/entered_at 제공 여부와 경로·응답 변환은 미확정이다. 기존 API 경로를 임의로 팀 계약으로 바꾸지 않았다.
- 후보는 본인 차량을 확정하지 않으며 사진·인증·요금 연결이 없다. 여러 미인식 차량의 후보 정보만 안내한다. 칸 번호 차이 1은 물리적 거리 계산이 아니다.
- 실제 팀 DB·카메라·차단기·유료 LLM·Windows 및 Python 3.11/3.13은 미실행이다. 로컬 100%는 실사용 정확도나 운영 무오류 보장이 아니다.
- 단일 프로세스 메모리 상태이며 재시작 복원·다중 worker 공유는 없다. 문의·사건 이력과 화면 DOM의 장기 누적은 별도 보존/표시 정책이 필요하다. 무조건 삭제하는 정책은 넣지 않았다.
- 외부 요청 제한은 운영체제 DNS 중단이나 모든 외부 코드를 강제로 정지시키는 watchdog이 아니다. 이후 LangChain을 넣을 때는 앞선 잠금·늦은 결과·지연 작업 상한 검사를 별도로 적용해야 한다.
- 관리자 자동 연락·119 신고·실제 결제/환불·차단기 제어·충전 구역 일반차 감지는 추가하지 않았다. 다음 LangChain 작업은 이 보완본 병합 후 도구 목록을 현재 범위에 맞춰 진행한다.

## 최종 추가 실행 기록

선택 의존성 없는 최종 환경: 전체 255개 중 239개 통과, FastAPI 관련 16개 정상 건너뜀(11.389초). 설치 환경에서는 같은 전체 255개가 모두 통과했다(15.666초). 단계별 원시 로그와 현재 변경은 이 압축본에 포함했다.

## Claude 검수 후 수정 (2026-11-02)

검수에서 발견한 3건을 직접 수정했다. 다른 동작은 바꾸지 않았다.

1. `docs/review_history.bundle`(git 이력 묶음, 약 0.9MB) 삭제. 레포에 올리지 않는다. 단계별 커밋 목록은 `commit_history.txt`에 남아 있다.
2. `chatbot/briefing.py` 장비 참고 판단: 번호 미인식 차량이 주차장 어디에든 있으면 충전 구역·인접 전기차 근거가 있어도 "전기차 여부 미확인 — 대비 필요"로 덮어쓰던 문제. 판단 범위를 화재 칸과 양옆(칸 번호 차이 1)으로 좁히고, 근거(충전 구역, 인접 전기차)가 있으면 "필요 가능성"을 우선한다. 근거가 없고 그 범위에 목록 실패·번호 미인식이 있을 때만 "대비 필요". 주차장 전체 전기차 수의 "미확인" 표시는 그대로.
3. `chatbot/navigation.py` 확인 중 칸 문구: 전체 조회에서 다른 주차장의 확인 중 칸 수가 추천 주차장 문장 뒤에 붙어 그 주차장 것처럼 읽히던 문제. 전체 조회는 "(카메라 확인 중: D 주차장 6칸)"처럼 주차장 이름을 붙이고, 주차장 지정 조회는 그 주차장 문장(첫 줄)에 붙인다.

추가 검사 `tests/test_review_fixes.py` 6개 (수정 전 코드에서 4개 실패, 수정 후 6개 통과). `test_changes.json`에 test_stability 수정 기록 누락분을 추가했다.

### PR에 명시할 계획 밖 변경
- 채팅 입력 200자 초과 거절 (화면 입력창은 원래 200자 제한), 시작 시 설정값 검증, 번호가 있는 차량 변경 문장("17번 차로 바꿔줘")을 확인 단계로 연결. 모두 개선 방향이나 02 계획서 범위 밖.

### Windows 실행 후 추가 수정
- `tests/test_supplement.py`의 시작 설정 검사가 Windows에서 실패: 띄운 Python이 한글 오류를 cp949로 출력해 UTF-8로 읽지 못함. 띄우는 Python에 `PYTHONIOENCODING=utf-8`을 주고 읽기에 `errors='replace'` 추가. 같은 방식으로 Python을 띄우는 `tests/test_navigation.py`, `tests/run_layout_matrix.py`에도 `PYTHONIOENCODING=utf-8` 추가. 제품 코드 변경 없음.

### 설정값 정리 (v1.2)
- 설비 `height_limit_m`에 `false`(해당 없음) 허용. 브리핑에 "높이 제한: 해당 없음" 표시. `null`은 계속 "미확인".
- `facility` 검증 메시지에 주차장과 항목 이름 표시, 알 수 없는 항목(철자 오류) 거절.
- `docs/settings_guide.md` 추가: 값 넣는 순서, 항목별 뜻·담당, 미정 값 체크리스트. README 연결.
- 검사 `tests/test_settings_facility.py` 4개 추가. 설정값 자체(현재 null)는 팀 논의 후 입력.

- LangChain v2 결과물(v1.1 기준 작업)에 Claude가 v1.2 변경분을 병합함 (catalog.py, briefing.py, settings_guide.md, test_settings_facility.py, README 한 줄). 두 작업이 겹친 파일은 README·이 보고서뿐.

## 통합요청 v3 후속 작업 (v2.3)

설정값 정리(v1.2), R1 관리자 호출 제안, R3 평가 파일 이름 변경은 최신 v2.2 기준본에 이미 있어 지시서와 일치함을 확인해 유지했다. 규칙 오분류 4건을 수정하고 리허설 5문장·LLM 평가 35문장으로 확장했다. 단계별 실행 결과·기존 검사 수량 변경 이유는 [통합요청 v3 변경 요약](integrated_v3_report.md)에 있다.
