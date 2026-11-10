# 시연 서버에서 대화 AI 켜기 (D-44 결정 뒤 설정, 11/10 정리)

대상: 시연 서버를 띄우는 사람(김채린·PM). 키 값은 이 문서·Git·채팅에 적지 않는다(시연 PC 키 담당: PM, D-33).

## 1. 반드시 챗봇 오버레이와 함께 띄운다

팀 기본 이미지(`compose.yaml`만)에는 대화 AI 라이브러리(LangChain)가 **들어 있지 않다.** 이 상태에서는 키를 넣어도 오류 없이 **규칙 답만** 나간다(응답 `meta.route`에 `agent:off`).

```powershell
docker build --target runtime -t smart-parking-api-base:chatbot -f backend/Dockerfile .
docker compose -f compose.yaml -f backend/app/chatbot/parking-chatbot/compose.agent.yaml up -d --build
```

## 2. 루트 `.env`에 넣을 값

| 변수 | 값 | 비고 |
|---|---|---|
| `OPENROUTER_API_KEY` | (PM이 넣음) | 없으면 오버레이가 시작을 막는다 |
| `LLM_MODEL` | D-44 결정대로. Haiku면 `anthropic/claude-haiku-5.5`, 유지면 비워 둠(기본 `google/gemma-4-26b-a4b-it`) | |
| `REQUEST_BUDGET_SEC` | 비워 둠(오버레이 기본 8) | `compose.yaml` 기본은 6 — 오버레이 없이 띄우면 6초가 적용된다 |
| `LLM_PROVIDER` | 비워 둬도 됨 | 오버레이가 `openrouter`로 고정 |
| `MONITOR_BACKEND` | `off` 또는 비워 둠 | Langfuse 무료 한도는 12/8까지 수집 정지 상태 |

compose 파일의 `environment`에 **없는 변수는 `.env`에 적어도 컨테이너에 전달되지 않는다.** 지금 전달되는 챗봇 변수: `LLM_PROVIDER`, `LLM_ENGINE`, `LLM_MODEL`, `LLM_TIMEOUT_SEC`, `BACKEND_TIMEOUT_SEC`, `REQUEST_BUDGET_SEC`, `OPENROUTER_API_KEY`(compose.yaml) + `LLM_ANSWER_TIMEOUT_SEC`, `MONITOR_*`, `LANGFUSE_*`(오버레이).

11/10 저녁부터 오버레이가 함께 전달: `LLM_MAX_PER_SESSION_MIN`(한 대화 1분 12질문, 넘으면 규칙 답), `LLM_MAX_PER_MIN`(서버 전체 1분 60질문), `LLM_MAX_INFLIGHT`(동시 모델 호출 8), `CHAT_MAX_CONCURRENT`(동시 대화 12), `LLM_PREFETCH`. 아직 전달되지 않는 것: `MAX_SESSIONS`(200), `LLM_MAX_PER_DAY`(하루 1000질문 — 리허설을 많이 하는 날은 서버 재시작으로 초기화).

## 3. 켜졌는지 확인

1. 챗봇에 "공항 자리 있어?" → 개발자 도구에서 `POST /api/v1/chat` 응답의 `meta.route`가 `… > agent:ok > agent:answer`, `meta.source`가 `llm`
2. `agent:off` → 오버레이 없이 떴거나 키 없음 / `agent:api_error` → 키 한도·키 오류 / `agent:rate_limited` → 1분 질문 한도
3. 키 한도: OpenRouter Keys 화면에서 키별 Limit 확인(11/09에 $0.50 한도 소진으로 전체가 규칙 답이 된 적 있음)

## 4. 비용·속도 실측 (11/10, 미리 조회 적용 뒤)

| 모델 | AI 답(화면 사례 24턴) | 평균 응답 | 턴당 비용 |
|---|---|---|---|
| Claude Haiku 5.5 | 22/24 | 2.3초 | 약 $0.0009 |
| gemma-4-26b-a4b-it | 21/24 | 1.9초 | 약 $0.0003 |

나머지 2턴은 일부러 코드가 맡는 충전 현황 카드·혼잡 예측이다. 근거: `docs/eval/EVAL_LOG.md`.

## 5. 채린님 PC(`compose.preview.yaml`)에서 켜기 — 11/10 17:00 배포용

`compose.preview.yaml`은 저장소에 없는 로컬 파일이라, 오버레이를 **뒤에** 붙여 함께 지정한다. 봇 PC에서 먼저 같은 조합을 시험한 뒤 적용한다(봇 PC는 `compose.yaml + 오버레이`로 성공).

```powershell
docker build --target runtime -t smart-parking-api-base:chatbot -f backend/Dockerfile .
docker compose -f compose.preview.yaml -f backend/app/chatbot/parking-chatbot/compose.agent.yaml up -d --build
```

확인: 챗봇에 "공항 자리 있어?" → 개발자 도구 `POST /api/v1/chat` 응답 `meta.route`에 `agent:ok`, `meta.prefetched`에 조회한 도구 이름. `agent:off`면 오버레이·키 확인.

## 6. D-44 모델 결정용 한 줄 (11/10 실측, 미리 조회 적용 뒤)

Haiku 5.5: 화면 사례 AI 답 22/24, 평균 2.3초, 턴당 약 $0.0009 · gemma-4-26b: 21/24, 1.9초, 턴당 약 $0.0003. 둘 다 시연 가능, 답 품질은 Haiku가 근소하게 앞서고 비용은 gemma가 1/3. 키 한도(Limit)는 시연 전 PM 확인.
