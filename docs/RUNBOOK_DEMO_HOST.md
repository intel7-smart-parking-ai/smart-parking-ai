# 시연·리허설 서버 구축 절차 (다른 PC에서 그대로 따라 하기)

> 목적: 리허설·시연 서버를 **어느 PC에서든 같은 방법으로** 다시 구축한다 (PM 요청 11/05 10:05, D-51).
> 작성 시작: 2026-11-05, 첫 구축 PC = 김채린 PC (D-50). **☐ 표시는 실제 구축하면서 확인·수정할 칸**이다. 해 보고 다르면 고치고, 막힌 점은 9장에 적는다.
> 기준: main `4e048c8` + PR #52·#53·#54 (D-49 승인). 함께 볼 문서: [SETUP.md](SETUP.md)(개발 환경), `docs/CHAERIN_IMPLEMENTATION_AUDIT.md`·`docs/INTEGRATION_REVIEW_REQUEST.md`(PR #53·#54), [INTEGRATION_GUIDE.md](INTEGRATION_GUIDE.md)

---

## 0. 어떤 방법으로 띄우나

| 방법 | 언제 | 포트 |
| --- | --- | --- |
| **A. 시연 서버 `demo_main`** | 11/05 리허설 (D-50), 11/06 중간 점검 | API 8002 · 화면 5173 |
| **B. Docker 통합본** | 따로 시험, 최종 시연 후보 (D-28·D-38) | 웹·관리자 80 · 게이트웨이 8090 |

둘은 **DB가 따로**다. 한 PC에서 둘 다 켜도 데이터가 섞이지 않는다.

---

## 1. 준비물 (한 번만)

| 항목 | 버전·확인 명령 | ☐ |
| --- | --- | :-: |
| Git | `git --version` | ☐ |
| Python 3.11 | `py -3.11 --version` (팀 기준. 3.13에서도 테스트 통과 확인됨) | ☐ |
| Node.js (Vite 7용) | `node --version` | ☐ |
| Docker Desktop (방법 B만) | `docker --version`, `docker compose version` | ☐ |
| 같은 와이파이 (카메라 PC·휴대폰 연결 시) | `ipconfig` → IPv4 주소 기록: `__________` | ☐ |

## 2. 저장소·의존성

```powershell
git clone https://github.com/intel7-smart-parking-ai/smart-parking-ai.git
cd smart-parking-ai
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend/requirements-dev.txt
cd frontend; npm ci; cd ..
```

- 예전에 만든 `.venv`도 **다시 설치**한다 (11/05 `httpx` 추가, 예전 환경엔 SQLAlchemy 없음)
- 확인: `.\.venv\Scripts\python.exe -m pytest tests/ -q` → 350 passed 근처 ☐

## 3. 환경변수 (PowerShell, 창마다 다시 설정)

| 변수 | 값 | 쓰는 곳 |
| --- | --- | --- |
| `VISION_TOKEN` | ASCII 16자 이상 임의 문자열 (서버·이벤트 보내는 쪽 **같은 값**). Git·디스코드에 올리지 않기 | 서버·`run_parking_demo`·카메라 |
| `DATA_SOURCE` | `api` | 챗봇 (없으면 8000을 찾아가 조회 실패) |
| `BACKEND_BASE_URL` | `http://127.0.0.1:8002` (방법 A) | 챗봇 |
| `API_HOST` | `127.0.0.1`(이 PC만) / `0.0.0.0`(다른 PC 접속 허용) — **PR #53 병합 후** | `demo_main`·`main` |
| `API_PORT` | `8002` (방법 A 기본) | 같음 |
| `LLM_PROVIDER` | `none` (리허설 기본, 규칙 기반) | 챗봇 |
| `VITE_DATA_SOURCE` | `team` | 화면 |
| `API_PROXY_TARGET` | `http://127.0.0.1:8002` | 화면 |

---

## 4. 방법 A — 시연 서버 (리허설)

**터미널 1 — 서버**
```powershell
$env:VISION_TOKEN="<토큰>"
$env:DATA_SOURCE="api"; $env:BACKEND_BASE_URL="http://127.0.0.1:8002"
$env:LLM_PROVIDER="none"
.\.venv\Scripts\python.exe -m backend.app.demo_main
```
- 확인: 브라우저 `http://127.0.0.1:8002/docs` 열림 ☐

