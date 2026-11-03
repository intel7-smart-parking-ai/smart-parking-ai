> 이력 문서: 아래의 대기·문의 상태 변경·독립 이용 현황 기능은 2026-11-02 보완본에서 삭제됨. 현재 기준은 `implementation_guidelines.md`와 `cleanup_and_supplement_report.md`입니다.

> 현재 안정화 버전: [수정 보고서](stability_fix_report.md). 로컬 기본 실행은 loopback 전용이며 공유망 관리자·시연 API에는 접근 키가 필요합니다. Lv2에서는 미완료 결제를 취소합니다.

# 주차장 안내 챗봇 · EPIC 06 테스트 모델

본 작업(11/02~) 전에 미리 만들어 둔 **텍스트 챗봇 테스트 모델**입니다.
가짜 데이터로 지금 바로 돌아가고, 팀 백엔드 API v1이 확정되면 연결층만 바꾸면 됩니다.

## 실행

```bash
# 1) 설치 없이 바로 (파이썬 3.10 이상)
python server_dev.py            # → 이용자 화면 http://127.0.0.1:8080
                                #   관리자 화면 http://127.0.0.1:8080/admin.html

# 2) FastAPI로 (팀 백엔드와 같은 방식)
pip install fastapi uvicorn
uvicorn server_fastapi:app --reload --port 8080

# 테스트 질문 세트 정답률
python tests/run_eval.py

# 오류 재발 방지 테스트 (선택 FastAPI 포함 170개)
python -m unittest discover -s tests -v
# 선택 FastAPI 라우터 검사도 실행하려면: pip install fastapi httpx
```

최신 구조 개선과 API 계약은 [`docs/architecture_review.md`](architecture_review.md), [`docs/api_contract.md`](../api_contract.md)에 있습니다.
1차 코드 검토 기록은 [`docs/code_review.md`](code_review.md)에 있습니다.
실행 방식과 화면 구성은 기존과 같습니다. 메모리 상태를 공유하므로 서버는 단일 프로세스로 실행하세요
(`uvicorn --workers`를 늘리면 세션·보고서·대기열이 프로세스마다 나뉩니다).

휴대폰으로 보려면 `.env`의 `HOST=0.0.0.0`으로 dev 서버를 실행한 뒤 같은 와이파이에서 `http://<PC IP>:8080` 으로 접속하면 됩니다.
화면 오른쪽 위 **테스트** 버튼으로 모의 화재 경보(Lv1/Lv2/해제), 차량 출차(빈자리 대기 알림 시연), 대화 통계, 관리자 화면 열기를 할 수 있습니다.

## 추가 기능 (다른 분야에서 가져온 편의 기능)

| 기능 | 가져온 곳 | 동작 |
|---|---|---|
| 관리자 호출 상황 요약 | 콜센터 AI 상담 요약 | 관리자를 부르면 차량·위치·결제·최근 문의·경보 상태를 한 줄로 자동 정리 |
| **상황 보고서 자동 작성** | 안전 보고서 자동완성 | 경보가 뜨는 순간 보고서 초안이 생기고, 단계 변화·영향 차량(근접 차량 강조)·챗봇 안내 수·대피 문의·관리자 호출·조치 체크가 실시간으로 쌓임. 해제 후 관리자가 메모를 붙여 확정 (`admin.html`) |
| 이용 상태 추적 | 배달 앱 주문 추적 | 입차 → 주차 중 → 결제 → 출차 준비 진행 표시. 요금·결제·출차 진단 카드와 "내 이용 현황" |
| 빈자리 대기 등록 | 식당 원격 줄서기 | 만차면 "자리 나면 알려줘" 칩 → 대기 순번 안내 → 자리가 나면 챗봇이 먼저 알림, 대기 취소 가능 |

**상황 보고서 흐름**: 경보 Lv1/Lv2 → 보고서 자동 생성(진행 중) → 관리자가 체크리스트를 누르면 시각이 경과에 기록 → 경보 해제 → 메모 작성 → 확정(확정 후 수정 불가). 진행 중에는 확정할 수 없음.


## 구조

