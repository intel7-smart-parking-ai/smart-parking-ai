# v2.14 변경 요약 — 11/06 시연 준비

작성일: 2026-11-04. 기준은 공개 팀 레포 main `1ca963e30b0b649e7e6122726691098893ff10b6`이다. 첨부 ZIP 대신 레포 main을 직접 받아 확인했고 첨부본과 같은 기준 커밋이었다. `chore/progress-status`의 D-20~D-24도 함께 확인했다. 수정 범위는 `backend/app/chatbot/` 안으로 제한했다.

**A~E의 구현·검증을 수행했다. 다만 전체 테스트가 전부 통과한 상태는 아니다.** 새 CSV 추가 지시와 기존 테스트 기대값 유지 지시가 충돌해, 질문 파일 수를 고정한 기존 검사 2건은 실패 상태로 남겼다. 기대값·기존 CSV·skip 조건을 바꾸거나 파일을 숨겨 통과시키지 않았다. 아래 결과를 확인하고 이 두 검사에 대한 예외를 결정해야 한다.

## 1. 범위별 결과

| 항목 | 변경 파일 | 새 검사·증거 | 결과 |
|---|---|---|---|
| A 경보 404 | `chatbot/contracts.py`, `state.py`, `engine.py`, `navigation.py`, `templates.py`, `http_router.py`, `server_dev.py`, `static/admin.html` | `tests/test_v214_alert_404.py` 6건. 기존 API 계약 가짜 서버를 조합해 사용하며 부모 테스트를 상속하지 않음 | 404와 장애 구분, 연속 404에서 세션별 안내 한 번, 정상 빈자리 형식·기존 칩 복원, 마지막 Lv2 유지 |
| B 앱 등록 | `tests/test_v210_embed.py`의 허용된 한 메서드, `tests/test_v214_team_apps.py` | 기존 embed 10건과 새 앱 검사 4건. FastAPI 0.110.3·0.162.0 | 실제 POST 200으로 등록 확인, main·demo·미리 실행 팩토리 및 두 앱의 공유 런타임 확인 |
| C 시연 | `docs/demo_script_1106.md`, `tools/rehearsal_1106.py`, `tests/test_v214_rehearsal.py`, `tests/questions_demo_1106.csv` | 실제 `team_preview.create_demo_app` + 팀 `run_demo(short=True, hold=True)` + HTTP 8회(여섯 질문과 확인 두 번) | 리허설 6/6, 신규 규칙 질문 6/6 |
| D OS 점검 | 기존 코드 변경 없음 | 오류 문구·경로·줄바꿈·인코딩·수신 포트 사용 점검, 기존 환경 검사 실행 | 보고된 Windows 문구 차이·고정 포트 충돌은 특정/재현하지 못함. 아래 한계 명시 |
| E 아이디어 | `docs/ideas_d24.md` | 현재 추천·목적지 안내를 직접 실행한 답 예시와 두 표 | 문서만 작성, 기능 구현 없음 |

## 2. A — 재현과 허용한 API 차이

### 재현에서 확인한 원인

**같은 세션에서 본문 끝 안내가 매번 붙는 현상은 재현되지 않았다. `/updates`를 사이에 호출해도 기존 본문 안내는 1회였다. 실제 확인한 문제는 404를 장애와 동일하게 처리해 빈자리 형식·관리자 호출 칩·경보 배너가 계속 표시되는 것이었다.** 화면이 세션 ID를 매 요청 새로 만드는지는 실제 ChatPanel 리허설에서 확인해야 하며, 원인으로 확정하지 않았다.

`/alerts`가 404인 복제 서버에서 같은 세션으로 아래 세 질문을 보냈다. 폴링 없음/질문 사이 `/updates` 있음 모두 같은 결과였다. 표는 폴링 있음의 답 전문이다.

