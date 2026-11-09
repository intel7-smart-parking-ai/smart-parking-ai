# EPIC06 주차장 안내 챗봇 v3.3

v3.3은 v3.2에 구조화(경로 기록 `meta.route`, 코드 계획표, 역할별 지시문 — [구조](docs/structure.md)), 문장 해석 다각화(모델에 후보 해석 전달, 모델 실패 시 문장에 맞는 규칙 답 선택, 주차장 성격 기반 추천), 안전·허용 여부·없는 칸 처리(김채린 `turn_policy.py` 이식, D-05 기준으로 수정), 새 대화의 지난 경보 말풍선 제거, Langfuse 전송 범위 수정과 답 검토 고리를 더했다. [v3.3 변경 요약](docs/v33_change_summary.md), [v3.3 후보 검토](docs/v33_review.md), [v3.2 변경 요약](docs/v32_change_summary.md).

v3.1은 v3.0.1의 구조(대화 에이전트 1개 + 읽기 도구 + 업무 흐름 실행)를 유지하면서, 모델이 여러 자료를 묶어 쓴 답이 실제 화면까지 나가도록 막힌 곳을 풀었다. 차이·경과 시간·예상 요금은 코드가 계산해 도구로 주고, 근거 검사는 거짓 실행·약속·엉뚱한 숫자를 막으며, 걸린 답은 이유와 함께 한 번 고쳐 쓰게 한다. Langfuse는 모니터링(선택, 기본 꺼짐)으로만 연결했다. 변경 내용은 [v3.1 변경 요약](docs/v31_change_summary.md), 설정은 [설정값 넣는 법 6장](docs/settings_guide.md), Langfuse 켜는 법은 [에이전트 실행 안내](docs/agent_runbook.md)에 있다.


일반 문장은 LangChain 대화 에이전트가 최근 대화와 함께 읽고, 내부 조회 도구에서 받은 결과로 답합니다. 빈자리·기본요금 비교와 차량 위치·요금 복합 조회를 지원합니다. 명확한 차량번호는 바로 조회하고, 모의 결제는 서버 견적을 보여 준 뒤 승인받습니다. 기존 칩과 업무 흐름은 같은 팀 API를 사용합니다.

팀 Docker에서 모델을 켜는 절차는 [에이전트 실행 안내](docs/agent_runbook.md)에 있습니다. 소스 책임과 데이터 흐름은 [코드 지도](docs/CODEMAP.md), 질문 검수 범위는 [대화 개편 검토](docs/v3_conversation_review.md)를 확인하세요. 이번 패키지의 팀 합의 반영 여부와 시연 PC 확인 항목은 [팀 합의 대조표](docs/team_agreement_review_v301.md)에 있습니다. [v2.16.1 변경 기록](docs/v2161_dialog_context.md)과 [이전 기록](docs/history/README.md)은 별도로 보관합니다.

## 실행

Python 3.10 이상, 기본 모의 데이터와 규칙 파서:

```bash
python server_dev.py
```

이용자 화면은 http://127.0.0.1:8080, 관리자 화면은 http://127.0.0.1:8080/admin.html 입니다.
팀 서버에서는 FastAPI·uvicorn을 설치하고 `uvicorn server_fastapi:app --port 8080 --workers 1`로 실행합니다.
`router`를 팀 FastAPI 앱에 포함할 수도 있습니다. 같은 망 공유는 개발 서버의 `HOST=0.0.0.0`, 관리자·시연 기능 공유는 `ADMIN_TOKEN` 설정이 필요합니다.

## 구현 범위

빈자리·구역 조회, 목적지 선택·변화 안내, 내 차 위치, 요금·모의 결제(견적 확인), 출구 문제 시 현장 관리자 안내, 전용 칸 위반 조회, 관리자 즉시 호출·접수 확인, 모의 화재(LED) 알림·보고서·체크리스트를 제공합니다.

이번 보완:

- 자리가 나면 알려 달라는 질문은 알림 기능이 없음을 밝히고 현재 빈자리를 보여줍니다.
- 이용 현황 질문은 위치 카드로 연결합니다. 요금·영수증·출차 카드 안의 이용 단계는 유지합니다.
- 관리자 문의는 접수까지만 지원합니다. 처리 상황은 현장 관리자에게 확인하도록 안내합니다.
- Lv2는 안에 있는 사람의 출구 이동, 밖에 있는 사람의 재진입 금지, 차량 이동 금지를 안내합니다.
- 관리자 화면에 소방 전달용 현장 정보와 신고 정보 복사 버튼을 제공합니다. 실제 신고·외부 연락은 하지 않습니다.
- 확인 중인 칸은 빈자리와 따로 표시하고, 확정 빈자리만 추천합니다.
- 번호 미인식 차량은 최대 3대의 위치 후보만 보여줍니다. 후보를 차량 번호에 연결하거나 결제하지 않습니다.

