# V3 세션 ID 격리 검증 · 2026-11-03

- 기준: 현재 로컬 Step2 main 기반 d1f9248, 기존 billing 코드 변경 없음. 외부 조회 생략.
- 시험 파일: tests/test_v3_session_ids.py. 기존 Python3.11.18/SQLAlchemy2.1.6/pytest9.2.0 환경 재사용.
- 최종 실행: `python -B -X utf8 -m pytest tests/test_v3_session_ids.py -q -p no:cacheprovider --tb=short` → **19 passed, 실패/skip0, 기존 Starlette/httpx 경고1, 2.14초**.
- 짧은 ses_·36자 ses_·새 uuid4 문자열을 active/closed 양쪽에서 시험했다. 접두어로 상태나 세션 종류를 판단하지 않는다.
- 실제 billing TEXT 스키마 및 시험 전용 SQLAlchemy String(36) 표에서 요금 조회2500원·결제·멱등 재요청·다른 키 중복409·목록·취소·원문 ID 보존을 검증했다. 혼합 ID와 다른 세션의 동일 키409도 확인했다.
- String(36)/active·closed는 테스트 파일의 독립 Session36 모델이다. 공식 팀 모델에는 현재 ParkingSession이 없으며 이 결과를 공통 모델 구현 완료로 표시하지 않는다.
- 상태 조합 제약의 정상/거절을 임시 모델에서 시험했다. 실제 billing은 status 컬럼 없이 exit_at으로 계산하며 결제·취소는 세션 상태를 바꾸지 않았다.
- 문제 재현1: SQLite String(36)은 36자를 넘는 문자열도 저장한다. 기존 장문 ID 보존·향후 길이 검증 정책은 별도 결정이 필요하다.
- 문제 재현2: 개인 원본 Mapped[UUID]에 대응하는 SQLAlchemy UUID 바인더는 ses_ 문자열 입력을 받지 못한다. 원본 모델은 변경/실행하지 않았고 타입 처리만 격리 재현했다. 공통 문자열 ID 모델 이전 또는 어댑터 합의가 필요하다.
- 미구현 확인: 운영 GET /api/v1/sessions는404, 차량 위치 목업 session_id는null이다. 실제 세션 조회/위치 연결을 검증 완료로 간주하지 않는다.
- 보호: pytest tmp_path에 O_EXCL로 새 UUID 파일만 생성. sqlite3.connect는 해당 파일/동일 정체성만 허용하고 FK를 활성화했다. 기존 DB·다른 파일은 열지 않았고 접속은 종료 시 모두 닫았다.
- 기존 V1/vision 미커밋19파일의 작업 전후 SHA-256을 비교한다. 운영 모델·API·billing·DB·migration·Git Stage/Commit/Push/PR·외부 기록 변경 없음.
- 다음: 문자열 세션 모델·길이/기존 ID 정책을 합의하고 실제 sessions/location 연결 후 같은 시험을 재사용한다. V2 백업/이관은 별도 승인 단계다.