| 질문 | 수정 전 text | 수정 후 text | 수정 전 칩 ID | 수정 후 칩 ID |
|---|---|---|---|---|
| A 빈자리 | 개발원주차장 빈자리 1자리, 확인 중 1칸이에요.<br>경보 상태를 확인할 수 없어요.<br>※ 시연용 데이터예요 | 개발원주차장에 1자리 있어요. (카메라 확인 중 1칸)<br>경보 상태를 확인할 수 없어요.<br>※ 시연용 데이터예요 | empty_all, call_admin | go:A, empty_all |
| B 빈자리 | 부산역주차장 빈자리 1자리, 확인 중 1칸이에요.<br>※ 시연용 데이터예요 | 부산역주차장에 1자리 있어요. (카메라 확인 중 1칸)<br>※ 시연용 데이터예요 | empty_all, call_admin | go:B, empty_all |
| 전체 빈자리 | 개발원주차장 빈자리 1자리, 확인 중 1칸 / 부산역주차장 빈자리 1자리, 확인 중 1칸 / 북항주차장 빈자리 1자리, 확인 중 1칸이에요.<br>※ 시연용 데이터예요 | 개발원주차장에 1자리 있어요. (카메라 확인 중: 개발원주차장 1칸, 부산역주차장 1칸, 북항주차장 1칸)<br>※ 시연용 데이터예요 | empty_all, call_admin | go:A, go:B, zone:ev, zone:accessible |

경보가 정상이고 경보가 없는 경우와 404 경우의 빈자리 형식·칩은 동일하다. 404에서만 첫 안내가 한 번 붙는다. 경보 API 없음은 경보 없음으로 해석하지 않는다. 기존 Lv2를 확인한 뒤 404가 오면 대피 안전 정보와 배너를 유지한다. 새 세션에는 안내가 새로 한 번 나온다. `경보 알려줘`처럼 직접 묻는 질문은 매번 기존 `ALERT_UNAVAILABLE`로 답한다.

| 상태 | 수정 전 → 후 |
|---|---|
| 404, 마지막 활성 경보 없음 | 장애용 빈자리 형식·관리자 칩·불가 배너 → 기존 정상 빈자리 형식·기존 선택 칩, 불가 배너 생략. 안전 상태를 단정하는 문구 추가 없음 |
| 404, 마지막 Lv2 존재 | Lv2 보존 → Lv2 보존. 입장·결제 정책 유지 |
| 404 ↔ 장애 | 구분 없는 불가 구간 → 출처가 바뀌면 새 불가 구간으로 판단하고 기존 안내 재사용 |
| 404 → 200 | 경보 없음/새 Lv2 등 실제 서버 결과로 자동 복귀 |
| 통계·관리자 | `alerts_source` 키 추가. `ok`, `not_provided`, `unavailable`. `/stats`와 기존 `/incidents` 응답에 추가하고 관리자 화면에 별도 표시 |

새 관리자 표시 문구 `경보 API 미구현`·`경보 상태 확인 불가`는 미구현과 장애를 구분해 보이라는 지시에 따른 관리자 상태명이다. 사용자 답에는 기존 문구만 재사용했다. stats는 최근 경보 스냅샷을 사용하며 추가 API 조회를 만들지 않는다.

### 장애 503·연결 실패/시간 초과 결과 0 비교

기준 코드와 수정 코드에서 같은 세션의 세 질문과 중간 `/updates`를 각각 실행했다. HTTP 503과 실패 결과 0 모두 `text/chips/flow` 차이 0이었다. 아래는 두 코드에서 동일한 답 전문이다. 실제 transport TimeoutError·연결 오류 처리는 기존 검사를 통해서도 확인했다.

