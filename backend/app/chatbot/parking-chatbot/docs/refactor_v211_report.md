# EPIC06 v2.11 시스템 레벨 정리

2026-11-04 KST. 첨부 `chatbot-v2.10-full.zip` 기준. 변경 범위는 `backend/app/chatbot/`이다.

## 기준선과 최종 측정

| 지표 | 첨부 v2.10 | v2.11 |
|---|---:|---:|
| 실행 Python 줄 수 | 4,792 | 5,155 |
| 50줄 초과 함수 | 14 | 13 |
| 200자 초과 줄 | 5 | 4 |
| pyflakes 경고 | 1 | 0 |
| 모듈 최상위 import 순환 | 0 | 0 |
| 1,000문장 p50 / p95 / 최대 (ms) | 0.614 / 1.498 / 5.460 | 0.215 / 0.708 / 2.481 |
| 104칸 빈자리 질문 p50 / p95 / 최대 (ms) | 1.457 / 2.069 / 8.162 | 0.519 / 0.828 / 15.892 |
| 1,000문장 후 Python 추적 메모리 현재 / 최대 (MiB) | 3.216 / 3.263 | 3.221 / 3.268 |
| 프로파일 총 시간 | 3.354초 | 0.705초 |
| deepcopy 누적 / 비중 | 2.399초 / 71.5% | 0.031초 / 4.4% |

고정 시각, 로컬 Mock, LLM 끔, 동일 질문과 세션 순서를 사용했다. 지연은 각각 3회 측정한 통계의 중앙값이다. 빈자리 p50·p95는 줄었고 최대치는 늘었다. 메모리는 tracemalloc의 Python 할당만 집계했다. 동시 20세션 × 50문장 실행에서 전후 예외는 0건이었다. 팀 장비 및 실제 네트워크 성능은 별도다.

<details><summary>파일별 줄 수와 긴 함수·긴 줄, 프로파일 상위 15</summary>

| 파일 | 이전 줄 / 함수 / 긴 줄 | 최종 줄 / 함수 / 긴 줄 |
|---|---:|---:|
| `backend/app/chatbot/router.py` | 28 / 0 / 0 | 35 / 0 / 0 |
| `chatbot/__init__.py` | 0 / 0 / 0 | 0 / 0 / 0 |
| `chatbot/access.py` | 25 / 0 / 0 | 25 / 0 / 0 |
| `chatbot/adapters.py` | 913 / 2 / 1 | 924 / 2 / 1 |
| `chatbot/briefing.py` | 51 / 0 / 0 | 51 / 0 / 0 |
| `chatbot/catalog.py` | 282 / 1 / 0 | 351 / 1 / 0 |
| `chatbot/config.py` | 92 / 0 / 0 | 152 / 0 / 0 |
| `chatbot/contracts.py` | 164 / 1 / 0 | 192 / 1 / 0 |
| `chatbot/diagnostics.py` | 0 / 0 / 0 | 25 / 0 / 0 |
| `chatbot/engine.py` | 1193 / 6 / 3 | 1233 / 5 / 2 |
| `chatbot/handlers/__init__.py` | 0 / 0 / 0 | 1 / 0 / 0 |
| `chatbot/handlers/exit_help.py` | 0 / 0 / 0 | 7 / 0 / 0 |
| `chatbot/http_api.py` | 33 / 0 / 0 | 33 / 0 / 0 |
| `chatbot/http_router.py` | 0 / 0 / 0 | 256 / 0 / 0 |
| `chatbot/incidents.py` | 298 / 1 / 0 | 328 / 1 / 0 |
| `chatbot/llm.py` | 85 / 0 / 0 | 86 / 0 / 0 |
| `chatbot/llm_router.py` | 222 / 1 / 0 | 223 / 1 / 0 |
| `chatbot/navigation.py` | 186 / 0 / 1 | 185 / 0 / 1 |
| `chatbot/notifications.py` | 66 / 0 / 0 | 66 / 0 / 0 |
| `chatbot/parser.py` | 338 / 1 / 0 | 324 / 1 / 0 |
| `chatbot/retention.py` | 0 / 0 / 0 | 18 / 0 / 0 |
| `chatbot/session_flow.py` | 0 / 0 / 0 | 22 / 0 / 0 |
| `chatbot/snapshots.py` | 0 / 0 / 0 | 33 / 0 / 0 |
| `chatbot/state.py` | 69 / 0 / 0 | 51 / 0 / 0 |
| `chatbot/templates.py` | 266 / 0 / 0 | 269 / 0 / 0 |
| `chatbot/transport.py` | 90 / 1 / 0 | 90 / 1 / 0 |
| `server_dev.py` | 162 / 0 / 0 | 164 / 0 / 0 |
| `server_fastapi.py` | 229 / 0 / 0 | 11 / 0 / 0 |

