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

## 4. 확인

- 챗봇 패널에 "지금 빈자리 있어?", "2003 차량 어디 있어?"(가상 차량 뒤 4자리), "Where is car 2008?"(영어 → 자동 번역)
- 모델 답인지: 개발자 도구 → `POST /api/v1/chat` 응답 `meta.source`(`llm`/`rule`), `meta.agent_status`
- 상태: `docker compose -f compose.yaml -f backend/app/chatbot/parking-chatbot/compose.agent.yaml ps`
- 끄기: `... down` (DB 볼륨 유지), 팀 기본 이미지 복귀는 `agent_runbook.md` 참고
