# 챗봇 구조 (v3.3) — 총괄·계획표·역할별 지시문

질문 하나를 **코드가 총괄**하고, 말을 만드는 **대화 AI는 하나**다. 판단 단계마다 **경로 기록**을 남겨 이상한 답의 원인을 바로 찾는다.

## 1. 나무 구조

```
총괄 (코드: engine._handle_message)            ← 경로 기록: meta.route
├─ 시작·버튼                                     (규칙)
├─ 안전 확인   turn_guard: 연기 신고·허용 여부·없는 칸·자기소개   (규칙, Lv2 중에는 건너뜀)
├─ 계획표      plan.make: 문장을 여러 방식으로 읽음              (코드, AI 아님)
├─ 정해진 답   boundaries: 예약·할인·매출·남의 차·미등록 주차장  (규칙)
├─ 업무 흐름   결제 확인·관리자 호출·대피 안내                   (규칙, 상태 변경은 여기서만)
├─ 대화 AI     agents/conversation (지시문 conversation.md)     (AI 1개)
│   ├─ 참고 정보: 계획표 hints (결정권 없음)
│   ├─ 읽기 도구: 빈자리·차량/요금·요금 기준·비교·예상 요금·경보·규칙 안내
│   └─ 근거 검사 → 한 번 고쳐 쓰기 → 통과한 답만 화면으로
├─ 대체 규칙 답  AI가 꺼졌거나 실패하면 계획표로 문장에 맞는 해석을 다시 골라 답함
└─ 번역         질문 번역(translate_question.md) · 답 번역(translate_answer.md)   (AI, 각 지시문 1개)
```

교수님 의견(에이전트·프롬프트 1대1, 플래너, 슈퍼바이저, 트리)과의 대응:

| 의견 | 이 구조에서 | AI를 더 부르지 않는 이유 |
|---|---|---|
| 에이전트 하나에 프롬프트 하나 | AI를 부르는 역할 4개 = 지시문 파일 4개(`prompts/`), 버전 기록 | — |
| 플래너 | 계획표(`plan.py`) — 코드가 만듦 | 질문 대부분이 조회 1~3번. AI 플래너는 호출 1번(약 2초)이 더 들어 6초 제한에 자주 걸림 |
| 슈퍼바이저 | 총괄(`engine`) — 코드가 정함, 경로 기록 | 결제·대피·상태 확정은 서버가 정해야 함(절대 규칙). AI가 정하면 같은 질문도 다르게 갈 수 있음 |
| 트리 | 위 나무 구조(코드·도구 묶음) | 빈자리·요금 AI를 나누면 섞인 질문("여행 + 자리 + 요금")을 합치려 호출이 또 필요하고 답이 어색해짐 |

## 2. 경로 기록 읽기 (`meta.route`)

응답 `meta.route`에 거친 단계가 `단계:결과`로 이어진다. 모니터링 기록(Langfuse metadata.route, 로컬 JSON `route`)에는 단계별 이유(`why`)까지 남는다.

| 단계 | 뜻 | 결과 예 |
|---|---|---|
| `start` / `chip` | 대화 시작 / 버튼 | `welcome`, `empty_all` |
| `guard` | 안전 확인 | `pass`, `emergency`, `parking_permission`, `space_missing`, `self_intro`, `lot_names`, `cancel_clarify` |
| `plan` | 계획표(원하는 것) | `vacancy`, `fee+location`, `payment`, `none` |
| `boundary` | 정해진 답 | `pass`, `unknown_lot`, `reservation`, `discount` … |
| `workflow` | 업무 흐름이 맡음 | `pay_fee`, `admin_help`, `evacuation_guide` … |
| `agent` | 대화 AI | `ok`(답 사용), `answer`, `off`(꺼짐·한도·시간 부족), `timeout`, `ungrounded`(근거 검사 실패), `dropped`(답 버림), `skip`(규칙이 맡음) |
| `fallback` | AI 실패 뒤 규칙 답 | 실제 쓴 해석(`get_empty_spots` 등). 첫 해석을 바꿨으면 `why`에 "첫 해석 get_fee → …" |
| `rule` | 처음부터 규칙이 처리 | `pay_fee` 등 |
| `translate_in` / `translate_out` | 외국어 질문 번역 / 답 번역(화면 언어가 한국어가 아닐 때만) | `ok`, `kept_original` / `en`·`ja`·`zh-CN` |