| 순위 | 기준 누적 | 최종 누적 |
|---:|---|---|
| 1 | `measure.py:measure` 3.363초 | `measure.py:measure` 0.708초 |
| 2 | `measure.py:ask` 3.355초 | `measure.py:ask` 0.703초 |
| 3 | `engine.py:wrapped` 3.353초 | `engine.py:wrapped` 0.702초 |
| 4 | `copy.py:deepcopy` 2.399초 | `engine.py:handle` 0.471초 |
| 5 | `engine.py:handle` 2.218초 | `engine.py:_handle_message` 0.466초 |
| 6 | `engine.py:_handle_message` 2.211초 | `engine.py:_dispatch` 0.360초 |
| 7 | `copy.py:_deepcopy_dict` 2.175초 | `engine.py:_run` 0.330초 |
| 8 | `parser.py:extract_lot` 1.586초 | `adapters.py:_call` 0.305초 |
| 9 | `catalog.py:lots` 1.540초 | `adapters.py:_fetch` 0.297초 |
| 10 | `engine.py:_dispatch` 1.270초 | `adapters.py:_read` 0.284초 |
| 11 | `copy.py:_deepcopy_list` 1.222초 | `engine.py:_occupancy` 0.284초 |
| 12 | `engine.py:_run` 1.124초 | `adapters.py:get_empty_spots` 0.283초 |
| 13 | `parser.py:parse` 0.931초 | `engine.py:h_get_empty_spots` 0.281초 |
| 14 | `parser.py:parse_rules` 0.929초 | `adapters.py:get_empty_spots` 0.183초 |
| 15 | `engine.py:h_get_empty_spots` 0.896초 | `catalog.py:zone_spots` 0.100초 |

</details>

## 팀 서버 안전과 고정 접점

| 항목 | 변경·관측 근거 |
|---|---|
| S1 모듈 충돌 | 팀 등록은 전용 패키지 이름을 사용한다. `sys.path`, 전역 환경변수, 단독 서버 모듈 import에 영향이 없음을 별도 프로세스에서 확인했다. 기존 최상위 `chatbot`이 다른 패키지면 위치가 명시된 오류로 중단한다. 공개 main의 챗봇 폴더 밖 Python import에서 `chatbot`, `server_fastapi`, `server_dev`, `tests` 최상위 import는 0건이었다. |
| S2 import 부작용 | `http_router.py`와 단독 앱을 분리했다. LLM 모델 준비는 첫 분류 시 실행된다. 라우터 import 전후 새 스레드 0개. `FastAPI()` 생성 차단 상태에서 import 성공. |
| S3 동일 프로세스 API | 팀 앱의 기본 AnyIO 용량과 챗봇 실행 용량을 분리했다. 챗봇 기본 상한 4. 공용 용량 4, 동시 대화 16건, 동일 ASGI 앱 안 동기 API 의존 재현에서 내부 요청 시간 초과 16 → 0건(약 4.35초 → 0.36초, 전체 검사 실행 시간). 로컬 ASGI 재현이며 실제 팀 서버의 부하 시험은 남아 있다. |
| S4 다중 워커 | `WEB_CONCURRENCY>1` 신호가 있으면 시작 경고를 남긴다. 워커 1개 운영을 README에 명시했다. 명령줄 `--workers`를 런타임에서 추측하지 않는다. |
| S5 로그 | 팀 실행에서 `parking_chatbot.*`을 사용한다. 정상 명부 갱신은 INFO, API 반복 실패는 첫 경고 후 60초 간격으로 누적 억제 수를 표시한다. 키·요청 본문·서버 오류 본문을 기록하지 않는다. 단독 어댑터의 알 수 없는 경보 종류 경고 한 곳은 기존 로그 구독 계약을 유지한다. |
| S6 설정 | `config.py`가 환경변수, 로컬 `.env`, 기본값 순으로 해석한다. 시작 로그에 값과 출처를 남기며 URL의 인증·경로·쿼리를 제외한다. |

