# [AI 스마트 주차장] 팀원 연동 로컬 서버 가동 및 통합 가이드

본 문서는 윤명수 님이 구현한 **요금 계산 · 모의 결제 · 매출 집계 프로그램(EPIC 05)**을 프로젝트 운영계획서에 따라 다른 팀원들과 실제로 연동하기 위한 로컬 서버 가동 방법 및 팀원별 연동 가이드입니다.

---

## 1. 다른 팀원과 연동하기 위한 로컬 서버 가동 핵심 원리

내 컴퓨터에서만 실행할 때는 `127.0.0.1`로 충분하지만, **다른 팀원(김채린 님의 스마트폰/노트북, 박해준 님의 서버, 서정범 님의 챗봇)이 내 서버에 접속하려면 두 가지가 필요합니다:**

1. **외부 접속 바인딩 (`host="0.0.0.0"`)**: 이미 본 패키지의 `server.py`에 적용되어 있습니다.
2. **내 PC의 로컬 네트워크 IP (LAN IPv4)**: 같은 Wi-Fi나 공유기에 연결된 팀원들이 접속할 주소입니다.

### 🚀 서버 가동 명령어 (포트 충돌 없는 8080 권장)
```bash
# 8080번 포트로 외부 팀원 접속 허용 모드 실행
python server.py --port 8080
```
Windows에서는 `run_server.bat`을 더블클릭해도 동일하게 실행됩니다.

### 🖥️ 서버 가동 시 콘솔에 출력되는 안내 예시
서버를 실행하면 내 PC의 IP를 자동으로 감지하여 팀원에게 전달할 주소를 콘솔에 바로 띄워줍니다:
```text
====================================================================
[AI 스마트 주차장 FastAPI 로컬 서버 기동 완료 — 팀원 연동 모드]
• 내 PC(로컬) 접속:     http://localhost:8080
• 다른 팀원 공유용 접속: http://192.168.0.15:8080
• Swagger API 문서:    http://192.168.0.15:8080/docs
• 서버 상태(Health):   http://192.168.0.15:8080/api/v1/health
====================================================================
💡 같은 Wi-Fi/공유기에 연결된 김채린 님(모바일/웹 UI), 박해준 님(서버),
   서정범 님(LLM 챗봇)에게 위 '공유용 접속' IP 주소를 전달하세요!
```

> **방화벽 팁 (Windows)**: 서버를 처음 켤 때 나타나는 Windows 보안 경고 창에서 **[액세스 허용]**을 반드시 클릭해야 다른 팀원의 노트북/스마트폰에서 접속할 수 있습니다.

---

## 2. 역할별 팀원과의 실전 연동 방법

### 📱 1) 김채린 님 (사용자 모바일 웹 & 관리자 관제 UI 연동)
김채린 님이 개발하는 모바일 웹(스마트폰 PWA)과 관리자 관제 화면에서 윤명수 님의 요금 및 결제 API를 호출합니다.

* **전달 파일**: 패키지 내 `parking_billing_client.js`
* **연동 방법**:
  ```javascript
  import { ParkingBillingClient } from './parking_billing_client.js';

  // 윤명수 님의 로컬 서버 IP 지정
  const billing = new ParkingBillingClient("http://192.168.0.15:8080");

  // ① 모바일 웹: 특정 차량 실시간 예상 요금 조회
  const feeInfo = await billing.getFee("ses_0001");
  console.log("주차요금:", feeInfo.amount_krw);

  // ② 모바일 웹: [모의 결제하기] 버튼 클릭 시
  const payResult = await billing.pay("ses_0001", "MOCK_CARD");

  // ③ 관리자 웹: [결제 취소 / 모의 환불] 버튼 클릭 시 (매출 즉시 차감)
  await billing.cancel(payResult.payment_id, "고객 모의 환불");

  // ④ 관리자 웹: 주차장별(A·B·C·D) 및 일별/월별 매출 집계 차트용 데이터 조회
  const revenueReport = await billing.getRevenue("2026-11");
  ```
