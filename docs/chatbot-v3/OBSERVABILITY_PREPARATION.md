# Langfuse 연결 코드 준비 · 2026-11-07

현재 단계는 **코드 준비와 모의 HTTP 검증**이다. Langfuse 실제 서버나 LLM을 호출하지 않았다.
API 키를 환경에 넣는 것만으로 기록을 수집하거나 전송하지 않는다.
기본 CLI/preview 서비스는 observer=None으로 기록 기능이 꺼져 있다.

## 구성

- `TraceRecorder`: ConversationService에 명시적으로 주입하는 제한된 메모리 기록기.
- `ConversationService(..., observer=recorder)`: coordinator, tools.<intent>, reporter,
  전체 turn의 시작·종료 시각, 상태와 프롬프트 SHA256 기록. 캐시 재응답은 새 trace를 만들지 않음.
- `LangfuseSettings`: LANGFUSE_BASE_URL 또는 LANGFUSE_HOST, PUBLIC_KEY, SECRET_KEY 확인.
  두 URL이 서로 다르면 실패하며 키는 repr에 표시하지 않는다. 원격 URL은 HTTPS만 허용한다.
- `LangfuseExporter.export(events)`: Langfuse public ingestion endpoint
  `/api/public/ingestion`으로 trace-create/span-create/generation-create 이벤트를 명시적으로 전송.
  기존 httpx만 사용하므로 SDK 의존성을 추가하지 않는다.

## 기록 범위

포함: 임의 trace ID, 고정 단계명, 상태, 시작/종료 시각, 프롬프트 내용의 해시,
공급자가 반환한 유효한 토큰 수/비용. Reporter 표현 정리 실패 시 전체 turn은 fallback으로 표시.

제외: 질문·답변 원문, 차량번호, 사용자/대화/요청 ID, API URL 및 파라미터,
원시 서버 오류, API 키. 프롬프트 본문을 Langfuse에 업로드하지 않는다.
도구 단계는 현재 역할별 집계이며 개별 HTTP 요청별 span은 아니다.
이 최소 기록만으로 모든 오답 원인을 재구성할 수는 없다. 실제 평가 문항 ID와 결과 파일을
별도로 관리하고 향후 필요한 수집 범위를 결정한다.

메모리 기본 최대1000 이벤트, 초과하면 오래된 이벤트를 버리고 dropped 카운트를 올린다.
기록기 오류가 답변을 실패시키거나 업무 요청을 다시 실행하지 않도록 격리했다.
기록기는 현재 대화별 서비스의 직렬 실행을 전제로 한다. 여러 대화의 공용 수집기는 아직 구성하지 않았다.

## 다음 단계에서 연결하는 예시 (이번 작업에서는 실행하지 않음)

```python
from backend.app.chatbot_v3.observability import TraceRecorder, LangfuseSettings, LangfuseExporter
recorder = TraceRecorder()
# 기존 서비스 생성 시 observer=recorder를 전달한다.
# 대화를 수행한 뒤, 연결 설정·네트워크와 수집 범위를 확인한 경우에만:
# with httpx.Client() as client:
#     exporter = LangfuseExporter(client, LangfuseSettings.load())
#     result = exporter.export(recorder.snapshot()[:100])
```

한 번에100개 이하를 내보내며 timeout3초, 리다이렉트·자동 재시도 없음.
207 부분 성공을 전체 성공으로 오인하지 않고 성공 ID를 확인한다.
응답에는 공급자 오류 본문을 포함하지 않는다. 내보내기는 채팅 응답 경로에 연결하지 않았으므로
기존18초 예산에 별도 네트워크 대기를 추가하지 않는다.
실제 서버 버전의 ingestion 수용 여부, dashboard 표시, 네트워크 허용·인증은 키 준비 후 확인할 항목이다.
내보내기를 반복 호출하면 같은 관측이 중복 기록될 수 있으므로 향후 export 체크포인트/배치 큐 구현이 필요하다.
현재 단계에서 백그라운드 전송이나 자동 flush를 활성화하지 않았다.
