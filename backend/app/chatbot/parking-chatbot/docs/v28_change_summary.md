# v2.8 변경 요약 및 검증 결과

2026-11-03. 기준은 v2.7(`9604647`), 검증한 코드 HEAD는 `e1158b7`입니다. 첨부 작업지시서 1~6장만 반영했습니다. 소스 수정은 `parking-chatbot/` 안에서 진행했습니다.

## 1. 변경과 완료 기준

| 지시서 | 변경 파일 | 추가 검증 | 실행 결과 |
| --- | --- | --- | --- |
| 1장 위치 미상 경보 | `adapters.py`, `contracts.py`, `templates.py`, `navigation.py`, `briefing.py`, `incidents.py`, `engine.py`, 이용자·관리자 HTML | `test_v28_alarms.py`: null 칸 Lv2, null 주차장 Lv1, 두 허용 type·다른 type 로그, 전체 범위 처리 | 통과. Lv2 사람 먼저 안내 유지, 임의 칸 번호 없음. 다른 type은 무시하고 항목당 경고 1줄 |
| 2장 API 경보 문구 | `templates.py`, `engine.py`, `adapters.py`, `static/index.html` | 같은 파일: 없음·active·acknowledged·Lv2·직전 resolved, 발생 시각 누락·오류·UTC 변환 | 통과. KST 시각 표시, 잘못된 시각은 괄호 생략, API Lv1도 경보로 표시 |
| 3장 신선도 | `config.py`, `.env.example`, `adapters.py`, `contracts.py`, `templates.py`, `engine.py` | `test_v28_freshness.py` 7개: annotate/reject, 네 조회 종류, Lv0·Lv1·Lv2, 미래, 계산 시각, 잘못된 설정, 잘못된 해제 후 장애 | 통과. 유효한 이전 활성 경보를 오래된/미래 해제 정보가 덮어쓰지 않음 |
| 4장 목업 요금 | `config.py`, `.env.example`, `adapters.py`, `templates.py`, `engine.py` | `test_mock_fee_policy.py` 3개: 11개 경계값, 초 단위 올림·결제 일치, 양수 설정 검증 | 통과. 기본요금·부분일·일 최대요금·상한 플래그 반영 |
| 5장 레포 대비 | `adapters.py`, `templates.py`, `engine.py`, `README.md`, `settings_guide.md` | `test_v28_repo.py` 4개: 세션 상태 무관, 결제 본문 3필드, A16/B12 구역 범위, 서버 번호 보존 | 통과. 범위 50 별도 시험 결과는 아래 참조 |
| 6장 안내 출처·입력 오류 | `adapters.py`, `engine.py` | `test_v28_alarms.py`: 현재 답의 헤더만 사용, 이전 목업 경보 헤더 제거, 입력 오류 후 첫 정상 안내 보존 | 통과. 전역 ‘어느 조회든 목업’ 표시는 제거하고 현재 답의 근거만 사용 |

새 Python 테스트는 총 22개입니다. 새 브라우저 검사는 `tests/browser_v28.cjs`의 5개 항목입니다. 기존 Python 테스트에서 바꾼 기대값은 아래 요금 영향 4행뿐입니다. 기존 skip 조건·질문 CSV·API 경로·칩 ID는 변경하지 않았습니다.

### 위치 미상 시 내비게이션·브리핑 처리

- 주차장만 알면 `A 주차장(정확한 칸 확인 중)`, 주차장도 모르면 `주차장 위치 확인 중`으로 표시합니다. 이용자 본문·카드·배너·알림·상황 보고서·관리자 목록에 적용했습니다.
- 기존 내비게이션은 Lv2 주차장 전체를 막는 구조여서 이를 재사용했습니다. 주차장까지 null이면 모든 주차장의 이동 안내를 제한합니다.
- 칸이 null이면 브리핑의 인접 범위와 상황 기록의 근접 차량 범위를 해당 주차장 전체로 봅니다. 주차장도 없으면 전체 조회 범위가 대상입니다. 개별 칸을 추정하지 않습니다.
- 실제 API에는 차량 목록 조회가 없어 영향 차량과 전기차 수는 ‘확인 불가’입니다. 합성 차량 목록 검사에서는 전체 범위의 전기차를 장비 참고에 반영하는지 확인했습니다.

