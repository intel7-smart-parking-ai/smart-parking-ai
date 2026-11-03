# V3 세션 ID 격리 검증 · 2026-11-03

## 최신 main 기반 안전 분리·최종 검증

- PR#18/#19의 main 병합을 재조회했다. main25134e8 기반 별도 feature/KAN-17-v3-quote-id-compat에서 원본의 테스트/본 문서2개만 복사했다. 기존 V3 작업 폴더·HEAD46b1a28·MERGE_HEAD672124a·Stage/미커밋 상태는 그대로 보존한다.
- 이번 새 작업 공간에서 실제 재실행: V3 19개+billing 회귀44개=63 passed, 실패/skip0, 기존 Starlette/httpx 경고1, 4.87초. 이전4.71초 결과와 구분한다. 새 pytest 임시 SQLite 경로만 허용하는 연결 가드를 사용했다.
- 최신 billing이 요금 조회 API에서 발급한 quote_id만 사용하며 같은 요청 재시도/새키 결제충돌은 같은 견적을 유지한다. ses_·UUID 및 active/closed의 조회·결제·재요청·취소·원문ID 보존을 검증했다.
- 이 새 브랜치는 main을 부모로 하는 두 파일만의 변경이며 main 병합 커밋이나 billing/운영 소스 수정은 없다. 실제 운영 세션 목록·위치 연동·V2 이관·운영 DB는 검증하지 않았다.
- 아래 원본 문서의 "PR#19 병합 전"·"main 통합"은 이전 작업 상태의 이력이다. 현재는 선행 PR 병합 후 안전하게 분리한 결과가 우선한다.

## 원본 작업의 이전 검증 이력

## quote_id 최신 계약 반영

- 최신 main672124a(PR#15 견적·PR#20 기본 요금표 포함)를 V3 브랜치에 통합했다. billing/운영 main 원본을 직접 수정하지 않는다. 아래19개 기록은 quote_id 도입 전 결과로 보존한다.
- 수정 전 V3 실행에서 quote_id 누락으로 결제422가 발생해13 failed/6 passed·경고1(2.25초)을 재현했다.
- 결제 전에 GET /sessions/{session_id}/fee가 발급한 quote_id를 사용하고, 동일 payload 재요청·새 멱등키 충돌에도 같은 견적을 재사용한다. 다른 세션 멱등키 충돌은 해당 세션에서 받은 견적을 유지한 채 키만 바꾼다. 임의 견적 ID는 만들지 않는다.
- 수정 후 실제 시험: V3 19개+billing API/순수계산44개=63 passed, 실패/skip0, 기존 Starlette/httpx 경고1, 4.71초. 매 실행 새 임시 SQLite 경로만 허용하는 sqlite3.connect 가드와 기존 V3 파일 정체성 검사를 사용했다.
- ses_ 짧은/36자·UUID의 active/closed 양쪽에서 원문 ID·견적의 세션 연결·조회/결제/재전송/충돌/취소를 검증했다. 원본 UUID 타입/SQLite 문자열 길이 및 실제 세션 조회 미구현 한계는 아래와 같다.
- 실행 대상: tests/test_v3_session_ids.py tests/test_api_integration.py tests/test_billing.py, 옵션 -q -p no:cacheprovider --basetemp <신규 임시 경로> --tb=short. 기존 Python3.11.18 환경을 재사용했다.
- 선행 Vision e8b80bf를 포함하는 브랜치이며 최신 Vision ed369910의104칸 변경은 이번 세션 ID 시험과 별도로 남아 있다. PR#19 병합 전 V3 PR은 생성하지 않는다. PR#18/#19/#8·D5/V1 브랜치·기존 다른 worktree는 수정하지 않았다.

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
