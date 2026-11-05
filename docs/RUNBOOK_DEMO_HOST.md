# 김채린 PC 리허설 RUNBOOK (D-49·D-50·D-51)

## 1. 역할·결정

- D-49: PM 11/05 10:01 + 윤명수 10:05 승인(사용자 전달). #53 API 계약 반영.
- D-50: 리허설 호스트는 김채린 PC, demo_main 8002. 챗봇 내부 연결/409 처리는 담당자.
- D-51: 이 문서를 따라 실제 확인한 항목만 체크한다. 11/05 오후 팀 실물 리허설은 별도.
- main 병합은 PM이 #52 → #53 → #54 순서로 진행. 선행 병합 후 다음 base=main 및 재검증.

## 2. PC 준비

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend/requirements-dev.txt
cd frontend
npm ci
cd ..
```

기존 환경도 의존성을 다시 설치한다(httpx·SQLAlchemy 포함). Docker Desktop은 Docker 시험/포트80 게이트웨이에 필요하다.
토큰·관리자 계정은 로컬 `.env`·`.local-access/`에만 저장한다. Git 업로드 금지.

## 3. 서버 (터미널1)

```powershell
$env:DATA_SOURCE="api"
$env:BACKEND_BASE_URL="http://127.0.0.1:8002"
$env:LLM_PROVIDER="none"
$env:API_HOST="0.0.0.0"
$env:API_PORT="8002"
# VISION_TOKEN은 로컬 .env와 동일한 ASCII16자 이상 비공개 값으로 지정
.\.venv\Scripts\python.exe -m backend.app.demo_main
```

토큰 미지정이면 서버에서 직접 입력한다. 다른 터미널에도 같은 값을 사용한다.
demo_main DB는 임시이며 재시작 시 초기화된다. 매 회차 hold부터 종료하고 서버를 재시작한다.

## 4. 관측 유지 (터미널2)

```powershell
.\.venv\Scripts\python.exe -m backend.app.simulation.run_parking_demo --short --hold
```

정상: A-01~03 occupied, 빈자리9/12, 10번=A-01. hold는 관측을 계속 보낸다.
합성 이벤트와 실제 카메라를 동일 칸에 동시에 보내지 않는다. 카메라 확인 전 hold 종료.
출차는 hold Ctrl+C 후 `--short --depart --keep-empty` 실행, 확인 후 종료한다.

## 5. 화면 (터미널3)

```powershell
cd frontend
$env:VITE_DATA_SOURCE="team"
$env:API_PROXY_TARGET="http://127.0.0.1:8002"
npm run dev
```

직접 개발 화면 http://127.0.0.1:5173/ (동일 LAN에서는 서버IP:5173).
관리자 로그인은 Docker 게이트웨이8090이 필요하다. 개발 화면과 포트80 화면은
API_UPSTREAM이 같은 demo_main:8002를 향해야 같은 세션을 본다.

## 6. Docker 및 포트80

```powershell
python deployment/setup_local.py
docker compose up --build -d --wait
docker compose ps
```

기본은 최종 통합 api:8000와 영속 SQLite. 사용자 http://서버IP/,
관리자 http://서버IP/admin/login. WEB_BIND_ADDRESS=0.0.0.0이면 LAN에서 접근 가능하다.
8090은 서버 로컬 게이트웨이로 유지한다.

리허설 demo_main을 포트80에서도 보려면 로컬 `.env`의
`API_UPSTREAM=host.docker.internal:8002`로 설정 후 `docker compose up -d --force-recreate web`.
이때 Compose api는 실행 중이어도 화면은 호스트 demo_main을 조회하며 서로 DB가 다르다.
Docker 통합본 시험은 `API_UPSTREAM=api:8000`으로 되돌린 뒤 web 재생성.
실제 실행 모드를 10장에 기록한다. `docker compose down`은 볼륨 보존, `-v` 금지.

## 7. 다른 PC·카메라 연결

다른 PC의 127.0.0.1은 그 PC 자신이다. 서버 LAN IP를 사용한다.
11/05 현재 김채린 PC: `10.10.21.32` (네트워크 변경 시 재확인).

- 사용자: http://10.10.21.32/
- 관리자: http://10.10.21.32/admin/login
- 카메라: http://10.10.21.32:8002/api/v1/internal/vision-events
- 같은 LAN/VPN·동일 VISION_TOKEN·PC 시각 동기화 확인. 라우터 포트포워딩은 필요 없음.
- Windows 방화벽 TCP80/8002(개발 화면은5173)를 같은 서브넷에 허용한다.
  전체 방화벽을 끄지 않는다. 서버에서 LAN IP 접속 성공은 원격 PC 성공을 보장하지 않는다.
  #54의 `deployment/enable_lan_access.ps1`을 관리자 PowerShell에서 실행할 수 있다.

```powershell
# 다른 PC에서 확인
Test-NetConnection 10.10.21.32 -Port 80
Test-NetConnection 10.10.21.32 -Port 8002
```

## 8. 오후20분 팀 리허설 체크

- [ ] 회차 전 hold·서버 재시작
- [ ] 빈자리9/12 → 10번 A-01
- [ ] 요금 → 모의 결제 → 영수증
- [ ] 관리자 active 세션
- [ ] 챗봇 담당자 질의/응답 확인
- [ ] 출차 → closed
- [ ] LED 모의 경보 → 대피 표시 → UI 결제 차단
- [ ] 다른 PC 카메라 실물 관측 수신

demo_main과 통합 main의 서버측 결제 차단 제공 범위는 다르다.
서버의 409 EVACUATION_ACTIVE 검증은 통합 main/Docker 시험에 기록한다.

## 9. 문제 기록

| 시각/회차 | 서버/화면 | 현상 | 조치/담당 | 재확인 |
| --- | --- | --- | --- | --- |
| 11/05 준비 | 원격 URL | 다른PC의127.0.0.1에 접속 | 서버IP10.10.21.32 사용 | 8002/5173 호스트 LAN주소200, 원격PC 미실측 |
| 11/05 준비 | Docker LAN80 | localhost만200, LAN IP는timeout | 관리자 방화벽 규칙 적용 필요. OS 접근거부 | 미해결, 대체5173 안내 |
| 11/05 준비 | 문서 | 전달받은 RUNBOOK이 main에 없음 | 이 문서 신규 추가 | 팀 원본과 추후 대조 |

## 10. 실제 실행 기록

아래는 도구 실행 결과로 갱신한다. 팀 실물 리허설·다른 PC 실측은 대신 체크하지 않는다.

| 항목 | 결과 |
| --- | --- |
| Python 의존성·npm ci | 설치 완료 |
| 프론트·백엔드 회귀 | Windows pytest350 passed(경고2), 프론트48 passed, team build 성공 |
| demo_main8002 + hold + 화면5173 | 실행 중. 빈자리9/12, 10=A-01, 로그인·관리자active3건·요금·모의결제/영수증 확인 |
| 웹80·관리자 로그인·LAN IP | localhost80 및 /admin/login 200. LAN8002/5173 200. LAN80 시간초과(미해결) |
| 방화벽 허용 | 관리자 권한 부족. RunAs 시도 후에도 규칙 없음. 관리자 실행/다른PC 재검증 필요 |
| Docker 통합 api 별도 시험 | 3서비스healthy. 별도API 입차→출차→LED→결제409→해제→결제·D49금액 일치·재시작 결제보존 통과 |
| 팀 실물 카메라·챗봇·20분 리허설 | 담당자와 현장 확인 대기 |

## 종료·복구

각 터미널 Ctrl+C (background 실행 시 work/rehearsal-processes.json의 해당 프로세스만 종료).
포트80의 API_UPSTREAM을 바꿨다면 `.env`에서 원래 값으로 복구하고 web 재생성.
토큰/관리자 비밀번호는 로컬 파일만 보존. API 계약/실행 문서는 PR #53, Docker 배포는 #54.