### 신선도 적용 범위

`STALE_POLICY=annotate`가 기본이며 `reject`만 추가로 허용합니다. 실제 API 정규화 결과(`api_result=True`)에 적용합니다. 기존 목업 검증 계약과 타임아웃·캐시 만료 처리는 유지했습니다. 오래된 활성 경보는 두 정책 모두 유지하고, 오래된 Lv0와 미래 5초 초과 정보는 거절합니다. 1분 미만은 기준 문구를 붙이지 않고, 그 이상은 올림한 분으로 한 번 표시합니다. 기준 시각 줄을 시연 데이터 줄보다 먼저 붙입니다.

요금은 `calculated_at`을 우선 사용합니다. 기존 테스트/이전 API 호환을 위해 `calculated_at` 키가 없을 때만 요금 응답 자체의 `as_of`를 지원합니다. 위치의 카메라 시각으로 대체하지 않습니다. 두 시각 모두 없으면 확인 불가입니다.

## 2. 요금 기대값 변경 — 허용된 예외

기본 30분 요금이 0원에서 1,000원으로 바뀌어 무료 처리 상태도 정상 결제로 바뀝니다. 따라서 마지막 두 행은 요금 변경에 직접 딸린 결제 상태 기대값입니다. 이 외 기대값은 바꾸지 않았습니다.

| 파일·행 | 이전 | 이후 | 이유 |
| --- | --- | --- | --- |
| `tests/test_reliability.py:36` | `self.assertIn("1,500원", result["text"])` | `self.assertIn("2,500원", result["text"])` | 58분 차량 기본요금 반영 |
| `tests/test_claude_round2.py:48` | `self.assertIn("무료",r["text"])` | `self.assertIn("1,000원",r["text"])` | 무료 요금 정책이 기본요금으로 변경 |
| `tests/test_regressions.py:62` | `self.assertEqual(e.data.pay("03")["status"], "free")` | `self.assertEqual(e.data.pay("03")["status"], "paid")` | 0원 무료 처리에서 1,000원 결제로 변경 |
| `tests/test_regressions.py:66` | `self.assertFalse(d["paid"])` | `self.assertTrue(d["paid"])` | 앞 행의 실제 기본요금 결제가 유지됨 |

11개 경계값은 지시서의 0·30·31·40·41·200·201·1440·1441·1671·2880분을 그대로 사용했습니다. API 경로는 로컬 계산기를 호출하지 않으며, 기존 ‘API에서 calc_fee 사용 금지’ 검사도 통과했습니다. `free_minutes` 응답 키는 호환을 위해 남기고 값은 0으로 설정했습니다.

### 응답 스냅샷 전후 비교

고정 시각·동일 목업 데이터로 질문 242개를 각각 새 세션에서 실행했습니다. 시간·토큰·런타임 버전 같은 변동 필드를 제외한 비교입니다. **229개 동일, 13개는 요금 관련 필드만 변경**됐습니다. 질문 CSV의 분류 정답은 수정하지 않았습니다.

공통 정책 안내 이전: `처음 30분은 무료, 이후 10분마다 500원이에요. (임시 요금 기준)`

공통 정책 안내 이후: `처음 30분은 1,000원, 이후 10분마다 500원이에요. 하루 최대 10,000원이에요.`

