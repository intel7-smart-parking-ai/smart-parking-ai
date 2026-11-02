# EPIC06 주차장 안내 챗봇 — 기능 정리·보완본

2026-11-02. 첨부 보완 계획서의 단계 1~5와 현재 기준본에 해당하는 안정성 검수 항목을 반영했습니다.

- [구현·검증 가이드라인](docs/implementation_guidelines.md)
- [변경·삭제·검증 결과](docs/cleanup_and_supplement_report.md)

## 실행

Python 3.10 이상, 기본 모의 데이터와 규칙 파서:

```bash
python server_dev.py
```

이용자 화면은 http://127.0.0.1:8080, 관리자 화면은 http://127.0.0.1:8080/admin.html 입니다.
팀 서버에서는 FastAPI·uvicorn을 설치하고 `uvicorn server_fastapi:app --port 8080 --workers 1`로 실행합니다.
`router`를 팀 FastAPI 앱에 포함할 수도 있습니다. 같은 망 공유는 개발 서버의 `HOST=0.0.0.0`, 관리자·시연 기능 공유는 `ADMIN_TOKEN` 설정이 필요합니다.

## 구현 범위

빈자리·구역 조회, 목적지 선택·변화 안내, 내 차 위치, 요금·모의 결제, 출차 진단, 관리자 즉시 호출·접수 확인, 모의 화재(LED) 알림·보고서·체크리스트를 제공합니다.

이번 보완:

- 자리가 나면 알려 달라는 질문은 알림 기능이 없음을 밝히고 현재 빈자리를 보여줍니다.
- 이용 현황 질문은 위치 카드로 연결합니다. 요금·영수증·출차 카드 안의 이용 단계는 유지합니다.
- 관리자 문의는 접수까지만 지원합니다. 처리 상황은 현장 관리자에게 확인하도록 안내합니다.
- Lv2는 안에 있는 사람의 출구 이동, 밖에 있는 사람의 재진입 금지, 차량 이동 금지를 안내합니다.
- 관리자 화면에 소방 전달용 현장 정보와 신고 정보 복사 버튼을 제공합니다. 실제 신고·외부 연락은 하지 않습니다.
- 확인 중인 칸은 빈자리와 따로 표시하고, 확정 빈자리만 추천합니다.
- 번호 미인식 차량은 최대 3대의 위치 후보만 보여줍니다. 후보를 차량 번호에 연결하거나 결제하지 않습니다.

## 시연

이용자 화면 우측 테스트 도구에서 주차장을 선택합니다.

1. D 목적지를 선택한 후 만차·운영 제한을 만들어 대안을 확인합니다.
2. 빈 칸 번호를 입력하고 ‘확인 중’을 눌러 빈자리와 분리됨을 확인합니다. ‘확인 완료’로 되돌립니다.
3. ‘번호 미인식 입차’를 누른 뒤 미입차 번호(기본 24)의 위치를 질문하고 후보를 선택합니다. 요금·결제 요청이 차단되는지 확인합니다.
4. 경보를 Lv2로 만들고 이용자 안내와 관리자 현장 정보·복사를 확인합니다.
5. 경보 해제 후 메모·체크리스트를 확인하고 보고서를 확정합니다.

## 설정·연결

`chatbot/lots.json`은 실측 전 PROVISIONAL입니다. `ev_vehicles`, 주차장별 `zone_layout`, `exits`, `facility`를 수정합니다. 시설을 모르면 null을 사용합니다. 구역 배치를 생략하면 기존 자동 배정을 사용합니다.

`DATA_SOURCE=api`, `BACKEND_BASE_URL`, 필요한 `BACKEND_TOKEN`·`ADMIN_TOKEN`을 설정합니다. 실제 API 경로·응답 변환은 `docs/api_contract.md`와 `chatbot/api_paths.json`을 팀 계약에 맞춰 확정해야 합니다. 실제 운영 연결에서는 `DEMO_ENABLED=0`으로 둡니다.

이번 묶음은 LangChain 도입 전 기준본입니다. 선택 직접 호출 분류기는 `LLM_PROVIDER=none`이 기본이며, 버튼·규칙만으로 동작합니다. LangChain 도입과 초기화 잠금 회귀 검사는 후속 작업입니다.

## 검사

```bash
python -m unittest discover -s tests
python tests/run_eval.py tests/questions.csv tests/questions_extended.csv tests/questions_holdout.csv tests/questions_realistic.csv tests/questions_stability.csv tests/questions_navigation_dev.csv tests/questions_navigation_holdout.csv
python tests/run_optimization_soak.py
python tests/run_browser_checks.py browser_supplement.cjs
```

FastAPI/httpx가 없으면 해당 검사는 건너뜁니다. 브라우저 검사는 Node·Playwright·Chromium이 필요하며 필요 시 `BROWSER_EXECUTABLE`을 지정합니다. 원시 결과는 `docs/validation_supplement/`에 있습니다.

상태는 단일 프로세스 메모리에 저장되며 재시작 복원은 없습니다. 실제 팀 API·DB·카메라·차단기·유료 LLM·Windows 실행은 이번 로컬 검증 범위 밖입니다. 과거 문서는 변경 이력이며 현재 범위는 위 가이드라인과 결과 보고서가 기준입니다.
