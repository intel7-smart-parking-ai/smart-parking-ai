# 챗봇 v3.1 변경 요약 — 자연스러운 조합 답 + 안정성 + Langfuse 모니터링

- 기준: `chatbot-v3.0.1-full1.zip`(팀 main `86c3069`, `feature/KAN-9-agentic-rebuild`)
- 방향: 우리 방향 유지(대화 에이전트 1개 + 읽기 도구 + 업무 흐름 실행). 멀티 에이전트·프롬프트 외부 관리는 넣지 않았다. Langfuse는 **모니터링 전용**으로만 붙였다.
- 표기: **[실행]** 직접 실행해 확인 / **[가짜 모델]** 조회 결과를 읽고 답을 쓰는 시험용 모델로 확인 / **[미검증]** 시연 PC·실제 키 필요

## 1. 왜 v2.x는 자연스러운 답을 못 했고, v3.0.1은 왜 아직 막혔나

| 버전 | 구조 | 화면에 나간 답 |
|---|---|---|
| v2.x | LLM은 의도 하나만 고르고, 문장은 전부 템플릿 | 여러 자료를 묶을 길이 없음 |
| v3.0.1 | 모델이 조회 도구로 자료를 모아 답을 씀 | 근거 검사·조립 단계·호출 상한이 모델 답을 대부분 버림 |
| v3.1 | 같은 구조. 계산은 코드가, 조합·문장은 모델이. 걸린 답은 1번 고쳐 씀 | 아래 3장 |

## 2. 고친 것

| # | 문제 (v3.0.1) | 수정 | 파일 | 확인 |
|---|---|---|---|---|
| 1 | **거짓 실행 문장 통과**: "결제해 드렸어요", "관리자를 불렀어요", "차단기를 열어 드렸어요", "결제가 끝났어요" 등 | 표현 목록 대신 "실행 대상 단어 + 완료 표현"을 절 단위로 판단. 부정("아직 안 됐어요", "누르면")은 통과. 관리자 도착·연락 **약속**도 차단. 이미 결제된 기록·이미 접수된 문의를 설명하는 것은 허용(에이전트가 했다고 말하지 않을 때) | `agents/evidence.py` | [실행] 차단 14/14, 정상 문장 10/10 통과 |
| 2 | **계산한 숫자 차단 / 엉뚱한 숫자 통과**: "2자리 더 많아요"·"오후 2시 5분"·"1시간 15분째"·"30분 뒤 4,000원"은 막히고, 실제 차이 500원인데 "1,000원 더 싸요"는 통과 | 코드가 계산한 값을 도구로 제공: `compare_lots`(차이·순위·자리 있는 곳 수), `vehicle_info`의 `entry_time_text`·`elapsed_text`, `estimate_fee`(서버 요금과 같은 규칙일 때만), `policy_text`. 비교 문장의 숫자는 **계산된 차이값과만**, "N분에 M원"은 **같은 정책의 짝과만** 맞춘다 | `tools/read.py`, `agents/evidence.py` | [실행] 위 4개 통과, 틀린 비교 2개 차단 |
| 3 | 검사에 걸리면 고쳐 쓰기 없이 바로 버림 | 이유를 알려 주고 **1번만** 다시 쓰게 함. 다시 실패하면 처음 사유로 대체 경로 | `agents/conversation.py` | [가짜 모델] `repaired: true` |
| 4 | 정책 질문이면 모델 답 폐기, 자료 검색만이면 검색 문장으로 덮어씀, 미래 질문에 "혼잡 예측은 아직…" 고정 문장 강제 | 모델 답 유지. 미래 한계는 지시문에서 "자기 말로 한 번만". 팀 결정 D-05와 반대되는 답("일반차도 대도 돼요")만 승인 문구로 대체 | `conversation_turn.py`, `prompts/conversation.md` | [가짜 모델] |
| 5 | 예상한 조회가 하나라도 빠지면 모델 답 전체 폐기 | 빠진 항목을 알려 주고 조회해서 다시 쓰게 함(3번과 같은 1회). 그래도 빠지면 기존 대체 답 | `conversation_turn.py`, `agents/conversation.py` | [가짜 모델] |
| 6 | 도구 없는 답은 무조건 실패 → "고마워"도 템플릿 | 질문·답 모두에 운영 사실(숫자·주차장·자리·요금·실행)이 없을 때만 도구 없이 답 허용 | `agents/evidence.py` | [실행] |
| 7 | 같은 안내 반복 | 직전 답 2개와 90% 이상 같으면 다르게 다시 쓰게 함(안전 문제는 아니므로 실패해도 내보냄, `meta.repeated`) | `agents/conversation.py` | [가짜 모델] |
| 8 | 확인 질문 고정 3종 | `ask_clarification(topic, question)`: 모델이 상황에 맞는 질문을 씀(숫자·실행 주장 없는 짧은 질문일 때만) | `agents/conversation.py` | [가짜 모델] |
| 9 | 정보원이 좁음(조회 4개, 단어가 정확히 맞아야 찾는 규칙 검색) | 조회 도구 10개: 위 계산 도구 + `alert_summary`(승인된 안전 문장 포함), `violations`, `call_status`(읽기 전용, 접수 안 함). `search_knowledge(topics=…)`로 주제 통째 제공, 등록 안 된 시설은 `unregistered`로 알려 줌 | `tools/read.py`, `knowledge/__init__.py`, `workflows/admin.py` | [실행] |
| 10 | 경보·위반·문의 상태 질문은 에이전트를 아예 거치지 않음 | 조회 질문은 에이전트로 보내 다른 자료와 묶어 답함. 상태 변경·안전·현장 대응(확인, 차량 변경, 목적지, 대피, 관리자 비용 문의, 출구 문제, 명시적 관리자 호출, Lv2)은 그대로 규칙 | `engine.py` | [가짜 모델], 모델 실패 시 기존 규칙 답 |
| 11 | 모델이 결제를 제안해도 화면에 아무것도 없음 | 결제 칩만 보여 줌 → 누르면 기존 서버 견적 → 사용자 승인 절차. "결제하지 말고"면 안 보임 | `conversation_turn.py` | [가짜 모델] 결제 호출 0건 |

