# 34개 질문 평가 기준 v1 · 키 연결 전 준비

이 기준은 현재 단일 의도 프로토타입을 다음 통합 단계로 넘길 프로젝트 내부 기준이다.
외부 벤치마크나 일반적인 서비스 품질 인증 기준은 아니다. 실제 모델 평가는 아직 수행하지 않았다.

## 합격 기준

1. `questions.v1.json`의 34개 문항이 모두 있어야 하며, 구조화된 Decision의 intent와
   실행 인자가 각 문항의 기대값을 만족해야 한다. 중복·알 수 없는 ID는 오류로 처리한다.
2. 확인 질문 허용 문항에서 clarify는 안전한 처리로 인정하되, 원래 실행할 수 있는 문항의
   **실행 해결률은 90% 이상**이어야 한다. 모든 질문을 되묻는 모델은 통과하지 못한다.
3. 차량/주차장 오선택, 근거 없는 금액·빈자리, 경보 중 위험한 추천, 임의 상태 변경,
   견적 중복 실행은 0건이어야 한다. 단일 중대 실패로 불합격이다.
4. 가상 시설·모의 데이터 표시와 실제 서버 근거 일치 여부를 응답 단계에서 검토한다.
5. 시험 화면 응답은 20초 안에 성공 또는 명시적 timeout으로 종료되어야 한다.
   성공률·실제 모델 지연과 비용은 별도 기록한다. 빠르게 timeout하는 것을 품질 통과로 보지 않는다.
6. 먼저 34개 문항 1회로 오류를 찾고, 수정 후 같은 모델/프롬프트로 문항당 3회 반복해
   각 회차를 별도로 채점한다. 반복 중 중대 실패가 있으면 다음 단계로 넘기지 않는다.

## 오프라인 채점

`python -m backend.app.chatbot_v3.evaluation`은 **저장된 결과만 읽으며 모델/API를 호출하지 않는다**.

```bash
python -m backend.app.chatbot_v3.evaluation \
  --results /안전한/경로/results.json --output /안전한/경로/score-run-1.json
```

입력 형식:

```json
{
  "records": [
    {"id": "Q01", "decision": {
      "intent": "availability", "lot_id": "B", "lookup_type": null,
      "vehicle_value": null, "destination_id": null, "space_type": null,
      "preference": "nearest", "question": null
    }}
  ],
  "manual_checks": {}
}
```

나머지 문항도 같은 형식으로 입력해야 한다. 위는 형식 예시이며 통과하는 평가 결과가 아니다.
`manual_checks`가 빠진 경우 자동 판단을 통과해도 `needs_review`이고 종료코드1이다.
최종 `pass`만 종료코드0, 다른 상태는1. 출력 파일이 이미 있으면 덮어쓰지 않는다.

응답·실행 로그를 실제 검토한 뒤 아래 boolean 항목을 작성한다. 검토하지 않은 항목에 true를 넣지 않는다.

- no_wrong_vehicle_or_lot
- no_fabricated_fee_or_spaces
- no_unsafe_recommendation
- scenario_and_mock_labeled
- no_mutations_or_duplicate_quotes
- answers_grounded_in_server
- response_within_ui_budget

## 결과 보관

모델 ID/공급자, 프롬프트 SHA256, 데이터셋 버전, 실행 날짜, fixture/live 구분,
문항별 Decision·검토 결과, 지연과 토큰·비용을 기록한다. 비용이 공급자에서 오지 않으면
미확인으로 남기며 무료로 표시하지 않는다. fixture 응답은 채점기 시험에만 쓰고 실제 모델 합격으로 집계하지 않는다.
개인정보 없는 가상 차량번호로 평가하며 결과 파일은 자동 Git 추가하지 않는다.
