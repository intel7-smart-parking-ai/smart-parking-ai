# 답 검토 고리 (Langfuse 피드백 → 수정 → 회귀 테스트)

> 버전별 실행 기록과 문제 추적표는 [eval/EVAL_LOG.md](eval/EVAL_LOG.md). 실행은 `tools/run_dialogs.py --save`, 비교는 `tools/compare_runs.py`.

챗봇이 잘못 답한 턴을 모아서 고치는 순서. 모델·프롬프트를 바꿀 때마다 같은 질문으로 다시 확인한다.

```
질문 ─ /api/v1/chat ─ 턴 기록(Langfuse 또는 로컬)
                       ├ needs-review 태그 + needs_review 점수 (자동)
                       └ 👍/👎 → POST /api/v1/chat/feedback → user_feedback 점수 (사용자)
        ↓
tools/langfuse_review.py → .monitoring/review_*.md (표) + review_*.json (테스트 후보)
        ↓
고칠 질문을 tests/에 옮기고 수정 → python tests/run_guarded.py
```

## 1. 자동으로 표시되는 턴 (`needs-review`)

| 이유 | 뜻 |
|---|---|
| `not_understood` | "잘 이해하지 못했어요"로 끝남 |
| `model_fallback:<상태>` | 모델 답을 못 쓰고 규칙 답으로 대체(시간 초과·근거 부족 등) |
| `grounding:<검사>` | 근거 검사에 걸림(조회에 없는 숫자·칸·주차장, 실행 주장 등) |
| `repair_failed:<이유>` | 고쳐 쓰기 한 번도 실패 |
| `unknown_lot` | "등록되지 않은 주차장" 판정. 오판(예: '실외 주차장')인지 확인 |
| `slow` | 6초 넘게 걸림 |

Langfuse 화면: Tracing → 태그 `needs-review`로 거르거나, 점수 `needs_review = 1`, `user_feedback = 0`으로 거른다.

## 2. 사용자 피드백 API

```
POST /api/v1/chat/feedback
{"session_id": "...", "request_id": "답을 받은 /chat 요청의 request_id(선택)", "rating": "up" | "down", "comment": "선택, 300자"}
→ {"ok": true, "recorded": true | false}
```

- `request_id`가 없으면 그 세션의 마지막 답에 붙는다. 답·대화 상태는 바꾸지 않는다.
- `recorded=false`: 모니터링 꺼짐(`MONITOR_BACKEND=off`) 또는 그 답의 기록 없음.
- 기록 위치: Langfuse면 그 답의 trace에 `user_feedback`(1/0)과 의견, `local`이면 `MONITOR_LOCAL_PATH`에 한 줄.

### 변경 요청 (프런트·배포, 담당: 김채린 / 승인: PM)

챗봇 쪽 API는 준비됐다. 화면에 버튼을 붙이려면 담당 영역 변경이 필요하다.

1. `frontend/src/chatSession.js`: 보낸 요청의 `request_id`를 답 row에 저장(`addReply` 시 `task.body.request_id`).
2. `frontend/src/ChatPanel.jsx`: 챗봇 답 말풍선 아래 👍/👎(👎는 선택 의견 입력) → `POST /chat/feedback` (`request('/chat/feedback', …, 'team')`). 실패해도 대화는 그대로.
3. `frontend/nginx.conf`: `location = /api/v1/chat/feedback { limit_except POST { deny all; } … proxy_pass http://parking_api; }` (지금은 `/api/` 403에 걸림). 개발 모드(5173)는 바로 동작.

API 변경 여부: **추가** (`POST /api/v1/chat/feedback`, 기존 응답 형식 변경 없음).

## 3. 검토 목록 뽑기

```powershell
cd backend/app/chatbot/parking-chatbot
# Langfuse에서 최근 24시간
..\..\..\..\.venv\Scripts\python.exe tools/langfuse_review.py --hours 24 --env-file ../../../../.env
# 로컬 기록에서 (MONITOR_BACKEND=local)
..\..\..\..\.venv\Scripts\python.exe tools/langfuse_review.py --local .monitoring/turns.jsonl --hours 24
```

결과는 `.monitoring/`(Git 제외). 키는 환경 변수에서만 읽고 출력하지 않는다.

- `401 Invalid credentials`는 키·지역(`LANGFUSE_HOST`, US면 `https://us.cloud.langfuse.com`)이 안 맞을 때 나지만, 무료 한도 초과로 수집이 정지된 동안에도 났다(11/09). 정지 메일을 먼저 확인한다.

### 무료 한도(월 50,000 이벤트) 주의

- 11/09 하루 28만 건으로 한도를 넘었다. 원인: Langfuse v3 SDK가 프로세스 전역 OpenTelemetry tracer가 되어 FastAPI의 **모든 HTTP 요청**(Docker 헬스 체크 5초마다, 화면 폴링)이 trace로 전송됨. 챗봇 턴은 그중 일부.
- 수정: `monitoring._langfuse()`가 전용 tracer provider를 넘긴다. 이제 `chat-turn`과 그 안의 모델·도구 호출만 보낸다(`tests/test_v33_review_loop.py` PrivateTracerTests).
- 그래도 턴 하나가 trace 1개 + 관측 여러 개 + 점수 1~3개다. 대량 테스트·시트 실행은 `MONITOR_BACKEND=off` 또는 `local`로 돌린다. 수집 정지 동안(무료 플랜은 다음 초기화일까지)은 루트 `.env`를 `MONITOR_BACKEND=local`로 바꾸고 `--local`로 검토한다.
- Langfuse Cloud 응답에 따르면 이 도구가 쓰는 `GET /api/public/traces`는 **2026-11-16**에 없어진다(대체: `/api/public/v2/observations`). 시연(11/13)까지는 그대로 쓰고, 그 뒤에도 쓸 거면 옮긴다. 로컬 기록(`--local`)은 영향 없다.

## 4. 고치는 방법 고르기

| 증상 | 먼저 볼 곳 |
|---|---|
| 정상 질문이 정해진 거절 답으로 막힘 | `chatbot/boundaries.py` (모델보다 먼저 처리) |
| 모델이 조회 없이 답하거나 엉뚱한 곳 추천 | `chatbot/prompts/conversation.md` (주차장 성격·도구 고르기) |
| 맞는 답인데 검사에 걸려 버려짐 | `chatbot/agents/evidence.py` |
| 모델이 없을 때 "이해하지 못했어요" | `chatbot/parser.py` (규칙 대체 답) |

고친 질문은 `tests/test_v33_review_loop.py`처럼 회귀 테스트로 남긴다.
