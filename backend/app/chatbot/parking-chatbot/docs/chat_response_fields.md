# 챗봇 API 요청·응답 필드 (화면 연결용, v2.10)

대상: 사용자 화면 `frontend/src/ChatPanel.jsx` (김채린님). 기준 코드는 `chatbot/http_router.py`의 `POST /api/v1/chat`입니다.

## 1. 요청 `POST /api/v1/chat` (JSON)

| 필드 | 필수 | 설명 |
|---|---|---|
| `session_id` | O | 화면 하나당 고정 문자열(1~128자). 지금처럼 `crypto.randomUUID()` 그대로 |
| `text` | 둘 중 하나 | 사용자가 입력한 문장(최대 200자) |
| `chip_id` | 둘 중 하나 | 버튼(칩)을 눌렀을 때 그 칩의 `id` |
| `confirmation_token` | 확인 칩일 때 | 칩 객체에 들어 있는 값을 그대로 보냄(차량 확인·결제 확인) |
| `request_id` | 권장 | 요청마다 새 값. 같은 값 재전송 시 결제가 두 번 되지 않음 |

지금 `ChatPanel`의 요청 body(`session_id`·`text`·`request_id`)는 이미 맞다.

## 2. 응답에서 화면이 쓸 필드

| 필드 | 형식 | 화면에서 |
|---|---|---|
| **`text`** | 문자열(줄바꿈 `\n` 포함) | **챗봇 답변의 기준 필드** (`white-space: pre-wrap`) |
| `messages` | `[{text}]` | 팀 앱에 포함된 POST 응답의 호환 필드. Lv2 대피 안내가 본문과 분리된 경우 먼저 표시 |
| `chips` | `[{id, label, confirmation_token?}]` | 답변 아래 버튼. 누르면 `{session_id, chip_id: id, confirmation_token}` 전송 |
| `confirmation_token` | 문자열 또는 null | 확인 단계(차량 확인·**결제 확인**) 토큰. 칩 안에도 같은 값이 있음 |
| `flow` | 문자열 | 현재 단계. `confirm_vehicle`(차량 확인), `confirm_action`(결제 확인), `idle` 등 |
| `card` | `{type, data}` 또는 null | 추가 표시용(선택). type: `empty_spots`, `car_location`, `fee`, `payment`, `admin_call`, `alert`, `unrecognized_candidates` |
| `banner` | 객체 또는 null | 모의 화재 경보 배너(선택) |
| `safety` | 객체 또는 null | Lv2 경보 시 "사람 먼저" 안내(선택, 있으면 크게 표시 권장) |
| `vehicle_context` | `{car_id, state, ...}` | 지금 대화 중인 차량. state: `unselected`·`parked`·`not_found`·`pending`(입차 확인 중)·`unavailable` |

그 밖의 필드(`meta`, `runtime_id`, `state_version`, `alert_version`, `updated_at`, `destination`, `catalog`)는 화면에서 무시해도 된다.

## 3. 화면 연결

현재 팀 화면은 `messages`를 읽는다. 팀 모드의 POST 응답은 기존 `text`와 함께 `messages`를 반환하므로 질문과 답변을 표시할 수 있다. 이 배열은 POST에 대한 답변 표시용이며 `/updates`의 알림 목록과 별개다.

카드와 확인 버튼까지 연결할 때는 `text`를 본문으로 표시하고 `safety`를 별도 영역에 표시한다. 버튼을 누르면 해당 `chip_id`와 칩 객체의 `confirmation_token`을 함께 보낸다. 팀 화면에서 카드·버튼을 구현하기 전에는 텍스트로 차량 번호를 확인할 수 있다.

## 4. 예시

```json
{"text": "11번 차 요금은 4,500원이에요. 이 금액으로 모의 결제할까요?",
 "flow": "confirm_action",
 "confirmation_token": "18e2…",
 "chips": [{"id": "confirm_yes", "label": "모의 결제하기", "confirmation_token": "18e2…"},
           {"id": "confirm_no", "label": "취소", "confirmation_token": "18e2…"}],
 "card": {"type": "fee", "data": {"car_id": "11", "fee": 4500, "quote_id": "…"}},
 "banner": null, "safety": null}
```

## 5. 오류

| HTTP | 뜻 | 화면 |
|---|---|---|
| 400 | 요청 형식 오류(빈 문장, 길이 초과 등) | "질문을 다시 입력해 주세요" |
| 409 | 같은 세션에서 동시에 요청이 겹침 | 잠시 후 다시 |
| 503 | 팀 데이터 서버 응답 없음 | "지금은 정보를 가져올 수 없어요" |
| 404 | 챗봇 라우터가 `main.py`에 아직 등록되지 않음 | 지금 ChatPanel 문구 그대로 |
