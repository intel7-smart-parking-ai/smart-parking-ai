# EPIC06 팀 API 계약 정렬 결과 — 2026-11-03

v2.4를 기준으로 소비자 측 API 계약을 정렬했다. 버전 표기는 v2.5다. 팀 서버의 소스·DB·API 라우터는 수정하지 않았다.

## 적용

- 경로를 주차장 목록·칸 목록·차량 위치·세션 요금·모의 결제·경보·세션 목록의 팀 경로로 바꿨다.
- 차량 필드는 car_id, 입차 시각은 entry_at으로 통일했다. 칸 번호 spot_no는 내부에서 유지하고 경계에서 space_id를 변환한다. CSV는 열 이름만 바꿨다.
- ApiAdapter는 로컬 목업을 생성하거나 그 데이터로 대체하지 않는다. 서버의 목록·칸 수·금액을 사용한다. 서버 요금으로 30분 후 금액을 계산하지 않는다.
- 결제 요청은 session_id, idempotency_key, payment_method 세 필드뿐이다. 내부 목업 견적 검사 인자 quote_amount는 전송하지 않는다.
- API 결제 확인의 UUID v4 키는 상태에 저장한다. 금액 차이는 서버 결과를 표시하며, 확인 뒤 방문 세션이 바뀌면 새 방문에 결제하지 않는다.
- 경보 조회 실패를 경보 없음으로 표현하지 않는다. 성공했던 Lv2는 조회 실패 뒤에도 안전 카드에 남는다. 상태 상수·레벨 매핑은 contracts.py에 모았다.
- mock 응답 헤더가 있는 API 답변만 끝에 시연 문구를 한 번 붙인다. 기본 목업 화면은 그대로다.
- 오류 결과를 성공 캐시에 저장하지 않는다. 기존 요청 잠금·캐시 세대·알림 커서·Mock 결제 직렬화 구조는 유지했다.

## 실제 검사 결과

| 단계·환경 | 전체 | 통과 | 건너뜀 | 실패 |
| --- | ---: | ---: | ---: | ---: |
| 기준 v2.4, LangChain 미설치 | 320 | 318 | 2 | 0 |
| 이름 변경 단계, LangChain 미설치 | 320 | 318 | 2 | 0 |
| 최종, LangChain 설치 | 344 | 344 | 0 | 0 |
| 최종, LangChain 미설치 | 344 | 342 | 2 | 0 |

두 건너뜀은 실제 제공자 생성자와 LangChain 가짜 모델 검사다. 설치 환경은 requirements-llm.txt의 3개 고정 버전을 설치했다. FastAPI 검사는 별도 설치된 테스트 의존성을 사용해 양쪽 환경 모두 실행했다. 실제 유료 모델 호출은 실행하지 않았다.

| 질문 세트 | 정답 / 전체 |
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

브라우저: 기존 화면 15/15, 보완 6/6, 통합 v3 5/5, R1 4/4, 합계 30/30. 지연·오류와 동시 요청 검사는 기존 전체 테스트에 포함되어 실행됐다.

## 새 HTTP 계약 검사

표준 라이브러리 http.server로 127.0.0.1의 자동 할당 포트에 가짜 서버를 시작했다. FastAPI 없이 실행한다. 아래 요청 19건과 추가 5건이 전부 통과했다.

| 번호 | 검사 | 결과 |
| --- | --- | --- |
| 1 | 서버 4칸·빈자리 1·확인 중 1을 표시 | 통과 |
| 2 | 선행 0 위치 경로와 1번 칸 표시 | 통과 |
| 3 | 차량 없음 코드와 미입차 안내 | 통과 |
| 4 | 세션 null이면 요금 경로 호출 없음 | 통과 |
| 5 | 서버 2,500원 사용, 로컬 요금 함수 호출 없음 | 통과 |
| 6 | 결제 본문 정확히 3키·UUID v4 | 통과 |
| 7 | 같은 확인에서 같은 결제 키·재처리 표시 | 통과 |
| 8 | 이미 결제 409 안내 | 통과 |
| 9 | 키 충돌 409 안내·자동 재요청 없음 | 통과 |
| 10 | billing 라우트 404 확인 불가 | 통과 |
| 11 | 경보 404를 경보 없음으로 표시하지 않음 | 통과 |
| 12 | mock 헤더 안내 한 번 | 통과 |
| 13 | 닫힌 포트에서 확인 불가·위치 생성 없음 | 통과 |
| 14 | 서버에 없는 D·지역 설정 8칸 사용 없음 | 통과 |
| 15 | canceled 안내·다른 철자 거절 | 통과 |
| 16 | UUID·특수문자 포함 세션 경로 quote | 통과 |
| 17 | 헤더 없는 응답은 시연 문구 없음 | 통과 |
| 18 | 미식별 점유칸 세션 없음·요금/결제 칩 없음 | 통과 |
| 19 | acknowledged Lv2 및 조회 실패 뒤 안전 유지 | 통과 |
| 20~24 | 응답 금액 갱신·세션 교체 차단·경보 422·잘못된 목록·API LLM 번호 차단 | 5/5 통과 |

