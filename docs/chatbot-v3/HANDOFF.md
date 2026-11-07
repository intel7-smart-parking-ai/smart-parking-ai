# 새 Codex 채팅 인계

## 가져올 작업
- 저장소: https://github.com/intel7-smart-parking-ai/smart-parking-ai
- 브랜치: chore/chatbot-v3-recovery-20261107
- 현재 문서를 포함한 브랜치 최신 커밋에서 시작한다. main으로 돌아가 작업하지 않는다.
- 기존 사용자 파일·DB·다른 세션을 삭제하지 않는다. 환경 초기화하지 않는다.

## 사용자 목표와 현재 범위
PARK:ON 챗봇을 기존 규칙 parser/큰 engine.py에 의존하지 않고 재작성한다.
가상 시설 설정집과 실제 서버 API를 결합해 Q&A/위치/빈자리/요금/목적지 추천을 제공한다.
시설은 가상 시나리오라고 표시하고 실제 현장 정보로 단정하지 않는다.
현재 사용자는 다음 단계 중 **1번: 평가 기준 확정 및 Langfuse 연결 코드 준비까지만** 승인했다.
실제 유료 LLM 평가·외부 Langfuse 전송은 아직 수행하지 않았다. 키 연결만으로 이를 시작하지 않는다.

## 완료한 작업
- backend/app/chatbot_v3: 엄격한 API 응답 검증, 차량 식별자별 API 분기, 요금 견적 중복 방지,
  시설 지식 조회, 신선도/경보 기반 추천, Coordinator/Reporter, 콘솔.
- docs/parking-knowledge: 156칸/28시설/13목적지 순위, FAQ40/언어 사례16.
- 독립 preview 서버8013 + frontend/chat-v3.html. 기존 운영 /chat은 교체하지 않음.
- fixture 모드는 예시 문장만 처리하고 유료 API 호출 없음. 실제 자연어 성능으로 간주하지 않는다.
- 18초 응답 대기, 2대화 병렬, 서명된 만료 세션, timeout 후 동일 요청 재실행 방지.
- 실제 runtime + 임시 SQLite + TestClient로 입출차/견적/추천/경보 검증.
- 실제 모델 평가 질문34개와 오프라인 채점 도구 evaluation.py.
- 관측 준비: observer 기본 None, TraceRecorder 주입 시 메모리에 기록.
  LangfuseExporter는 명시적 export 호출 때만 전송. 원문/차량/세션/키 제외.
  환경변수 존재만으로 자동 활성화하지 않는다.

## 검증 근거와 한계
- ede9f21 시점 전체 Python 테스트396개 통과.
- 앞 단계 프런트엔드 시험9파일·빌드 성공, Chromium 모바일 화면 확인.
- 마지막 평가/관측 변경은 관련20개 시험을 재확인한다. 전체 회귀 결과로 혼동하지 않는다.
- 이전 clear 시뮬레이터 시험에서 간헐503이 있었고 후속 전체 실행에서 재현되지 않았다.
- 실제 모델 성능, Langfuse 실제 ingestion/dashboard, Windows 실기는 미검증.
- 현재 HTTP 시험은 한국어/텍스트만. 운영 UI 다국어/업데이트/인증/영속·분산캐시는 남음.

## 이동 이유 / 비밀 설정
기존 채팅의 환경 설정에서 '초안을 사용할 수 없음' 및 변경 충돌로 저장 버튼이 잠김.
다른 채팅에서는 환경 설정 가능하다고 사용자가 확인했다. 정확한 잠금 원인은 미확정.
키 값은 Git·문서·채팅에 보관하지 않는다. 새 환경에서 OPENROUTER_API_KEY 비밀 연결 상태만
조회하고 OPENROUTER_MODEL 및 openrouter.ai 네트워크 허용을 별도로 확인해야 한다.
Langfuse BASE_URL/HOST 둘이 다르면 오류. PUBLIC_KEY/SECRET_KEY 필요.
이전 환경의 .env나 비밀값이 이동됐다고 가정하지 않는다.

## 새 채팅 첫 작업
1. 브랜치 최신 커밋과 git status 확인, 기존 변경은 보존.
2. 이 문서와 KEYLESS_PREVIEW.md, evaluation/ACCEPTANCE.md,
   OBSERVABILITY_PREPARATION.md, REVIEW_2026-11-07.md 읽기.
