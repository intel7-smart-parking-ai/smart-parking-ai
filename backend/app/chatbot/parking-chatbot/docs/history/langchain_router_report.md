# LangChain 도구 라우터 구현 결과 — v2 기준

> v2 당시 실행 기록입니다. v2.2 R1·R3 변경과 현재 검증 결과는 [v2.2 변경 요약](v2_2_change_summary.md)을 참조하세요. 평가 CSV 경로는 새 이름으로 갱신했습니다.

작성: 2026-11-02. 입력 ZIP SHA256: `70351972c9bf3963177941fdd9c73294eded24099a9e45e3af36cc1d4b83a89a`.
`v2.zip`과 `v2(1).zip`의 바이트가 동일함을 확인했다. 이 보고서는 사용자 최종 요청인 **파일 내부 지시서에 따른 LangChain 구현**의 결과다. 이전에 진행하던 광범위 안정화 작업본을 덮어씌우지 않았다.

## 범위와 변경

`00_GPT_작업요청.md`, `01_작업방향_LangChain_하이브리드.md`, 공통 챗봇 지침서, 직전 보완 결과를 읽고 작업했다. 코드 변경 전에 파일별 계획을 안내했다. 기존 초본은 261개 검사에 통과했다.

| 파일 | 변경 이유와 결과 |
|---|---|
| chatbot/llm_router.py | 21개 도구의 한 줄 설명·예문, 동적 명부 enum, 선택 제공자 생성, bind_tools, 첫 도구만 분류, 실패 카운터와 제한된 실행 |
| chatbot/parser.py | allow_llm, LLM_ENGINE 분기, 유효한 zone 전달. 기존 의도·차량·주차장 검증과 규칙 추출값 우선 유지 |
| chatbot/engine.py | 서버 초기 백그라운드 준비, Lv2 우회, 분류 중 Lv2 전환 시 결과 폐기, 관리자 stats에 llm 필드 추가 |
| chatbot/config.py, .env.example | LLM_ENGINE=langchain 기본값, direct 복구 경로, 환경변수 검증과 사용 안내 |
| requirements-llm.txt | 실제 설치한 LangChain 3개 패키지 버전 고정 |
| tests/test_llm_router.py | 새 검사 28개. 가짜 모델과 선택 설치 환경의 실제 LangChain 메시지·제공자 생성 검사 |
| tests/llm_eval_questions.csv, tests/run_llm_compare.py | 규칙 미인식 문장 20개, 규칙/하이브리드 정확도·평균/최대 지연·실패 이유 집계 |
| 기존 테스트 3개 파일 | 직접 분류기를 patch하던 5개 테스트에 LLM_ENGINE=direct를 명시. 기존 assertion·기대값·질문 CSV는 변경하지 않음 |
| README.md, 본 문서 | 설치·활성화·롤백·검증·남은 한계 |

`llm.py`, `adapters.py`, `api_paths.json`, `templates.py`, `briefing.py`, `navigation.py`, 두 서버, static 화면은 원본과 동일하다. v2의 브리핑 장비 판단 범위·확인 중 칸 문구와 Windows 테스트 호환 수정도 유지했다. 팀 API 연결·AgentExecutor·LangGraph·RAG·음성·삭제 기능 복구는 추가하지 않았다.

## 실행과 안정성 설계

1. 버튼은 기존 처리 함수로 바로 간다. 규칙이 처리하지 못한 문장만 선택 라우터에 들어간다.
2. LangChain import와 모델 생성은 최초 별도 daemon 스레드 한 개에서 수행한다. 준비 중 요청은 기다리지 않고 `preparing`으로 대체한다. 생성 실패 후 같은 설정은 30초 동안 반복 생성하지 않는다.
3. 모델은 제공자·모델·시간 제한·키 변경을 기준으로 재사용한다. API 키 원문을 캐시 키나 통계에 보관하지 않는다. 도구 명부와 시스템 지시문은 요청 때 다시 만든다.
4. 도구 결합과 호출을 포함해 기본 3초, 설정값이 더 작으면 그 값, 요청의 잔여 예산이 더 작으면 잔여 예산으로 제한한다. 설정값이 3초보다 커도 LangChain 경로의 상한은 3초다.
5. 동시에 실행되는 분류 작업은 최대 2개이며 대기열은 없다. 시간 초과 작업은 실제 종료할 때까지 슬롯을 차지한다. 응답한 요청에 늦은 결과를 반영하지 않는다. 스레드는 강제 종료하지 않는다.
6. 모델 잠금과 통계 잠금은 분리했고, import·모델 생성·네트워크 호출 중 둘 다 잡지 않는다. 초기화 지연 중 통계와 다른 요청이 반환됨을 검사했다.
7. 도구는 함수가 아닌 스키마다. 첫 tool call만 검증하고 나머지·자유 생성 문장은 사용하지 않는다. 확인 승인 도구, 명부 밖 인자, 규칙 전용 인자, 잘못된 타입은 거절한다. 결제·호출에 모델이 넣은 차량 번호는 제거한다.
8. Lv2에서는 direct와 langchain 모두 건너뛴다. 분류 중 경보가 올라간 경우 현재 경보를 다시 확인하고 모델 선택을 버린다. 기존 결제 확인·차량 확인·요청 재전송·관리자 호출 중복 통합을 유지한다.
9. 카운터는 `disabled`, `not_installed`, `missing_key`, `timeout`, `api_error`, `no_tool_call`, `invalid_intent`, `invalid_params`, `ok`, 추가로 `preparing`, `busy`다. 경고는 이유별 최초 1회, 카운터는 매번 증가한다. 사용자 원문·비밀키·SDK 오류 본문을 라우터 로그에 쓰지 않는다. 통계는 프로세스 누적이며 시연 데이터 초기화와 별개다.

