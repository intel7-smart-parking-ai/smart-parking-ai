# 내 PC에서 팀 사이트 그대로 띄우기 (챗봇 v3.2 포함)

팀 배포와 같은 구성(`compose.yaml`: api · admin-auth · web)에 챗봇 대화 에이전트 오버레이(`compose.agent.yaml`)를 얹는다. 저장소 루트에서 실행한다.

## 1. 처음 한 번 — 비공개 설정

| 파일 | 내용 | Git |
|---|---|---|
| `.local-access/htpasswd` | 관리자 화면 계정 (`python deployment/setup_local.py`로 생성) | 제외 |
| `.env` (루트) | `.env.example` 복사 후 `VISION_TOKEN`·`ALERTS_INTERNAL_TOKEN`(임의 문자열), `OPENROUTER_API_KEY`, `LLM_PROVIDER=openrouter` | 제외 |

이 PC에서만 열려면 루트 `.env`에 다음을 둔다.

```
WEB_BIND_ADDRESS=127.0.0.1
WEB_PORT=8088
```

Langfuse 기록(선택): 루트 `.env`에 `MONITOR_BACKEND=langfuse`, `INSTALL_MONITORING=1`, `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_HOST`.

## 2. 빌드·실행

```powershell
docker build --target runtime -t smart-parking-api-base:chatbot -f backend/Dockerfile .
docker compose -f compose.yaml -f backend/app/chatbot/parking-chatbot/compose.agent.yaml up -d --build
```

챗봇 코드만 바꿨으면 두 번째 명령에 `api`만 붙여 다시 빌드한다(`... up -d --build api`).

| 화면 | 주소 |
|---|---|
| 이용자 (모바일 웹 · 챗봇 패널) | http://127.0.0.1:8088 |
| 관리자 (로그인 필요) | http://127.0.0.1:8088/admin/ |

## 3. 시연 데이터 만들기

DB가 비어 있으면 빈자리만 보인다. 팀 리허설과 같은 방법으로 차량을 넣는다.

```powershell
$C = "docker compose -f compose.yaml -f backend/app/chatbot/parking-chatbot/compose.agent.yaml"
# 실물 A: 팀 시뮬레이터(카메라 대신 관측을 보냄)
iex "$C exec -d api python -m backend.app.simulation.run_parking_demo --base-url http://127.0.0.1:8000 --short --hold"
# 가상 C·D: 시뮬레이션 입차(번호판 뒤 4자리 2000번대가 생성됨)
iex "$C exec -T api python -c `"import urllib.request as u;[u.urlopen(u.Request('http://127.0.0.1:8000/api/v1/simulation/'+l+'/entry',b'{}',{'Content-Type':'application/json'},method='POST')) for l in 'CDCDCD']`""
```

## 3-1. 개발 모드 (코드 고치면 바로 반영)

Docker 없이 PC에서 직접 띄운다. 챗봇·백엔드 코드를 저장하면 서버가 다시 시작되고, 화면 코드는 새로고침 없이 반영된다. 팀 nginx는 `/api/v1/chat/updates`(경보·알림 폴링)를 막지만 개발 모드는 통과시키므로, 알림 말풍선은 이 모드에서만 보인다.

터미널 1 (저장소 루트, 백엔드 + 챗봇, 루트 `.env`를 읽음):

```powershell
.\.venv\Scripts\python.exe -m uvicorn backend.app.main:app --env-file .env --reload --reload-dir backend
```

터미널 2 (화면, http://127.0.0.1:5173):

```powershell
cd frontend; npm install; $env:DEMO_API_PROXY_TARGET="http://127.0.0.1:8000"; npm run dev
```

챗봇 코드를 고쳤는데 답이 그대로면 터미널 1을 Ctrl+C로 끄고 다시 실행한다(Windows에서 자동 재시작이 안 잡힐 때가 있음).

`DEMO_API_PROXY_TARGET`이 없으면 화면이 `/demo-api`를 8003으로 보내 "API 요청을 처리하지 못했습니다"가 뜬다. DB는 `.env`의 `SMART_PARKING_DB_PATH`(기본 `data/smart_parking.db`, Git 제외). 시연 차량은 3절 명령에서 `docker compose ... exec api` 대신 `.\.venv\Scripts\python.exe`로 실행한다. 관리자 화면 로그인은 Docker의 admin-auth(8090)가 떠 있어야 한다.

## 4. 확인

- 챗봇 패널에 "지금 빈자리 있어?", "2003 차량 어디 있어?"(가상 차량 뒤 4자리), "Where is car 2008?"(영어 → 자동 번역)
- 모델 답인지: 개발자 도구 → `POST /api/v1/chat` 응답 `meta.source`(`llm`/`rule`), `meta.agent_status`
- 상태: `docker compose -f compose.yaml -f backend/app/chatbot/parking-chatbot/compose.agent.yaml ps`
- 끄기: `... down` (DB 볼륨 유지), 팀 기본 이미지 복귀는 `agent_runbook.md` 참고