| 질문 파일·행 | 변경 필드 | 이전 → 이후 |
| --- | --- | --- |
| `tests/questions.csv:15` | `.text` | 위 공통 정책 안내 전후 |
| `tests/questions.csv:16` | `.text` | 위 공통 정책 안내 전후 |
| `tests/questions_extended.csv:34` | `.text` | 위 공통 정책 안내 전후 |
| `tests/questions_extended.csv:35` | `.text` | 위 공통 정책 안내 전후 |
| `tests/questions_extended.csv:36` | `.text` | 위 공통 정책 안내 전후 |
| `tests/questions_holdout.csv:19` | `.text` | 위 공통 정책 안내 전후 |
| `tests/questions_realistic.csv:24` | `.text` | 위 공통 정책 안내 전후 |
| `tests/questions_realistic.csv:25` | `.text` | 위 공통 정책 안내 전후 |
| `tests/questions_realistic.csv:26` | `.text` | 위 공통 정책 안내 전후 |
| `tests/questions_realistic.csv:27` | `.text` | 위 공통 정책 안내 전후 |
| `tests/questions_stability.csv:16` | `.card.data.summary` | 관리자 요약의 미결제 0원 → 1,000원 |
| `tests/questions_stability.csv:25` | `.text` | 위 공통 정책 안내 전후 |
| `tests/questions_stability.csv:31` | `.text` | 위 공통 정책 안내 전후 |

추가로 A~D 네 주차장에서 목업 Lv2 발생·해제를 실행한 상황 보고서 전체도 전후 동일했습니다.

## 3. 차량 범위 50 시험 및 결정 대기 사항

실제 `lots.json`은 그대로 두고, 동일 내용에서 `vehicle_max`만 50으로 바꾼 파일을 `PARKING_SETTINGS_PATH`로 지정해 전체 검사를 실행했습니다. **403개 실행, 실패한 테스트 메서드 2개(실패 기록 6건), skip 2개**입니다. 두 메서드 외 399개는 통과했습니다. 범위 24를 가정한 아래 기대값은 지시대로 고치지 않았습니다.

| 파일·테스트 | 실패 기록 | 원인 |
| --- | --- | --- |
| `test_regressions.ParserTests.test_out_of_range_korean_number_not_partial_match` | 1 | `이십오번`을 invalid로 기대하지만 범위 50에서는 25가 유효 |
| `test_robust_input.RobustInputTests.test_numbers_are_not_negative_or_long_suffixes` | 5 | 네 입력은 ‘01부터 24’ 고정 문구를 기대함. `25번`은 오류·대기 없음 대신 정상 차량 확인 대기로 진행 |

서버 번호 범위 밖 처리도 확인했습니다. 직접 API 계층에 `25`를 조회해 서버가 `25`를 주면 값을 바꾸지 않고 `25번` 위치를 표시합니다. 다만 기본 사용자 입력 경로는 24 범위를 검사하고, 범위 밖 서버 ID에 대한 전용 경고 로그는 현재 없습니다. 지시서의 ‘지금 동작이 다르면 보고만’에 따라 이 둘은 변경하지 않았습니다. 차량 범위 확정 후 처리할 항목입니다.

`session_status`는 active/exited/closed/임의 문자열/null/배열/객체여도 읽지 않고, payment_status로만 요금을 안내합니다. 결제 본문 조립은 `build_payment_body` 하나로 모았으며 session_id/idempotency_key/payment_method 세 키만 보냅니다. quote_id는 보내지 않습니다.

A16/B12 합성 응답에서 전체 면수·위치는 서버 값을 사용하고, 로컬 8칸 정의 밖은 구역 미지정으로 남습니다. 일반 구역의 나머지 칸 계산도 로컬 설정 용량까지만 적용합니다. 구역 답변에 `구역 정보가 일부 칸만 설정돼 있어요`를 덧붙였습니다.

## 4. 전체 검증 결과