| 질문 | 수정 전·후 동일한 text | 수정 전·후 동일한 칩 ID | 판정 |
|---|---|---|---|
| A 빈자리 | 개발원주차장 빈자리 1자리, 확인 중 1칸이에요.<br>경보 상태를 확인할 수 없어요.<br>※ 시연용 데이터예요 | empty_all, call_admin | 전후 동일 |
| B 빈자리 | 부산역주차장 빈자리 1자리, 확인 중 1칸이에요.<br>※ 시연용 데이터예요 | empty_all, call_admin | 전후 동일 |
| 전체 빈자리 | 개발원주차장 빈자리 1자리, 확인 중 1칸 / 부산역주차장 빈자리 1자리, 확인 중 1칸 / 북항주차장 빈자리 1자리, 확인 중 1칸이에요.<br>※ 시연용 데이터예요 | empty_all, call_admin | 전후 동일 |

## 3. B — FastAPI와 앱 두 개

| 구분 | 수정 전 | 수정 후 |
|---|---|---|
| 기존 embed 등록 검사 | `app.routes`의 모든 항목에 `.path`가 있다고 가정. FastAPI 0.162.0에서 `_IncludedRouter` AttributeError로 1건 실패 | mock 모드 실제 `POST /api/v1/chat` 200과 비어 있지 않은 text 확인. 등록·응답이라는 검사의 의미 유지 |
| 새 검사 실행 격리 | 없음 | main, demo, 두 앱, preview 각각 별도 subprocess. UTF-8 환경·출력 명시 |
| 두 앱 | 없음 | 동일 프로세스에서 main과 demo의 TestClient를 동시에 열어 양쪽 POST 200·같은 runtime_id 확인 |
| 공유 상태·상한 | 없음 | LLM 슬롯과 LOG 객체 동일, LLM 준비·실행 없음, 앱별 HTTP 처리 상한의 토큰 수·반납 상태 동일, 요청당 기록 1건 증가·기록 maxlen 유지 |

| FastAPI | 기존 embed | 새 팀 앱 검사 | 결과 |
|---|---:|---:|---|
| 0.110.3 (Starlette 0.37.2, httpx 0.27.2) | 10/10 | 4/4 | 통과 |
| 0.162.0 | 전체 검사에서 해당 10건 통과 | 4/4 | 해당 검사 통과. 전체의 별도 CSV 개수 충돌은 아래 참조 |

새 검사의 초안에서 HTTP 라이브러리의 모든 스레드가 즉시 사라진다고 검사한 부분은 0.110에서 종료 시차로 불안정했다. 챗봇 LLM 스레드·준비 상태·공유 슬롯·상한·기록을 직접 확인하도록 고쳤다. 이 변경 후 새 검사 4건을 두 FastAPI 환경에서 각각 다시 실행해 모두 통과했다. 운영 코드는 이 이유로 변경하지 않았다. 실제 LLM API 호출은 없었고 앱 등록 검사는 `LLM_PROVIDER=none`으로 실행했다.

## 4. C — 실제 demo_main 자동 리허설

대본: `docs/demo_script_1106.md`. PowerShell로 포트 8002에 미리 실행 팩토리를 띄우는 방법, 점유 유지·출차·다음 회차 초기화·오류 대응을 적었다. 자동 도구는 고정 포트를 쓰지 않고 바인딩한 빈 소켓을 서버가 그대로 넘겨받는다. 임의 토큰, 임시 DB, 합성 관측 유지 스레드를 사용하며 종료 시 정리한다. 팀 시나리오 코드는 import만 했다.

아래는 실제 도구 실행에서 출력된 답 전문이다. 금액은 해당 실행에서 팀 billing API가 계산한 값이며, 소급 입차나 임의 금액 조작은 하지 않았다.

