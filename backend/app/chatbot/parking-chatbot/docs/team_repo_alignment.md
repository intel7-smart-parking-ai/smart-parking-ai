# 팀 저장소 연동 확인 · 2026-11-04

비교 기준은 팀 저장소 공개 main `6bf8893a8189800cfeff1a446731579bd04732f5`와 챗봇 v2.12 `8c200d1`이다. 팀 쪽 동작은 해당 소스에서 직접 실행해 확인했다. 변경 파일은 `backend/app/chatbot/` 아래에 있다.

| 확인한 차이·문제 | 반영한 수정 또는 담당자 요청 |
|---|---|
| 팀 기본 앱의 `POST /api/v1/chat` 404 | 챗봇 담당 폴더에 `team_preview.py`를 추가했다. 로컬에서 팀 앱과 챗봇을 같은 프로세스로 실행한다. 공용 `main.py` 등록 변경은 아래 예시를 박해준님에게 전달한다. |
| `ChatPanel.jsx`가 `result.messages`만 읽음 | 팀 앱에 포함된 POST 응답에 `messages: [{text}]`를 덧붙였다. `text`, 카드, 칩, 토큰은 유지한다. Lv2 대피 안내가 본문에 없으면 배열의 첫 메시지로 보낸다. |
| 두 주차장의 부분 조회가 같은 진행 중 결과를 재사용 | API 빈자리 조회의 동시 공유와 저장 키를 전체 조회·주차장별 조회로 구분했다. A 조회를 대기시킨 동안 B가 B의 칸 상태를 받는 검사로 확인했다. |
| 요금·영수증이 다른 세션 또는 차량을 가리키거나 팀 영수증이 견적과 다름 | 응답의 세션·차량·주차장 식별자가 맞는지 검증한다. 명시적으로 불일치하면 과거 캐시 금액도 표시하지 않는다. 팀 형식 영수증은 완료 상태·금액·멱등키를 검증한다. POST 후 결과가 불일치하면 확인 불가로 안내하고 재전송하지 않는다. 식별자와 상태가 생략된 구형 목업 응답에는 기존 호환 처리를 유지한다. |
| 확인 대기 사이에 차량의 세션이 바뀜 | 기존 확인을 종료한다. 새 세션으로 결제를 보내거나 미전송을 불확실한 결제로 기록하지 않는다. |
| 팀 목업의 위치 응답에 `session_id: null`, 칸 응답에 `entry_at` 없음 | 요금 조회와 미인식 후보는 기존 확인 불가 안내를 유지한다. 팀 백엔드에서 확인된 세션 ID와 시간대가 있는 입차 시각을 내려줘야 한다. |
| 기본 main의 `/alerts`, `/rate-policies`가 404 | 챗봇의 기존 연결 대기 안내를 확인했다. billing은 승인된 임시 DB 연결을 넘긴 앱에서만 등록된다. 실사용용 연결을 임의로 활성화하지 않았다. |
| 팀 화면 D 주차장 표기와 구역 구분 | 서버와 챗봇 설정은 64칸 4구역이다. 프런트 `api.js`는 배열을 반으로 나눠 A/B만 설정하고, 모바일 도움말에는 L자형 10면이 남아 있다. 프런트 담당자에게 도면 구역 접두어와 도움말을 맞추도록 요청한다. |

팀 소스 참고. [main.py](https://github.com/intel7-smart-parking-ai/smart-parking-ai/blob/6bf8893a8189800cfeff1a446731579bd04732f5/backend/app/main.py) · [ChatPanel.jsx](https://github.com/intel7-smart-parking-ai/smart-parking-ai/blob/6bf8893a8189800cfeff1a446731579bd04732f5/frontend/src/ChatPanel.jsx) · [mock_queries.py](https://github.com/intel7-smart-parking-ai/smart-parking-ai/blob/6bf8893a8189800cfeff1a446731579bd04732f5/backend/app/api/mock_queries.py) · [API_CONTRACT.md](https://github.com/intel7-smart-parking-ai/smart-parking-ai/blob/6bf8893a8189800cfeff1a446731579bd04732f5/docs/API_CONTRACT.md)

## 로컬에서 팀 화면과 실행

ZIP의 `chatbot/` 내용을 팀 체크아웃의 `backend/app/chatbot/`에 반영한다. 팀 체크아웃 루트에서 다음 명령을 실행한다.

```sh
python -m pip install -r backend/requirements.txt
python -m uvicorn backend.app.chatbot.team_preview:create_app --factory --host 127.0.0.1 --port 8000 --workers 1
```

팀 모드의 기본 조회 주소는 `http://127.0.0.1:8000`이다. 다른 포트를 쓰면 `BACKEND_BASE_URL`도 맞춘다. Windows PowerShell에서 팀 프런트는 별도 터미널에서 실행한다.

```powershell
cd frontend
npm ci
$env:VITE_DATA_SOURCE = "team"
npm run dev
```

`http://127.0.0.1:5173`의 ‘주차 안내 챗봇’에서 `A 빈자리`, `10번 차 어디 있어?` → `네`를 입력한다. 팀 기본 목업의 기대값은 개발원 빈자리 9칸과 10번 차량 A-01이다. 현 팀 화면은 메시지 표시까지만 지원한다. 칩·카드·경보 배너의 구조를 별도로 표시하는 작업은 화면 담당 범위다.

## 팀 담당자에게 넘길 등록 예시

박해준님이 팀 앱의 `create_app()`에서 `application`을 생성한 뒤 다음 두 줄을 추가하면 공개 `/api/v1/chat` nginx 경로와 연결된다. 아래 코드는 전달용이며 팀 파일에는 적용하지 않았다.

```python
from backend.app.chatbot.router import router as chatbot_router

application.include_router(chatbot_router)
```

박해준님 요청 사항은 차량 위치의 실제 `session_id`, `/spaces`의 선택 `entry_at`, 팀 조회·billing·경보를 한 실행 주소에서 확인하는 것이다. 김채린님 요청 사항은 칩과 확인 토큰을 화면에서 전달하고, D 주차장의 4구역·64칸 표기를 서버 도면과 일치시키는 것이다.

## 실행 결과

| 검사 | 결과 |
|---|---|
| 수정 전 팀 기본 앱 | health·주차장 목록·10번 위치 200. 챗봇·요금표·경보 404. 10번 위치의 세션 ID null |
| 팀 백엔드 pytest | 312개 통과 |
| 팀 프런트 | `VITE_DATA_SOURCE=team` 빌드 성공, 테스트 41개 통과 |
| 팀 소스 + 챗봇을 같은 로컬 ASGI 앱에 등록한 Chromium 검사 | 1280px와 390px에서 각각 빈자리 9칸, 10번 확인과 A-01 응답 표시 |
| 팀 billing 라우터와 임시 SQLite | 테스트에서만 위치에 세션 ID를 연결. 견적 2,500원과 영수증 2,500원 일치. 같은 요청 재전송 후에도 모의 결제 DB 1건 |
| 챗봇 회귀 검사 | 467개 중 464 통과, 3 skip. 이후 신원 불일치 때 과거 캐시도 거절하도록 보완하고 관련 5개를 재실행해 통과 |

모의 결제 검사는 격리한 임시 DB를 사용했다. 실제 배포·카메라 데이터·팀 DB·운영 경보와 LLM 계정은 이번 검증에 포함되지 않았다.