| 검사 | 실행 | 통과 | 실패/오류 | 건너뜀 |
| --- | ---: | ---: | ---: | ---: |
| v2.7 마지막 저장된 기준 검사 — LangChain 설치 | 381 | 379 | 0 | 2 |
| v2.7 마지막 저장된 기준 검사 — LangChain 미설치 | 381 | 377 | 0 | 4 |
| 이번 수정 전 재실행 — LangChain 설치 | 381 | 378 | 오류 1 | 2 |
| v2.8 최종 — LangChain 설치 | 403 | 401 | 0 | 2 |
| v2.8 최종 — LangChain 미설치 | 403 | 399 | 0 | 4 |
| RUN_SLOW=1 안정성 묶음 | 30 | 30 | 0 | 0 |

수정 전 오류 1건은 `test_backend_wire.BackendWireTests.test_payment_passes_quote_amount_visit_and_idempotency_key`의 HTTP 조회 확인 불가였습니다. 수정 전 원본을 별도 경로에 풀어 같은 파일을 재실행한 결과와 원인 한계는 아래 비고에 적습니다. 중간에 전체 검사를 동시에 돌렸을 때 0.5초 예산 검사에서도 시간 제한 실패가 나왔으나, 기대값·시간 제한을 바꾸지 않고 순차 실행한 최종 전체 검사에서는 재현되지 않았습니다. 이 현상을 제품 오류가 해결됐다는 근거로 삼지 않습니다.

최종 기본 skip 2개는 RUN_SLOW 전용 퍼징·20,000회 검사입니다. LangChain 미설치 환경의 추가 skip 2개는 실제 선택 패키지가 필요한 기존 검사입니다. FastAPI/uvicorn/httpx는 두 환경 모두 제공해 HTTP 검사를 실행했습니다. 실제 유료 LLM 호출은 하지 않았습니다.

| 질문 세트 | 통과/전체 |
| --- | ---: |
| `questions.csv` | 36/36 |
| `questions_extended.csv` | 50/50 |
| `questions_holdout.csv` | 20/20 |
| `questions_navigation_dev.csv` | 20/20 |
| `questions_navigation_holdout.csv` | 12/12 |
| `questions_realistic.csv` | 69/69 |
| `questions_rehearsal.csv` | 5/5 |
| `questions_stability.csv` | 30/30 |

합계 242/242(7세트 237개 + rehearsal 5개). 35개 `llm_eval_questions.csv`는 실제 제공자 평가를 하지 않았습니다. 기존 파일명·규칙 미인식 계약 검사는 전체 테스트에 포함됩니다.

| Chromium 스크립트 | 통과 항목 |
| --- | ---: |
| `browser_audit_fixes.cjs` | 6 |
| `browser_checks.cjs` | 15 |
| `browser_final.cjs` | 7 |
| `browser_integrated_v3.cjs` | 5 |
| `browser_navigation.cjs` | 7 |
| `browser_optimization.cjs` | 3 |
| `browser_robust_poll.cjs` | 1 |
| `browser_robustness.cjs` | 6 |
| `browser_stability.cjs` | 11 |
| `browser_supplement.cjs` | 6 |
| `browser_v28.cjs` | 5 |
| `browser_v2_2.cjs` | 4 |

합계 12개 스크립트, 76개 항목 통과. 기존 LLM 호출 제안 검사는 전용 `run_v2_2_browser.py`의 고정 라우터를 사용했고, 다른 검사는 일반 목업 서버에서 실행했습니다. 새 위치 미상 UI 검사는 합성 API 카드 데이터를 렌더링한 검사이며, 실제 장치와의 종단 시험은 아닙니다. 900회 폴링 검사는 가상 시간 30분입니다.

## 5. 유지한 구조 및 미검증 범위

