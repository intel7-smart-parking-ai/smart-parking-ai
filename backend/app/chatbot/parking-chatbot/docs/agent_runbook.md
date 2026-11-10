# Docker에서 OpenRouter 대화 에이전트 실행

저장소 루트에서 실행한다. 팀 시연용 `api` 컨테이너 안에서 챗봇이 내부 API를 조회하고, 일반 문장은 OpenRouter 모델로 처리한다. 기존 `web`과 `admin-auth`가 같은 API를 사용한다. 로컬 모델 설치나 GPU가 필요하지 않다.

## 시연 PC 준비

1. 팀의 `docs/RUNBOOK_DEMO_HOST.md`에 따라 기존 관리자 인증 파일과 비공개 `.env`의 `VISION_TOKEN`을 준비한다. Docker Compose가 읽는 **저장소 루트 `.env`**에 시연용 `OPENROUTER_API_KEY` 값을 직접 입력한다. 파일과 키는 Git·로그·발표 화면에 올리지 않는다.
2. 루트 `.env`의 `LLM_MODEL`은 팀에서 선정한 도구 호출 지원 모델을 사용한다. 팀 Compose 기본값은 `google/gemma-4-26b-a4b-it`이다. OpenRouter 계정의 사용 한도와 해당 모델 접근 가능 여부는 시연 PC에서 확인한다.
3. 아래 명령을 순서대로 실행한다. 첫 명령은 팀 Dockerfile에서 현재 체크아웃의 기반 이미지를 만든다. 마지막 명령은 챗봇 패키지를 그 이미지에 추가한다. 코드·팀 의존성이 달라진 체크아웃으로 옮겼다면 첫 명령부터 다시 실행한다.

```powershell
docker build --target runtime -t smart-parking-api-base:chatbot -f backend/Dockerfile .
docker compose -f compose.yaml -f backend/app/chatbot/parking-chatbot/compose.agent.yaml config --quiet
docker compose -f compose.yaml -f backend/app/chatbot/parking-chatbot/compose.agent.yaml up -d --build
```

기본 Docker Desktop 빌더처럼 로컬 이미지를 공유하는 빌더를 사용한다. 선택한 빌더가 별도 컨테이너라면 첫 명령에서 만든 기반 이미지 태그를 찾지 못할 수 있다. Docker Desktop에서 기본 빌더를 선택하고 다시 빌드한다.

팀 공통 이미지의 설치 목록은 `backend/requirements-runtime.lock`이다. 이번 추가 이미지도 이를 제약으로 적용해 챗봇 `requirements-llm.txt`를 설치하고 `pip check`와 LangChain import를 수행한다. 공통 이미지로 편입할 때는 통합 담당자와 공유한다.

## 기동 점검

```powershell
docker compose -f compose.yaml -f backend/app/chatbot/parking-chatbot/compose.agent.yaml ps
docker compose -f compose.yaml -f backend/app/chatbot/parking-chatbot/compose.agent.yaml exec -T api python -c "import os; import importlib.metadata as m; print('provider',os.getenv('LLM_PROVIDER'),'model',os.getenv('LLM_MODEL'),'key_present',bool(os.getenv('OPENROUTER_API_KEY')),'langchain',m.version('langchain'))"
```

`api`와 `web`이 healthy이고 `provider openrouter`, `key_present True`가 표시되는지 확인한다. 이 점검은 키의 존재와 설치 상태만 확인한다. 실제 모델 호출은 별도로 검증한다.

한국어 화면에서 **직접 입력**으로 “공항이랑 신세계 중 충전 자리 있고 기본요금 싼 곳 알려줘”를 입력한 뒤 “그럼 일반 자리는?”으로 이어간다. 브라우저 개발자 도구에서 해당 `POST /api/v1/chat` 응답의 `meta`를 열어 모델 사용 여부를 확인한다. 칩 동작이나 자연스러운 문구만으로 모델 사용을 판단하지 않는다. 이어서 명확한 차 번호의 위치·요금 복합 조회, 모의 결제 승인 전 상태를 점검한다. 시연 데이터는 현재 서버 값과 대조한다.

외국어 UI는 챗봇 결과를 작성한 뒤 팀의 `response_locale.py`가 번역하므로, 비한국어 응답은 별도의 모델 호출과 지연이 추가될 수 있다. 한국어 화면의 대화 시험과 구분한다.

## 팀 기본 이미지로 복귀

```powershell
docker compose -f compose.yaml up -d --build --force-recreate api
```

같은 프로젝트와 `parking_data` 볼륨을 사용한다. 이 전환에는 DB 볼륨 삭제가 필요하지 않다. 팀 기본 모드로 복귀할 때는 `.env`의 `LLM_PROVIDER`를 팀 설정값으로 맞춘다.

## (선택) Langfuse 모니터링 켜기

모니터링은 답을 바꾸지 않는다. 질문마다 trace 1개가 생기고, 그 아래에 모델 호출·도구 호출 구간(LangChain 콜백), 출처(`llm`/`rule`), 에이전트 상태, 근거 검사 사유, 고쳐 쓰기 여부, 지연이 남는다. 점수 `model_answer_used`(모델 답이 화면에 나갔으면 1)와 `repaired`가 함께 기록된다. 세션 ID는 해시로만 남기고 키는 기록하지 않는다.

1. Langfuse 프로젝트에서 Public/Secret 키를 만든다(클라우드 또는 팀 자체 서버).
2. 저장소 루트 `.env`에 비공개로 넣는다.

```
MONITOR_BACKEND=langfuse
LANGFUSE_PUBLIC_KEY=pk-lf-...
LANGFUSE_SECRET_KEY=sk-lf-...
LANGFUSE_HOST=https://cloud.langfuse.com
INSTALL_MONITORING=1
```

3. 위 Compose 명령으로 다시 빌드·기동한다(`INSTALL_MONITORING=1`이면 `requirements-monitoring.txt`를 추가 설치).
4. 관리자 통계(`GET /api/v1/chat/stats`, 관리자 토큰 필요)의 `monitoring`에서 `active: langfuse`, `langfuse_sdk: v3`를 확인한다. 키가 없거나 패키지가 없으면 `disabled_reason`에 이유가 나오고 챗봇은 그대로 동작한다. 전송 실패가 20번 쌓이면 스스로 꺼진다.

Langfuse 없이 같은 정보를 보려면 `MONITOR_BACKEND=local`로 두고 `.monitoring/turns.jsonl`을 연다. 원문 기록이 부담되면 `MONITOR_CAPTURE_TEXT=0`.
