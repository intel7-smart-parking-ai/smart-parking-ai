# v2.10 변경 요약 (2026-11-04, main `504b5e2` 기준)

PM 11/04 할 일 1~4 반영. 변경은 `backend/app/chatbot/` 안만.

| # | 할 일 | 바꾼 파일 | 내용 |
|---|---|---|---|
| 1 | 화면 ↔ 챗봇 응답 형식 | `docs/chat_response_fields.md` (새 문서) | 요청·응답 필드 정리. 화면은 `messages` 대신 `text`를 읽고, 칩은 `chip_id`+`confirmation_token`으로 전송. 챗봇 응답 형식은 바꾸지 않음 |
| 2 | main.py 진입점 | `backend/app/chatbot/router.py` (새 파일), `chatbot/config.py`, `README.md` | `from backend.app.chatbot.router import router` → `app.include_router(router)`. 팀 서버 안에서는 기본 `DATA_SOURCE=api`, `BACKEND_BASE_URL=http://127.0.0.1:8000`, 시연 경로 꺼짐. `.env`·환경변수가 우선 |
| 3 | 입차 확인 중 | `chatbot/contracts.py`, `adapters.py`, `templates.py`, `engine.py`, `static/index.html` | 위치 응답의 `space_id`가 null이거나 상태가 `pending/entering/detected/assigning/unassigned`이면 "N번 차는 (주차장) 입차 확인 중이에요". 위치 카드·요금 조회·결제 요청 안 함. 관리자 요약·상단 차량 표시도 "입차 확인 중" |
| 4-1 | Windows 테스트 | `server_dev.py`, `tests/test_robust_environment.py` | 포트 사용 중이면 `PORT_UNAVAILABLE` 공통 문구로 종료 → OS별 문구에 의존하지 않음 |
| 4-2 | 전기차 판정 | `chatbot/briefing.py` | 서버 `vehicle_type`이 있으면 그 값 우선, 없을 때만 `lots.json` `ev_vehicles` |

## 검증 (작업 환경, Python 3.13, FastAPI 미설치)
- unittest 432개 OK, skip 22 (FastAPI·LangChain 미설치, 새 main 등록 테스트 1개 포함)
- 새 테스트 `tests/test_v210_embed.py` 9개(+FastAPI 있을 때 1개)
- 질문 세트 242/242, 브라우저 검사 12개 PASS
- 팀 레포 루트에서 `from backend.app.chatbot.router import router` → 경로 20개, api 모드, 시연 꺼짐 확인(FastAPI 대체 모듈로 import 경로만 확인)
- main `504b5e2` 조회 목업(4곳 104칸, 차량 10·20) 복제 서버로 `tools/api_smoke.py`: 빈자리 서버 숫자 일치, 10번 → 개발원주차장 A-01

## 미확인 (PC에서 확인 필요)
- 실제 FastAPI로 팀 `main.py`에 등록해 `POST /api/v1/chat` 호출 (박해준님 등록 후)
- 칸 배정 전 위치 응답의 실제 모양(박해준님 결정 후 상태값만 맞추면 됨)
