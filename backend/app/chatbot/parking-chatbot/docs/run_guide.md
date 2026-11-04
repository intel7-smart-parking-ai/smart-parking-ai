# 실행 및 검증

Python 3.10 이상에서 프로젝트 폴더를 작업 디렉터리로 사용합니다. 기본 모의 데이터와 규칙 파서는 추가 패키지와 API 키가 필요하지 않습니다.

```bash
python server_dev.py
```

이용자 화면: http://127.0.0.1:8080 · 관리자 화면: http://127.0.0.1:8080/admin.html

팀 FastAPI 환경에서는 `fastapi`·`uvicorn` 설치 후 단일 워커로 실행합니다.

```bash
uvicorn server_fastapi:app --port 8080 --workers 1
```

세션·알림·접수 상태는 프로세스 메모리에 있습니다. 워커를 늘리거나 서버를 재시작하면 상태가 공유·복원되지 않습니다. 외부 백엔드는 `DATA_SOURCE=api`, `BACKEND_BASE_URL`과 필요한 토큰을 설정하고 [API 계약](api_contract.md)을 연결합니다. 실제 운영 연결에서는 `DEMO_ENABLED=0`을 사용합니다. 설정 값의 뜻은 [설정 가이드](settings_guide.md)에 있습니다.

선택 LLM 라우터는 Python 3.11 이상에서 `python -m pip install -r requirements-llm.txt`로 준비합니다. `.env.example`을 참고하여 엔진, 제공자, 모델, 키를 설정합니다. 기본 `LLM_PROVIDER=none`은 키 없이 동작합니다.

## 검사

```bash
python -m unittest discover -s tests
python tests/run_eval.py tests/questions.csv tests/questions_extended.csv tests/questions_holdout.csv tests/questions_realistic.csv tests/questions_stability.csv tests/questions_navigation_dev.csv tests/questions_navigation_holdout.csv tests/questions_rehearsal.csv
python tests/run_browser_checks.py browser_integrated_v3.cjs
python tests/run_v2_2_browser.py
python tests/run_browser_checks.py browser_supplement.cjs
```

브라우저 검사에는 Node·Playwright·Chromium이 필요합니다. 환경에 따라 `BROWSER_EXECUTABLE`을 지정합니다. Windows에서는 UTF-8 인코딩으로 실행하고 subprocess에 `PYTHONIOENCODING=utf-8`을 전달합니다. 최신 검사 결과는 [v2.9 변경 요약](v29_change_summary.md)을 참고하세요.

## 팀 서버 연결 확인

```bash
DATA_SOURCE=api BACKEND_BASE_URL=http://127.0.0.1:8000 python tools/api_smoke.py
```

PM 규격 통일표 질문(빈자리, 7번, 99번, 27번 요금, 경보)과 서버 `/spaces`의 첫 차량 위치·요금, 98번 404 처리를 표로 출력합니다. 서버 차량 번호가 규칙(10~99) 밖이면 따로 표시합니다.
