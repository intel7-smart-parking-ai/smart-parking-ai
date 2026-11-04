# EPIC06 v2.13 — OpenRouter(D-18), 경보 시각 필드, demo_main 미리 실행

2026-11-04 KST. 기준은 v2.12(`chatbot-v2.12-team-aligned.zip`)이고, 팀 main `26c3f66`과 `chore/progress-status`의 `docs/DECISIONS.md` D-18을 참고했다. 수정 범위는 `backend/app/chatbot/`뿐이다. 팀 파일(`main.py`, `demo_main.py`, `nginx.conf`, 프런트)은 고치지 않았다.

## 바뀐 것

| 항목 | 이유 | 바뀐 파일 |
|---|---|---|
| OpenRouter 제공자 추가 | D-18: OpenRouter + `google/gemma-4-26b-a4b-it`, 키 변수 `OPENROUTER_API_KEY` | `chatbot/config.py`, `chatbot/llm_router.py`, `chatbot/llm.py`, `tests/run_llm_compare.py`, `.env.example`, `README.md` |
| 경보 시각을 `detected_at`·`occurred_at` 둘 다 받기 | `/alerts` 필드명 미결(화면 `detected_at` / 챗봇 `occurred_at`). 어느 쪽으로 정해져도 동작 | `chatbot/adapters.py`, `docs/api_contract.md` |
| `demo_main` 위에서 챗봇 미리 실행 | 11/06은 `demo_main`(점유→세션→요금) 기준. 박해준님 등록 전에 챗봇 폴더 안에서 같이 띄워 본다 | `team_preview.py` |
| 음성 입력 연결 가이드(문서만) | 김채린님 화면에 음성 인식을 붙이는 작업의 인수인계. 챗봇 코드 변경 없음 | `docs/voice_input_guide_for_frontend.md` |

### OpenRouter 세부
- `LLM_PROVIDER` 허용값에 `openrouter` 추가. 기본값은 계속 `none`(키 없는 팀원·시연 PC는 규칙으로 동작).
- 키 변수 이름 표 `API_KEY_NAMES`: `openrouter → OPENROUTER_API_KEY`, `openai → OPENAI_API_KEY`, `anthropic → ANTHROPIC_API_KEY`.
- LangChain 경로: `ChatOpenAI(base_url="https://openrouter.ai/api/v1", api_key=OPENROUTER_API_KEY)`. 시간 제한·재시도 0·도구 바인딩은 기존과 같다.
- 직접 호출 경로(`LLM_ENGINE=direct`): OpenAI 형식으로 `https://openrouter.ai/api/v1/chat/completions`에 보낸다. 모델마다 지원이 달라 `response_format`은 넣지 않고, 본문의 JSON은 기존 `_extract_json`이 찾는다. OpenAI 요청은 그대로다.
- 측정 스크립트 `tests/run_llm_compare.py`가 제공자별 키 이름을 쓰도록 수정(전에는 openai 외에는 `ANTHROPIC_API_KEY`를 찾아 건너뜀).
- v2.12의 호출 상한(세션 분당 5, 전체 분당 30, 하루 1,000)과 캐시는 제공자와 관계없이 그대로 적용된다.

### 경보 시각
- `occurred_at`이 있으면 그것을, 없으면 `detected_at`을 경보 발생 시각으로 쓴다. 둘 다 없으면 시각 없이 안내한다(기존과 같음).

### demo_main 미리 실행 (팀 레포 루트, PowerShell)
```powershell
$env:BACKEND_BASE_URL = "http://127.0.0.1:8002"
$env:PREVIEW_DEMO_TOKEN = "<ASCII 16자 이상 아무 문자열>"
python -m uvicorn backend.app.chatbot.team_preview:create_demo_app --factory --host 127.0.0.1 --port 8002 --workers 1
```
- 토큰을 비워 두면 시작할 때 터미널에서 입력받는다.
- 다른 터미널에서 `python -m backend.app.simulation.run_parking_demo --short`로 A-01~A-03 입차·출차 시나리오를 보낸다(같은 토큰). 입차 후 점유를 유지하는 `--hold`는 팀 브랜치 `feature/KAN-17-demo-hold-publish`에 있으며, 병합되면 차량 10·11·12번으로 위치→요금→모의 결제까지 확인할 수 있다.
- 포트가 8001로 확정되면 두 값(8002)을 함께 바꾼다.

## 박해준님께 전달할 등록 예시 (팀 파일에는 적용하지 않음)
`demo_main.create_app()`에서 `app`을 만든 뒤, `main.py`라면 `create_app()`에서 `application`을 만든 뒤 다음을 추가한다.
```python
from backend.app.chatbot.router import router as chatbot_router
app.include_router(chatbot_router)   # main.py에서는 application.include_router(chatbot_router)
```

## 검증

| 검사 | 결과 |
|---|---|
| 챗봇 전체 `python -m unittest discover -s tests` (FastAPI·LangChain 미설치 환경) | 477개 실행, 실패 0, 건너뜀 30 (v2.12 467개 + 신규 10개) |
| 신규 `tests/test_v213_openrouter.py` | 키 이름, 설정 시 키 누락 처리, 시작 시 `openrouter` 허용, 직접 호출 주소·헤더·`response_format` 없음, OpenAI 요청 불변. LangChain 모델 생성 검사는 `langchain_openai` 설치 시에만 실행 |
| 신규 `tests/test_v213_alert_time.py` | `detected_at`만 있어도 시각 표시, `occurred_at` 동작 불변, 둘 다 없으면 시각 없이 안내 |
| `run_llm_compare.py --compare` (가짜 키, LangChain 미설치) | 더 이상 건너뛰지 않고 비교 단계로 진입. 실제 API 호출 0 |
| 기존 질문 세트·응답 스냅샷 | 기존 테스트에 포함, 모두 통과(기대값 변경 없음) |

이 환경에서 확인하지 못한 것(사용자 PC에서 확인):
- FastAPI·httpx가 필요한 HTTP 검사와 LangChain 검사(위의 건너뜀 30개)
- `team_preview.create_demo_app` 실제 실행(팀 `demo_main` + FastAPI 필요)
- OpenRouter 실측: 키를 넣고 `python tests/run_llm_compare.py --compare`로 정답률·응답 시간 측정 후 PM 보고

## 남은 일
- `demo_main`(또는 11/10 통합 서버) 챗봇 등록: 박해준님
- `/spaces`의 `entry_at`, `/alerts` 담당·필드: PM·박해준님 결정 대기
- 칩·카드 표시, D 주차장 4구역 표기, nginx 챗봇 GET 경로: 김채린님 (필요 시)
- 시연 PC LLM 키 담당: PM
