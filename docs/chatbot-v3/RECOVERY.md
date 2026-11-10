# 복구 기록 · 2026-11-07

## 정확한 의미
기준 Git 코드와 사용자 첨부 문서는 원본을 다시 확보했다. chatbot_v3와 설정집·설계서는
이전 대화에 남은 내용을 토대로 재작성했다. 유실된 미커밋 파일과 동일한 원본 복구는 아니다.
과거 SQLite 세션 기록이나 Windows Codex 채팅 목록은 이번 작업에서 수정하지 않았다.

## 대화 순서에 따른 재구성
1. 프로젝트 확인: upstream `intel7-smart-parking-ai/smart-parking-ai`, 기준 커밋
   `86c30692379f474bf54947efa0df22e9b96d7aa2`. 기존 파일 변경 없음.
2. 정보·설정집: 156칸, 28시설, 13목적지 순위. 가상 설정과 실제 API 구분.
3. 표현 유형·Q&A: FAQ 40개, 의미 해석 사례 16개. 실제 LLM 평가 결과 아님.
4. 구조 설계: Coordinator → 검증/도구 → Reporter. 역할 분리, 기존 parser/engine 미사용.
5. API 연결: 스티커/가상 번호 분리, 404 의미 보존, 요금 견적 재시도 방지.
6. 코드: 엄격한 응답 모델, 설정집 조회, 추천, 대화 문맥, OpenRouter 어댑터, CLI.
7. 환경: 별도 Python 가상환경을 재생성. `.env` 비밀값은 복원하지 않음.
8. 검증과 보관: 아래 실제 결과 및 다운로드 ZIP. 원격 push/배포 없음.

## 검증
- Python 3.12 환경에서 `requirements-test.lock` 기준 의존성 설치.
- 도구·서버 연동 테스트 15개 통과. 임시 SQLite, 차량 관측 이벤트→위치→견적→모의결제 확인.
- 대화·모델 어댑터 테스트 8개 통과. 실제 LLM API 호출 없음.
- 설정집 칸 ID와 칸 종류 156개 모두 upstream과 일치.
- 기존 frontend `npm run build` 성공. 기존 큰 JS chunk 경고 있음.
- CLI --help 및 기본 앱 OpenAPI health 경로 확인 성공.
- 전체 회귀 테스트: `python -m pytest tests/ -q` — **380 passed, 7 warnings, 114.42초**.

FastAPI TestClient 시험은 제한된 sandbox에서 정지하여 승인된 확장 실행으로 검증했다.
Starlette의 httpx/timeout 사용 중단 예정 경고가 있으며 이번 복구에서 의존성 계약을 변경하지 않았다.

## 한계와 다음 작업
실제 OpenRouter 모델 품질/가격·가용성, API 키 연결, Langfuse, HTTP/UI 연결은 아직 검증되지 않았다.
현재 런타임 secrets 목록은 비어 있다. 실제 차량 DB, 비밀키, Windows 로컬 채팅 기록은
복원 대상 산출물에 포함되지 않는다. 대화 기록만으로 복원할 수 없는 값이다.
시설은 프로젝트 시연 설정이며 실측 시설 안내나 안전 보증으로 사용하지 않는다.

## 다시 실행
저장소 루트에서 Python 가상환경을 만들고 `backend/requirements-test.lock`을 설치한다.
`python -m pytest tests/test_chatbot_v3_tools.py tests/test_chatbot_v3_conversation.py -q`로 검증한다.
모델을 사용할 때만 별도로 준비한 환경 설정으로
`python -m backend.app.chatbot_v3 --env-file /안전한/경로/.env --backend-url http://127.0.0.1:8000`
을 실행한다. 실제 호출은 선택 모델에 따라 과금될 수 있다.

ZIP에는 Git 기준 소스와 재구성 파일, 첨부 Q&A를 포함하며 `.git`, 설치 의존성, DB,
비밀 환경 파일은 포함하지 않는다. 클라우드 밖에도 ZIP을 저장해야 환경 교체에 대비할 수 있다.

## 후속 검토·수정

복구 후 [팀 규약·코드 검토](REVIEW_2026-11-07.md)에서 발견한 오류를 수정했다.
이전 380개 통과는 복구 직후 결과이며, 수정 후 결과는 **387 통과·기존 시뮬레이터 1 실패**다.
해당 실패 시험은 단독 재실행 시 통과했다. 새 챗봇 시험은 31개 모두 통과했다.
최신 코드 백업은 `parkon-reviewed-2026-11-07.zip`, 초기 복구 ZIP은 그대로 보존했다.
