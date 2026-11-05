# v2.15 기능별 인수·검증 기준

작성: 2026-11-04(KST). 소프트웨어 기준 `f4a6bb0`, 원격 main `5ff9242b`, v2.14.2 브랜치 `e507073c`를 읽기 전용으로 확인했다.

이 문서/criteria.json을 검사 실행 전에 고정한다. 실패를 없애기 위해 기대값이나 기존 코드/CSV를 바꾸지 않는다.

## 기준의 우선순위

사용자 최신 지시 → 팀 확정 결정/계약 → 현재 챗봇 구현 범위와 고정 테스트 → 일반 가이드의 과거 예시 순서다. D-06(10~99), D-09(quote_id), D-11(모의 이벤트 허용), D-18(OpenRouter)을 적용한다.
API 계약 v0.1과 지침 일부는 초안/과거 예시이므로 미확정 수치나 삭제된 기능을 새 필수 조건으로 만들지 않는다. 기존 호환 영수증 기대값도 보존한다.

## 판정 규칙

- PASS: 검사 실행과 합격 근거가 있다. FAIL: 실행 결과가 기대 동작과 다르다.
- BLOCKED/NOT_RUN/SKIP는 PASS로 합산하지 않는다. 의존성 부족과 제품 결함을 구분한다.
- required는 현재 소프트웨어 필수 기준이다. conditional은 명시된 제공자/화면/정책 구성에서 적용한다. advisory는 팀이 정한 SLA가 아닌 이번 제안 품질 기준이다.
- 로컬 합격: 필수 critical/major 결함 0, 필수 검사 미실행 0, 기존 회귀 실패 0. minor 필수 실패도 남아 있으면 별도 미해결로 기록한다.
- 전체 팀 통합 합격은 main 등록·팀 화면·실제 LLM·Windows/기기 확인까지 별도로 필요하다. 미리보기 통과를 전체 통합 통과로 대체하지 않는다.
- 원치 않은 접수/미승인 결제, 허구 수치, 계약 변경은 우선 수정 대상이다. 정상적인 미지원·모의 범위는 실패로 세지 않는다.

## 요구사항 추적표

