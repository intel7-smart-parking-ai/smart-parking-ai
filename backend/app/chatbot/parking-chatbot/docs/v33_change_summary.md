# v3.3 변경 요약 (11/09)

v3.2(`dcb2d03`) 위에 화면 피드백(11/09), v3.3 후보(`feature/KAN-9-chatbot-v32-safety-port`) 검토 결과를 반영했다. 후보 판정과 근거는 [v33_review.md](v33_review.md).

## 0. 구조화 — 총괄·계획표·역할별 지시문 ([structure.md](structure.md))

교수님 의견(에이전트·프롬프트 1대1, 플래너, 슈퍼바이저, 트리)을 AI 호출을 늘리지 않는 방식으로 반영했다.

| 요소 | 반영 | 파일 |
|---|---|---|
| 경로 기록(총괄이 보이게) | 질문마다 거친 단계와 이유를 `meta.route`·모니터링에 남김. 예: `guard:pass > plan:vacancy > boundary:pass > agent:timeout > agent:dropped > fallback:get_empty_spots`. 외국어는 `translate_in`/`translate_out`까지 | `route.py`, `engine.py`, `conversation_turn.py`, `http_router.py` |
| 계획표(플래너, 코드) | 문장을 여러 방식으로 읽은 결과(규칙 의도·주차장·구역·차량·목적 주차장·원하는 것)를 하나로 모아 대화 AI 참고 정보와 대체 규칙 답이 함께 씀 | `plan.py` |
| 역할별 지시문 | AI를 부르는 역할 4개 = 지시문 파일 4개(대화·질문 번역·답 번역·질문 분류). 내용은 그대로 옮겼고(글자 단위 비교), 버전 해시를 모니터링에 기록 | `prompts/` |

경로 기록으로 바로 찾아 고친 것: '역에 주차장'의 대체 답이 부산역을 못 읽음(`info_gaps`), 번호판 질문을 빈자리로 읽음(`plan`).

## 1. 문장 해석을 한 갈래에 맡기지 않기 (유연성)

긴 일상 문장("일본 여행 중인데 친구가 올 거라 자리가 얼마나 여유가 있어?")이 키워드 하나("얼마")로 엉뚱한 흐름(차량 번호 요구)에 빠지던 구조를 바꿨다.

| 단계 | 바뀐 점 | 파일 |
|---|---|---|
| 모델에 넘기는 상태 | 규칙 의도·언급 주차장·구역·차량 번호·목적 주차장을 `readings`(후보 해석)로 함께 준다. 모델은 참고만 하고 반드시 도구로 조회한다 | `conversation_turn.readings`, `prompts/conversation.md` |
| 주차장 성격 | 공항=여행·출국, 부산역=기차·'역', 신세계=쇼핑, 개발원=방문. 목적만 말해도 맞는 곳을 조회해 추천 | `prompts/conversation.md`, `info_gaps._LOT_HINTS` |
| 조회 없이 답한 모델 | 버리기 전에 "먼저 조회하라"는 메모로 한 번 고쳐 쓰게 함. '안내 자료에 없어요'만 말하는 답은 운영 질문이어도 허용 | `agents/conversation.py`, `agents/evidence.py` |
| 모델이 실패했을 때 | 첫 규칙 해석을 그대로 쓰지 않고 문장과 맞는지 다시 고름: 차·번호·돈 이야기 없이 자리 이야기면 빈자리로, 자리 이야기인데 '이해 못 함'이면 목적·언급 주차장 빈자리로. "얼마나 여유/남았어"는 요금이 아님 | `engine._plausible_reading`, `parser._VACANCY_AMOUNT` |
| 정해진 거절 답 | "등록되지 않은 주차장"은 시설 이름(롯데백화점, 해운대역)일 때만. '실외 주차장', '역에 주차장'은 모델이 판단 | `boundaries.unknown_lot` |
| 화면 표시 | 모델 답의 마크다운(**, #, 목록 기호)을 일반 문장으로 | `conversation_turn.plain_text` |

## 2. 안전·허용 여부·없는 칸 (후보 이식, 수정)

김채린 `turn_policy.py`는 원본 그대로, `turn_guard.py`는 후보를 고쳐 엔진 앞단에 연결.

- 연기·불·부상 신고 → 대피·119·관리자 안내 + **지금 시스템의 모의 화재 경보 상태**. 진행 중인 확인은 끝낸다
- **Lv2 경보 중에는 guard를 건너뜀** → 경보 위치·출구가 담긴 기존 대피 안내 유지
- 전용 칸 허용 여부 → **팀 결정 D-05 문장만 사실로 사용**. 정해지지 않은 기준(전기차·장애인 차량의 일반 칸 등)은 "안내 자료에 없어요"
- 결제 확인 중 옆 질문을 해도 확인이 유지됨(다음 "네"로 결제 진행)
- 없는 칸: 별칭(공항·백화점·신세계)으로 말한 주차장도 칸 명부로 확인
- 자기소개는 '너/챗봇 이름'만. "주차장 이름이 뭐야?"는 주차장 4곳 목록
- holdout "거기 연기 나요" 기대값 `get_alerts` → `emergency`

## 3. 새 대화 첫 확인의 지난 경보 말풍선

화면은 `start` 없이 첫 질문을 보낸다. 첫 요청(질문 또는 poll)에서 경보 기준점을 잡아 "경보 없음"·지난 경보가 새 알림처럼 붙지 않게 했다(`Session.alert_seeded`). 후보 `74fd09b`와 같은 문제이며 후보 테스트(`test_v32_poll_baseline`)도 통과.

## 4. 모니터링

- **Langfuse 무료 한도 초과 원인 수정**: Langfuse v3 SDK가 전역 OpenTelemetry tracer가 되어 FastAPI의 모든 HTTP 요청(헬스 체크 5초마다, 화면 폴링)이 trace로 전송되던 문제. 전용 tracer provider로 챗봇 턴만 보냄
- 다시 볼 턴 자동 표시(`needs-review`), 사용자 👍/👎 `POST /api/v1/chat/feedback`(API 추가), 검토 목록 도구 `tools/langfuse_review.py` — [review_loop.md](review_loop.md)

## 검증

- 챗봇 `tests/run_guarded.py` 750 OK (skip 2), 저장소 `pytest tests/` 357 passed
- `test_robust_server`의 시간 제한 검사 1건은 v3.2에서도 8번 중 2번 실패하는 기존 흔들림(전체 실행에서는 통과)
- 모델 켠 상태 65턴(핵심 20·화면 피드백·11/09 질문지 일부·Lv2): 핵심 20 ○20, 치명 0, 평균 2.2초
- 미실행: 공통 질문지 A(63)·B(50)

## 남은 점

- Lv2 중 "안녕"·"넌 누구야"는 기능 안내/이해 못함(배너는 유지). v3.2와 같음
- 모델 응답 4~6초 구간이 있음(조회 1회 + 답 + 고쳐 쓰기). 시간이 넘으면 규칙 답으로 대체
- 화면의 "모의" 글자 삭제는 프런트(`ChatPanel.jsx`·`uiState.js`) 문제로 별도 수정 필요(후보 문서 기록)
- 👍/👎 버튼과 nginx 경로는 프런트 담당 변경 요청([review_loop.md](review_loop.md))
