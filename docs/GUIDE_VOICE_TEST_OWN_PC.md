# 내 PC에서 휴대폰 음성 → 챗봇 시험하기 (윤명수 PC 기준)

> 목적: 휴대폰 음성인식·조회·결제 퍼포먼스(D-48)가 **가능한지 본인 PC에서 직접 확인** (PM 요청 11/05 10:31)
> 작성: 2026-11-05. 제 PC에서 전체 흐름 실측 완료 — 개발 화면(5173) 프록시 → 시연 서버(8002) 챗봇: "A 빈자리 있어?" → "개발원주차장에 12자리 있어요", "십칠 번 차 어디 있어" → "17번 차 맞으세요?" + 버튼
> 휴대폰 실기기 시험은 아직 안 함 (이 문서로 처음 시험)
> 함께 쓰는 파일 (디스코드 첨부): `mic_test.html`(시험 페이지), `serve_mic_test.ps1`(설치 없는 간이 서버)
> 다른 PC 구축 전체 절차는 [RUNBOOK_ANY_PC.md](RUNBOOK_ANY_PC.md) (김채린 PC 기준 절차는 `RUNBOOK_DEMO_HOST.md`)

---

## 0. 시험 단계 — 어디까지 할지 고르기

| 단계 | 확인하는 것 | 필요한 설치 | 시간 |
| --- | --- | --- | --- |
| **A. 음성만** | 휴대폰 마이크가 켜지고 한국어가 글자로 바뀌는지 | **없음** (PowerShell만) | 15분 |
| **B. 음성 → 챗봇** | 말한 문장이 시연 서버 챗봇에 가서 답이 오는지, 결제 확인은 버튼으로만 | Python 3.11 · Node.js · Git | 설치 30분 + 15분 |
| **C. 실제 화면** | 사용자 화면(조회·결제)을 휴대폰으로 | B와 같음 | +5분 |

> 실제 화면의 챗봇 마이크 버튼은 아직 없음(김채린 작업 예정) → **음성은 A·B의 시험 페이지로 확인**한다.

---

## 1. 어떤 코드가 무엇을 띄우나 (호스팅 구성)

```
[휴대폰 Chrome] ──와이파이──▶ [내 PC :5173  Vite 개발 서버]  ← 밖에서 접속하는 포트는 이것 하나
                                   │  /api/* 는 PC 안에서 대신 전달(프록시)
                                   ▼
                              [내 PC 127.0.0.1:8002  시연 서버 demo_main]  ← 밖에 안 열어도 됨
                                   ├─ 조회·점유·세션 (/api/v1/parking-lots …)
                                   ├─ 요금·모의 결제 (/api/v1/sessions/…/fee, /mock-payments)
                                   └─ 챗봇 (/api/v1/chat)
                              [run_parking_demo]  가짜 차량 이벤트 → 8002
```

| 파일 | 하는 일 | 외부 접속 관련 설정 |
| --- | --- | --- |
| `backend/app/demo_main.py` | **시연 서버** 8002 — 조회·이벤트 수신·점유·세션·요금·결제·챗봇을 한 서버로 | `127.0.0.1` 고정(main 기준) → PC 안에서만. 휴대폰은 5173 프록시로 들어오므로 그대로 OK |
| `backend/app/chatbot/router.py` → `parking-chatbot/chatbot/http_router.py` | 챗봇 `POST /api/v1/chat`, `GET /api/v1/chat/config` | `DATA_SOURCE=api`, `BACKEND_BASE_URL=http://127.0.0.1:8002` 필요 |
| `backend/app/simulation/run_parking_demo.py` | 가짜 차량 이벤트 전송 (A-01~A-03에 10·11·12번) | `--base-url http://127.0.0.1:8002` |
| `frontend/vite.config.js` | **화면 서버** 5173 + 프록시 | `host: '0.0.0.0'`(**다른 기기 접속 허용**, 이미 설정됨), `/api` → `API_PROXY_TARGET` |
| `frontend/package.json` | `npm run dev` = `vite --host 0.0.0.0` | |
| `frontend/public/` | 정적 파일 폴더 → `mic_test.html`을 **임시로** 넣으면 `http://<IP>:5173/mic_test.html` | ⚠️ 커밋 금지, 시험 후 삭제 |
| `mic_test.html` (첨부) | 시험 페이지: 보안 연결·음성인식·마이크 권한 표시, 음성 → 글자, (B) 챗봇 전송·버튼, 결제 확인 단계 자동 전송 금지 | 같은 주소의 `/api/v1/chat`이 있으면 자동으로 챗봇 연결 |
| `serve_mic_test.ps1` (첨부) | (A) Python·Node 없이 `mic_test.html`만 띄우는 PowerShell 서버 8765 | `http://+:8765/` (관리자 필요) |