| ID | 기능 | 출처 | 합격 조건 | 기존 검사 연결 |
| --- | --- | --- | --- | --- |
| WIRE | 고정 HTTP·칩·응답 계약 | docs/API_CONTRACT.md; chatbot/docs/chat_response_fields.md; 기존 public_contract_v210.json | 경로·타입·필수 필드·칩 ID를 보존하고 messages 호환을 유지한다. | test_public_contract.PublicContractTests.test_static_contract<br>test_public_contract.PublicContractTests.test_http_contract<br>test_public_contract.PublicContractTests.test_embedded_routes |
| PARK | 서버 기준 빈자리·불확실 칸 | 통합 지침 §3/§11; DECISIONS D-03~05/D-14 | 서버 합계와 일치하고 uncertain을 빈자리로 추천하지 않는다. 미연결을 로컬 목업으로 바꾸지 않는다. | test_api_contract.ApiContractTests.test_01_server_counts_and_uncertain<br>test_supplement.SupplementTests.test_uncertain_is_excluded_from_empty_and_destination<br>test_api_contract.ApiContractTests.test_13_closed_port_never_uses_mock |
| IDENTITY | 차량·칸·입차 미확정 | DECISIONS D-04/D-06/D-19; chatbot/README v2.9 요약 | 실물 번호는 문자열 10~99, 번호 변경에는 확인이 있고 lot_id와 space_id를 혼동하지 않는다. 칸 배정 전에는 추측/결제를 하지 않는다. | test_supplement.SupplementTests.test_audit_vehicle_change_preserves_number_and_requires_confirmation<br>test_api_contract.ApiContractTests.test_04_null_session_no_fee_request |
| QUOTE | 서버 요금·견적·모의 결제 | DECISIONS D-09/D-10; chatbot/docs/api_contract.md 결제 | 차량·견적·금액 확인 후만 모의 결제하며 quote_id/멱등키를 보존한다. 다른 방문·만료 견적은 자동 실행하지 않는다. | test_api_contract.ApiContractTests.test_05_fee_is_server_amount<br>test_api_contract.ApiContractTests.test_06_exact_payment_body<br>test_api_contract.ApiContractTests.test_07_payment_retry_reuses_key<br>test_api_contract.ApiContractTests.test_21_session_change_after_quote_never_pays_new_visit<br>test_robust_state.RobustStateTests.test_failed_request_replay_never_sends_second_payment |
| INTENT | 명시 요청·문의·거절 구분 | chatbot/docs/chatbot_guidelines_v2.md §3.5; v2.15 지시서 B | 거절·존재 문의·방법 질문만으로 호출/결제를 시작하지 않는다. 긍정 규칙·칩 호출은 기존 즉시 접수를 유지한다. | test_stability.StabilityTests.test_negations_and_questions_do_not_create_calls<br>test_stability.StabilityTests.test_payment_problem_and_method_do_not_open_payment |
| LLM-ACTION | LLM은 분류만 담당 | chatbot/README 선택 LangChain; v2.15 지시서 고정 안전 규칙 | LLM 호출 선택은 칩 제안일 뿐 접수가 아니고 결제 확인·차량 근거·Lv2 생략을 유지한다. | test_llm_router.RouterTests.test_admin_tool_registers_once_after_call_chip<br>test_llm_router.RouterTests.test_payment_tool_still_requires_vehicle_and_amount_confirmations<br>test_api_contract.ApiContractTests.test_24_api_llm_vehicle_must_come_from_user |
| ALERT | 모의 경보·Lv2 우선 | 통합 지침 §3; chatbot/docs/api_contract.md 경보; 지침 §4.2 | 모의 표기·사람 먼저·재진입/차량 이동 금지·결제 차단을 유지하며 신뢰할 수 없는 해제로 Lv2를 지우지 않는다. | test_supplement.SupplementTests.test_every_lv2_response_carries_safety_and_safe_actions<br>test_robust_state.RobustStateTests.test_alert_transitions_cancel_confirmation_and_never_resume_payment<br>test_stability_review_20261104.TeamBoundaryTests.test_paginated_alerts_never_clear_last_level_two |
| ANONYMOUS | 번호 미인식 후보 | chatbot/README 구현 범위; 지침 §3.5 | 최대 3개 후보만 안내하고 번호로 확정하거나 요금/결제로 연결하지 않는다. | test_supplement.SupplementTests.test_unrecognized_candidate_cannot_pay_or_query_fees<br>test_supplement.SupplementTests.test_unrecognized_candidate_ownership_expiry_and_changed_location |
| DEST | 목적지·상태 변화 | chatbot/README 구현 범위; navigation 계약 | 만차/제한/미확인 목적지는 선택하지 않고 대안·취소·도착·TTL·중복 알림을 일관되게 처리한다. | test_navigation.NavigationTests.test_full_destination_rejected_with_two_alternatives<br>test_navigation.NavigationTests.test_destination_ttl_and_cancel<br>test_navigation.NavigationTests.test_arrival_ends_monitor_even_when_occupancy_is_unavailable |
| NOTIFY | 알림 cursor·재전송·재시작 | chatbot/docs/CODEMAP.md; 지침 안정성 필드 | 수신 cursor 확인과 중복 제거를 보존하고 과거 경보를 현재 대피 명령으로 재생하지 않는다. | test_stability.StabilityTests.test_transient_observed_alarm_delivered_as_history<br>test_stability.StabilityTests.test_queued_alarm_after_clear_does_not_repeat_evacuation<br>test_stability_review_20261104.ReplaySafetyTests.test_retry_adds_current_safety_after_new_level_two |
| REPORT | 보고서·관리자 편집 | chatbot/docs/chatbot_guidelines_v2.md 보고서 §6 | 관리자 권한·revision·진행 중 확정 금지·확정 후 불변·미저장 초안과 체크 갱신을 보존한다. | test_fastapi.FastAPITests.test_alert_and_report<br>test_stability.AccessBoundaryTests.test_admin_token_required_and_not_replaced_by_revision |
| DISPLAY | 값을 지어내지 않는 화면 | 통합 지침 §3/§11; API optional/null 계약; 지침 §3.1 | API의 null/미제공 값을 0%나 임의 시각으로 만들지 않고 정상 값은 정확하게 표시한다. | test_stability_review_20261104.TeamBoundaryTests.test_nullable_server_confidence_preserves_explicit_level_two |
| CONSISTENCY | 거절된 응답의 부작용 방지 | 통합 지침 단일 진실 공급원; CachedAdapter 세대/명부 계약에서 도출한 이번 검수 기준 | 응답이 검증/신선도 검사에서 거절되면 명부 게시도 일어나지 않는다. | test_navigation.NavigationTests.test_invalid_backend_catalog_is_not_adopted |
| CONFIG | 설정 출처와 제공자 SDK | DECISIONS D-18; chatbot/README .env 제공자 3개 지원 | .env/환경변수에 둔 키가 해당 SDK에 전달된다. OpenRouter는 팀 필수, OpenAI/Anthropic은 선택 제공자 조건이다. | test_v213_openrouter.OpenRouterSettingsTests.test_router_settings_use_openrouter_key_only |
| FLAGS | C·D 기본 꺼짐과 보호 유지 | v2.15 지시서 C/D; 기존 테스트 | 기본값 0, 예시 메시지 순서/캐시 분리, 첫 unknown 칩만 변경, 2회차/Lv2/관리자·결제 실행 보호를 유지한다. | test_v215_fewshot.FewshotTests.test_default_switch_is_off_and_off_messages_stay_two<br>test_v215_fewshot.FewshotTests.test_cached_classifications_are_separate_for_each_example_mode |
| VERIFY | 검증 도구의 유효 결과 | v2.15 지시서 P·사람 실측 도구; 이번 검수의 결과 신뢰성 기준 | 측정/연결 불능을 품질 통과로 보고하지 않고 잘못된 평가/저장 입력을 유료 측정 전에 거절한다. | 추가 검사/실행 게이트 |
| CAPACITY | 세션·부하·보관 | chatbot/docs/CODEMAP.md; 현재 한도 설정과 테스트 | 동일 세션 대기·공유 worker를 제한하고 다른 세션의 진행, 재전송, 메모리 상한과 보호 기록을 보존한다. | test_runtime_resilience.RuntimeResilienceTests.test_busy_session_has_bounded_wait_and_other_session_works<br>test_runtime_resilience.RuntimeResilienceTests.test_session_capacity_and_idle_cleanup_release_related_resources<br>test_embedded_capacity.EmbeddedCapacityTests.test_sync_backend_can_progress_under_chat_load |
| REHEARSAL | 팀 앱과 시연 흐름 | 통합 지침 §11; integration-gate B/C; DECISIONS D-11 | 모의 이벤트→점유→위치→요금→확인 결제가 팀 앱 미리보기에서 6/6이며 C/D를 켜도 보존된다. | test_v214_rehearsal.RehearsalTests.test_actual_demo_rehearsal |
| REGRESSION | 기존 명세 보존 | 사용자 작업 규칙; v2.15 지시서 테스트·작업 방식 | 기존 전체 테스트 실패 0, 질문 9세트 248문항 일치, 기본 응답 스냅샷 242건 차이 0, 기존 브라우저 검사 통과. | 추가 검사/실행 게이트 |
| INTEGRATION | 현재 main·팀 화면 연결 | integration-gate C/D; docs/PROGRESS.md 화면·챗봇 연결 항목 | 실제 팀 main에 라우터가 등록되고 필요한 화면·실기기 경로가 실행되어야 전체 통합 합격이다. 미리보기 PASS를 main 합격으로 대체하지 않는다. | 추가 검사/실행 게이트 |