3. Python 가상환경 생성 후 backend/requirements-test.lock 설치.
4. 마지막 추가 작업의 관련 시험 실행; 필요 시 전체 tests/ 실행.
5. 사용자에게 환경/키 연결 상태를 값 노출 없이 보고. 실제 모델 평가로 넘어가는 것은
   사용자의 후속 요청을 받은 후 진행.

RECOVERY_MANIFEST.json과 이전 ZIP은 초기 복구 시점 자료다. 최신 내용은 이 작업 브랜치를 기준으로 한다.
대화 자체가 다른 채팅으로 자동 이전되는 것은 아니므로 이 문서로 작업 맥락을 이어간다.

## 후속 상태 · 2026-11-08 KST (위 초기 인계 이후)

- 사용자는 gpt-4.1-mini 합성 질문 최대 3개 유료 연결 시험을 승인했다.
  실제 2회 요청 후 preference 검증 실패로 중단했다. 34문항 평가와 Langfuse 전송 승인은 아님.
- 이후 사용자 요청으로 추가 유료 호출 없이 프롬프트/strict 함수 스키마/실패 사용량 보존을 보완했다.
- 상세 결과·비용 한계: MODEL_CONNECTION_2026-11-08.md.
- fixture 기본값을 유지하고 실제 공급자에 대한 수정 후 재검증은 아직 하지 않았다.
- 환경 UI에는 저장만 표시됐으며 게시 완료라고 가정하지 않는다.
- 키는 저장 연결과 프로세스 존재를 확인했지만, 일반 변수 런타임 적용 상태는 별도 재확인한다.

### 마지막 연결 확인 갱신
- 수정 후 원래 3회 승인 중 마지막 1회를 사용: gpt-4.1-mini, 모호한 터미널 질문에
  clarify + preference=nearest, Decision 검증 성공. 1.551초, 보고 비용 $0.0020564.
- 지금까지 모델 생성 총 3회. 승인 잔여 호출 없음. 첫 호출 비용은 여전히 미확인.
- 34문항 평가는 별도 범위 승인 후 수행. 단일 연결 성공을 전체 품질 합격으로 보지 않음.
- 임시 backup 원격 khbyeol79-cloud/parkon-chatbot-backup은 연결했지만 동일한 인증 오류로 push 실패.
  origin은 보존. 로컬 Git 커밋과 Git bundle로 보존하며 GitHub 업로드 완료라고 표시하지 않음.

### 34문항 Coordinator 실제 평가 1회 완료
- 사용자가 34문항 1회 평가를 별도로 승인해 34회 모델 요청 수행. 재시도/Reporter/API/Langfuse 없음.
- 응답 스키마 34/34 통과, 의미 기준 22/34, 실행 해결률 10/17=58.8%, 결과 fail.
- 총 공급자 보고 비용 $0.0683392. 상세 실패 목록: evaluation/RUN_2026-11-08_COORDINATOR_1.md.
- 원본 결과와 score는 /workspace/parkon-reports/coordinator-34-run1-*.json에 보존.
- 다음은 식별자/목적지/복합 요청/안내 의도 프롬프트 및 실행 경계 오프라인 보완.
  후속 유료 재평가나 3회 반복은 아직 실행하지 않았음.

### 첫 34문항 실패 후 오프라인 보완
- 프롬프트/필드 설명에 식별자·목적지·복합 비교·guidance 구분을 구체화.
- 실행 의도+question 동시 출력은 API 실행 전 차단. Unicode 숫자의 ASCII 부분 매칭/이전 번호 재사용 방어.
- 관련 7파일 71 passed. 새 20개 회귀는 주입 Decision/모의 HTTP로 검증하며 모델 품질 점수 아님.
- evaluation/OFFLINE_FIX_2026-11-08.md 참고. 기존 실제 점수는 여전히 22/34, 재평가 미수행.
- 유료 호출/Langfuse 전송 추가 없음. 다음 유료 평가 범위를 정하기 전 모델을 호출하지 않음.