| 파일 | 역할 |
|---|---|
| `chatbot/engine.py` | 대화 흐름: 칩/문장 → 의도 → 처리 함수 → 응답 JSON. 차량번호 확인, 실패 2단계 처리, 경보 시 칩 교체 |
| `chatbot/parser.py` | 규칙 파서: "7번", "칠번 차", "에이 주차장" 같은 표현을 정리하고 의도 분류. 모르면 LLM에 넘김 |
| `chatbot/llm.py` | (선택) LLM 분류기. 기본은 꺼져 있음. 문장을 JSON으로 분류만 하고 답변은 만들지 않음 |
| `chatbot/templates.py` | 답변 문장 모음. 말투를 바꿀 때는 이 파일만 수정 |
| `chatbot/chips.json` | 추천 칩, 상황별 칩 묶음, 자동완성 문구. 코드 수정 없이 문구 변경 가능 |
| `chatbot/incidents.py` | 상황 보고서 자동 작성: 경보 단계 변화 기록, 영향 차량, 안내·문의 집계, 대응 체크리스트, 확정 |
| `chatbot/adapters.py` | 데이터 연결층. `mock`(가짜 데이터) / `api`(팀 백엔드). 백엔드가 응답 없으면 마지막 값 사용 |
| `server_dev.py` | 설치 없이 실행하는 테스트 서버 |
| `server_fastapi.py` | FastAPI 라우터. 팀 앱에는 `router`만 가져가서 `include_router` |
| `static/index.html` | 이용자 임시 화면 (모바일 웹). 디자인 값은 맨 위 `:root` 블록에 모여 있음 |
| `static/admin.html` | 관리자 임시 화면: 상황 보고서·체크리스트·호출 목록 (EPIC 08에 넘길 기능 미리보기) |
| `tests/questions.csv`, `tests/run_eval.py` | 테스트 질문 세트와 정답률 측정 |

## API (모두 `/api/v1/chat` 아래)

| 메서드·경로 | 설명 |
|---|---|
| `POST /api/v1/chat` | `{session_id, text}` 또는 `{session_id, chip_id}` 또는 `{session_id, event:"start"}` |
| `GET /suggest?q=` | 입력 자동완성 |
| `GET /updates?session_id=&cursor=&runtime_id=` | 먼저 말 걸 메시지 목록 (경보 변화, 빈자리 대기 알림). 화면이 2초마다 호출 |
| `GET /incidents` | 상황 보고서 목록 (보고서 문장 `report` 포함) |
| `POST /incidents/{id}/check` | 체크리스트 항목 완료/취소 `{index, checked, expected_revision, runtime_id}` |
| `POST /incidents/{id}/notes` | 관리자 메모 `{text, expected_revision, runtime_id}` |
| `POST /incidents/{id}/confirm` | 보고서 확정 `{notes, expected_revision, runtime_id}` (경보 해제 후) |
| `GET /admin/calls` | 관리자 호출 목록 + 자동 작성된 상황 요약 (EPIC 08용) |
| `GET /stats` | 대화 통계 (칩 사용률, 이해 못 한 비율, 평균 응답 시간, 의도별 건수) |
| `POST /demo/alert` | 시연용 경보 `{level:0|1|2, lot_id, spot_no}` |
| `POST /demo/exit` | 시연용 출차 `{vehicle_no}` (빈자리 만들기) |
| `GET /demo/cars` | 주차 중인 차량 목록 |
| `POST /demo/reset` | 가짜 데이터·세션·보고서 초기화 |

응답 형식:

```json
{ "text": "A 주차장에 1자리 있어요.",
  "card": {"type": "empty_spots", "data": {...}},
  "chips": [{"id": "lot_A", "label": "A 주차장만 보기"}],
  "banner": null,
  "updated_at": "2026-11-06T14:32:00+09:00",
  "alert_version": 0,
  "meta": {"intent": "get_empty_spots", "source": "rule", "latency_ms": 0.3} }
```

카드 종류: `empty_spots`, `car_location`, `fee`, `payment`, `exit_issue`, `journey`, `waitlist`, `alert`, `admin_call`
(`fee`·`payment`·`exit_issue` 카드에는 진행 표시용 `journey` 단계 목록이 함께 들어감)