## 추가 인수 사례 — 결과는 실행 후 별도 기록

| ID | 요구사항 | 조건 | 기대 결과 | 범위 | 심각도 |
| --- | --- | --- | --- | --- | --- |
| ADM-01 | INTENT | 명시 거절: 관리자 오지 마세요 | 등록 0건, 결제 0건 | required | critical |
| ADM-02 | INTENT | 직원 존재 문의 | 등록 0건 | required | major |
| ADM-03 | INTENT | 관리자 존재 문의 | 등록 0건 | required | major |
| ADM-04 | INTENT | 명시 긍정 호출 대조군 | 등록 1건, 기존 즉시 접수 유지 | required | major |
| ADM-05 | INTENT | 기존 거절 대조군 | 등록 0건 | required | major |
| PAY-01 | INTENT | 결제 방법 문의 | payment_help, pending 없음, 결제 0건 | required | major |
| DATA-01 | CONSISTENCY | 미래 점유 응답 | 조회 거절, 명부 칸 목록/버전 불변 | required | major |
| RATE-01 | DISPLAY | 서로 다른 기본 시간 정책 | 각 정책의 30분/60분을 구분하여 표시 | conditional | minor |
| CHK-01 | VERIFY | 요금/경보 unavailable 연결 검사 | 해당 항목 PASS 금지, 실패/미검증 표시 | required | minor |
| CHK-02 | IDENTITY | 유효 차량 27의 연결 검사 | 구식 01~24 검증으로 거절하지 않음 | required | minor |
| SDK-01 | CONFIG | openai: dotenv 가짜 키 | 설정에서 읽은 키와 SDK 키가 일치 | conditional | major |
| SDK-02 | CONFIG | anthropic: dotenv 가짜 키 | 설정에서 읽은 키와 SDK 키가 일치 | conditional | major |
| SDK-03 | CONFIG | openrouter: dotenv 가짜 키 | 설정에서 읽은 키와 SDK 키가 일치 | required | major |
| SDK-04 | CONFIG | openai: environment 가짜 키 | 설정에서 읽은 키와 SDK 키가 일치 | conditional | major |
| SDK-05 | CONFIG | anthropic: environment 가짜 키 | 설정에서 읽은 키와 SDK 키가 일치 | conditional | major |
| SDK-06 | CONFIG | openrouter: environment 가짜 키 | 설정에서 읽은 키와 SDK 키가 일치 | required | major |
| EVAL-01 | VERIFY | 모델 준비 실패의 비교 | hybrid 정상 점수 없음 또는 명시적 측정 무효 | required | major |
| EVAL-02 | VERIFY | 호출 오류의 비교 | api_error 원인/측정 무효를 결과에 기록 | required | major |
| EVAL-03 | VERIFY | 잘못된 출력 상위 폴더 | 측정 함수 실행 0회인 상태에서 사전 거절 | required | minor |
| CSV-01 | VERIFY | 존재하지 않는 평가 intent | 모델 호출 전에 거절 | required | minor |
| WAIT-01 | CAPACITY | 다른 결제 슬롯 대기 | 50ms 예산에서 슬롯 해제(250ms) 전 종료; 로컬 허용 오차 150ms | advisory | major |
| DNS-01 | CAPACITY | 느린 DNS 해석 | 20ms 제한에서 합성 해석 지연 200ms를 넘겨 기다리지 않음; 로컬 허용 오차 150ms | advisory | major |
| UI-01 | DISPLAY | null 신뢰도 | 미확인 표시, 0% 금지 | required | major |
| UI-02 | DISPLAY | 미제공 입차 시각 | 미확인/미제공 표시, 09:00/Invalid Date 금지 | required | major |
| UI-03 | REPORT | 편집 중 서버 체크리스트 변경 | 초안을 보존하고 체크 완료 상태도 갱신 | required | major |
| UI-04 | CAPACITY | 혼잡 응답과 다음 poll | 기존 결제 확인 버튼/선택 상태 보존 | conditional | major |
| UI-05 | DISPLAY | 정상 신뢰도 대조군 | 0.73을 73%로 표시 | required | major |
| UI-06 | DISPLAY | 정상 입차 시각 대조군 | 12:34+09:00을 12:34로 표시 | required | major |
| UI-07 | DISPLAY | 결제 확인 대조군 | 확인 버튼 존재, 승인 전 결제 없음 | required | major |
| UI-08 | REPORT | 편집 중 초안 대조군 | 자동 갱신 후 미저장 텍스트 보존 | required | major |