API 클래스의 AST를 검사한 결과 로컬 요금 함수 호출은 0건이다. 기존 docs/history의 모든 파일은 v2.4 ZIP과 바이트가 같음을 확인했다. 명세의 금지 이름 검색과 직접 DB 의존 검색 결과:

```text
이름 검색: 출력 없음 (0줄)
직접 DB 의존 검색: 출력 없음 (0줄)
ApiAdapter 내부 로컬 요금 함수 호출: 0건
```

## 기존 테스트 보존과 지시서 충돌

기존 테스트는 식별자 치환 외에 test_backend_wire.py만 수정했다. 이 파일의 구 서버 가짜 응답을 새 팀 형식으로 바꾸고 구 결제 본문 검사를 세 필드 계약으로 갱신했다. B 빈자리 0, 인증 전달, 차량 없음, 동일 키 결제 결과 동일이라는 기존 의도는 유지했다. 사용자 답변 기대값은 바꾸지 않았다.

두 항목은 동시에 만족할 수 없어 다음과 같이 처리했다.

1. 기존 목업 LLM 테스트는 사용자가 말하지 않은 번호를 분류 결과에서 허용하도록 기대한다. 그 기대값은 유지하고 API 모드에서는 추정 번호를 버린다. 전체 모드에서 제거하려면 기존 분류 명세를 팀이 먼저 결정해야 한다.
2. 기존 차량 입력 범위는 01~24다. 따라서 엔진의 99번 질문은 API를 부르기 전에 범위 오류를 안내한다. 가짜 서버에서는 연결부의 99번 조회가 차량 없음으로 매핑됨을 별도 검사했다.

## 연동 스크립트 관찰

tools/api_smoke.py는 질문·답변·경로·HTTP 상태를 표로 출력하고, 빈자리 답변의 카드 값과 같은 시점의 주차장 응답 숫자를 비교한다.

| 질문 | 가짜 서버 실행 관찰 | 팀 실서버 |
| --- | --- | --- |
| A 주차장 빈자리 몇 개야? | 1자리·확인 중 1칸·시연 문구, 서버 숫자 일치 | 미확인 |
| 01번 차 어디 있어? | A 주차장 1번 칸·정확한 선행 0 경로 | 미확인 |
| 99번 차 어디 있어? | 01~24 범위 안내, API 호출 없음 | 동일 입력 검증 |
| 01번 차 요금 얼마야? | 세션 null로 확인 불가, 세션 요금 호출 없음 | 미확인 |

현재 환경의 localhost:8000 연결은 실패했다. 팀 저장소와 main.py·billing 계산기가 제공되지 않아 라우터 등록·실제 DB 조회·실서버 금액은 확인하지 못했다. 서버 구현 여부·예정일은 첨부 지시서에 따른 정보다. Windows 실기기도 실행하지 않았다. UTF-8 파일 입출력과 subprocess 설정을 유지했다.

## 사용자 PC 적용

브랜치: feature/KAN-9-chatbot-api-align. 범위는 backend/app/chatbot/parking-chatbot/뿐이다.

1. 기존 v2.4 ZIP을 반영하고 검사 후 커밋 1.
2. 함께 제공한 step2 ZIP으로 덮어쓰고 검사 후 커밋 2.
3. 최종 v2.5 ZIP으로 덮어쓰고 검사 후 커밋 3.

검증 원시 덤프와 테스트 가상환경은 ZIP·커밋에서 제외했다. 과거 history 자료는 삭제하지 않았으며 새 덤프를 추가하지 않았다. 지시서의 11/04 목업 요금 변경은 별도 커밋 4로 남겼다. 담당자에게 전달할 요청서는 change_requests_api.md, 정리 제안은 followup_plan.md에 있다.

## PR 본문 초안

Jira: KAN-9 (팀 Jira 링크 입력). 제목: 챗봇 v2.4 반영 및 팀 API 소비 계약 정렬.

