# v2.15 인수 검사

> 현재 안내 (2026-11-05): 이 문서와 criteria.json은 v2.15 인수 기준의 기록이다. 이후 확정된 팀 명세·fixture를 반영하는 절차는 [구현·검증 원칙](../docs/validation_policy.md)을 따른다. 기준을 바꿀 때 출처·변경 전후를 기록하며, 무결성·안전성 실패를 현재 구현에 맞춰 통과시키지 않는다. DATA-01은 required, DNS-01은 advisory로 유지한다.

이 묶음은 운영 코드 교체본이 아니다. 최초 검증은 기준 소스 v2.15(`f4a6bb0`)에서 운영 코드·기존 테스트 기대값·CSV를 바꾸지 않고 수행했다. 현재 버전의 `backend/app/chatbot/parking-chatbot/`에서 실행할 때는 위 현재 안내와 최신 검증 원칙을 따른다.

## 실행

프로젝트의 기존 Python 의존성과 선택 LangChain 제공자 패키지를 사용한다. 브라우저 검사는 Node.js, Playwright, Chromium이 필요하다. 현재 환경에서 실행 가능한 Chromium이 따로 있으면 `BROWSER_EXECUTABLE`에 실행 파일을 지정한다.

```bash
PYTHONIOENCODING=utf-8 python qa/run_acceptance.py
```

Windows PowerShell:

```powershell
$env:PYTHONIOENCODING = "utf-8"
python qa/run_acceptance.py
```

결과는 `.validation/qa_acceptance/results/acceptance.json`과 UTF-8 로그로 남는다. `--output 경로`로 결과 폴더를 지정하거나, `--python-only`로 화면 검사를 NOT_RUN으로 남길 수 있다. 필요한 패키지·실행 파일이 없으면 관련 검사는 BLOCKED로 기록한다. FAIL/ERROR 등 필수 미충족이 있으면 종료 코드는 1이다.

이 명령은 추가 인수 사례 30개만 실행한다. 기존 전체 테스트·질문 세트·팀 통합 검사와는 별도이며, 기존 `tests/test_*.py` 검색에 포함되지 않는다. 전체 검증 결과와 실행 범위는 `docs/qa_acceptance_results_v215_20261104.md`를 참조한다.

## 기준과 해석

실행 전에 고정한 `criteria.json`과 `docs/qa_acceptance_criteria_v215_20261104.md`가 기준이다. 현재 버전에서 FAIL이 나는 사례도 요구한 동작을 assert한다. 실패를 감추기 위해 기대값을 현재 동작으로 바꾸지 않는다.

- required: 현재 소프트웨어 필수 기준.
- conditional: 해당 제공자·화면·정책 구성에서 적용.
- advisory: 이번에 제안한 로컬 장애 주입 기준이며 PM 확정 SLA가 아니다.
- PASS만 통과이며 SKIP/BLOCKED/NOT_RUN은 통과에 합산하지 않는다.

Python 검사는 가짜 키·모델·합성 API만 사용하며 외부 소켓 연결을 막는다. 브라우저는 로컬 모의 서버만 사용한다. 실제 LLM 호출이나 유료 측정을 실행하지 않는다. 홀드아웃 질문 파일도 읽지 않는다.

`evidence_20261104.json`은 이번 실행의 보관된 결과이며 재실행 결과를 대신하지 않는다. 최초 화면 검사 선택자 오류는 기대값을 바꾸지 않고 고쳤고, 추가 화면 8개만 재실행했다. 전체 회귀는 두 환경에서 각 1회만 실행했다.