## 시연

이용자 화면 우측 테스트 도구에서 주차장을 선택합니다.

1. D 목적지를 선택한 후 만차·운영 제한을 만들어 대안을 확인합니다.
2. 빈 칸 번호를 입력하고 ‘확인 중’을 눌러 빈자리와 분리됨을 확인합니다. ‘확인 완료’로 되돌립니다.
3. ‘번호 미인식 입차’를 누른 뒤 미입차 번호(예: 98)의 위치를 질문하고 후보를 선택합니다. 요금·결제 요청이 차단되는지 확인합니다.
4. 경보를 Lv2로 만들고 이용자 안내와 관리자 현장 정보·복사를 확인합니다.
5. 경보 해제 후 메모·체크리스트를 확인하고 보고서를 확정합니다.

## 설정·연결

`chatbot/lots.json`은 실측 전 PROVISIONAL입니다. 값이 정해지면 이 파일만 고치고 서버를 다시 켜면 됩니다. 항목별 뜻과 값 규칙(설비: null=미확인, true=있음, false=없음, 높이 제한 false=해당 없음), 아직 정해지지 않은 값 목록은 [설정값 넣는 법](docs/settings_guide.md)에 있습니다. 구역 배치를 생략하면 기존 자동 배정을 사용합니다.

`DATA_SOURCE=api`, `BACKEND_BASE_URL`, 필요한 `BACKEND_TOKEN`·`ADMIN_TOKEN`을 설정합니다. 실제 API 경로·응답 변환은 `docs/api_contract.md`와 `chatbot/api_paths.json`을 팀 계약에 맞춰 확정해야 합니다. 실제 운영 연결에서는 `DEMO_ENABLED=0`으로 둡니다.

## 대화 에이전트

팀 Compose에서는 D-18의 OpenRouter 모델 설정을 사용합니다. 팀 Docker로 기동할 때는 [에이전트 실행 안내](docs/agent_runbook.md)의 추가 Compose 파일을 사용합니다. 단독 실행에서는 Python 3.11 이상에서 `requirements-llm.txt`를 설치하고 `.env`의 `LLM_ENGINE=langchain`, `LLM_PROVIDER=openrouter`, `LLM_MODEL`, `OPENROUTER_API_KEY`를 설정합니다.

```bash
python -m pip install -r requirements-llm.txt
```

모델은 이번 턴에 허용된 내부 조회 도구를 호출하고 결과를 받아 답합니다. 후보 범위, 차량 식별자, 금액, 상태를 도구 근거와 대조합니다. 세션의 최근 대화와 후보를 다음 턴에 활용합니다. 모델이 실행 작업을 제안하면 화면의 해당 칩이나 별도 확인 절차로 연결합니다. 칩·비상 안내·승인 처리는 업무 흐름에서 수행합니다.

`LLM_PROVIDER=none` 또는 키가 없는 환경에서는 칩과 규칙 조회가 동작합니다. 모델 연결이 실패하면 가능한 복합 조회는 확인된 자료를 표시합니다. `meta.source=llm`, `meta.agent_status`, `meta.model_calls`와 관리자 통계의 `llm.conversation`으로 실제 대화 경로를 확인할 수 있습니다. `LLM_ENGINE=direct`는 기존 직접 분류기 호환 설정입니다.

기존 분류기 평가 스크립트 `tests/run_llm_compare.py`는 분류기 비교용입니다. 새 대화 경로는 `tests/test_v3_agent_http.py`와 `tests/test_v3_acceptance.py`로 검사합니다. 실제 모델의 응답 품질과 시간은 시연 PC의 모델과 키로 별도 측정합니다.

## 검사

```bash
python -m unittest discover -s tests
python tests/run_eval.py tests/questions.csv tests/questions_extended.csv tests/questions_holdout.csv tests/questions_realistic.csv tests/questions_stability.csv tests/questions_navigation_dev.csv tests/questions_navigation_holdout.csv tests/questions_rehearsal.csv tests/questions_demo_1106.csv
python tests/run_optimization_soak.py
python tests/run_browser_checks.py browser_supplement.cjs
```

