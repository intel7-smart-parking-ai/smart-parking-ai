# v2.9 변경 요약 (2026-11-03, v2.8 기준)

팀 레포: https://github.com/intel7-smart-parking-ai/smart-parking-ai (main `3c20761` 기준 대조)
기준 결정: 11/03 결정 문서 D-02~D-17, 16:25 할 일 최신판, PM 규격 통일표.

## 1. 바뀐 것 (파트별)

| 파트 | 파일 | 내용 |
|---|---|---|
| 차량 번호 | `parser.py`, `adapters.py`, `templates.py`, `engine.py`, `lots.json` | `"10"`~`"99"`(D-06). 한 자리·0 시작은 조회 안 하고 "두 자리로 말씀해 주세요". 전각 숫자 처리. `assigned_car_ids`(null=전부 허용). 서버의 규칙 밖 번호는 그 칸만 번호 미인식(로그 1회) |
| 가상 차량 | `parser.py`, `engine.py`, `api_paths.json` | `17가1724`/`1724` 인식 → 검색 경로 확정 전 "준비 중" |
| 주차장·칸 | `lots.json`, `catalog.py`, `parser.py` | 4곳 104칸(A 12 / B 16 / C 12 / D 64, D-03·D-14). 이름 개발원·부산역·북항·신세계백화점(D-16). 도면 번호 `space_id`(D-04), 칸 수는 `spaces` 길이로 자동 계산. 가·나·다·라는 "주차장" 앞에서만, 짧은 이름은 주차 문맥에서만 인식 |
| 칸 표시 | `templates.py`, `engine.py`, `incidents.py`, `briefing.py`, `static/*.html` | 답변·카드·관리자 화면 모두 "개발원주차장 B-06". 칸 번호만 말하면 주차장을 되물음. 이웃 칸은 같은 줄 ±1 |
| 구역 | `lots.json`, `contracts.py`, `adapters.py` | `general/ev/accessible`(D-05). 서버 `space_type`·`vehicle_type` 우선(D-07) |
| 요금·결제 | `adapters.py`, `engine.py`, `templates.py`, `contracts.py` | billing 실제 응답 기준(`target_at`, `rate_policy`, `quote_id`, `quote_expires_at`). 결제 body 4필드(D-09). 견적 없으면 결제 안 보냄, 만료·불일치면 재조회 후 재확인. `PAYMENT_CONFLICT`·`INVALID_PAYMENT_STATE` 처리. 주차장별 목업 요금 = billing `db.py` 시드. 요금 규칙 질문은 `/rate-policies` |
| 세션 상태 | `contracts.py`, `templates.py` | `active/closed`(D-02), 출차 완료 안내. 별칭 `exited`·`parked` 수용 |
| 경보 | `adapters.py`, `templates.py`, `engine.py` | `source` simulation/manual → "(모의 이벤트)"(D-11). `space_id` null 허용. 전용 칸 위반은 화재와 분리, "위반 차량 있어?"(`get_violations`) |
| **제거** 출차 진단 | `engine.py`, `parser.py`, `chips.json`, `llm_router.py`, `templates.py`, `index.html` | `check_exit_issue`·출차 칩·카드 삭제 → `exit_help`("출구 문제는 현장 관리자가 도와드려요" + 관리자 호출 칩). 차량 없음 답의 칩은 [차량 번호 바꾸기][관리자 호출] |
| **제거** 신선도 세부 규칙 | `adapters.py`, `config.py`, `templates.py`, `.env.example` | `STALE_POLICY`, "N분 전 기준" 표기 삭제. **유지:** 오래된 '경보 없음' 거절, 미래 시각 거절, 30초 캐시 대체("갱신 중"), 마지막 Lv2 유지 |
| 유지 | 관리자 화면 | 그대로(표기만 도면 번호·이름으로) |
| LLM 도구 | `llm_router.py` | 22개(`exit_help`, `get_violations` 포함). LLM은 도구 선택만, Lv2 중 호출 안 함 — 그대로 |
| 문서·도구 | `docs/api_contract.md`, `docs/change_requests_api.md`, `docs/settings_guide.md`, `README.md`, `docs/run_guide.md`, `tools/api_smoke.py` | v2.9 규칙 반영. smoke는 PM 표 질문 + 서버 첫 차량 + 98번 404 + 규칙 밖 번호 표시 |

## 2. 설계 메모 — 칸 순번과 도면 번호

내부는 칸 순번(1..N, `spaces` 목록 위치)을 그대로 쓰고, 경계(API 해석·답변·카드·화면)에서만 도면 번호로 바꾼다. 도면 번호의 글자는 `lot_id`와 무관하다(개발원주차장에도 `B-06`). 그래서 내부 로직을 갈아엎지 않고 표시만 바꿀 수 있었다. 서버에 명부에 없는 칸이 오면 목록에 추가하고 구역은 "미지정"으로 둔다.

## 3. 테스트 기대값 변경

- 차량 번호: 테스트·질문 세트의 차량 번호를 일괄 **+10**(01→11, 07→17, 24→34 …). 칸 순번은 그대로라 다른 기대값은 안 바뀜. 모의 채움 차량은 90부터 내려가며 배정(테스트 번호와 충돌 방지).
- 주차장 이름: "A 주차장" → "개발원주차장" 등. 칸 표기: "3번 칸" → "A-03".
- 구역: `normal`→`general`, `disabled`→`accessible`.
- 질문 세트 변경 50행: 의도 `check_exit_issue`→`exit_help`, 차량 번호 +10, 구역 이름. 전체 목록은 `docs/v29_csv_changes.txt`.
- 다시 쓴 테스트: `test_v28_freshness.py`(남긴 규칙 6개만 검증). 출차 진단 테스트는 `exit_help` 테스트로 교체.
- `test_api_contract.py`의 가짜 요금 응답을 billing 실제 형식(`quote_id` 포함)으로 교체.
- 새 테스트: `tests/test_v29_rules.py` 20개(번호 규칙, 가상 차량, 도면 번호 해석·되묻기, 이웃 칸, 견적 결제, 세션 상태, 위반 분리 등).

## 4. 검증 결과 (이 작업 환경, Python 3)

| 검사 | 결과 |
|---|---|
| `python -m unittest discover -s tests` | 422개 OK, skip 21(FastAPI·LangChain 미설치) |
| 질문 세트 8개(`run_eval.py`) | 242/242 |
| 브라우저 검사 12개 | 모두 PASS(`browser_v2_2`는 `run_v2_2_browser.py`로 실행) |
| `tools/api_smoke.py` + main 모양 가짜 서버(차량 01~03, billing 미등록) | 빈자리 서버 숫자 일치, 7번 → 두 자리 안내(호출 0), 99·98번 → 404 "입차 기록 없음", 규칙 밖 01·03은 번호 미인식으로 표시, 요금 → "아직 요금 정보를 확인할 수 없어요"(session_id null) |

확인하지 못한 것: 실제 팀 서버·DB, billing `main.py` 등록 후 실요금·결제, 실제 LLM 호출, Windows 실행.

## 5. 임시 값·대기

- `assigned_car_ids` null(배정 번호 PM 결정 대기), `ev_vehicles` 13·21·28 임시.
- `/alerts` 필드·`as_of` 의미 → `docs/change_requests_api.md` 신규 2건.
- 조회 목업은 아직 A/B/C × 4칸, 차량 01~03(박해준님 할 일). 반영 전까지 해당 차량은 번호 미인식으로 보인다.