* **CORS 설정**: 이미 `server.py`에 모든 출처(`allow_origins=["*"]`)가 허용되어 있어 프론트엔드가 `localhost:3000`이나 스마트폰 브라우저에서 실행되어도 에러가 발생하지 않습니다.

---

### ⚙️ 2) 박해준 님 (FastAPI 메인 서버 & DB 소유자 연동)
박해준 님은 지침서상 전체 FastAPI 메인 서버(`app = FastAPI()`)와 통합 DB를 총괄합니다.

* **전달 파일**: 패키지 내 `billing_router.py`
* **연동 방법 (FastAPI APIRouter 모듈 통합 — 단 2줄)**:
  박해준 님의 메인 `main.py`에 아래 코드를 추가하여 메인 백엔드에 요금·결제 API를 하나로 합칠 수 있습니다.
  ```python
  from fastapi import FastAPI
  from routers.billing_router import billing_router  # 윤명수 님 라우터

  app = FastAPI(title="AI 스마트 주차장 메인 서버")

  # 요금 및 모의 결제 라우터 등록
  app.include_router(billing_router)
  ```
* **독립 서버 유지 시 (마이크로서비스 방식)**:
  박해준 님의 서버가 따로 돌고 있다면, 입출차 확정 이벤트 발생 시 박해준 님이 `http://192.168.0.15:8080/api/v1/sessions/{id}/fee`를 HTTP 통신으로 호출하는 구조로도 즉시 연동됩니다.

---

### 🤖 3) 서정범 님 (LLM 챗봇 & 조회 Tool 연동)
서정범 님이 개발하는 챗봇이 사용자의 질문("1번 차량 요금 얼마야?", "오늘 주차장 매출 알려줘")에 답변할 수 있도록 Tool을 제공합니다.

* **전달 파일**: 패키지 내 `chatbot_billing_tools.py`
* **연동 방법**:
  ```python
  from chatbot_billing_tools import ChatbotBillingTools

  # 윤명수 님 서버 주소 지정
  tools = ChatbotBillingTools("http://192.168.0.15:8080")

  # 챗봇 펑션 콜링에서 실행
  def get_parking_fee_tool(session_id: str):
      """차량 주차 요금 조회 도구"""
      return tools.get_vehicle_fee(session_id)

  def get_revenue_report_tool(month: str = "2026-11"):
      """주차장별 매출 집계 조회 도구"""
      return tools.get_monthly_revenue(month)
  ```

---

### 📋 4) 김한별 님 (PM) 및 통합 일정 체크포인트
운영계획서 제5장 및 제10장 일정에 따른 연동 관문입니다:
* **11/04(수) 3일차**: 김채린 님(UI)과 서정범 님(챗봇)에게 Mock API 제공하여 프론트/챗봇 병렬 개발 착수.
* **11/06(금) 5일차 중간 점검 ①**: 실물 A 주차장에서 `카메라 → 입차 세션 → 윤명수 요금 API → 화면` 1차 통합 시연.
* **11/10(화) 7일차 운영 기능 통합**: 모의 결제, 결제 취소 차감, 주차장별 매출 리포트 본 연동 완료.
* **11/11(수) 8일차 인수 테스트**: 중복 결제 방지(`idempotency_key`), 기결제 409 차단, 모의 환불 매출 정정 전수 검증.

---

## 3. 다른 장소(원격/외부망)에서 연동해야 할 경우

팀원들이 같은 공유기 Wi-Fi에 있지 않고 각자의 집이나 다른 네트워크에서 개발할 경우:
1. **휴대폰 핫스팟 사용 (가장 간단)**: 팀원들이 모두 같은 스마트폰 핫스팟에 연결하면 동일한 로컬 IP 대역이 형성됩니다.
2. **ngrok 무료 터널링 도구 사용**:
   ```bash
   ngrok http 8080
   ```
   생성되는 공개 주소(예: `https://abcd-1234.ngrok-free.app`)를 팀원들에게 공유하면 원격에서도 윤명수 님의 로컬 서버에 접속할 수 있습니다.