| 순서·입력 | 답 전문 | 결과 |
|---|---|---|
| ① A 빈자리 있어? | 개발원주차장에 9자리 있어요.<br>경보 상태를 확인할 수 없어요.<br>※ 시연용 데이터예요 | PASS. 서버 빈자리 수와 카드 비교 |
| ② 10번 차 어디 있어? | 10번 차 맞으세요? | PASS. 차량 확인 단계 |
| ② 네 | 10번 차는 개발원주차장 A-01에 있어요.<br>※ 시연용 데이터예요 | PASS |
| ③ 요금 얼마야? | 현재 요금은 1,000원이에요. | PASS. 견적 포함 |
| ④ 결제해 줘 | 10번 차 요금은 1,000원이에요. 이 금액으로 모의 결제할까요? | PASS. 결제 확인 단계 |
| ④ 네 | 1,000원 결제됐어요. 출구 개방 여부는 현장에서 확인해 주세요. (모의 결제) | PASS. 확인 금액과 영수증 금액 동일 |
| ⑤ 요금표 알려줘 | 주차장마다 요금이 달라요. 개발원주차장 기본 1,000원 / 부산역주차장 기본 2,000원 / 북항주차장 기본 3,000원 / 신세계백화점주차장 기본 4,000원 (기본 30분). | PASS |
| ⑥ 7번 차 어디야? | 차량 번호는 두 자리로 말씀해 주세요 (예: 27). | PASS. 임의 07 조회 없음 |

재실행: 레포 루트에서 `python backend/app/chatbot/parking-chatbot/tools/rehearsal_1106.py`. 성공 종료 코드 0, 질문 실패 시 1. 팀 레포나 HTTP 의존성이 없는 별도 폴더에서는 한 줄 안내 후 종료한다.

## 5. D — OS 의존 점검

| 확인 대상 | 관찰 | 처리 |
|---|---|---|
| 운영체제 원문 오류 문자열 비교 | 예외 타입 검사와 앱의 공통 오류 표시를 사용. 포트 점유 검사는 원문 대신 `PORT_UNAVAILABLE` 확인 | 보고된 Windows 문구 1건은 특정하지 못해 기대값 변경 없음 |
| 경로·인코딩 | `test_cp949_console_and_unicode_working_path`가 cp949 콘솔과 한글 임시 경로를 실행. 새 subprocess는 `PYTHONIOENCODING=utf-8`, UTF-8 디코딩 명시 | 관련 기존 검사 통과. Windows 실기기를 실행한 것은 아님 |
| 줄바꿈 | HTTP 원문 검사에서 `\r\n`은 프로토콜 구분자이며 OS 파일 줄바꿈 비교가 아님 | 변경 없음 |
| 테스트 수신 포트 | `robust_support`, `test_api_contract`, `test_audit_fixes`, `test_backend_wire`, `test_runtime_resilience`, `test_regressions`, `test_supplement`, 브라우저 실행 도우미는 포트 0으로 바인딩. 환경·벤치마크 검사도 빈 포트 사용 | 고정 수신 포트 충돌은 재현하지 못함 |
| 코드에 남은 포트 1·9·8000 | 1·9는 실패를 유도하는 목적지, 8000은 설정 기본값 또는 ASGI 테스트의 가상 주소. 고정 포트로 서버를 중복 실행하는 사례와 구분 | 변경 없음 |
| 새 리허설·앱 검사 | 빈 포트 소켓 유지, Path 사용, subprocess UTF-8. 앱 검사에서 라이브러리 스레드 종료 시차를 챗봇 오류로 오인하지 않도록 수정 | 두 FastAPI 버전 확인 |

Windows 실기기와 4조BOT의 실패 테스트 이름·출력이 없어 원래 제보는 **재현 불가**다. 빈 포트를 찾고 닫은 뒤 다시 여는 일부 기존 테스트의 순간적인 다른 프로세스 선점 가능성까지 제거했다고 주장하지 않는다. 실제 재현 증거가 없어 이번 범위에서 일괄 구조 변경하지 않았다.

## 6. E — 문서만 작성

