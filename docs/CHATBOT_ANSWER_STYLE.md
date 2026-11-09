# 챗봇 공통 답변 형식

- 빈자리: `전기차 전용칸 빈자리는 다음과 같아요.` 다음 줄에 `개발원 2자리 · 부산역 2자리` 형식. 일반·장애인도 같은 형식입니다.
- 이름: A/B/C/D는 개발원/부산역/김해공항/신세계로 표시합니다. A구역·B-01 같은 실제 구역·칸 번호는 유지합니다.
- 상세 정보: 차량·요금·매출·경보는 불필요한 소개 없이 `항목: 값`을 줄별로 표시합니다. 서버 숫자와 상태를 그대로 사용합니다.
- 복합 질문: 담당별 제목과 결과를 표시하며 처리 개수 소개는 생략합니다.
- 지도: 위치·시설·빈자리 답변은 공통 지도 카드 사용. 전체 빈자리는 주차장별 지도, 특정 주차장은 해당 지도만 표시합니다. 전기차·장애인 필터는 질문에 맞게 선택합니다. 경보 안전 안내에서는 일반 지도를 숨깁니다.
- 안내 사실: 주차장 guide만 근거로 사용하고 미등록 사실을 생성하지 않습니다. 예약 확정 창의 시연 안내는 유지합니다.

변경 위치:
- 공통 문장: parking-chatbot/chatbot/answer_format.py
- 기존 챗봇 빈자리: navigation.py
- 에이전트 빈자리: report_lab/spaces.py
- 역할별 템플릿: Langfuse의 parkon-report-*와 report_lab/prompts.py 기본값
- 지도: frontend/src/ChatResponseCard.jsx
- 운영 안내: lots.json의 guide 및 CHATBOT_SITE_GUIDES.md

대표 질문 묶음은 parkon-report-lab/test_scoped_agents.py의 공통 형식 검사에서 실행합니다. API 통합 검사는 tests/test_report_chat_integration.py에서 번호 확인·지도·예약·안전 흐름을 확인합니다.
