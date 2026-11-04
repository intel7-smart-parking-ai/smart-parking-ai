# KAN-17 백엔드 작업 현황 보고 (2026-11-04)

> 역할 변경을 위한 인계 준비 문서. **PM 후임 담당자 미지정 / 권한 이전 미완료**. PR 상태는 11/04 GitHub 조회 결과이며 시험 수치는 Codex·4조BOT 보고이다. 이번 문서 작업에서 재시험하지 않았다. 실행 절차 및 인수 체크리스트는 [별도 인수인계서](KAN17_BACKEND_HANDOVER_2026-11-04.md) 참조.

## 담당 범위
- 기존 담당: 박해준 — FastAPI 서버, SQLite DB/주차 세션, Vision 이벤트 수신·D5 점유, 조회 API, 시연 통합·합성 이벤트.
- 윤명수: 요금·견적·모의결제·매출 및 경보 API 공동 소유; 서정범: 챗봇·경보 API 공동 소유; 김채린: 화면·프록시·관리자; 우지원: 카메라·ROI; 김한별: PM·규격·리허설.
- 공식 팀 DB는 **SQLite + SQLAlchemy**, 기존 개인 PostgreSQL/Alembic 실습 모델은 팀 운영 DB가 아니다.

## 완료한 핵심 기능과 근거
| 범위 | 기능·증빙 | 상태 |
| --- | --- | --- |
| DB/이벤트·D5 | SQLite/SQLAlchemy 기반 + Vision 수신·중복·점유·조회: [#18](https://github.com/intel7-smart-parking-ai/smart-parking-ai/pull/18), [#19](https://github.com/intel7-smart-parking-ai/smart-parking-ai/pull/19), [#24](https://github.com/intel7-smart-parking-ai/smart-parking-ai/pull/24), [#32](https://github.com/intel7-smart-parking-ai/smart-parking-ai/pull/32) | 기존 팀 main 반영 |
| 세션 통합 | 점유→입차 active/출차 closed, 공용 `parking_sessions`, 위치 `session_id`, billing 경계: [#30](https://github.com/intel7-smart-parking-ai/smart-parking-ai/pull/30), [#33](https://github.com/intel7-smart-parking-ai/smart-parking-ai/pull/33), [#36](https://github.com/intel7-smart-parking-ai/smart-parking-ai/pull/36) | main 병합 |
| 시연 서버 | `demo_main`의 임시 SQLite·요금·결제 라우터: [#31](https://github.com/intel7-smart-parking-ai/smart-parking-ai/pull/31), [#34](https://github.com/intel7-smart-parking-ai/smart-parking-ai/pull/34) | main 병합 |
| 합성 반복 시연 | A 12칸/3칸, `--short`·`--hold`·`--depart --keep-empty`, 100,000건(시연 전용): [#37](https://github.com/intel7-smart-parking-ai/smart-parking-ai/pull/37), [#38](https://github.com/intel7-smart-parking-ai/smart-parking-ai/pull/38), [#40](https://github.com/intel7-smart-parking-ai/smart-parking-ai/pull/40) | main 병합 |
| 세션 목록 | `GET /api/v1/sessions`, `lot_id/car_id/status` 필터, active/closed, 미식별 null: [#43](https://github.com/intel7-smart-parking-ai/smart-parking-ai/pull/43), Jira [KAN-42](https://rgq548.atlassian.net/browse/KAN-42) | main 병합·Jira 완료 |
| 챗봇 등록 준비 | 시연 서버에 `POST /api/v1/chat` 등록: [#45](https://github.com/intel7-smart-parking-ai/smart-parking-ai/pull/45) | **Draft/Open, 미병합** |

### 검증 수치 (재실행 아님)
- 4조BOT: 12칸 `--hold` **6분40초**, 이후 **25,000건** HTTP 202·503 0 (시연 이벤트 한도 100,000).
- Codex: `--depart --keep-empty` 12/3칸 출차 후 15초 이상 빈자리 관측, 재입차·견적·결제 기록 보존.
- Codex: 세션 목록 관련 **91 passed**; 4조BOT: PR #43 main 결합 **331 passed/충돌 0**, 입차→active→출차→closed 실제 시연.
- Codex: PR #45 관련 **17 passed/0 failed/경고1** + A 빈칸 12→11, 차량 10번 A-01 조회 HTTP 확인. **PR 설명의 '미실행' 표기는 오래된 정보**.

## 인수 시점 미완료/의존
1. **[PR #45](https://github.com/intel7-smart-parking-ai/smart-parking-ai/pull/45):** Codex 검증 결과를 PR 설명에 반영하고 리뷰·PM 승인 후 병합. 지금은 Draft.
2. **[PR #46](https://github.com/intel7-smart-parking-ai/smart-parking-ai/pull/46):** 윤명수·서정범의 경보 API가 Ready/Open(아직 미병합). 병합 후 백엔드가 `demo_main`에 라우터를 등록하고 `mock_fire_led_observed`를 `ingest_led_fire_event()`에 연결. 202 접수만으로 경보 생성 완료가 아님.
3. **[KAN-45](https://rgq548.atlassian.net/browse/KAN-45):** 칸 응답 `entry_at` 추가. **[KAN-46](https://rgq548.atlassian.net/browse/KAN-46):** 11/09까지 `main.py` 단일 앱·Docker·SQLite 영속 볼륨. **[KAN-47](https://rgq548.atlassian.net/browse/KAN-47):** 근접 판독 예비안 입차 매칭(후순위).
4. **리허설:** 11/05 오후·11/06 오전. 김채린 화면 team모드, 우지원 A 12칸 ROI, 챗봇·요금·경보 모의 표기. 재료/모델 준비가 미완료면 D-11에 따라 **모의 이벤트**로 시연.
5. **주의:** 현재 `demo_main`의 `TemporaryDirectory` SQLite는 서버 재시작 때 초기화. 관측 중단 10초 후 `uncertain`; 동일 칸에 simulation/vision 동시 송신 금지. `main.py`는 아직 동일 기능이 아님.

## 관련 관리 업무
- 완료: [KAN-43](https://rgq548.atlassian.net/browse/KAN-43) 공용 세션, [KAN-44](https://rgq548.atlassian.net/browse/KAN-44) 단일 시연, [KAN-42](https://rgq548.atlassian.net/browse/KAN-42) 조회.
- 진행/할 일: [KAN-17](https://rgq548.atlassian.net/browse/KAN-17), [KAN-45](https://rgq548.atlassian.net/browse/KAN-45), [KAN-46](https://rgq548.atlassian.net/browse/KAN-46), [KAN-47](https://rgq548.atlassian.net/browse/KAN-47), [KAN-48](https://rgq548.atlassian.net/browse/KAN-48) 경보 공동.
- **PM이 후임자를 지정하기 전 Assignee 변경·업무 종료 선언 금지. team main 직접 push/merge 금지.** 본 보고는 인수 준비 증빙이며 인수 완료 확인서가 아니다.

관련 규격: [API_CONTRACT](API_CONTRACT.md) · [DECISIONS](DECISIONS.md). 최신 코드/PR의 상태가 이전 문서의 '미병합' 표기보다 우선한다.