FastAPI/httpx가 없으면 해당 검사는 건너뜁니다. 브라우저 검사는 Node·Playwright·Chromium이 필요하며 필요 시 `BROWSER_EXECUTABLE`을 지정합니다. 이번 정리 전후 결과는 `docs/refactor_report.md`에 있습니다. 과거 단계별 원시 기록은 `docs/history/`에 있습니다.

상태는 단일 프로세스 메모리에 저장되며 재시작 복원은 없습니다. v2.15.4에서 격리된 최신 팀 서버의 HTTP 연결·리허설을 확인했으며, 현장 API·DB·카메라·차단기·유료 LLM·Windows 실행은 별도 미검증 범위입니다. 과거 문서는 변경 이력이며 현재 작업은 [구현·검증 원칙](docs/validation_policy.md)과 최신 변경 요약을 따릅니다.

## 팀 API 연결 확인

기준 경로는 [API 계약](docs/api_contract.md)에 있습니다. API 모드에서는 서버 목록·칸 수·요금을 사용하고 연결 실패를 로컬 목업으로 대체하지 않습니다.

```powershell
$env:DATA_SOURCE="api"
$env:BACKEND_BASE_URL="http://127.0.0.1:8000"
python tools/api_smoke.py
```

현재 알려진 서버 제약과 명세 충돌은 [최신 검증 결과](docs/api_alignment_report.md)에 명시했습니다.

기본 검사는 python tests/run_guarded.py, 장기 검사는 RUN_SLOW=1에서 test_robust_*.py 묶음을 실행합니다. [보고서](docs/robustness_report.md)에 실행법과 미확인 범위가 있습니다.

## v2.9 규칙 요약

- 차량 번호는 `"10"`~`"99"` 두 자리(D-06). "7번"처럼 한 자리는 조회하지 않고 "두 자리로 말씀해 주세요"라고 답합니다.
- 가상 차량은 번호판 뒤 4자리(예: `1724`)로 묻지만 검색 경로가 정해지기 전까지 "준비 중"으로 답합니다.
- 칸은 도면 번호로 표시합니다: "개발원주차장 B-06". 주차장 4곳·104칸(A 12, B 16, C 12, D 64). 칸 번호만 말하면 어느 주차장인지 되묻습니다.
- 구역 이름은 `general`/`ev`/`accessible`. 서버 `space_type`·`vehicle_type`이 있으면 우선합니다.
- 결제는 요금 응답의 `quote_id`로만 진행합니다. 견적이 만료·불일치면 새 금액으로 다시 확인받습니다. 요금 응답은 billing 실제 응답(`target_at`, `rate_policy`)을 기준으로 읽습니다.
- `active`/`closed` 세션 상태를 표시합니다(출차 완료 안내).
- 제거: 출차 진단 기능(→ 현장 관리자 안내), 응답 신선도 세부 규칙(`STALE_POLICY`, "N분 전 기준" 표기). 오래된 '경보 없음' 거절·미래 시각 거절·30초 캐시 대체는 유지합니다.
- 관리자 화면은 유지합니다.

## 팀 서버(main.py)에 붙이기 (v2.10)

팀 `backend/app/main.py`에 두 줄만 추가합니다.

```python
from backend.app.chatbot.router import router as chatbot_router
app.include_router(chatbot_router)   # POST /api/v1/chat 등
```

- `backend/app/chatbot/router.py`가 이 폴더를 검색 경로에 넣고 `router`만 내보냅니다(폴더 이름의 `-` 때문에 직접 import 불가).
- 팀 서버 안에서는 기본값이 `DATA_SOURCE=api`, `BACKEND_BASE_URL=http://127.0.0.1:8000`, 시연 경로 꺼짐(`DEMO_ENABLED=0`)입니다. 포트가 다르면 `BACKEND_BASE_URL`을 환경변수로 바꿉니다. 이 폴더의 `.env`나 환경변수 값이 있으면 그것이 우선합니다.
- 화면 연결 필드: [챗봇 API 요청·응답 필드](docs/chat_response_fields.md).
- 혼자 실행(`python server_dev.py`, `uvicorn server_fastapi:app`)은 그대로 모의 데이터가 기본입니다.
- 위치 응답에서 칸이 아직 없거나(`space_id` null) 상태가 `pending`·`entering` 등이면 "N번 차는 입차 확인 중이에요"로 답하고 요금·결제를 진행하지 않습니다.


팀 서버는 워커 1개로 실행하세요. 실행 경계와 운영 설정은 [코드 지도](docs/CODEMAP.md)에서 확인할 수 있습니다.

BACKEND_BASE_URL은 숫자 IP(예: http://127.0.0.1:8002) 권장, Docker에서는 서비스 이름 사용 가능.
