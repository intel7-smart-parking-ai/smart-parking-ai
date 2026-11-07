# 첫 모델 연결 시험과 오프라인 보완 · 2026-11-08 KST

## 승인과 실제 호출

사용자는 `openai/gpt-4.1-mini`로 합성 질문 최대 3개를 시험하도록 승인했다.
Coordinator만 2회 요청했고 응답 검증 실패 후 세 번째 요청은 보내지 않았다.
자동 재시도, Reporter, 주차 조회 API 실행, Langfuse 외부 전송은 없었다.
첫 요청의 정확한 실패 필드/사용량은 보존되지 않았다. 두 번째는 HTTP 200,
submit_result 함수 1개와 필수 인자 전체를 받았으나 preference에서 literal_error가 발생했다.
잘못된 값 자체는 보존하지 않았으므로 null이었다고 단정하지 않는다.

두 번째 요청 공급자 보고: 입력 4817, 출력 42, 합계 4859토큰, USD 0.001994.
첫 요청 비용이 미확인이므로 전체 비용은 미확인이다. 실패 요청을 무료로 취급하지 않는다.

## 수정

- preference는 항상 nearest/easy, 추천 외 의도에는 nearest라는 규칙을 프롬프트와
  필드 설명에 명시했다. 무관한 필드를 null로 반환하라는 기존 지시의 예외를 명확히 했다.
- submit_result 함수에 strict=true를 요청한다. Decision/Selection의 모든 필드가 required이고
  additionalProperties=false인지 모의 HTTP 시험에서 확인한다.
- 응답 검증 실패 전에 공급자의 숫자 사용량/비용을 보존한다. 대화 응답과 명시적 메모리
  관측에도 실패 호출 사용량을 포함한다. 자동 Langfuse 전송은 활성화하지 않는다.
- 잘못된 preference를 정상 값으로 변환하지 않는다. 파싱 실패 시 주차 API를 실행하지 않고,
  같은 request_id 재전송도 모델을 다시 호출하지 않는다.

## 검증과 한계

추가 오프라인 회귀 시험은 null/잘못된 문자열/숫자 preference, 정상 Decision/Selection,
실패 사용량 보존 및 다음 호출에서 사용량 초기화를 다룬다. 모두 MockTransport를 사용한다.
수정 후 실제 공급자의 strict 지원/출력 품질은 아직 검증하지 않았다.
카탈로그 매개변수 지원은 실제 공급자의 스키마 수용이나 한국어 정확도 보장이 아니다.

전체 34문항 평가와 Langfuse 전송은 아직 수행하지 않는다.
추가 유료 호출 없이 오프라인 보완까지만 진행했다. fixture 기본값과 OPENROUTER_MODEL
미설정 상태를 유지한다. 다음 유료 재검증의 모델/문항/예산 범위를 먼저 명확히 한다.

원시 로컬 결과는 /workspace/parkon-reports 아래에 있다. 이 문서는 비밀값을 제외한
인계 기록이며 키/.env/원시 HTTP 헤더는 Git에 포함하지 않는다.

검증 실행: chatbot_v3 model_contract/conversation/evaluation_observability/preview/tools/live_pipeline
6개 시험 파일, **51 passed, 13 warnings / 11.18초**. 경고는 기존 Starlette/httpx 사용 중단 예고.
전체 저장소 회귀 시험이나 실제 모델 재평가 결과를 의미하지 않는다.

작업 브랜치 원격 저장도 시도했으나 현재 HTTPS Git 인증에서 Username을 읽지 못해
push가 실패했다. 읽기/clone 성공은 쓰기 권한을 보장하지 않는다. 비밀 토큰을 추출하거나
원격 주소를 변경하지 않았다. 로컬 커밋과 /workspace/parkon-reports의 Git bundle을 보존한다.
