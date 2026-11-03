# [AI 스마트 주차장] billing(요금·모의결제·매출) 연동 가이드

담당: 윤명수 · API 상세는 [API_SPECIFICATION.md](API_SPECIFICATION.md)

---

## 1. 실행 · 테스트 (저장소 루트, Windows PowerShell)

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend/requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest tests/ -q
```

- billing 라우터는 **아직 `backend/app/main.py`에 등록되지 않았습니다.** main은 현재 DB 없는 조회 목업만 허용합니다.
  DB 방식(KAN-17) 확정 후 박해준 님과 등록합니다. 그 전까지 billing API는 `tests/test_api_integration.py`로 검증합니다.
- 공용 서버 주소는 `uvicorn backend.app.main:app` 기준 `http://<서버PC_IP>:8000` 입니다.
  다른 팀원 PC에서 접속하려면 `--host 0.0.0.0`으로 실행하고 Windows 방화벽에서 접근을 허용합니다.
- DB 파일 위치: 기본 `data/smart_parking.db` (git 무시 대상). 환경변수 `SMART_PARKING_DB_PATH`로 변경 가능.

---

## 2. 팀원별 연동

### 김채린 (모바일 웹 · 관리자 관제)
- `clients/parking_billing_client.js` 사용 (기본 주소 `http://127.0.0.1:8000`)
- 요금은 **서버 응답(`amount_krw`, `fee_breakdown`)을 표시만** 하고 다시 계산하지 않습니다.
- 결제 재시도 시 같은 `idempotency_key`를 다시 보내야 중복 결제가 막힙니다.
- 오류는 `response.error.code`로 분기합니다 (`ALREADY_PAID`, `SESSION_NOT_FOUND` 등).

### 박해준 (FastAPI 메인 서버 · DB)
- 라우터: `backend/app/api/billing_router.py` / DB 연결: `backend/app/billing/db.py`의 `get_db_conn`
- 공용 DB 확정 후 `get_db_conn`을 공용 연결로 교체하고 `app.include_router(billing_router)` 등록
- `parking_lots`·`parking_sessions` 테이블은 공용 정의를 따르고, billing은 `mock_payments`·`rate_policies`·`audit_logs`를 사용합니다.

### 서정범 (LLM 챗봇)
- `clients/chatbot_billing_tools.py` 사용 (기본 주소 `http://127.0.0.1:8000`)
- 요금은 `GET /sessions/{session_id}/fee` 결과만 근거로 답합니다. 차량번호 → `session_id`는 `GET /vehicles/{car_id}/location`의 `session_id`로 찾습니다.

---

## 3. 일정 체크포인트

| 날짜 | 확인 |
| --- | --- |
| 11/06 중간 점검 | A 주차장 세션 → 요금 조회 API 연결 |
| 11/10 운영 기능 통합 | 모의 결제·취소·매출이 화면·챗봇과 연결 |
| 11/11 테스트 | 중복 결제(멱등키), 기결제 409, 취소 후 매출 차감 |