## 가짜 데이터 (시연용)

- A·B·C 주차장 각 8칸. A는 1자리, B는 만차, C는 6자리 남음
- 미니카 01~24 중 17대 주차 중. **24번은 입차 기록 없음** (출차 문제 시연용)
- 임시 요금: 처음 30분 무료, 이후 10분마다 500원 (`.env`에서 변경, EPIC 05 확정 시 교체)

## 설정 (`.env.example` 복사 → `.env`)

- `DATA_SOURCE=mock|api`, `BACKEND_BASE_URL`
- `LLM_PROVIDER=none|anthropic|openai`, `LLM_MODEL`, API 키
- 요금 규칙 값

## 11/02 이후 할 일

1. `adapters.py` 의 `ApiAdapter` 경로·필드를 API v1에 맞춰 수정 (`TODO` 표시)
2. 경보 응답 형식을 EPIC 03 `GET /api/v1/alerts` 에 맞춰 변환
3. 범위가 확정되면 충전 구역·침수·장애인구역 칩을 `chips.json`에 추가
4. 화면은 김채린 님 사용자 웹에 넣을지 결정 → 응답 JSON 형식 그대로 사용
5. 팀원 테스트로 나온 실패 문장을 `questions.csv`에 계속 추가
6. 상황 보고서·대기열·세션은 지금 서버 메모리에 있음 → 서버를 껐다 켜면 사라짐. 본 작업에서 DB(EPIC 04)에 저장하도록 변경
7. 빈자리 대기: 지금은 알림 대상이 된 사람이 화면을 닫아 두면 그 자리를 놓침. 실제로는 메시지 알림(EPIC 03 알림 경로)과 함께 쓰는 게 좋음
8. 대기 알림은 실제 자리 예약이 아닙니다. 이미 안내한 빈자리는 다음 조회에서 중복 배정하지 않으며,
   빈자리 감소가 관찰되면 이용된 것으로 처리합니다. 조회 사이에 입차·출차가 동시에 일어나
   빈자리 수가 같아지는 상황까지 정확히 구분하려면 백엔드 입출차 이벤트가 필요합니다.
9. 현재 경보 모델은 활성 위치 1곳입니다. 위치가 바뀌면 이전 기록을 '위치 변경'으로 종료하고
   새 위치의 보고서를 생성합니다. 이는 이전 위치의 실제 경보 해제를 뜻하지 않습니다.

## 시연 순서 예시

1. "B 주차장 자리 있어?" → 만차 → "B 자리 나면 알려줘"
2. 테스트 도구에서 12번 차 출차 → 챗봇이 먼저 "B 주차장에 자리가 났어요"
3. "7번 차 이용 현황" → 진행 표시 → 미리 결제 → 출차 준비 단계로 이동
4. 테스트 도구에서 A 주차장 6번 칸 Lv2 경보 → 챗봇이 먼저 경보 + "내 차 바로 근처" 안내
5. 관리자 화면에서 보고서가 실시간으로 쌓이는 것 확인 → 체크리스트 처리 → 해제 → 메모 → 확정

## 추가된 조작

- 결제: 차량 확인 → 금액 확인 → 모의 결제 실행. 확인은 120초 뒤 만료됩니다.
- 관리자 호출: 등록 확인 후 관리자 화면에 문의를 기록합니다.
- `내 대기 상태`, `진행 중 요청 취소`를 사용할 수 있습니다.
- 응답을 받지 못하면 **같은 요청 다시 확인** 버튼으로 원래 요청의 결과를 조회합니다.
- 관리자 메모 충돌 시 **최신 보고서 확인**으로 갱신할 수 있습니다. 입력 중인 메모는 유지됩니다.

팀 백엔드 연결 후 읽기 전용 검사: `python tests/check_backend.py 07`

선택 브라우저 검사: Node.js에서 `playwright`와 Chromium 설치 후
`python tests/run_browser_checks.py`를 실행합니다. 별도 실행 파일은 `BROWSER_EXECUTABLE` 환경변수로 지정합니다.