## 실제 실행한 검증

Linux, Python 3.12에서 실행했다. Windows·Python 3.11/3.13에서 새 구현을 실행했다고 주장하지 않는다.

| 검사 | 결과 | 로그 |
|---|---|---|
| 입력 기준본 전체 | 261/261 | validation_langchain/baseline.txt |
| LangChain 설치 전체 | 289/289 | validation_langchain/installed.txt |
| LangChain 미설치, FastAPI 설치 | 287 통과, LangChain 설치 전용 2개 정상 skip (총 289) | validation_langchain/without_langchain.txt |
| 기존 질문 7세트 | 237/237, LLM 꺼짐 | validation_langchain/questions.txt |
| Chromium 7세트 | 55개 통과 | validation_langchain/browser_*.txt |
| 혼합 동시 반복 | 30세션·2,400요청·경보 변경 40회, 불변 조건 통과 | validation_langchain/soak.json |
| 선택 패키지 의존성 | pip check 통과 | validation_langchain/installed_versions.txt |
| LLM 전용 비교, 키 없음 | 규칙 3/20, 하이브리드 정상 건너뜀 | validation_langchain/llm_compare_without_key.json |

새 검사는 정상 분류, 잘못된 인자·도구, 확인 승인 차단, 명부 변경, 사용자 번호 우선, 모델 번호 제거, 결제 확인 유지, 관리자 단일 접수, 준비 지연, 작업 상한, 늦은 결과 폐기, 통계 비차단, 실제 3초 경과 후 반환, bind_tools 지연, 잔여 예산 소진, Lv2 및 분류 중 경보 전환을 다룬다.

실제 `GenericFakeChatModel`과 `AIMessage.tool_calls`를 통과시켰다. 실제 `ChatOpenAI`·`ChatAnthropic` 객체 생성과 21개 도구 bind_tools도 검사했다. 이 생성 검사는 실행 환경의 SOCKS 프록시 변수를 테스트 안에서만 제외했다(프록시의 선택 의존성 미설치 때문). 유료 API 통신은 전혀 하지 않았다.

LLM 전용 20문장은 모두 규칙 결과가 unknown임을 확인했다. 그중 범위 밖 질문 3개는 unknown이 정답이다. 따라서 규칙 3/20은 의도적으로 규칙 미인식 문장만 모은 결과이며 전체 성능을 뜻하지 않는다. 하이브리드 정답률은 **미측정**이다. 가짜 모델로 정답률 상승을 만들어 보고하지 않았다.

## 설치 버전과 팀 통합

고정한 직접 의존성: `langchain-core==1.7.3`, `langchain-openai==1.7.3`, `langchain-anthropic==1.7.7`.
함께 검증한 버전: FastAPI 0.162.0, Pydantic 2.14.2. 팀 조건 `fastapi>=0.110,<1.0`, `pydantic>=2,<3` 범위 안이다. 전체 전이 의존성은 `validation_langchain/installed_versions.txt`에 기록했다. 설치 파일은 챗봇 폴더에만 추가했으며 팀 루트 requirements는 바꾸지 않았다. 프록시 환경에서는 해당 SDK/HTTP 클라이언트의 프록시 선택 의존성도 팀 환경에서 확인해야 한다.

로컬 브랜치 `feature/KAN-9-langchain-router`에서 기능/연결/검사 단위로 커밋했다. 제공된 ZIP에는 GitHub 원격 저장소 연결 정보가 없어 원격 PR 생성·병합은 하지 않았다. 결과 ZIP에는 .git, .bundle, __pycache__, .env, 설치 패키지를 넣지 않는다.

## 남은 한계와 후속 검사

- 실제 모델별 도구 호출 지원, 실계정 키·모델 ID, 자연어 정확도·요금은 미검증이다. 팀원 블라인드 질문을 LLM 전용 CSV에 추가해 비교해야 한다.
- 모델이 잘못 선택한 정상 도구의 의미까지 완전히 검증할 수는 없다. v2 당시에는 LLM 관리자 호출도 의도 선택 후 즉시 접수되므로, 작업 방향서의 “최대 피해는 잘못 안내”라는 설명은 엄밀히는 부족하다. 부정·문의 문장을 실행 요청으로 오분류하지 않는지 실제 모델 평가가 필요하다.
- timeout은 호출자 대기 제한이다. 중단 불가능한 SDK 호출이 2개 쌓이면 이후 LLM은 busy로 우회한다. 프로세스 메모리·CPU를 완전히 격리하는 구조는 아니다.
- direct는 기존 코드 그대로의 복구 경로이며 새 LangChain 전용 작업 슬롯·실패 카운터의 보호 대상이 아니다.
- 기존 단일 프로세스·메모리 세션/보고서, 재시작 시 소멸, 다중 worker 비공유, 보고서·문의·화면 장기 누적 한계는 유지된다. 이번 결과가 운영 무오류 인증은 아니다.
- 이전 안정화 검토에서 지적한 보고서 외부 조회 중 잠금·결제 캐시 무효화 등 광범위 엔진 변경은 최종 지시서 범위와 섞지 않았다. 별도 안정화 검수/수정 대상으로 남긴다.
- 실제 팀 API·DB·카메라·차단기·Windows 시연 기기는 별도 통합 검사 대상이다. 이번은 첨부 v2의 기존 기능 회귀와 새 라우터 경계 검증이다.

## 발표용 한 줄

“버튼과 규칙으로 먼저 처리하고, 모르는 문장만 LangChain이 도구를 선택하며, 실행·답변·숫자는 기존 시스템 데이터와 확인 절차로 처리합니다.”
