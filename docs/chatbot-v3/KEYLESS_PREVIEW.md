# 키 없는 시험 서버·화면 연결 · 2026-11-07

기존 main/runtime의 챗봇 라우터를 교체하지 않고 별도 loopback 서버와 시험 화면을 추가했다.
새 서버는 `preview:create_preview_app`이고 기존 `/api/v1/chat` 필드의 텍스트 응답 부분을
사용한다. 운영 UI 전체 통합·배포가 완료된 것은 아니다.

## 실행 (터미널 3개)

저장소 루트, Python 의존성 설치 후:

```bash
# 1. 기존 조회 서버. 키 없는 고정 mock. 실제 runtime을 이미 실행 중이면 그 주소를 사용한다.
PARKING_MODE=mock python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8014

# 2. 새 챗봇 시험 API. fixture는 OpenRouter를 호출하지 않는다.
CHATBOT_V3_MODEL_MODE=fixture BACKEND_BASE_URL=http://127.0.0.1:8014 \
  python -m uvicorn backend.app.chatbot_v3.preview:create_preview_app --factory --host 127.0.0.1 --port 8013

# 3. 프런트엔드
cd frontend
npm ci
npm run dev -- --host 127.0.0.1
```

브라우저: `http://127.0.0.1:5173/chat-v3.html`.
Windows PowerShell에서는 앞의 환경 변수를 `$env:PARKING_MODE='mock'`,
`$env:CHATBOT_V3_MODEL_MODE='fixture'`, `$env:BACKEND_BASE_URL='http://127.0.0.1:8014'`처럼
각 터미널에서 설정한 뒤 `python -m uvicorn ...` 명령을 실행한다.

## 모의 모델과 실제 조회

fixture는 버튼에 표시된 예시 문장만 정해진 의도로 바꾸는 연결 시험용 모델이다.
자유로운 질문을 이해하는 챗봇으로 평가하지 않는다. 예시 밖 문장은 확인 안내만 한다.
주차 데이터는 fixture가 만들지 않고 기존 ParkingTools로 지정한 백엔드에서 조회한다.
기존 main mock은 고정 시각/차량·빈자리이며 세션 생성이 없어 요금 조회와 최신 추천이 제한된다.
그 경우 정상적인 미확인 안내가 나와야 한다. 최신 관측과 견적 시험은 demo/real runtime을 연결한다.
시설은 로컬 프로젝트 가상 설정이다. 실제 장소 정보로 확인된 자료가 아니다.

키를 준비한 다음에만 `CHATBOT_V3_MODEL_MODE=openrouter`와 `OPENROUTER_MODEL`,
`OPENROUTER_API_KEY`를 설정한다. 명시 환경 파일은 `CHATBOT_V3_ENV_FILE`로 지정할 수 있다.
기본 fixture에서 키가 존재하더라도 유료 API를 자동 호출하지 않는다.

## 별도 시험 API 계약

- GET `/api/v1/chat/config`: runtime_id, 서명된 session_id, model_mode, 예시 질문,
  supported_languages=['ko'], 고정 세션 유효기간(1800초).
- POST `/api/v1/chat`: session_id/runtime_id/request_id/text(1~200자)/language='ko'.
- 응답: text와 동일한 messages[0].text, chips=[], card=null, flow='idle',
  confirmation_token/vehicle_context/banner/safety=null, runtime_id, language, meta.
  임의의 v3 card 구조를 기존 카드 컴포넌트에 전달하지 않고 검증된 본문을 표시한다.
- 오류: 팀의 `{error:{code,message,details:[]}}` 형태. 입력400, 충돌409, 포화503, 대기초과504.
- `/updates`, 칩·확인 토큰·결제·다국어는 이 시험 서버에서 지원하지 않는다.
  기존 `/chat` 운영 API와 이 시험 서버를 같은 포트에 중복 등록하지 않는다.
- Vite 개발 프록시만 `/chat-v3-api` → `127.0.0.1:8013/api`를 연결한다.
  production nginx는 변경하지 않았다. 빌드된 페이지가 있다는 이유로 운영 연결됐다고 가정하지 않는다.

## 제한·중복 방지

HTTP 응답 대기는 18초, 브라우저는 20초. 모델·API 호출 직전에 남은 예산으로 timeout을 줄이고,
느린 모델이 뒤늦게 끝나도 예산이 만료됐으면 새 차량/견적 조회를 시작하지 않는다.
이미 시작한 외부 HTTP 작업이나 Python thread를 강제로 취소하지 않는다.
따라서 응답이 504여도 작업은 잠시 계속될 수 있으며, 완료까지 슬롯과 같은 request_id의
결과/실패를 유지한다. 화면의 재확인은 동일 요청 ID/본문을 보낸다. 자동 재시도는 없다.

독립 대화는 최대 2개 병렬, 같은 대화는 한 번에 하나. 대화별 서비스·도구·모델 인스턴스를
분리하여 기존 ConversationService의 전역 lock/usage 공유 영향을 피한다.
대화 최대100, 대화별 요청1000. 전체 서버가 1000요청 후 영구 정지하는 대신 만료 시 대화와
캐시를 함께 회수한다. 한도에 도달한 대화는 새 대화가 필요하다.
서명된 고정 TTL 세션으로 만료 ID를 다시 보내도 캐시 없는 재실행을 허용하지 않는다.
서버 재시작 시 runtime_id가 바뀌며 이전 세션은 거절한다. 여러 워커/영속 캐시는 아직 없다.
새 세션·새 request_id는 별개 요청이므로 사용자가 새 요청으로 요금을 다시 묻는 경우까지
중복으로 막지 않는다. 로컬 CLI 단독 실행은 이 gateway의 병렬/TTL 관리를 사용하지 않는다.

인증 없는 개발 서버이므로 loopback 단일 worker로 사용한다. 세션 서명은 사용자 인증이 아니다.
외부 배포에는 사용자 권한·저장소·분산 처리·추가 보안 검토가 필요하다.

## 검증 결과

- Python 전체: **394 passed, 7 warnings / 121.37초**.
- 프런트엔드: `npm test` **9개 test file 통과**, `npm run build` 성공.
- 새 gateway 시험: API 규격/200자/한국어 제한, 같은 요청 재전송, timeout 후 worker 유지,
  다른 대화 병렬, 세션 만료·회수, 늦은 LLM 후 fee 미실행, 실제 도구 인터페이스 사용.
- Chromium 모바일390px: 빈자리 질문→백엔드 mock 조회, 시설 질문→영화관 안내,
  자바스크립트 오류0, 수평 넘침 없음. screenshot은 Git 밖 다운로드 폴더에 보관.
- 유료 모델·실제 LLM 의미 이해는 검증하지 않았고 API 비용을 사용하지 않았다.
- 이전 검토의 시뮬레이터 clear 간헐 실패는 이번 전체 실행에서 재현되지 않았으나 원인 해결을 의미하지 않는다.
