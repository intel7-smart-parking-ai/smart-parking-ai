# 챗봇 코드 지도

| 실행 | 진입점 | 기본 데이터와 시연 |
|---|---|---|
| 단독 HTTP, 8080 | `python server_dev.py` | mock, 시연 켬 |
| 단독 FastAPI | `uvicorn server_fastapi:app --port 8080` | mock, 시연 켬 |
| 팀 FastAPI | `backend.app.chatbot.router.router` 등록 | api, `127.0.0.1:8000`, 시연 끔 |

환경변수 → 챗봇 폴더 `.env` → 기본값 순서로 읽는다. 팀 서버는 워커 1개를 사용한다. 팀 라우터는 전용 패키지 이름을 사용한다. 팀 요청의 동기 작업은 별도 AnyIO 용량으로 실행한다.

HTTP → 세션 예약과 경보 확인 → 칩·승인 업무 또는 일반 대화 → 내부 조회 도구 → 검증된 응답·카드.

일반 대화는 `conversation_turn.py`가 현재 차량, 비교 범위, 최근 대화를 에이전트에 연결한다. 에이전트는 `tools/`에 정의된 조회만 실행한다. `dialog/`는 모델 미사용·실패 상황에서 지원되는 복합 질문을 같은 자료로 답한다. 부작용이 있는 기능은 `workflows/`와 엔진의 확인 상태가 맡는다.

| 바꿀 대상 | 파일과 경계 |
|---|---|
| 팀 등록·라우트 | 상위 `router.py`의 패키지 로드, `chatbot/http_router.py`의 공용 라우트, `server_fastapi.py`의 앱·정적 파일 |
| 환경변수·실행 상한 | `config.py`의 `getenv`, `EMBEDDED_CHAT_WORKERS`(기본 4), `*_LIMIT` |
| 주차장·칸·구역 | `lots.json`의 값, `catalog.py`의 명부와 도면 순번, 별칭 정규식, 공개 스냅샷 |
| 차량 번호 | `parser.normalize_car_id`; 어댑터는 이 규칙에 목업 배정 검증을 더함 |
| 칩·답변 | `chips.json`, `templates.py`; 현재 일부 흐름 전용 문장은 `engine.py` |
| API 경로·응답 | `api_paths.json`, `adapters.ApiAdapter`, `contracts.validate` |
| 위치·입차 확인 중 | `ApiAdapter.find_car`에서 상태 정규화, `engine.h_find_my_car` 및 `vehicle_context`에서 표시 |
| 빈자리·목적지 | `navigation.py`의 조회·카드·목적지 상태 감시 |
| 요금·견적·결제 | `engine.h_pay_fee`와 `_execute_payment`, `contracts.PAYMENT_ERROR_STATUS`, `adapters.build_payment_body` |
| 경보·위반·현장 정보 | `adapters.get_alert`, `engine.current_alert`, `incidents.py`, `briefing.py` |
| 대화 모델 | `agents/conversation.py`의 도구 호출, 턴 예산(질문 단위 상한·동시 자리·답 작성 제한 시간), 근거 검사 후 1회 고쳐 쓰기. `agents/evidence.py`에서 출력의 수치·비교값·요금 짝·범위·실행 주장·약속을 검증. `prompts/conversation.md` 지시문 |
| 내부 자료 | `tools/read.py`의 읽기 도구 10개(빈자리·차량·요금 정책·비교·예상 요금·경보 요약·위반·문의 상태·규칙 안내·작업 제안)와 턴 내 스냅샷. 차이·경과 시간·시각 표현은 코드가 계산해 도구 결과에 넣는다. `knowledge/`의 승인된 정책·설정 안내(주제 단위) |
| 모니터링(선택) | `monitoring.py`: 턴 단위 Langfuse trace 또는 로컬 JSON 줄. 기본 꺼짐, 실패해도 답에 영향 없음. `engine.handle`이 감싸고 `conversation_turn`이 콜백·도구 목록을 넘김 |
| 복합 조회 대체 경로 | `dialog/query.py`의 조회 조건, `dialog/render.py`의 응답과 카드 |
| 업무 처리 | `workflows/payment.py` 서버 견적·결제, `workflows/admin.py` 접수, `workflows/vehicle.py` 후보 조회, `workflows/parking.py` 칩 조회 |
| 세션·확인·재전송 | `session_flow.py`와 `state.py`; `engine._session_request`, `_confirm_pending` |
| 데이터 조회·보관 | `transport.py` 네트워크 제한, `CachedAdapter` 캐시 세대·동시 조회, `retention.py` 완료 기록 정리 |
| 출구 문의 | `handlers/exit_help.py`, `templates.EXIT_HELP` |