팀 공개 `main`은 읽기 전용으로 확인했다. 지정 feature 브랜치 아카이브는 HTTP 404였다. 공개 main에는 등록용 `router.py`와 main의 챗봇 등록 줄이 없고, 첨부 ZIP과 비교해 실행 파일 7개도 달랐다. main의 `ChatPanel.jsx`는 `result.messages`를 읽으며 첨부 ZIP 계약은 최상위 `text`를 반환한다. nginx의 공개 경로는 `POST /api/v1/chat`으로 제한돼 있다. 병합 전 담당자가 main 라우터 등록과 ChatPanel 응답 계약을 맞춰야 한다.

라우터 prefix와 경로·메서드 20개, 대표 응답 12개의 키·타입, Flow 값, 칩 ID, 차량 상태 6개와 카드 type 7개를 비교했다. 설정 JSON 3개, `.env.example`, 정적 화면, 기존 테스트 파일은 바이트 단위로 유지했다. `stats`에만 보존 집계를 추가했다.

## 복사, 설정, 잠금과 구조

| 항목 | 적용 및 남은 범위 |
|---|---|
| 명부·칩 | 동결된 중첩 JSON 값을 공유한다. `configure`·`adopt`·`register_space`·`adopt_spaces`에서 새 뷰와 순번표를 발행한다. 파서의 별칭·구역 패턴은 발행 시 컴파일한다. `public()`·`chips()`는 직접 호출자가 수정할 수 있게 분리본을 유지하고, 엔진은 `public_snapshot()`·`chip_snapshot()`을 사용한다. |
| 캐시 | 저장과 동시 조회 결과는 동결한다. 빈자리 카드의 화면 전용 속성만 주차장별 얕은 복사에 쓴다. 오래된 응답의 표시 속성도 새 바깥 객체에 붙인다. `get_empty_spots`·`find_car`·`get_fee`의 직접 Python 호출은 기존처럼 분리본을 받는다. |
| 중앙 설정 | 실행 코드의 환경변수 읽기는 `config.py` 두 곳이다. 숫자 파싱 오류에 키 이름이 붙는다. 차량 번호는 `parser.normalize_car_id`, 칸은 `catalog.normalize_space`와 순번표, 위치 상태는 `ApiAdapter.find_car`, 결제 오류와 body는 기존 계약 표와 `build_payment_body`를 사용한다. |
| 보존 한도 | 관리자 호출 500건, 보고서 100건은 정리 목표다. 오래된 완료·확정 항목부터 지운다. 현 v2.10의 관리자 문의에는 완료 처리 API가 없고, 확정되지 않은 보고서도 보호하므로 이 둘은 보호 항목만 쌓이면 목표를 넘을 수 있다. 불확실한 결제 기록 1,000건에 도달하면 기록을 보존하고 새 결제 POST를 차단한다. `stats.retention`에 정리 수와 불확실 기록 수를 제공한다. |
| I/O와 잠금 | 보고서 조회의 차량 읽기는 보고서 잠금 밖으로 옮겼다. 반영 전 경보 버전을 재검사한다. 결제는 조건 잠금 밖에서 I/O를 하되 한 건의 직렬 처리 예약을 유지한다. 세션별 잠금은 중복 작업·확인 소비를 보호하기 위해 세션의 HTTP/LLM 작업을 포함한다. |
| 예외 | 직접 LLM에서 예상 I/O·형식 예외만 처리한다. LangChain SDK 경계에는 넓은 포착을 유지하며 오류 종류와 준비/호출 위치만 기록하고 규칙 응답으로 전환한다. |
| 상태와 시간 | 확인 슬롯은 세션당 하나다. `_confirm_pending`으로 토큰 소비 경계를 분리했다. 입력 시각 파싱은 견적·후보 경로에서 시간대 검사 함수에 합쳤다. 표기 문구를 보존하기 위해 기존 표시 함수의 일괄 KST 변환은 하지 않았다. |
| 코드 분리 | `server_fastapi.py` → `http_router.py`(라우트), `state.py` → `session_flow.py`(Flow/Pending, 기존 이름 재노출), `engine.py` → `handlers/exit_help.py`(출구 문의). `contracts.validate`의 빈자리·경보 검증, 엔진의 확인·입력 검증·미인식 후보 처리를 내부 함수로 분리했다. 빈자리와 위치 반환 형태는 TypedDict로 명시했다. |
| 제거 | pyflakes가 찾은 어댑터의 미사용 `re` 및 참조가 없는 HTTP 내부 `_ok`을 제거했다. `free_minutes`는 목업 요금 계약·화면에서 사용하고 `call_status`는 기준 ZIP에서 실제 라우팅하므로 보존했다. |

