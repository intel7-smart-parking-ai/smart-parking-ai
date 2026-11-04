# v2.2 변경 요약 — 작업계획서 v3 단계 1 (R1·R3)

> v2.2 당시 기록입니다. 현재 v2.3 결과는 [통합요청 v3 변경 요약](integrated_v3_report.md)을 참조하세요.

기준: 첨부 `parking-chatbot-langchain-v2.1(1).zip`. 작성: 2026-11-02.
구현 범위는 작업계획서 v3의 “4. 단계 1 상세” R1·R3다.

## R1: LLM 관리자 호출은 제안 후 접수

- `chatbot/engine.py`: 최종 의도 실행 직전에 source가 llm이고 의도가 call_admin인 경우를 처리한다. 접수 함수는 호출하지 않는다.
- `chatbot/templates.py`: “관리자를 부를까요? / 아래 버튼을 누르면 바로 접수돼요.” 문구를 두 줄로 제공한다.
- 기존 `call_admin` 칩을 맨 앞에 놓고, 미선택·차량 선택·Lv1 상황에 맞는 기본 칩을 합해 최대 4개를 표시한다.
- 제안 단계에서는 카드·접수·새 pending 확인을 만들지 않는다. “네” 입력은 제안 승인으로 처리하지 않는다. 기존 칩을 클릭하면 즉시 접수한다.
- 이전 차량·결제 확인이 남아 있으면 제안 시 취소해 이전 확인이 실행되지 않게 한다. 기존 선택 차량·후보 문맥은 유지한다.
- 제안 응답의 request_id 재전송에서도 호출 칩을 유지하며 새 접수를 만들지 않는다. 이후 다른 결제 확인이 생겼으면 그 확인을 우선한다.
- `LLM_ENGINE=direct`도 ParseResult.source가 llm이므로 같은 제안 정책을 적용한다.
- 칩·규칙 관리자 호출은 기존대로 즉시 접수하며, Lv2의 LLM 생략과 명시적 호출 동작은 유지한다.

## R3: 평가 CSV 이름 변경

- `tests/questions_llm.csv` → `tests/llm_eval_questions.csv`.
- 파일 내용은 원본과 바이트 단위로 동일하다. 20문장 정답·문장·인자·열을 수정하지 않았다.
- `tests/run_llm_compare.py`의 기본 경로, README, `docs/langchain_router_report.md`, `docs/langchain_change_list.txt`의 참조를 수정했다.
- `questions*.csv`로 기존 7세트·237문장만 선택된다.

## 테스트 변경과 기대값 보존

기존 `test_admin_tool_registers_once_without_confirmation`은 LLM 자동 접수를 전제로 해 R1과 충돌했다. 이를 `test_admin_tool_registers_once_after_call_chip`으로 바꾸고, 접수 검사 전에 확인 칩 클릭 및 동일 클릭 재전송을 추가했다. 기존 ‘idle’, ‘접수 1건’, ‘replayed=True’ assertion은 그대로다. 새로 ‘제안 시 접수 0건’과 ‘호출 칩 존재’를 검사한다. 다른 기존 테스트는 변경하지 않았다.

원본 ZIP의 모든 기존 Python 테스트 assertion을 AST로 비교해 제거·기대값 변경이 없음을 확인했다(`validation_v2_2/scope.json`).

새 `tests/test_v2_2.py`는 R1·R3 14개를 검사한다. 가짜 모델을 이용하는 기존 라우터 검사도 새 칩 클릭 흐름을 실행한다. `tests/browser_v2_2.cjs`와 `tests/run_v2_2_browser.py`는 실제 LLM 대신 도구 선택만 주입해 모바일 화면에서 제안·클릭·규칙 호출을 확인한다.

## 실제 실행 결과

Linux / Python 3.12. 실제 유료 LLM·Windows·팀 API 연결은 실행하지 않았다. Python 파일 읽기·쓰기는 UTF-8을 사용하고, 새 subprocess 검사는 `PYTHONIOENCODING=utf-8`, `encoding='utf-8'`, `errors='replace'`를 적용했다.

| 검사 | 결과 | 원시 기록 |
|---|---|---|
| 기준 v2.1 전체 | 293/293 통과 | validation_v2_2/baseline.txt |
| R1·R3 신규 단위 검사 | 14/14 통과 | validation_v2_2/r1_r3.txt |
| LangChain 설치 전체 | 307/307 통과 | validation_v2_2/installed.txt |
| LangChain 미설치, FastAPI 설치 전체 | 305 통과 + 설치 전용 2개 정상 skip (총 307) | validation_v2_2/without_langchain.txt |
| 질문 CSV 일괄 실행 | 237/237 통과 | validation_v2_2/questions.txt |
| 실제 Chromium R1 흐름 | 4개 통과 | validation_v2_2/browser_r1.txt |
| 이름 변경 후 LLM 비교 도구 | 기본/명시 경로 정상, 키 없을 때 하이브리드 skip | validation_v2_2/llm_compare_without_key.json, 신규 단위 검사 |
| 구문 검사 | compileall 통과 | 로컬 실행 |

전체 검사 첫 실행 중 기존 `test_slow_concurrent_alert_fetch_is_shared_and_latency_includes_wait`에서 조회 횟수 1 기대에 2가 관찰됐다. 해당 검사 파일 35개를 단독 실행해 통과했고, 이후 전체 307개도 통과했다. 첫 실패 로그와 재실행 결과를 모두 보관했다. 첫 실행은 다른 검증 작업과 동시에 진행됐으나 실패 원인은 확정하지 않았다. 이 검사나 관련 공용 조회 코드는 수정하지 않았다.

## 재현 명령

챗봇 폴더에서:

```bash
python -m unittest discover -s tests
python -m unittest discover -s tests -p test_v2_2.py
python tests/run_eval.py tests/questions.csv tests/questions_extended.csv tests/questions_holdout.csv tests/questions_realistic.csv tests/questions_stability.csv tests/questions_navigation_dev.csv tests/questions_navigation_holdout.csv
python tests/run_llm_compare.py
python tests/run_llm_compare.py tests/llm_eval_questions.csv
python tests/run_v2_2_browser.py
```

브라우저 검사는 Node·Playwright·Chromium이 필요하며, 필요 시 `BROWSER_EXECUTABLE`을 설정한다. `run_llm_compare.py --compare`는 활성화된 키와 모델이 있을 때 실제 API를 호출할 수 있다. 이번 검증은 LLM을 꺼서 실행했다.

## 변경 범위

기존 파일 수정: `chatbot/engine.py`, `chatbot/templates.py`, `tests/test_llm_router.py`, `tests/run_llm_compare.py`, `README.md`, `docs/langchain_router_report.md`, `docs/langchain_change_list.txt`.
파일 이름 변경 1개, 신규 단위 검사·브라우저 검사·실행 도구·본 요약·검증 기록을 추가했다.

설정값 정리(v1.2), API 경로, 어댑터, 모델 라우터, 결제 로직, static 화면, 상태 모델, 기존 질문 7세트의 내용은 변경하지 않았다. R2 평가 문장 확장, Windows 실행, 실제 모델 평가, GitHub PR, API 연결 등 다른 단계는 구현하지 않았다.

결과 ZIP에는 수정된 parking-chatbot 전체를 담고 `.bundle`, `__pycache__`, `.pyc`, `.git`, `.env`, 설치 패키지는 제외한다.
