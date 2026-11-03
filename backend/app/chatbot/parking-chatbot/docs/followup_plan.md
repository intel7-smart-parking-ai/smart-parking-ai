# 후속 제안 — 이번 소비자 API 정렬과 분리

1. 폴더 이름을 parking_chatbot으로 바꾸는 작업은 별도 커밋·별도 PR로 제안한다. 실행 디렉터리, 서버 실행 명령, 팀 통합 import 경로, README·실행 가이드, tests의 부모 경로 설정, tools/api_smoke.py 경로를 함께 검토한다. 이번에는 폴더명을 변경하지 않는다.
2. 문서는 README, settings_guide.md, chatbot_guidelines_v2.md, api_contract.md, 검증 요약 하나를 남기는 방향으로 제안한다. 기존 실행 안내와 이전 결과 문서는 다음 문서 정리 PR에서 보관 위치를 정한다. docs/history의 검증 JSON·이미지·로그는 삭제 후보이며 이번에는 삭제하지 않는다. 용량보다 재현 자료 보존 여부를 팀이 판단한다.
3. 저장소 루트 테스트에 챗봇 핵심 검사를 연결하는 얇은 래퍼 한 개를 제안한다. QA 담당과 테스트 수집 경로·선택 의존성·환경변수 격리를 협의해야 한다. 예: 루트 래퍼가 챗봇 폴더를 cwd로 subprocess unittest를 실행하고 종료 코드를 검증한다. subprocess에는 PYTHONIOENCODING=utf-8, encoding=utf-8, errors=replace를 지정한다. 이번에는 루트 파일을 수정하지 않는다.
4. 목업 요금 변경은 11/04 별도 작업으로 둔다. 실제 팀 billing 계산기를 읽고 0·30·31·40·41분과 일 최대 상한 경계값을 대조한 후, 설정값 및 달라지는 기대값을 별도 표로 제시한다. 현재 작업 공간에는 해당 계산기가 없다.
