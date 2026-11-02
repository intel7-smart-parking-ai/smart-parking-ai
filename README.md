# [AI 스마트 주차장 통합관제] 윤명수 담당 개발 패키지

**GitHub 조직**: `intel7-smart-parking-ai` / **저장소**: `smart-parking-ai`  
**개발자**: **윤명수** (3팀 대표 Assignee & 요금·결제·매출 단일 소유자)  
**공동 개발자**: 서정범 (3팀 LED 모의 화재 AI 공동 개발)  
**협업 담당자**: 박해준(FastAPI/DB), 김채린(UI/관제), 우지원(카메라/ROI), 김한별(PM)

---

## 1. 프로젝트 내 윤명수 님의 공식 담당 업무 (2대 핵심 영역)

본 패키지는 프로젝트 README 및 운영계획서에 정의된 윤명수 님의 2대 공식 업무를 공식 저장소 구조(`smart-parking-ai/backend/app/`)에 1:1로 정확하게 일치시켜 구현한 개발 결과물입니다.

```text
smart-parking-ai/
├── backend/
│   ├── app/
│   │   ├── billing/              # ⭐ [윤명수 전담] 요금 계산 · 모의 결제 · 매출 집계
│   │   │   ├── calculator.py     # 입·출차 시간 기반 기본/추가/최대요금 순수 계산 엔진
│   │   │   ├── payment_service.py# 멱등키(idempotency_key) 중복방어, 기결제 409 차단, 모의 환불
│   │   │   ├── revenue_service.py# 일별·월별·주차장별(A·B 12면, C·D 10면) 순매출 집계
│   │   │   └── schemas.py        # Pydantic v2 데이터 모델 스키마
│   │   ├── vision/
│   │   │   └── fire/             # ⭐ [3팀: 윤명수·서정범 공동] LED 모의 화재 상황 인식 AI
│   │   │       ├── detector.py   # 빨간 LED 점등 감지 및 빨간색 미니카 차체 오탐 방어
│   │   │       ├── roi_matcher.py# Frame Hub 영상 수신 및 주차칸 ROI 좌표 매칭 (space_id)
│   │   │       └── events.py     # mock_fire_led_observed 표준 이벤트 생성 어댑터
│   │   └── api/
│   │       ├── billing_router.py # 박해준 님 메인 FastAPI에 include할 공식 4대 API 라우터
│   │       └── fire_router.py    # LED 모의 화재 경보 조회/해제 API 라우터
├── tests/
│   ├── test_billing.py           # 요금 및 결제 순수 함수 단위 테스트
│   ├── test_fire_detection.py    # LED 점등 검출 및 적색 차량 오탐 방어 테스트
│   └── test_api_integration.py   # 박해준 님 4대 API 전수 통합 테스트 (100% PASS)
├── clients/
│   ├── parking_billing_client.js # 김채린 님 모바일 웹/관리자 관제 UI 연동 클라이언트
│   └── chatbot_billing_tools.py  # 서정범 님 LLM 챗봇 펑션 콜링 도구
├── docs/
│   ├── API_SPECIFICATION.md      # 공식 4대 REST API 명세서
│   └── TEAM_INTEGRATION_GUIDE.md # 박해준·김채린·서정범 팀원 연동 종합 가이드
├── server.py                     # 로컬 독립 서버 (FastAPI + SQLite, 0.0.0.0 외부접속 지원)
├── ai_led.html                   # 44면 바둑판 정렬 통합 시뮬레이터 (브라우저 즉시 구동)
└── run_server.bat / run_server.sh# Windows / Mac 원클릭 서버 실행 런처
```

---

## 2. 박해준 님 확정 공식 4대 API 현황 (`backend/app/api/billing_router.py`)

1. **`GET /api/v1/sessions/{session_id}/fee`** — 요금 조회 (소비자: 웹/관제/챗봇)
2. **`POST /api/v1/mock-payments`** — 모의 결제 (소비자: 사용자 모바일 웹)
3. **`POST /api/v1/mock-payments/{id}/cancel`** — 결제 취소 / 모의 환불 (소비자: 관리자 관제)
4. **`GET /api/v1/reports/revenue`** — 매출 조회 (소비자: 관리자 관제, LLM 챗봇)

---

## 3. 3팀 AI 공동 과업 현황 (`backend/app/vision/fire/`)

* **담당자**: 윤명수 (Jira 대표 Assignee) · 서정범
* **목표 기능**:
  * 빨간색 LED 점등을 고휘도 스팟 필터링으로 감지
  * 우지원 담당 공통 영상 입력(Frame Hub) 프레임 수신 및 주차칸 ROI(`space_id`) 매칭
  * **빨간색 미니카(09번 등) 차체 색상 오탐 방어** (면적비 및 휘도 집중도 검증 완료)
  * 표준 분석 이벤트(`mock_fire_led_observed`) 출력 및 "LED 모의 화재" 표기

---

## 4. 로컬 서버 실행 및 단위 테스트

### 1) 단위 테스트 및 API 전수 검증 실행 (100% PASS)
```bash
# 요금 계산 단위 테스트
python tests/test_billing.py

# 3팀 LED 모의 화재 감지 및 오탐 방어 테스트
python tests/test_fire_detection.py

# 박해준 님 4대 API 라이프사이클 전수 테스트
python test_api.py
```

### 2) 로컬 서버 실행 (FastAPI + SQLite, Swagger /docs 지원)
```bash
python server.py --port 8080
```
* **내 PC 접속**: `http://localhost:8080` (시뮬레이터 웹 화면 자동 서빙)
* **팀원 공유 접속**: `http://<내PC_IP>:8080` (콘솔에 자동 감지된 IP 출력)
* **Swagger API 문서**: `http://<내PC_IP>:8080/docs`

---

## 5. 팀 공식 저장소 Git 브랜치 작업 및 PR 절차

Jira 및 Git 협업 규칙에 따라 아래 순서로 공식 저장소(`intel7-smart-parking-ai/smart-parking-ai`)에 반영합니다:

```bash
# 1. 저장소 최신화 및 브랜치 생성
git checkout main
git pull origin main
git checkout -b feature/KAN-28-billing-and-fire-vision

# 2. 본 패키지의 backend/, tests/, clients/, docs/ 폴더 복사 후 커밋
git add backend/ tests/ clients/ docs/
git commit -m "feat(billing,fire): implement billing engine and LED fire detection modules"

# 3. GitHub Push 및 Pull Request 생성
git push origin feature/KAN-28-billing-and-fire-vision
```
* **PR 리뷰어 지정**: 박해준(FastAPI/DB), 서정범(3팀 공동 개발자), 김한별(PM)