- 잠금 순서·공유 조회·캐시 세대·결제 직렬화·재전송 키·알림 cursor·Lv2 전체 결제 중지 정책은 재설계하지 않았습니다. 경보 저장 시 신뢰할 수 없는 해제 정보가 마지막 안전 상태를 지우지 않도록 검증 조건만 보완했습니다.
- `lots.json`, `api_paths.json`, `chips.json`은 v2.7과 바이트 단위로 동일함을 확인했습니다. 기본 차량 최대값 24와 주차장별 8칸을 유지했습니다.
- 서버 /alerts·/sessions 구현, main 등록, LED 인식 복원, quote_id 전송, 세션 상태 표준 결정, 폴더명 변경, docs/history 정리, STT, push/PR/메시지 전송은 하지 않았습니다.
- 실제 팀 백엔드·DB·결제 모듈·카메라·LED·시연 PC·실제 Windows 환경·실제 LLM 제공자 연결은 미검증입니다. Python 3.12 로컬 환경, 실제 로컬 HTTP와 장애 서버, Chromium으로 확인했습니다. 파일은 UTF-8로 처리하고 실행한 subprocess에는 PYTHONIOENCODING=utf-8을 전달했습니다.
- billing 계산기 원본 파일은 이번 입력에 없었습니다. 지시서가 제공한 순수 계산 규칙과 11개 표를 기준으로 구현·검증했으며 원본 billing 코드와 직접 실행 비교한 결과는 아닙니다.
- 합성 벤치마크는 동일성 검증에 사용했습니다. 동시 작업으로 시간 편차가 커서 이번 결과로 성능 향상을 주장하지 않습니다.

## 6. 커밋·수정 묶음

| 묶음 | 주요 파일 | 커밋 |
| --- | --- | --- |
| ① 경보·안내 출처(1·2·6장) | adapters/contracts/templates/engine/navigation/briefing/incidents, test_v28_alarms | 5897d46 |
| ② 신선도(3장) | config/.env.example/adapters/contracts/templates/engine, test_v28_freshness | 8164969 |
| ③ 목업 요금(4장) | config/.env.example/adapters/templates/engine, test_mock_fee_policy | 0218bde |
| ④ 레포 대비(5장) | adapters/templates/engine, README/settings_guide, test_v28_repo | d0d3898 |
| ③ 요금 기대값 반영 | 기존 테스트 3파일·4행 | 8b1c618 |
| ①② 연속 장애 경보 보존 | adapters, 경보·신선도 회귀 검사 | 01b5a21 |
| ① 화면 위치 미상·기존 보고서 보존 | static 두 화면, incidents, 미사용 헤더 ContextVar 제거 | 0c33b49 |
| ① UI 검사 입력 메타데이터 보완 | browser_v28.cjs | e1158b7 |

여러 장이 같은 파일을 건드리므로 사람에게 전달할 묶음은 파일 단위보다 커밋 단위로 나누는 것이 안전합니다. 문서 결과는 별도 커밋에 포함합니다.

## 7. 재현 명령

```powershell
$env:DATA_SOURCE="mock"
$env:LLM_PROVIDER="none"
$env:PYTHONIOENCODING="utf-8"
python tests/run_guarded.py
python tests/run_eval.py tests/questions.csv tests/questions_extended.csv tests/questions_holdout.csv tests/questions_navigation_dev.csv tests/questions_navigation_holdout.csv tests/questions_realistic.csv tests/questions_stability.csv tests/questions_rehearsal.csv
$env:RUN_SLOW="1"
python tests/run_guarded.py "test_robust_*.py"
Remove-Item Env:RUN_SLOW
python tests/run_browser_checks.py browser_v28.cjs
python tests/run_v2_2_browser.py
```

브라우저 검사는 Node.js/playwright/Chromium, 선택 제공자 검사는 `requirements-llm.txt`의 의존성이 필요합니다. 전체 검사는 시간 제한 오검출을 줄이도록 순차 실행했습니다. ZIP에는 `.git`, `.test-env`, `.validation`, `.bundle`, `__pycache__`, `.env`, `*.log`와 실행 중간 산출물을 제외합니다.

### 수정 전 원본의 HTTP 검사 재시도

원본 v2.7의 `test_backend_wire.py` 4개를 단독 재실행해 4/4 통과했습니다. 최초 전체 실행의 오류는 단독 재현되지 않았고 원인을 확정하지 않았습니다.