**터미널 2 — 가짜 이벤트 (카메라 대신, D-11)**
```powershell
$env:VISION_TOKEN="<같은 토큰>"
.\.venv\Scripts\python.exe -m backend.app.simulation.run_parking_demo --base-url http://127.0.0.1:8002 --short --hold
```
- "점유 유지 중" → A-01·A-02·A-03에 10·11·12번. **창을 닫지 않는다** (끊기면 칸이 `uncertain`으로 바뀜) ☐
- 출차 시연: 위 창 중지 후 `... --short --depart --keep-empty`

**터미널 3 — 화면**
```powershell
cd frontend
$env:VITE_DATA_SOURCE="team"; $env:API_PROXY_TARGET="http://127.0.0.1:8002"
npm run dev
```
- 사용자 `http://127.0.0.1:5173/` · 관리자 `http://127.0.0.1:5173/admin` (☐ 실제 주소 확인)

**리허설 확인 순서 (D-35, 20분 이내)**
빈자리 9/12 → 10번 A-01 → 요금 → 모의 결제 → 관리자 active 세션 → 챗봇 대본 6문항 → 출차 → (모의 경보) 결제 막힘 → 끝나면 **서버 재시작**

## 5. 다른 PC에서 카메라 이벤트 받기 (D-28 "다른 PC에서 전송")

1. 서버 PC: PR #53 병합 후 main(또는 그 브랜치)에서 `$env:API_HOST="0.0.0.0"` 추가하고 4장 터미널 1 실행
   - main(`4e048c8`)의 `demo_main`은 `127.0.0.1` 고정 → 다른 PC가 못 붙음
2. 서버 PC 방화벽 열기 (관리자 PowerShell, 리허설 후 삭제):
   ```powershell
   New-NetFirewallRule -DisplayName "parking-demo-8002" -Direction Inbound -Protocol TCP -LocalPort 8002 -Action Allow -Profile Private
   # 끝나면
   Remove-NetFirewallRule -DisplayName "parking-demo-8002"
   ```
3. 카메라 PC: 보내는 주소 `http://<서버 PC IP>:8002/api/v1/internal/vision-events`, 헤더 `Authorization: Bearer <같은 토큰>`, 칸당 1초마다
4. 두 PC 시계 차이 **10초 이내** (Windows 설정 → 시간 → 지금 동기화) ☐
5. 확인: 서버 `/api/v1/parking-lots/A/spaces`에 카메라 관측(`source`가 카메라)이 보임 ☐

## 6. 방법 B — Docker 통합본 (PR #54)

```powershell
python deployment/setup_local.py        # 관리자 계정 입력 → .env·.local-access/ 생성 (Git 제외, 기존 파일 보존)
docker compose up --build -d
docker compose ps                       # api·web·admin-auth 모두 healthy ☐
```
- 사용자 `http://127.0.0.1/` · 관리자 `http://127.0.0.1/admin/` · 8090 = 관리자 게이트웨이 전용
- 다른 카메라 PC: `http://<서버 IP>/api/v1/internal/vision-events` + `.env`의 `VISION_TOKEN`, 방화벽 80 허용
- 실제 모드: `.env`에 `PARKING_MODE=real` → C/D 자동 관측·모의 경보 버튼 꺼짐 (바꾼 뒤 컨테이너 다시 생성)
- 끄기: `docker compose down` (**데이터 유지**) · ⚠️ `docker compose down -v`는 **DB까지 삭제** — 리허설 초기화 때만
- 백업: API를 멈춘 뒤 SQLite 볼륨 통째로 보관

## 7. 휴대폰 접속 (D-48, 선택)