---

## 2. 외부(휴대폰) 접속 설정 — A·B 공통

1. **같은 와이파이**: 휴대폰 모바일 데이터 끄고 PC와 같은 와이파이
2. **PC IP 확인**: PowerShell `ipconfig` → 와이파이 어댑터의 IPv4 (예: `10.10.21.33`)
3. **방화벽 열기** — **관리자 PowerShell**(시작 → PowerShell 우클릭 → 관리자 권한으로 실행), **한 줄씩만** 붙여 넣기
   ```powershell
   # A단계
   New-NetFirewallRule -DisplayName "mic-test-8765" -Direction Inbound -Protocol TCP -LocalPort 8765 -Action Allow
   # B·C단계
   New-NetFirewallRule -DisplayName "parking-web-5173" -Direction Inbound -Protocol TCP -LocalPort 5173 -Action Allow
   ```
4. **휴대폰 Chrome 플래그** (안드로이드)
   - 주소창 `chrome://flags/#unsafely-treat-insecure-origin-as-secure`
   - "Insecure origins treated as secure" 아래 입력칸: `http://10.10.21.33:8765,http://10.10.21.33:5173` (본인 IP로, 띄어쓰기 없이)
   - 드롭다운 **사용 설정됨** → 아래 **다시 시작**
5. 휴대폰에서 안 열리면 → 기관 와이파이가 기기 간 접속을 막는 경우 → **휴대폰 핫스팟에 PC 연결**, IP 다시 확인, 플래그 주소도 새 IP로

---

## 3. A단계 — 음성만 (설치 없음)

1. `mic_test.html`, `serve_mic_test.ps1`을 같은 폴더에 저장 (예: 바탕화면 `mic_test`)
2. 관리자 PowerShell:
   ```powershell
   cd $HOME\Desktop\mic_test
   powershell -ExecutionPolicy Bypass -File .\serve_mic_test.ps1
   ```
   → "휴대폰에서: `http://<IP>:8765/mic_test.html`" 줄 확인
3. 휴대폰에서 그 주소 → **보안 연결 ✅ / 음성인식 지원 ✅** → 🎤 → 마이크 허용 → "에이 주차장 빈자리 있어?" 말하기 → 결과 목록에 문장
4. "챗봇 서버 연결 ❌"은 A단계에선 정상
5. 끝: `Ctrl + C`

---

## 4. B·C단계 — 음성 → 챗봇 (설치 필요)

### 4-1. 설치 (한 번만, 관리자 PowerShell)
```powershell
winget install -e --id Python.Python.3.11
winget install -e --id OpenJS.NodeJS.LTS
winget install -e --id Git.Git
```
- `winget`이 없으면 python.org(3.11, **"Add python.exe to PATH" 체크**) · nodejs.org(LTS) · git-scm.com에서 설치
- 설치 후 **PowerShell 창을 새로 열고** 확인: `py -3.11 --version`, `node --version`(20 이상), `git --version`
- 공용 PC라 설치가 안 되면 → A단계까지만 하고 B·C는 김채린 PC(리허설 서버, D-50)에서

### 4-2. 저장소·의존성 (일반 PowerShell)
```powershell
cd $HOME\Desktop
git clone https://github.com/intel7-smart-parking-ai/smart-parking-ai.git
cd smart-parking-ai
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend/requirements-dev.txt
cd frontend; npm ci; cd ..
```
(Git 없이: GitHub → Code → Download ZIP → 압축 풀고 그 폴더에서 `py -3.11 -m venv .venv`부터)

### 4-3. 실행 — PowerShell 창 3개, 모두 저장소 폴더에서

**창 1 — 시연 서버 (8002)**
```powershell
$env:DATA_SOURCE="api"; $env:BACKEND_BASE_URL="http://127.0.0.1:8002"; $env:LLM_PROVIDER="none"
.\.venv\Scripts\python.exe -m backend.app.demo_main
```
→ "시연 토큰:"이 나오면 **영문·숫자 16자 이상** 아무 문자열 입력 (예: `testtoken12345678`), 창 2에도 같은 값

