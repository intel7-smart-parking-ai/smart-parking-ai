> 최종 PM 규칙 반영: 아래의 이전 `01~24`/한 자리 보정 기록은 폐기합니다. 현재 실물 스티커는 정확히 두 자리 `10~99`이며 `00~09`와 한 자리 입력은 거부합니다. 최신 수정 내역은 아래 후속 검토 대응을 참고하세요.

# 11/03 검토 문서 대응 결과

## 구현
- `VITE_DATA_SOURCE=demo|team` 빌드/개발 환경변수. URL로 임의 전환하지 않음. 기본은 기존 데모 유지.
- 팀 점유: 목록 + 주차칸 API, 2.5초 폴링, 서버 `as_of` 표시. 미제공 전용구역/도면을 실제 데이터처럼 생성하지 않음.
- 스티커 01~24와 한 자리 입력의 0 보정. `/vehicles/{id}/location` → `session_id`가 있을 때만 `/sessions/{id}/fee`. 세션 없음/요금 API 미구현은 위치를 유지하고 요금 정보 없음 표시.
- 요금은 서버 `amount_krw` 그대로 사용. `fee_breakdown`, `rate_policy`, `payment_status` 지원.
- 결제는 `session_id`, `idempotency_key`, `payment_method`만 전송. MOCK_CARD/MOCK_EASY_PAY/MOCK_ACCOUNT. 실패 체험은 명시적 로컬 시뮬레이션으로 서버를 호출하지 않음.
- 취소는 `canceled`; 기존 DB의 `cancelled`와 기존 결제 수단을 금액/이력 삭제 없이 마이그레이션.
- IDEMPOTENCY_CONFLICT/PAYMENT_CONFLICT/ALREADY_PAID는 요금 재조회로 결제 상태 확인. 네트워크 오류 시 멱등키 유지.
- 매출 캘린더/표/CSV는 `net_revenue_krw`, `gross_amount_krw`, `canceled_amount_krw`, `completed_count`, `daily_revenue[].net_krw` 사용.
- 팀 매출 API는 월 전체 집계만 제공하므로 팀 모드에서는 일별 주차장 필터를 비활성화. 임의로 일별 주차장 매출을 추정하지 않음.
- 사용자·관리자 권한 검사는 유지. 공개 팀 API도 명시적 허용 목록만 통과.
- 팀 API 미구현 기능의 실패를 점유 조회와 분리. 매출/경보/정책/세션 오류가 주차 화면을 중단하지 않음.
- 웹·모바일 챗봇 UI: 팀 `/api/v1/chat`에 session_id/text/request_id 전송. API 미연결은 연결 대기 안내, 가짜 답변 없음.
- 관리자 카메라 상태: 서버 제공 값만 표시, 미제공은 연결 대기.
- cloudflared 이미지를 SHA-256 다이제스트로 고정. 외부 프로필은 실행하지 않음.

## 실제 확인한 한계 / 담당자 협의
확인한 main d1f9248의 공통 `backend/app/main.py`에는 조회 목업 라우터만 연결되어 있습니다. billing_router 모듈과 chatbot 라우터 소스는 존재하지만 공통 앱에 include되지 않았습니다. 실제 팀 조회는 A/B/C 합성 데이터이고 session_id가 null입니다. 따라서 팀 A 점유/01 위치/세션 없음 처리는 실제 서버로 검증했고, 결제·매출은 공식 schemas.py/라우터와 계약 테스트 및 정렬된 데모 API로 검증했습니다. 실시간 카메라/실제 세션·결제·챗봇 전체가 통합됐다고 주장하지 않습니다.

수동 입차·출차는 확정된 팀 API가 없어 구현하지 않았습니다. PR #7 처리, main 병합, Jira 번호와 브랜치명 변경은 PM 결정이 필요하며 임의 처리하지 않았습니다. 데모는 백업용이며 실제 기능의 목표는 팀 API입니다.

## 실행
```powershell
# 개발
$env:VITE_DATA_SOURCE='team'
npm --prefix frontend run dev

# Docker: .env에 VITE_DATA_SOURCE=team 설정 후 빌드
# Vite 환경변수는 빌드 시 반영됨. 컨테이너 재시작만으로 바뀌지 않음.
docker compose up -d --build web

# 기본 데모 복귀: .env에서 VITE_DATA_SOURCE=demo 후 재빌드
```
초기 실행은 `scripts/start.ps1`. `PRIVATE_ACCESS.md` 참고. frontend/는 화면, dev/parkon-api/는 데모 전용 백업, backend/는 팀 서버입니다.

## 검증 결과
- Node 계약/결제/PWA 테스트 32개 통과.
- demo 및 team 프로덕션 빌드 성공.
- team 빌드 임시 18080 포트에서 실제 공통 서버 A/B/C 조회, 스티커 1→01, null session 처리 확인.
- 기존 관리자 인증, CSRF, 로그아웃, 위조 세션 차단 검증 통과.
- 데모 동시 결제 재시도에서 단일 결제 생성, 취소 멱등성, 취소 후 순매출 복원 검증 통과. 테스트에서 만든 결제만 취소하고 다른 결제는 변경하지 않음.
- 월/일/주차장/빈 기간 매출 합계 검증 통과.
- 캘린더와 엑셀형 표는 유지됨.

## 후속 검토 대응 — PM 최종 규칙
- 최신 main의 PR #13 문서 결정을 확인하고 프런트/모바일/데모 검증을 `^[1-9][0-9]$`로 통일. padStart 제거. 한 자리는 두 자리 입력 안내.
- 데모 초기 차량은 10부터 시작. 기존 구형 시드 번호는 1회 마이그레이션으로 충돌 없이 재배정. 기존 세션 ID, 결제 ID, 금액, 상태, 멱등키, 건수 보존 검증 통과.
- 모든 데모 목록은 `{items,next_cursor,as_of}`, 오류는 `{error:{code,message,details}}`. 요청 검증 오류도 동일 구조. SESSION_NOT_FOUND/PAYMENT_NOT_FOUND/ALERT_NOT_FOUND/PARKING_LOT_NOT_FOUND 구분.
- 경보 위치 미상은 `space_id:null`, 타입은 `mock_fire_led_observed`. 기존 임의 칸 지정 경보도 위치 미상으로 정정하고 LEFT JOIN으로 목록에서 누락되지 않게 수정. 화면은 위치 미확인 표시.
- 35개 프런트 테스트, 실제 API 계약 검증, 세션/결제 보존 비교, 모의 결제·취소·매출·인증 검증 통과.
- 결정 대기 항목인 세션 parked 상태, 결제 표시 이름/색상, 면수·bay_label, PR/브랜치 정책은 변경하지 않음.
- 참고: PR #13은 문서 변경이며 origin/main의 조회 목업 시드는 아직 01/02/03임을 확인. 실제 팀 서버도 최종 규칙에 맞는 시드/인식값을 제공해야 팀 모드에서 10~99 위치 조회가 성공함. 이 수정에서 팀 담당자의 목업/업무 API 구현을 임의 변경하지 않음.

추가 검증: `python scripts/verify_demo_contract.py`, `python scripts/verify_layouts.py`.