## 실행 범위

1. 기존 Python 전체: LangChain 설치/미설치 각각 1회. RUN_SLOW 묶음 1회. 기존 테스트 기대값 불변.
2. 질문 9세트/248문항, 기존 242응답 스냅샷 비교. LLM 평가/holdout을 회귀 glob에 넣지 않는다.
3. 기존 브라우저 14개 + 새 화면 인수 8개. 신규 Python 인수 사례는 가짜 키/모델·합성 API만 사용한다.
4. 팀 루트 pytest, 프론트 unit/build, 시연 기본/옵션 켬. main 라우터 등록과 미리보기 구성을 구분한다.
5. 실제 LLM 호출·실기기 등은 미실행으로 기록한다. 생성 파일/의존성은 chatbot 내부의 격리 복사본에서만 만든다.

시간 예산 검사의 150ms 허용 오차는 이 로컬 장애 주입용 기준이며 운영 SLA나 PM 확정 성능 목표가 아니다. 무제한 부하/장시간 검사를 임의로 추가하지 않는다.

실행: `python qa/run_acceptance.py`. 재현 가능한 QA 코드와 machine JSON 결과를 제공한다. 이 명령의 실패는 제품 기준 미충족을 의미할 수 있으므로 기대값을 현재 오동작에 맞추지 않는다.