| 기능 | 방법 |
| --- | --- |
| 조회·결제 | 같은 와이파이에서 `http://<서버 PC IP>:<화면 포트>` (방화벽에 화면 포트 허용) |
| 음성 (안드로이드) | Chrome `chrome://flags/#unsafely-treat-insecure-origin-as-secure` → "Insecure origins treated as secure" 설명 아래 입력칸에 `http://<서버 PC IP>:5173`(개발 화면) 또는 `http://<서버 PC IP>`(Docker) — `http://`·포트 포함, 끝 `/` 없이, 여러 개는 쉼표 → 드롭다운 Enabled → Relaunch (☐ 미검증, 11/06 시험). PC IP가 바뀌면 다시 입력 |
| 음성 (아이폰) | HTTPS 터널 `cloudflared tunnel --url http://localhost:80` — 시연 시간만, 관리자 8090은 열지 않기 |

- 화면 마이크 버튼이 들어간 뒤에만 음성 가능. 결제 확인은 화면 버튼으로

## 8. 초기화·재시작

| 상황 | 방법 |
| --- | --- |
| 리허설 회차 사이 | 방법 A: 터미널 1·2 중지 → 다시 실행 (메모리 상태 초기화) ☐ 시연 DB 파일 위치 확인 `__________` |
| Docker 데이터 초기화 | `docker compose down -v` → `up --build -d` |
| 포트 충돌 | `Get-NetTCPConnection -LocalPort 8002` 로 사용 중인 프로그램 확인 |

## 9. 알려진 문제·해결 (구축하면서 계속 추가)

| 증상 | 원인 | 해결 |
| --- | --- | --- |
| 챗봇이 "현재 정보를 확인하지 못했어요" | `DATA_SOURCE`·`BACKEND_BASE_URL` 없음 → 8000 조회 | 3장 변수 설정 후 서버 재시작 |
| pytest 수집 오류 `No module named 'sqlalchemy'` | 예전 가상환경 | 2장 재설치 |
| 다른 PC 카메라가 연결 안 됨 | `127.0.0.1` 고정 또는 방화벽 | 5장 `API_HOST`·방화벽 |
| 잠시 뒤 칸이 `uncertain` | 이벤트가 2초 넘게 끊김 | `--hold` 창 유지, 카메라 전송 간격 확인 |
| `New-NetFirewallRule : 액세스가 거부되었습니다` | 관리자 권한 없음 | PowerShell을 "관리자 권한으로 실행". 공용 PC라 권한이 없으면 리허설 서버 PC(관리자 가능)에서 하거나, `cloudflared tunnel --url http://localhost:5173`(방화벽 규칙·플래그 불필요, 시험 때만) |
| 관리자 PowerShell에서 `cd frontend` 경로 없음 | 관리자 창은 `C:\Windows\system32`에서 시작 | 먼저 `cd <저장소 폴더>` |
| `npm` 인식 안 됨 | Node.js 미설치 또는 설치 후 창을 새로 열지 않음 (11/05 윤명수 PC: Python 3.11·Git은 있었고 Node만 없었음 → `winget install -e --id OpenJS.NodeJS.LTS`로 26.13.0 설치) | 서버·화면은 준비된 PC(김채린 PC, D-50)에서. 휴대폰 시험도 그 PC IP 기준 |
| Git Bash `curl`로 한글 보내면 400 | 콘솔 인코딩 | PowerShell·브라우저·Python으로 시험 |
| 휴대폰 마이크 안 켜짐 | http 주소 | 7장 |
| 휴대폰에서 `http://<PC IP>:5173`이 안 열림 | ① 화면(`npm run dev`) 미실행 ② 휴대폰이 모바일 데이터·다른 와이파이 ③ PC 방화벽(공용 네트워크면 기본 차단) ④ 기관 와이파이의 기기 간 접속 차단(AP 격리, `10.10.x.x`) | PC에서 같은 주소 먼저 열기 → 같은 와이파이 → `New-NetFirewallRule ... -LocalPort 5173` → 안 되면 휴대폰 핫스팟에 PC 연결(IP 바뀜, 플래그 주소도 변경) |
| (추가) | | |

## 10. 구축 기록

| 날짜 | PC | 방법 | 결과 | 기록자 |
| --- | --- | --- | --- | --- |
| 11/05 | 김채린 PC | A (리허설) | ☐ | 김채린 |
| 11/05 | 김채린 PC | B (Docker 시험) | ☐ | 김채린 |
| | | | | |