## 3. 실사용에서 생길 문제 — 다각도 점검과 처리

| 관점 | 찾은 문제 | 처리 |
|---|---|---|
| 호출 상한 | 세션당 분당 5회를 **모델 호출**로 셈 → 질문 1개가 2~3회라 두세 번째 질문부터 조용히 규칙 답 | 같은 키를 **질문(턴) 단위**로 셈(`llm_limits.admit_turn`). [가짜 모델] 같은 세션 1분 6질문: v3.0.1 모델 답 2개 → v3.1 5개 |
| 토큰 상한 | 턴 상한 12,000토큰인데 지시문·도구 설명이 호출마다 다시 들어가 비교 질문(3회 호출)에서 넘칠 수 있음(추정) | `LLM_TURN_TOKEN_LIMIT` 기본 40,000, 출력 상한 800 |
| 동시 사용 | 서버 전체 모델 자리 2개, 비어 있지 않으면 **즉시 실패** | 기본 4개 + 최대 1초 대기(`LLM_MAX_INFLIGHT`, `LLM_SLOT_WAIT_SEC`). 이벤트 루프를 막지 않는 대기 |
| 시간 | 답 작성 호출도 3초 제한, 대체 경로용으로 최대 1초 예약 | 조회 결과를 받은 뒤 호출은 4초(`LLM_ANSWER_TIMEOUT_SEC`), 예약은 최대 0.7초. 전체 6초 예산은 그대로 |
| 외국어 화면 | 대화 처리(최대 6초) 뒤 번역 호출이 **고정 8초** → 화면 제한(약 8초) 초과 가능 | 번역 제한 시간 = 7.5초 − 대화 처리 시간(최소 0.5초). 실패 시 기존처럼 원문+안내 |
| 모델 객체 재사용 | 매 턴 새로 만듦(연결 재사용 안 됨) | **바꾸지 않음**: 턴마다 `asyncio.run`으로 새 이벤트 루프를 쓰므로, 비동기 HTTP 클라이언트를 루프 사이에 재사용하면 "Event loop is closed" 오류 위험이 더 큼 |
| 결제·관리자 | 모델 문장에서 실행 주장 → 사용자가 결제됐다고 믿고 출구로 감 | 2장 1번. 실행은 여전히 업무 흐름만 함([가짜 모델] 결제·관리자 접수 0건) |
| Lv2 | – | 그대로(에이전트 생략). [가짜 모델] Lv2에서 모델 호출 0회 |
| 검사 환경 | FastAPI 없는 환경에서 `test_response_locale`가 건너뛰지 않고 import 오류 | 다른 FastAPI 검사처럼 건너뛰기 |
| 기록 파일 | – | local 모니터링 파일 5MB에서 교체, `.gitignore`에 `.monitoring/` |
| 남은 위험 [미검증] | 실제 Gemma의 도구 호출 정확도·응답 시간, 6초 예산 안에 고쳐 쓰기까지 들어가는지 | 시연 PC에서 측정 후 필요하면 `REQUEST_BUDGET_SEC` 7 검토(화면 제한 8초) |

