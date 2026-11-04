# EPIC06 주차장 안내 챗봇 v2.12 — 팀 저장소 연동 수정본

최신 변경·검증 결과는 [팀 저장소 연동 확인](docs/team_repo_alignment.md)과 [v2.12 변경 요약](docs/v212_change_summary.md)을 참고하세요. 팀 레포: https://github.com/intel7-smart-parking-ai/smart-parking-ai

v2.5 API 계약을 유지하며 재현된 오류와 긴 답변 표시를 수정했습니다. [최신 안정성 결과](docs/robustness_report.md)를 확인하세요. [API 계약 반영 기록](docs/api_alignment_report.md)을 확인하세요. [정리 결과](docs/refactor_report.md), [챗봇 작업지침서 v2](docs/chatbot_guidelines_v2.md), [이전 결과](docs/history/integrated_v3_report.md), [기록 보관함](docs/history/README.md)을 참고하세요.

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

## 선택 LangChain 라우터

기본 `LLM_PROVIDER=none`에서는 설치·키 없이 버튼과 규칙으로 동작합니다. LLM 사용 시 Python 3.11 이상 환경에서 다음을 설치합니다.

```bash
python -m pip install -r requirements-llm.txt
```

`.env.example`을 `.env`로 복사한 뒤 `LLM_ENGINE=langchain`, `LLM_PROVIDER`, `LLM_MODEL`을 설정하고 해당 제공자의 키를 `.env`에만 넣습니다.

| 제공자 | `LLM_PROVIDER` | 키 변수 | 비고 |
|---|---|---|---|
| OpenRouter (팀 결정 D-18) | `openrouter` | `OPENROUTER_API_KEY` | `LLM_MODEL=google/gemma-4-26b-a4b-it`. 주소는 `https://openrouter.ai/api/v1` 고정. 키마다 OpenRouter 사용 한도 설정 |
| OpenAI | `openai` | `OPENAI_API_KEY` | |
| Anthropic | `anthropic` | `ANTHROPIC_API_KEY` | |

모델이 도구 호출을 지원해야 합니다. 실측은 키를 넣은 뒤 `python tests/run_llm_compare.py --compare`로 합니다(실제 API 비용 발생, 규칙이 못 알아듣는 문장 35개). 도구 선택 정답률과 3초 이내 응답을 보고, 부적합하면 OpenRouter 안에서 `LLM_MODEL`만 바꿔 다시 잽니다.

LLM이 관리자 호출을 선택하면 “관리자를 부를까요?”와 `[관리자 호출]` 칩을 보여줍니다. 이 단계에서는 접수하지 않으며, 칩 클릭 또는 명확한 규칙 요청이 들어오면 접수합니다.

첫 분류 요청에서 모델을 백그라운드로 준비합니다. 준비 전·미설치·키 없음·오류·3초 시간 초과 시 기존 안내로 대체합니다. Lv2에서는 LLM을 부르지 않습니다. 모델은 도구 하나와 인자만 선택하며 숫자·답변·업무 실행은 기존 엔진이 담당합니다. 동시에 최대 2개만 분류하며, 시간 초과된 작업도 실제 종료 전에는 슬롯을 점유합니다.

`LLM_ENGINE=direct`로 기존 직접 호출 분류기에 되돌릴 수 있습니다. 새 초기화·작업 상한·실패 카운터는 LangChain 경로의 기능입니다. 관리자 권한의 `/api/v1/chat/stats` 응답 `llm` 필드 또는 `chatbot.llm_router.stats()`에서 이유별 카운터·최근 지연·준비 상태를 확인합니다. 별도 화면은 추가하지 않았습니다.

규칙이 모르는 문장 35개로 준비한 비교 도구 (`tests/llm_eval_questions.csv`). 규칙 수정 대상 리허설 5문장은 `tests/questions_rehearsal.csv`에 따로 둡니다.

```bash
python tests/run_llm_compare.py
python tests/run_llm_compare.py --compare
```

두 번째 명령은 LLM을 활성화하고 키를 설정한 경우 실제 API 비용이 발생할 수 있습니다. 키가 없으면 비교를 건너뜁니다. 현재 배포본은 실제 모델 정답률을 측정한 결과가 아닙니다.

## 검사

```bash
python -m unittest discover -s tests
python tests/run_eval.py tests/questions.csv tests/questions_extended.csv tests/questions_holdout.csv tests/questions_realistic.csv tests/questions_stability.csv tests/questions_navigation_dev.csv tests/questions_navigation_holdout.csv tests/questions_rehearsal.csv
python tests/run_optimization_soak.py
python tests/run_browser_checks.py browser_supplement.cjs
```

FastAPI/httpx가 없으면 해당 검사는 건너뜁니다. 브라우저 검사는 Node·Playwright·Chromium이 필요하며 필요 시 `BROWSER_EXECUTABLE`을 지정합니다. 이번 정리 전후 결과는 `docs/refactor_report.md`에 있습니다. 과거 단계별 원시 기록은 `docs/history/`에 있습니다.

상태는 단일 프로세스 메모리에 저장되며 재시작 복원은 없습니다. 실제 팀 API·DB·카메라·차단기·유료 LLM·Windows 실행은 이번 로컬 검증 범위 밖입니다. 과거 문서는 변경 이력이며 현재 범위는 위 가이드라인과 결과 보고서가 기준입니다.

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
