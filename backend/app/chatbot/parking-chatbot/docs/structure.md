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

## 6. 구조 점검 (11/09 밤)

예전에 치명적이었던 유형(대화 AI가 통째로 멈춰 규칙 답만 나가던 '이벤트 루프 종료', 판정이 너무 넓어 정상 질문을 막던 경계 규칙)과 같은 종류의 구조 문제가 남았는지 기록(경로·단계별 이유)으로 확인했다.

| 점검 항목 | 결과 | 조치 |
|---|---|---|
| AI가 조용히 멈춤(키·이벤트 루프·설치) | 경로에 `agent:api_error`/`off`로 보이고 `needs-review`로 표시됨. 11/09 OpenRouter 키 총액 한도($0.50) 소진이 실제로 발생 | 모델이 없어도 규칙 답이 문장에 맞도록 보강(계획표). 키 한도는 운영 확인 사항 |
| 고쳐 쓰기가 시간 안에 못 끝남 | 버려진 AI 답 5건 모두 고쳐 쓰기 시간 초과 → 사용자는 5.3초 대기 후 규칙 답 | 남은 시간이 `REPAIR_MIN_SEC`(2.5초) 미만이면 고쳐 쓰기 없이 바로 규칙 답 |
| 근거 검사가 사용자가 말한 주차장까지 막음 | '공항 말고…'에서 공항 이름만 말해도 `unsupported_lot` | 사용자 문장에 나온 주차장 이름은 허용(숫자는 여전히 조회 값만) |
| AI가 조회할 수 있는 차량 = 규칙이 읽은 번호 | 개인정보 보호용 설계. 규칙이 못 읽으면('스물세번') AI도 조회 못 함 | 숫자 읽기 보강(고유어). 두 차 동시 비교는 한 대씩 안내(설계상 유지) |
| 업무 흐름으로 바로 가는 판정(결제 승인 등) | '네 근데 공항 자리는?' 같은 문장은 승인으로 읽지 않음(안전). '넵'은 승인 못 알아들어 확인이 취소됨 | '넵·넹·네네'를 승인으로 |
| 세션당 AI 질문 수 | 1분에 5개(`LLM_MAX_PER_SESSION_MIN`), 넘으면 `agent:rate_limited`로 규칙 답 | 시연 때는 `.env`에서 10~12로 올리는 것을 권장(비용과 함께 결정) |
| 응답 시간 | AI 답 평균 3.8초, 입력 약 1.2만 토큰(호출 2번), 출력 길면 5초 이상 | 짧게 쓰기 지시 + 출력 상한 500. 외국어는 번역 시간 때문에 AI가 4초 안에 끝내야 해 규칙 답 비중이 큼 |
| 시간 제한 테스트(`test_robust_server`) | v3.2에서도 가끔 실패. 질문당 HTTP 요청 수는 푸시한 v3.3과 같음(4번) | 환경 의존(윈도우 소켓·0.15초 제한)으로 판단 |

## 7. 모델 경로 점검 — AI가 '도구로만' 쓰이지 않게 (11/09 밤)

질문 93턴(꼬는 질문·화면 사례·질문지)을 모델 호출 자리에 기록만 하는 가짜를 끼워 전수 조사했다(`tests/test_v33_notice.py`, 도구 `tools/run_dialogs.py`).

| 시점 | 모델 호출까지 간 질문 | 비고 |
|---|---|---|
| 점검 전 | 72% | 안내성 질문(자기소개·예약 불가·번호 분실·허용 여부 등)을 코드가 정해진 문장으로 바로 답함 |
| 점검 후 | 88% | 나머지 12%: 연기 신고·결제 진행/승인·관리자 접수·매출·남의 차·지시 무시(코드가 맡아야 함), 가상 차량 번호판(AI 도구 없음) |

바꾼 것

- **확정 안내(service_notice)**: 안내성 가로채기는 정해진 문장을 바로 내보내지 않고, 그 문장을 대화 AI의 읽기 도구로 넘긴다. AI가 앞 대화·다른 조회와 엮어 말하고, 핵심(예약 불가, 스티커, 칸 번호, 전용 칸 불가 등)을 빠뜨리거나 실패하면 정해진 문장. 확정 안내 답에서는 AI의 작업 제안(관리자 호출·결제 버튼)을 쓰지 않는다.
- **두 차 비교**: 사용자가 직접 쓴 번호들은 대화 AI가 각각 조회(그 밖의 번호는 거부).
- **도구 허용 목록**: 새 읽기 도구를 넣을 때 `agents/conversation.py`의 허용 목록에 없으면 AI 실행 전체가 `unsafe_tools`로 거부된다(실제로 한 번 걸림). 경로에 `agent:unsafe_tools`로 보인다.
- **규칙 의도는 AI에 넘기지 않음**, 짧게 쓰기 지시 완화, 출력 상한·고쳐 쓰기 최소 시간은 설정값(`LLM_MAX_OUTPUT_TOKENS`, `LLM_REPAIR_MIN_SEC`).

모델이 호출된 뒤에도 AI 답이 버려질 수 있는 지점(실제 모델로 확인 필요)

| 지점 | 경로 표시 | 조정 |
|---|---|---|
| 세션당 1분 5질문 한도 | `agent:rate_limited` | `.env` `LLM_MAX_PER_SESSION_MIN` |
| 질문당 6초(외국어 4초) | `agent:timeout` | `REQUEST_BUDGET_SEC`, 답 길이 |
| 근거 검사 | `agent:ungrounded` + 이유 | `agents/evidence.py` |
| 키 한도·키 없음 | `agent:api_error`/`off` | OpenRouter 키 한도 |

화면 사례 검증 파일: `tests/dialogs/screenshot_cases.json` (모델 켠 상태로 `python tools/run_dialogs.py tests/dialogs/screenshot_cases.json --env-file ../../../../.env`).