`docs/ideas_d24.md`에 만차 대안 추천과 엘리베이터·출입구 가까운 빈자리 두 표를 작성했다. 기존 `navigation.alternatives/reason/choose/monitor`와 `lots.json`의 시설 정보, 실제 실행 답, 추가 데이터·담당자·예상 시간·위험을 구분했다. 거리·소요 시간을 만들어 내지 않으며, 칸별 시설 순위는 제안 필드로만 남겼다.

## 7. 최종 검증 결과

Python 3.12/Linux에서 실행. 선택 의존성 설치 환경은 `requirements-llm.txt`의 langchain-core 1.7.3, langchain-openai 1.7.3, langchain-anthropic 1.7.7이다. 미설치 환경은 이 세 패키지가 없는 별도 PYTHONPATH로 실행했다. 실제 LLM·OpenRouter 요청은 하지 않았다.

| 검사 | 결과 | 비고 |
|---|---|---|
| 챗봇 전체 — 선택 LangChain 설치 | 실행 488: 통과 484 / 실패 2 / skip 2 | 실패는 아래 CSV 고정 수 검사만. 기존 skip 유지 |
| 챗봇 전체 — LangChain 미설치 | 실행 488: 통과 481 / 실패 2 / skip 5 | 동일한 고정 수 실패. 선택 패키지 검사 skip 추가 발생, skip 코드는 수정하지 않음 |
| `RUN_SLOW=1` robust 검사 | 30/30 통과 | 명시적 soak·동시성·환경 검사 포함 |
| 질문 세트 | 9세트 248/248, 100% | 기존 8세트 242개 + 새 6개. 평균 0.27ms는 이 환경의 규칙 파싱 측정으로 실제 API/LLM 지연을 의미하지 않음 |
| 기존 mock 응답 스냅샷 | 242/242 동일, 차이 0 | text/card/chips/flow 비교. 시간·토큰 등 변동 필드 제외. 새 CSV는 기준본에 없으므로 이 비교에서 제외하고 위 질문 검사에 포함 |
| 기존 파서 기준 스냅샷 | 전후 차이 0 | 기존 질문과 별도 LLM 평가 질문의 출력 비교 |
| API 장애 응답 비교 | 503·결과 0 각각 세 질문, 전후 차이 0 | text/chips/flow와 `/updates` 교차 호출. 허용한 404 차이는 2절 참조 |
| 브라우저 | 12개 스크립트 모두 통과 | `audit_fixes`, `checks`, `final`, `integrated_v3`, `navigation`, `optimization`, `robust_poll`, `robustness`, `stability`, `supplement`, `v28`, `v2_2`. 마지막은 지정된 `run_v2_2_browser.py`로 실행 |
| API smoke | 성공 종료 0 | 실제 팀 main 팩토리 목업 서버를 빈 포트로 실행. 빈자리 서버 숫자 일치, 404·한 자리 번호·차량 조회 확인. main 목업에서 요금 세션 미제공도 그대로 노출 |
| 실제 demo 리허설 | 6/6 | 차량 확인·견적·모의 결제까지 실제 demo_main HTTP 연결 |
| 팀 루트 pytest | 322/322 통과 | `python -m pytest tests -q`. Starlette/httpx 사용 중단 예정 경고 1건, 실패 없음 |
| FastAPI 0.110/0.162 새 앱 검사 | 각각 4/4 통과 | 전체 검증 뒤 새 검사 보완에 대한 관련 검사만 재실행 |
| 공개 계약·고정 설정 | 변경 없음 | `public_contract_v210.json`, `.env.example`, `lots.json`, `chips.json`, `api_paths.json`, router 접점 유지 |

### 기존 기대값을 보존해 남은 실패 2건