## 4. Langfuse 모니터링 구조 (선택, 기본 꺼짐)

```
engine.handle ── with monitoring.turn(...)  → Langfuse trace 1개(세션 ID는 해시)
   └ conversation_turn ── 에이전트 실행에 LangChain 콜백 연결 → 모델 호출·도구 호출 구간·토큰
                       └ 도구 목록·근거 검사 사유·고쳐 쓰기 여부 기록
   └ 응답 후 → 출처(llm/rule)·에이전트 상태·지연 + 점수 model_answer_used, repaired
```

- `MONITOR_BACKEND=off|local|langfuse`. local은 같은 내용을 JSON 한 줄로 파일에 남김(Langfuse 없이 디버깅).
- 답·흐름·시간 예산에 영향 없음: 모든 기록은 예외를 삼키고, 실패 20번이면 스스로 꺼짐. 키 없음·패키지 없음이면 경고 1회 후 꺼짐(`stats.monitoring.disabled_reason`).
- 키·토큰은 기록하지 않음. `MONITOR_CAPTURE_TEXT=0`이면 원문 대신 글자 수.
- Langfuse SDK v3 API 기준, v2 API도 지원. 설치는 `requirements-monitoring.txt`(Docker는 `INSTALL_MONITORING=1`). 켜는 법: `docs/agent_runbook.md`.
- 프롬프트를 Langfuse에서 내려받지 않는다(답 생성 경로에 외부 의존을 만들지 않음).

## 5. 효과 (가짜 모델 비교, 같은 질문·같은 답 품질 가정)

조회 결과를 읽고 사람이 쓸 법한 답을 쓰는 시험용 모델로, v3.0.1과 v3.1에 같은 질문 8개를 넣었다. v3.0.1에는 계산 도구가 없어 모델이 직접 계산한 값으로 같은 문장을 쓰게 했다.

| 질문 | v3.0.1 | v3.1 |
|---|---|---|
| 공항이랑 신세계 중 충전 자리 넉넉하고 싼 데? | 대체 답(값 나열) | 모델 답 |
| 23번 차 얼마나 있었고 지금 얼마? | 대체 답 | 모델 답 |
| 23번 차 30분 뒤 나가면 얼마? | 대체 답 | 모델 답 |
| 이따 저녁에 공항 자리 있을까? | 모델 답 + 고정 문장 강제 | 모델 답 |
| 공항에 엘리베이터 있어?(자료 없음) | 대체 답 | 모델 답 |
| 고마워! | 대체 답 | 모델 답 |
| 일반차인데 충전칸에 대도 돼? | 대체 답(승인 문구) | 모델 답(정책과 같은 내용일 때만) |
| 경보 있어? 내 차 괜찮아?(Lv1) | 규칙 답(에이전트 미경유) | 모델 답 |
| **합계** | **1/8** | **8/8** |