팀 API와 경로·필드·결제 본문이 다른 문제를 소비자 측에서 수정했다. 서버 API 변경 없음(소비자 측 수정). 수정 영역은 챗봇 폴더 내부다. 목업 요금 정책과 기존 답변 기대값은 유지했다.

mock: python server_dev.py. api: DATA_SOURCE=api, BACKEND_BASE_URL을 팀 서버로 지정하고 tools/api_smoke.py 실행. 위 연동 표는 가짜 서버 결과이며 실서버 결과는 사용자 PC 실행 뒤 첨부한다.

검증: LangChain 설치 344/344, 미설치 342 통과·2 건너뜀, 질문 242/242, 브라우저 30/30. 대기: KAN-28 요금·결제 라우터 등록, 담당자 API 요청 3건, 11/05 실서버 재확인. 롤백은 git revert <커밋3>부터 역순으로 적용한다. Push 뒤 amend 및 Merge는 하지 않는다.

## 커밋별 파일 목록

커밋 1은 v2.4 전체 기준본(과거 원시 덤프 신규 커밋 제외)이다. 아래 2는 필드명 통일, 3은 API 계약 정렬이다. 같은 파일이 양쪽에 있으면 step2에서 먼저 이름 변경만 커밋한다.

| 파일 | 커밋 2 | 커밋 3 |
| --- | --- | --- |
| .gitignore |  | 반영 |
| README.md |  | 반영 |
| chatbot/adapters.py | 반영 | 반영 |
| chatbot/api_paths.json | 반영 | 반영 |
| chatbot/briefing.py | 반영 |  |
| chatbot/contracts.py | 반영 | 반영 |
| chatbot/engine.py | 반영 | 반영 |
| chatbot/incidents.py | 반영 |  |
| chatbot/llm.py | 반영 |  |
| chatbot/llm_router.py | 반영 |  |
| chatbot/parser.py | 반영 | 반영 |
| chatbot/state.py | 반영 |  |
| chatbot/templates.py | 반영 | 반영 |
| chatbot/transport.py |  | 반영 |
| docs/api_alignment_report.md |  | 반영 |
| docs/api_contract.md | 반영 | 반영 |
| docs/change_requests_api.md |  | 반영 |
| docs/chatbot_guidelines_v2.md | 반영 |  |
| docs/followup_plan.md |  | 반영 |
| server_dev.py | 반영 |  |
| server_fastapi.py | 반영 |  |
| static/admin.html | 반영 |  |
| static/index.html | 반영 |  |
| tests/browser_audit_fixes.cjs | 반영 |  |
| tests/browser_checks.cjs | 반영 |  |
| tests/browser_stability.cjs | 반영 |  |
| tests/llm_eval_questions.csv | 반영 |  |
| tests/questions.csv | 반영 |  |
| tests/questions_extended.csv | 반영 |  |
| tests/questions_holdout.csv | 반영 |  |
| tests/questions_navigation_dev.csv | 반영 |  |
| tests/questions_navigation_holdout.csv | 반영 |  |
| tests/questions_realistic.csv | 반영 |  |
| tests/questions_rehearsal.csv | 반영 |  |
| tests/questions_stability.csv | 반영 |  |
| tests/run_eval.py | 반영 |  |
| tests/run_llm_compare.py | 반영 |  |
| tests/run_optimization_benchmark.py | 반영 |  |
| tests/run_optimization_soak.py | 반영 |  |
| tests/test_api_contract.py |  | 반영 |
| tests/test_audit_fixes.py | 반영 |  |
| tests/test_backend_wire.py | 반영 | 반영 |
| tests/test_claude_round2.py | 반영 |  |
| tests/test_extended.py | 반영 |  |
| tests/test_final_cleanup.py | 반영 |  |
| tests/test_integrated_v3.py | 반영 |  |
| tests/test_llm_prompt.py | 반영 |  |
| tests/test_llm_router.py | 반영 |  |
| tests/test_navigation.py | 반영 |  |
| tests/test_navigation_conflicts.py | 반영 |  |
| tests/test_optimization.py | 반영 |  |
| tests/test_realistic.py | 반영 |  |
| tests/test_regressions.py | 반영 |  |
| tests/test_reliability.py | 반영 |  |
| tests/test_review_fixes.py | 반영 |  |
| tests/test_stability.py | 반영 |  |
| tests/test_supplement.py | 반영 |  |
| tests/test_ux.py | 반영 |  |
| tests/test_v2_2.py | 반영 |  |
| tools/api_smoke.py |  | 반영 |