### 수정 후 실제 34문항 재평가
- 사용자가 승인한 별도 34회 재평가 완료. 모델 gpt-4.1-mini, 코드 ea976b7.
- 31/34 통과, 실행 해결률 15/17=88.2%, 여전히 fail. 첫 평가 대비 9문항 개선, 기존 정답의 신규 실패 없음.
- 남은 Q18/Q23: 0765를 sticker로 분류, Q25: 전각 차량번호를 vehicle로 선택.
- 세 실패 모두 현재 _validate가 dispatch 전에 차단함을 오프라인 확인. 모델 정답으로 집계하지 않음.
- 총 보고 비용 $0.080036. 상세 evaluation/RUN_2026-11-08_COORDINATOR_2.md.
- 후속 후보: lookup_type-식별자 패턴 계약 강화. 추가 유료 호출/3회 반복/Langfuse 전송 없음.

### 식별자 출력 계약 오프라인 강화
- OpenRouter Coordinator 출력은 vehicle_identifier={kind,value} union/null. 종류별 ASCII 패턴을 적용.
- 로컬에서 검증한 객체만 기존 평면 Decision으로 매핑. 내부 API/fixture/채점 형식 유지.
- 부적합 번호를 자동 수정/재분류하지 않음. 실패 비용 기록과 동일 요청 재실행 방어 유지.
- 관련 8파일 92 passed. 상세 evaluation/IDENTIFIER_CONTRACT_2026-11-08.md.
- 유료 재시험 미수행: 공급자 nested schema 수용은 미검증, 기존 실제 점수는 31/34.
- 후속 실제 호출은 소량 스키마 수용 시험부터 범위를 정하고 시작. 프롬프트+스키마 SHA를 함께 기록.

### 새 식별자 스키마 실제 3문항 확인
- 후속 승인으로 Q18/Q23/Q25 각 1회, 실제 3회 호출. nested 스키마 수용과 Decision 검증 성공.
- 차량 위치/요금의 0765를 plate_suffix로 보존, 전각 번호는 clarify. 부분 채점 3/3.
- 총 공급자 비용 $0.0072008. 상세 evaluation/IDENTIFIER_SMOKE_2026-11-08.md.
- 전체 최신 34문항은 미실행. 이전 31/34와 이 3개를 합산해 전체 통과로 표시하지 않음.
- 수동 종단 검토 미실행으로 부분 scorer는 needs_review. 주차 API/Reporter/Langfuse 전송 없음.
- 다음은 최신 계약 전체 34문항 재평가 범위를 정한 뒤 진행. 원격 저장 여전히 미완료.

### 최신 식별자 계약 전체 34문항 1회 결과
- 사용자 승인으로 34회 추가 평가. 코드 b4b023e. 새 스키마 기준 전체 1회이며 반복 3회 완료 아님.
- 32/34, 실행 해결률 17/17=100%, 여전히 fail. Q18/Q23/Q25 개선, Q16/Q33 신규 실패.
- Q16 오타 장소를 facilities/D로 단정, Q33 허위 빈자리 지시를 availability/null로 분류.
- 두 실패 모두 _validate 통과. 실제 API/최종 답변은 미실행이므로 허위 답변 발생으로 단정하지 않음.
- 공급자 보고 $0.0763184. 상세 evaluation/RUN_2026-11-08_COORDINATOR_3.md.
- 다음: 미확인 장소/비정상 지시 분류의 오프라인 보완. 추가 유료 호출/Reporter/Langfuse 없음.

### Q16/Q33 오프라인 실행 경계 보완
- 선택한 주차장의 catalog ambiguous_examples에 해당하는 표현은 차량/요금 외 실행 전 확인.
- availability/null은 전체 주차장 범위를 명시한 요청에만 허용. 장소 누락의 전체 조회 확대 차단.
- 허위 값/시스템 지시 변경 요구와 장소만의 입력에 대한 모델 우선 지시 강화.
- 일반 프롬프트 주입 해결이 아니며 부정/정정에도 보수적으로 확인할 수 있음.
- 관련 9파일 106 passed. 상세 evaluation/REQUEST_BOUNDARY_FIX_2026-11-08.md.
- 추가 유료 호출 없음. 실제 점수는 32/34로 유지. 후속은 실패/변형 소량 실제 검증 범위를 정해 진행.