| 잠금·예약 | 보호 대상 | 잠금 안의 작업과 중첩 |
|---|---|---|
| 엔진 `_lock` | 세션 목록, 호출, 로그, 런타임 변경 | 예약 후 해제. 변경 시 보고서 잠금. 초기화 시 세션 잠금은 비차단 획득 → 경보 잠금 → 캐시/어댑터 초기화 |
| 세션 `lock` | 한 세션의 입력 순서, 확인 소비, 재전송, 알림 | 같은 세션을 직렬화하며 조회와 LLM 처리 포함. 세션 간 공유 자료 잠금과 별개 |
| 결제 `_payment_condition` | 결제 한 건의 처리 예약 | 상태 플래그만 잠금 아래 변경. 조회·POST·결과 확인 중 조건 잠금 해제, 예약 유지 |
| 경보 `_alert_condition` | 조회 소유권, 최근 경보, 대기자 | 조회와 보고서 갱신 중 조건 잠금 해제. 종료 시 대기자 통지 |
| 캐시 `lock` | 결과, 세대, in-flight 목록 | 캐시 조회와 발행, 명부 반영. 어댑터 조회 및 Future 대기는 잠금 밖 |
| 목업/서버 어댑터 `lock` | 목업 차량·칸, 서버 경보 버전 | 목업에서 명부 접근. 서버 HTTP 뒤 상태만 반영 |
| 명부 `lock` | 설정·별칭·칸·공개 명부 스냅샷 | 새 뷰를 계산해 교체. HTTP 없음 |
| 보고서 `incidents._lock` | 버전, 수정본, 완료 기록 | 차량을 먼저 조회하고 버전 재확인 후 반영. 관리자 변경은 엔진 `_lock` → 보고서 잠금 |
| 알림/LLM 잠금 | 세션 큐, 모델 준비, 집계 | 메모리만 갱신. LLM SDK 호출 중 모델·통계 잠금 해제 |

| 현재 상태 | 입력 | 다음 상태 |
|---|---|---|
| 대기 | 차량이 필요한 기능 | 차량 요청 또는 차량 확인 |
| 차량 조회 대기 | 명확한 차량 번호 | 위치·요금 조회를 이어서 실행 |
| 차량 변경·결제 대상 확인 | 새 차량 번호 | 최신 번호로 차량 확인 대기 교체 |
| 차량 확인 | 승인 / 거절 | 차량 저장 후 기능 실행 / 차량 요청 |
| 작업 확인 | 유효한 승인 / 거절 | 승인 소비 후 작업 실행 / 대기 |
| 작업 확인 | 견적 만료·변경 | 새 견적으로 결제 확인. 사용자 승인 전 POST 없음 |
| 확인 대기 | 취소·다른 기능·만료 | 이전 확인 종료. 다른 기능이 필요로 하면 새 확인 생성 |
| 확인 대기 | Lv2 | 결제 차단, 대피 안내 우선 |
| 작업 처리 중 | 동일 요청 키 재전송 | 저장된 응답 재사용. 내용이 다른 요청 키 재사용은 충돌 |

외부 계약은 `chat_response_fields.md`와 `tests/test_public_contract.py`를 본다. 라우터 prefix, 경로·메서드, 키·타입, 칩 ID, 화면 파일, 설정값, 결제 body가 고정 접점이다. `tests/safety_check.py`는 Lv2 안내, 모의 표기, 차량과 금액 추측 금지 등 응답의 안전 조건을 확인한다.

배정 번호, 경보 API 형식, 칸 배정 전 위치 응답 형식은 팀 확인 대상으로 남아 있다. 4자리 가상 차량은 준비 중 상태다. 완료 처리 경로가 없는 관리자 문의는 보존 한도를 넘을 수 있다. 확정 전 보고서도 같은 원칙을 적용한다.
