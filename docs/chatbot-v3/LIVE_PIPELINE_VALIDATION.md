# 변경되는 서버 데이터와 챗봇 연동 검증 · 2026-11-07

검증 대상은 실제 `create_runtime_app`과 새 preview HTTP 서버의 연결이다.
FastAPI TestClient의 ASGI HTTP 경계로 두 앱을 연결했으며 외부 TCP 네트워크·부하 시험은 아니다.
운영 DB 대신 pytest 임시 SQLite를 사용했고, 실제 LLM 대신 명시적 FixtureModel을 사용했다.
실제 카메라·현장·결제기관을 검증한 것은 아니다. 서버의 관측 안정화·입출차·견적 로직은 실제 구현을 실행했다.

## 가상 차량 흐름

`tests/test_chatbot_v3_live_pipeline.py::test_virtual_entry_location_quote_replay_exit_through_chat_http`

1. D 주차장 빈자리 질문 결과를 `/parking-lots/D/spaces`의 empty 수와 비교.
2. demo의 `/simulation/D/entry`로 일반칸 A-04에 가상 번호 **0765** 입차.
3. 빈자리 감소와 새 질문의 갱신된 답변 비교.
4. `/chat`에 위치 질문 → `/vehicle-search`의 칸 A-04와 일치, 모의 데이터 안내 확인.
5. 요금 질문 → 실제 세션의 fee quote와 답변 금액 일치. DB 읽기로 견적 1건 확인.
6. 동일 request_id 재전송 → 동일 응답, 견적 여전히 1건.
7. `/simulation/D/exit` → 공실 복원, 새 질문의 갱신된 수 확인.
8. 위치 질문은 단일 위치 미확인 응답, 차량 미등록으로 단정하지 않음.
9. 출차 후 현재 위치 기반 요금 질문은 조회 불가로 응답하고 새 견적을 만들지 않음.

현재 챗봇의 요금 흐름은 **현재 차량 위치 → 활성 세션**이다. 출차한 과거 세션 요금 조회는
다른 기능이며 구현하지 않았다. 같은 request_id 재전송은 과거 응답의 재확인이고,
새로운 현황 질문은 새 request_id로 보내야 한다.

## 추천·경보 흐름

`tests/test_chatbot_v3_live_pipeline.py::test_recommendation_occupied_stale_and_acknowledged_alarm_on_runtime`

- real 런타임에 인증된 simulation 관측 이벤트를 보내 A-04/A-03/A-02의 상태를 확정.
- 영화관 근처 추천에서 가상 시설 설정임을 표시하고 실제 empty 칸을 선택.
- A-04 점유 관측을 확정한 뒤 새 질문에서 A-04 제외, A-03 추천 유지.
- LED **모의 화재** 이벤트로 active Lv2 경보 생성 → 추천 중단.
- acknowledged로 변경해도 추천 중단 유지. resolved 뒤에는 추천 재개.
- 관측 없이 11초 경과 → 오래된 칸 추천 안 함.

오래된 관측 시험에서는 시간을 기다리는 대신 런타임·추천·경보의 시계를 함께 제어했다.
처음에는 경보 모듈만 실제 시계를 사용해 경보 자체의 신선도 검사에서 중단됐고,
시험 시계를 맞춘 후 칸 관측 만료까지 검증했다. 서버 코드를 바꾸어 시험을 통과시킨 것이 아니다.

## 새 시험 화면 예시

기존 fixture 질문에 아래 3개를 추가했다. 자유로운 자연어 파서는 추가하지 않았다.
- 신세계 빈자리 있어?
- 0765 차량 어디 있어?
- 0765 차량 요금 알려줘

demo runtime을 연결하고 시험용 0765 차량을 입차시켜야 위치/요금 조회가 가능하다.
이 버튼들은 조회만 한다. 입·출차나 경보 변경은 테스트/관리자 시뮬레이터에서 수행한다.
`KEYLESS_PREVIEW.md`의 고정 mock 서버만 연결하면 실제 활성 세션을 만들지 않는다.

## 결과

- 새 통합 시험: **2 passed / 9.24초**.
- 전체 회귀: **396 passed, 14 warnings / 124.44초**. 경고는 기존 Starlette/httpx 및 crypt 사용 중단 예정 안내.
- 실제 LLM 호출·API 과금 없음. 프런트엔드 소스는 이번 단계에서 변경하지 않음.
- 실제 한국어 이해력은 아래 평가 자료로 별도 검증해야 함.

## 실제 모델 평가 준비

[evaluation/questions.v1.json](evaluation/questions.v1.json)에 **34개** 문항을 정리했다.
정상 표현, 유사 명칭, 부정/번호 정정, 문맥, 모호한 표현, 미지원 기능, 오타,
앞자리가 0인 차량번호, 모르는 시설 정보, 지시문 삽입을 포함한다.

각 문항에 기존 context, 허용 intent, 실행할 경우 반드시 맞아야 할 인자를 기록했다.
`clarify`가 허용된 문항에서는 확인 질문 시 실행 인자를 생략할 수 있다.
명확한 요청을 자꾸 clarify하는 것도 별도 비율로 집계해야 하며 안전하게 거절했다는 이유만으로
자연어 품질이 좋다고 평가하지 않는다.

평가 시 기록할 항목: 모델명/프롬프트 버전/질문 ID/원본 Decision/도구 선택/답변 근거/
정확 여부/확인 질문 적절성/지연/토큰·비용. 외부 모델 평가에서는 개인정보 없는 가상 번호만 사용한다.
먼저 Coordinator만 평가하고, 그다음 고정된 테스트 서버 상태에서 응답까지 평가한다.
번호·주차장 오선택, 근거 없는 요금, 모의 경보 중 추천, 허구 안전 보장은 별도 중대 실패로 기록한다.
이 자료의 status는 `not_run_against_live_llm`이며 모의 모델 통과를 실제 성능 결과로 기록하지 않는다.

재현 명령 (저장소 루트, 테스트 의존성 설치 후):

```bash
python -m pytest tests/test_chatbot_v3_live_pipeline.py -q
```

임시 DB는 pytest가 생성하므로 운영 DB 경로를 설정하거나 기존 DB를 지울 필요가 없다.