| 기존 테스트 | 기존 기대값 | 새 파일 포함 실제값 | 원인·다음 결정 |
|---|---|---|---|
| `test_integrated_v3.LLMEvaluationExpansionTests.test_regression_glob_contains_eight_sets_and_no_llm_set` | CSV 8개, 질문 242개 | CSV 9개, 질문 248개 | `questions_demo_1106.csv` 추가와 고정 수 기대가 충돌. 기대값 갱신은 별도 허용 필요 |
| `test_v2_2.EvaluationFilenameTests.test_glob_selects_exactly_seven_existing_question_sets` | rehearsal 제외 CSV 7개, 질문 237개 | rehearsal 제외 CSV 8개, 질문 243개 | 같은 원인. 기존 CSV·테스트 기대값·skip을 보존함 |

각 테스트는 첫 파일 수 assert에서 실패하므로 뒤의 질문 수 assert까지 실행되지 않는다. 위 질문 수는 CSV 직접 집계 결과다. 실패를 프로그램 응답 오류로 보지는 않지만, **전체 테스트 통과/병합 준비 완료로 보고하지 않는다.** 새 CSV 파일명을 변경하거나 평가 대상에서 숨기는 방법도 사용하지 않았다.

## 8. 바꾸지 않은 것·남은 일

| 대상 | 결정·이유 |
|---|---|
| `main.py`, `demo_main.py`, 팀 시나리오·프런트·nginx | 수정 금지 범위라 import·실행 검증만. 라우터 실제 등록은 팀 담당자 작업 |
| 잠금·결제 직렬화·캐시 세대·알림 cursor·Lv2 정책 | 구조 변경 없음. 기존 안정성 처리 유지 |
| 기존 응답 필드·API 경로·카드 type·칩 ID·설정 키 | 삭제·변경 없음. 경보 출처 상태만 허용된 추가 |
| 엔진 분리·죽은 코드 정리·옛 문서 이동 | 이번 다섯 항목 밖이므로 하지 않음 |
| 실제 LLM·Windows·팀 화면·entry_at 실서버 | 키·실기기·담당자 연동이 필요한 사람 작업. 자동으로 검증했다고 표기하지 않음 |
| 패키지 | `chatbot/router.py`, `chatbot/team_preview.py`, `chatbot/parking-chatbot/` 포함. .env·키·캐시·.bundle·.validation·원시 로그 덤프 제외. .env.example 유지. 기존 소스의 과거 로그 덤프는 소스에서 삭제하지 않고 ZIP에서만 제외 |

### 사람 할 일

- [ ] 박해준님 `demo_main` 등록 후 같이 실행 확인 (`DATA_SOURCE=api`, `BACKEND_BASE_URL=http://127.0.0.1:8002`).
- [ ] 김채린님과 화면 연결 리허설 (`ChatPanel` → `POST /api/v1/chat` → 답 표시), 칩 버튼 표시 여부 합의.
- [ ] D-18 OpenRouter 실측: `parking-chatbot/.env`에 키 → `python tests/run_llm_compare.py --compare` → 도구 호출 여부·정답률·3초 안 응답 PM 보고. 부적합하면 모델 이름만 바꿔 재측정.
- [ ] D-21 `entry_at`이 칸 응답에 들어오면 후보 안내 실서버 확인.

다음에 할 것: CSV 고정 수 검사 2건의 기대값 갱신 허용 여부를 먼저 정하고, 새 범위 배정 전에는 추가 리팩터링을 하지 않는다.

## 9. 반영 안내

ZIP의 `chatbot/`을 팀 레포의 `backend/app/chatbot/`에 반영한다. 개발 브랜치는 `feature/KAN-9-chatbot-demo-1106`이다. 커밋은 A / B / C / E / 보고서로 나눴으며 변경 없는 D는 별도 커밋을 생략했다. Git 변경 목록에서 챗봇 폴더 밖 변경이 없는지 확인했다. 새 PR·병합·push는 하지 않았다.

```powershell
git switch -c feature/KAN-9-chatbot-demo-1106
git add backend/app/chatbot
git diff --cached --stat
```

위 테스트 충돌을 팀에서 결정한 뒤 사람이 PR을 올리고 김한별님이 병합한다.
