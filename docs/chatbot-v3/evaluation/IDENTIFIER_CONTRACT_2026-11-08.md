# 차량 식별자 출력 계약 보완 · 2026-11-08

## 변경 이유

2회차 평가의 Q18/Q23에서 lookup_type=sticker와 vehicle_value=0765 조합이 나왔고,
Q25는 전각 숫자를 차량 조회로 선택했다. 프롬프트 설명만으로는 해결되지 않았다.

## 모델 출력과 내부 조회 형식 분리

Coordinator의 submit_result에 한해 vehicle_identifier를 객체/union/null로 받는다.

- {"kind":"sticker","value":"23"}: ASCII 두 자리 10~99.
- {"kind":"plate_suffix","value":"0765"}: ASCII 네 자리, 앞의 0 보존.
- 다른 의도: vehicle_identifier=null. 차량/요금 의도에는 유효한 객체가 필요하다.

각 객체는 kind/value를 모두 요구하고 추가 필드를 금지한다. 숫자형/전각/혼합 숫자/
길이 불일치/누락/잘못된 kind는 거절한다. 실패를 다른 종류로 바꾸거나 값을 정규화하지 않는다.
두 종류에 서로 다른 문자열 패턴을 제공하고, 공급자가 스키마를 지키지 않아도 로컬 검증이 차단한다.
의도와 identifier 존재의 일치도 로컬에서 검증한다.

검증된 객체만 기존 Decision.lookup_type/vehicle_value로 옮긴다. 내부 도구·문맥·오프라인
채점 결과 형식·FixtureModel은 기존 필드를 유지한다. Selection/Reporter 출력 계약도 유지한다.
입력 confirmed_context의 평면 필드와 출력의 묶음 필드 차이는 프롬프트에 명시했다.
이전 평면 Coordinator 공급자 응답을 자동 수용하는 fallback은 두지 않았다.

## 검증과 한계

- 관련 8개 파일 **92 passed, 13 warnings / 11.42초**. 전체 저장소 회귀가 아니다.
- 새 계약 시험 21개: 정상 번호 보존, 스키마 패턴/필수 필드, 부적합한 조합 거절,
  실패 사용량 보존, 재시도/주차 API 실행 없음, 확인 질문 유지.
- 기존 API 입력과 채점용 Decision 형식은 유지한다. 모델 응답을 주입하는 모의 HTTP 시험만 수행했다.
- 실제 OpenRouter 공급자가 nested anyOf/$ref/const/pattern을 수용하고 강제하는지는 미검증이다.
  이 보완이 31/34를 개선했다는 실측 근거는 아직 없다. 스키마가 오류를 거절하는 것과
  모델이 처음부터 정확한 의도/번호를 선택하는 것은 별도다.
- 후속 실제 시험은 소량으로 공급자 스키마 수용과 선행 0/전각 질문을 확인한 뒤 범위를 확대한다.
  프롬프트 SHA뿐 아니라 공급자 스키마 SHA와 코드 기준을 함께 기록해야 한다.
- 추가 유료 호출/자동 재시도/Langfuse 전송 없음. fixture 기본 모드 유지.