남은 정리 범위는 전체 처리기의 도메인별 이동, 긴 함수 13개와 200자 초과 줄 4개, 전체 공개 함수 docstring 보강, 전 상태 리터럴·응답 문구·시각 표시의 일괄 통합이다. 기존 환경변수 요금 기본값과 응답 문구는 호환성 때문에 유지했다. 이 결과는 해당 항목까지 완료한 구조 정리본으로 판정하지 않는다.

## 검증 결과

| 검사 | 결과 |
|---|---|
| 기준 전체 테스트, FastAPI 설치 및 LangChain 미설치 | 435개 중 430 통과, 5 skip |
| LangChain 설치, FastAPI 설치 | 첫 통합 442개 중 433 통과, 3 skip, 6 실패. 호환성 수정 후 실패 6개를 포함한 선별 11개 전부 통과 |
| LangChain 미설치, FastAPI 설치 | 첫 통합 442개 중 431 통과, 5 skip, 6 실패. 선별 11개 전부 통과 |
| 수정 후 순서 섞은 전체 테스트, LangChain 미설치 | 442개 중 437 통과, 5 skip |
| RUN_SLOW=1 | robust 30개 통과 |
| 고정 접점 / 팀 import·동시성 | 각각 3개 통과 |
| 브라우저 | Chromium 12개 검사 스크립트 통과. 기본 Chromium은 환경의 socket 제한으로 시작하지 못했고 headless shell로 실행했다. |
| 응답 스냅샷 | text·card·chips·flow 242개 비교, 차이 0. 시간·토큰 등 비결정적 필드 제외 |
| api_smoke | 로컬 가짜 HTTP 서버. A 빈자리 일치, 11번 위치·요금 200, 98번 위치 404, 7번 HTTP 0건, POST 0건 |

| 질문 파일 | v2.10 일치 / 총 | v2.11 일치 / 총 |
|---|---:|---:|
| `questions.csv` | 36/36 | 36/36 |
| `questions_extended.csv` | 50/50 | 50/50 |
| `questions_holdout.csv` | 20/20 | 20/20 |
| `questions_navigation_dev.csv` | 20/20 | 20/20 |
| `questions_navigation_holdout.csv` | 12/12 | 12/12 |
| `questions_realistic.csv` | 69/69 | 69/69 |
| `questions_rehearsal.csv` | 5/5 | 5/5 |
| `questions_stability.csv` | 30/30 | 30/30 |
| `llm_eval_questions.csv` | 5/35 | 5/35 |

LLM 평가용 35문장도 두 버전 모두 LLM을 끄고 분류 결과를 비교했다. 실제 모델 응답, 팀 서버 실배치, Windows 실기기, LED 연동은 검증 범위 밖이다. 환경에 따른 기존 skip은 유지했다. 순서 섞은 전체 검사 뒤에는 팀 import 설정만 추가로 점검했다.

사람이 PR을 올릴 때는 `feature/KAN-9-chatbot-stabilize`에서 `git add backend/app/chatbot`으로 범위를 제한한다. PR에 공개 main과 첨부 ZIP의 차이, 고정 접점 검사, 팀 화면 연결 확인을 적는다.