예시

```
guard:pass > plan:vacancy > boundary:pass > agent:ok > agent:answer
  → 대화 AI가 조회해서 답함
guard:pass > plan:vacancy > boundary:pass > agent:timeout > agent:dropped > fallback:get_empty_spots
  → AI 시간 초과, 계획표로 고른 빈자리 규칙 답
guard:pass > plan:payment > boundary:pass > workflow:pay_fee > rule:pay_fee
  → 결제는 처음부터 업무 흐름(확인 절차)
guard:emergency
  → 연기 신고: 안전 안내 바로 응답
```

## 3. 계획표 (`chatbot/plan.py`)

| 항목 | 내용 |
|---|---|
| `rule` | 키워드 규칙 해석(의도·주차장·구역·차량) |
| `lots` · `zone` · `car` | 문장에 나온 주차장(별칭 포함)·구역·차량 번호 |
| `purpose_lot` | 목적으로 짐작한 주차장(여행→공항, 쇼핑→백화점, 기차→부산역) |
| `wants` | 원하는 것: vacancy·fee·location·payment·rates·policy·alert·compare·future |
| `talk` | 자리·차·돈 이야기가 있는지(규칙 답 고르기에 사용) |

쓰는 곳: 대화 AI 참고 정보(`conversation_turn.readings` = `plan.hints()`), 대체 규칙 답 고르기(`engine._plausible_reading`), 경로 기록(`plan` 단계).

## 4. 역할별 지시문 (`chatbot/prompts/`)

| 역할 | 파일 | 결과 검사 |
|---|---|---|
| 대화 AI | `conversation.md` | 근거 검사(`agents/evidence.py`) → 한 번 고쳐 쓰기 |
| 질문 번역 | `translate_question.md` | 숫자가 바뀌면 원문 사용 |
| 답 번역 | `translate_answer.md` | 자리표시 복원·개수 확인 |
| 질문 분류(예전 방식, `LLM_ENGINE=direct`) | `intent_classifier.md` | 허용 의도 목록 |

버전(내용 해시 8자리)이 모니터링 기록 `metadata.prompts`에 남는다. 지시문 파일은 매번 읽으므로 고치면 서버를 다시 켜지 않아도 반영된다.

## 5. 증상별로 볼 곳

| 증상 | 경로에서 보이는 것 | 고칠 곳 |
|---|---|---|
| 정상 질문이 거절됨 | `boundary:unknown_lot` 등 | `boundaries.py` |
| 안전·허용 답이 틀림 | `guard:…` | `turn_guard.py`(판정 정규식은 `turn_policy.py`) |
| AI가 엉뚱한 곳을 조회·추천 | `agent:ok` + 도구 목록 | `prompts/conversation.md`, `plan.py`(참고 정보) |
| AI 답이 버려짐 | `agent:ungrounded`, `why`/`problem` | `agents/evidence.py` |
| "이해하지 못했어요"·엉뚱한 규칙 답 | `fallback:…` | `engine._plausible_reading`, `parser.py` |
| 느림 | `agent:timeout`, `latency_ms` | `config.REQUEST_BUDGET_SEC`, 모델 호출 수 |
| 번역이 이상함 | `translate_in:kept_original`(질문 번역 실패·숫자 바뀜), `translate_out:en` | `prompts/translate_*.md`, `response_locale.py` |

다시 볼 턴을 모으는 방법은 [review_loop.md](review_loop.md).