**창 2 — 가짜 차량 (데이터가 있어야 "10번 차" 등이 나옴)**
```powershell
.\.venv\Scripts\python.exe -m backend.app.simulation.run_parking_demo --base-url http://127.0.0.1:8002 --short --hold
```
→ 같은 토큰 입력 → "점유 유지 중"이면 창 그대로 두기

**창 3 — 화면 서버 (5173, 휴대폰이 접속하는 곳)**
```powershell
Copy-Item $HOME\Desktop\mic_test\mic_test.html .\frontend\public\mic_test.html   # 시험 페이지 임시 복사 (커밋 금지)
cd frontend
$env:VITE_DATA_SOURCE="team"; $env:API_PROXY_TARGET="http://127.0.0.1:8002"
npm run dev
```
→ `Network: http://<IP>:5173/` 줄 확인

### 4-4. 휴대폰에서 확인

| 단계 | 주소 | 성공 기준 |
| --- | --- | --- |
| **B** | `http://<IP>:5173/mic_test.html` | 보안 연결 ✅ · 음성인식 ✅ · **챗봇 서버 연결 ✅** → 🎤 "에이 주차장 빈자리 있어?" → 챗봇 답 "개발원주차장에 9자리 있어요" (가짜 차량 3대) |
| B | 같은 페이지 | 🎤 "십 번 차 어디 있어" → "10번 차 맞으세요?" + 버튼 → **버튼** "네, 맞아요" → 위치 A-01 |
| B | 같은 페이지 | 🎤 "요금 얼마야" → 결제 → **결제 확인 단계에서 🎤 "네"** → 자동 전송되지 않고 입력칸에만 채워짐 + 안내 문구 → 버튼으로 결제 |
| **C** | `http://<IP>:5173/` | 사용자 화면: 빈자리·내 차 찾기(10)·요금·모의 결제 |

> 고유어 숫자("열 번")는 지원 안 함 → "십 번" 또는 "10번"으로 말하기

---

## 5. 끝내기·정리 (꼭)

```powershell
# 창 1·2·3: Ctrl + C
Remove-Item .\frontend\public\mic_test.html          # 저장소 폴더에서. 커밋하지 않기
# 관리자 PowerShell
Remove-NetFirewallRule -DisplayName "mic-test-8765"
Remove-NetFirewallRule -DisplayName "parking-web-5173"
```
- 휴대폰 플래그는 그대로 둬도 되지만, 시험이 끝나면 "Default"로 돌려 두는 것을 권장

---

## 6. 문제 해결

| 증상 | 확인 |
| --- | --- |
| `New-NetFirewallRule : 액세스가 거부되었습니다` | 관리자 권한 창이 아님 |
| `단항 연산자 '+' 뒤에 식이 없습니다` | 오류 메시지까지 같이 붙여 넣음 → 명령 한 줄만 |
| 관리자 창에서 `cd frontend` 경로 없음 | 관리자 창은 `C:\Windows\system32`에서 시작 → 먼저 저장소 폴더로 `cd` |
| `npm`/`py` 인식 안 됨 | 설치 안 됨, 또는 설치 후 창을 새로 열지 않음 |
| 휴대폰에서 페이지가 안 열림 | 서버 실행 중? → 같은 와이파이? → 방화벽? → 기관 와이파이 차단이면 핫스팟 |
| 보안 연결 ❌ | 플래그 주소가 접속 주소와 정확히 같은지(`http://`, 포트), "사용 설정됨", 다시 시작 |
| 음성인식 `network` 오류 | 인터넷 연결 (Chrome 음성인식은 Google 서버 사용) |
| 챗봇 서버 연결 ❌ (B단계) | 창 1 실행 중? 창 3에 `API_PROXY_TARGET` 설정 후 `npm run dev`? `5173` 주소로 열었나(8765 아님) |
| 챗봇이 "현재 정보를 확인하지 못했어요" | 창 1 실행 전에 `DATA_SOURCE`·`BACKEND_BASE_URL` 설정했는지 → 설정 후 창 1 재시작 |
| 칸이 "확인 필요"로 바뀜 | 창 2(`--hold`)가 꺼짐 → 다시 실행 |

## 7. 결과 기록 (시험 후 디스코드에 공유)

| 항목 | 결과 |
| --- | --- |
| 휴대폰 기종·Chrome 버전 | |
| 와이파이 (기관 / 핫스팟) | |
| A: 보안 연결·마이크·한국어 인식 | |
| B: 음성 → 챗봇 답 · 결제 확인 자동 전송 막힘 | |
| C: 휴대폰 화면 조회·결제 | |
| 막힌 점 | |