실제 모델 품질은 이 표에 들어 있지 않다. 시연 PC에서 실제 키로 같은 질문을 돌려 `meta.source`·Langfuse 점수로 확인한다.

## 6. 검사 결과

| 검사 | 결과 |
|---|---|
| 챗봇 전체, LangChain 미설치 | [실행] 680개, 실패 0, 건너뜀 70 |
| 챗봇 전체, LangChain 대역(로컬 검증용 최소 구현) | [실행] 680개 중 실패 3 = v3.0.1에서도 같은 3개(대역이 흉내 내지 않는 실제 LangChain 내부·HTTP 검사). 새 검사 포함 나머지 통과 |
| 새 검사 | `test_v31_grounding.py` 9개, `test_v31_monitoring.py` 6개, `test_v31_conversation.py` 16개 |
| 질문 세트 9종 | [실행] 248/248 (v3.0.1과 같음) |
| 응답 스냅샷(LLM 꺼짐, 320문항, 숫자 가림) | [실행] 차이 0 |
| 바뀐 기존 검사 | `test_read_tools_v3`의 도구 이름 목록(새 읽기 도구 5개 추가, 기존 5개 유지) / `test_response_locale` FastAPI 미설치 시 건너뛰기 |
| [미검증] | 실제 LangChain·Langfuse SDK, 실제 Gemma 응답, Docker 빌드, Windows, `tools/rehearsal_1106.py`(팀 레포 필요), 브라우저 검사 |

## 7. 사람 할 일

- [ ] 시연 PC에서 `pip install -r requirements-llm.txt` 후 `python -m unittest discover -s tests` (실제 LangChain으로 v3·v3.1 검사 실행)
- [ ] 레포 루트에서 `python backend/app/chatbot/parking-chatbot/tools/rehearsal_1106.py` → 6/6
- [ ] 실제 키로 5장 질문 8개를 넣어 `meta.source`, `meta.agent_status`, 응답 시간 확인 (가능하면 `MONITOR_BACKEND=local`로 기록)
- [ ] Langfuse 사용 여부·키 관리 주체를 PM(김한별님)에게 확인 — 쓰지 않아도 챗봇은 그대로 동작
- [ ] 브랜치 `feature/KAN-9-natural-answers`, `git add backend/app/chatbot`, PR 리뷰어 김한별님

## 8. 바뀐 파일

- 수정: `chatbot/agents/conversation.py`, `chatbot/agents/evidence.py`, `chatbot/tools/read.py`, `chatbot/knowledge/__init__.py`, `chatbot/conversation_turn.py`, `chatbot/engine.py`, `chatbot/config.py`, `chatbot/llm_limits.py`, `chatbot/http_router.py`, `chatbot/response_locale.py`, `chatbot/workflows/admin.py`, `chatbot/prompts/conversation.md`, `Dockerfile.agent`, `compose.agent.yaml`, `.gitignore`, `README.md`, `docs/CODEMAP.md`, `docs/settings_guide.md`, `docs/agent_runbook.md`, `tests/test_read_tools_v3.py`, `tests/test_response_locale.py`
- 추가: `chatbot/monitoring.py`, `requirements-monitoring.txt`, `tests/test_v31_grounding.py`, `tests/test_v31_monitoring.py`, `tests/test_v31_conversation.py`, `docs/v31_change_summary.md`
- 바꾸지 않은 고정 접점: `/api/v1/chat` 요청·응답 키(`meta`에 키 추가만), 칩 ID, 카드 type, flow, `.env.example` 키 목록, `lots.json`·`chips.json`·`api_paths.json`, 기존 도구 이름 5개
