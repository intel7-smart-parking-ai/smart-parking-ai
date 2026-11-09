# 주차장 설정값 넣는 법 (`chatbot/lots.json`, v2.15.4)

> 값이 정해지면 **이 파일만 고치고 서버를 다시 켜면** 반영된다. 코드 수정은 필요 없다.
> 잘못 넣으면 서버가 켜질 때 어느 주차장의 어느 항목이 틀렸는지 알려 주고 멈춘다.
> 기준: D-03·D-04·D-05·D-06·D-14·D-16 및 승인된 D-39 스티커 배정표.

## 1. 값을 넣는 순서

1. `chatbot/lots.json`을 UTF-8로 연다.
2. 아래 표를 보고 값을 바꾼다.
3. `python -m unittest discover -s tests`로 확인한다. 칸 목록·모의 차량을 바꾸면 그 값을 쓰는 테스트 기대값도 함께 고친다.
4. `python server_dev.py`로 다시 켠다.

API 모드(`DATA_SOURCE=api`)에서는 주차장 목록·칸 목록·칸 종류(`space_type`)를 서버 응답이 우선한다. 출구·설비·구역 배치(서버 값이 없을 때)·전기차 목록은 이 파일 값을 쓴다.

## 2. 전체 공통

| 항목 | 현재 값 | 뜻 |
|---|---|---|
| `vehicle_min`, `vehicle_max` | `10`, `99` | 실물 차량 번호 범위(D-06). 두 자리 문자열 `"10"`~`"99"`. 한 자리·0 시작은 받지 않음 |
| `assigned_car_ids` | `null` | 실제 배정 번호 목록. `null`이면 범위 안 전부 허용. 목록을 넣으면 그 밖 번호는 "등록되지 않은 번호" (PM 결정 대기) |
| `ev_vehicles` | `["23","42","57","85"]` | D-39 전기차 미니카 번호. **빨간색 계열 도색 금지(LED 오탐 방지)** |
| `mock_vehicle_types` | D-39의 24대 → `general`/`ev`/`accessible` | 단독 목업의 명시적 차종 메타데이터. 전기차 집합은 ev_vehicles와 같아야 함. API 응답 차종을 덮지 않음 |
| `zones` | `general`/`ev`/`accessible` | 구역 이름(D-05). 별칭: 충전·전기차·EV / 장애인·휠체어 |

## 3. 주차장별 (`lots`)

D-39 변경 대응표는 [목업 이행표](d39_mock_migration.md)를 따른다. 명시적 초기 차량은 19대이며 B의 추가 만차 8칸은 기존 번호 없는 시연용 점유로 채운다. assigned_car_ids=null은 유지한다. D-39 배정표를 파서/API의 10~99 입력 허용 범위 제한으로 사용하지 않는다. 알 수 없는 동적 목업 차량에 차종을 임의 부여하지 않는다.

| 항목 | 예시(A) | 뜻 |
|---|---|---|
| `lot_id` | `"A"` | 바꾸지 않음 |
| `name` | `"개발원주차장"` | 화면·답변 이름. A 개발원 / B 부산역 / C 북항 / D 신세계백화점 |
| `kind` | `"real"`/`"virtual"` | A·B 실물, C·D 가상 |
| `spaces` | `["A-01",…,"B-06"]` | **도면 번호 목록(D-04)**. 칸 수는 이 목록 길이로 자동 계산(`capacity`를 따로 넣지 않음). A·C 12, B 16, D 64 |
| `aliases` | `["개발원", "에이"]` | 이름 별칭 |
| `order_alias` | `"가"` | "가 주차장"처럼 **주차장 앞에서만** 인식하는 순서 이름(가·나·다·라) |
| `loose_aliases` | `["개발원"]` | "개발원 빈자리"처럼 주차 문맥이 있을 때만 인식하는 짧은 이름 |
| `zone_layout` | `{"ev":["A-05","A-06"],"accessible":["B-06"]}` | 구역별 **도면 번호**. 목록에 없는 번호를 넣으면 시작 시 오류 |
| `rate_policy` | `{base_minutes:30, base_fee:1000, unit_minutes:10, extra_fee:500, daily_cap:10000}` | 목업 요금. **billing `db.py` 시드와 같게** 유지(B 2000/700/20000, C 3000/800/40000, D 4000/1000/50000) |
| `exit`, `exits` | `"개발원주차장 동쪽 출구"` | 대피 안내·소방 브리핑 출구 |
| `facility` | `{underground:null,…}` | null=미확인, true=있음, false=없음(높이 제한 false=해당 없음, 숫자=미터) |
| `mock_cars` | `[["11",1,95],…]` | 모의 차량 `[번호, 칸 순번, 주차 분]`. 칸 순번은 `spaces` 목록의 1부터 센 위치 |
| `mock_full` | B만 `true` | 모의 데이터에서 만차로 시작 |

## 4. 칸 번호 설계 (유지보수 참고)

- 챗봇 내부는 칸을 **순번(1..N, `spaces` 목록 위치)**으로 다루고, API 응답 해석·답변·카드·화면에서 도면 번호로 바꾼다(`catalog.space_label` / `catalog.space_index`).
- 이유: 도면 번호의 글자(A/B/C/D)는 주차장 안 구역이라 `lot_id`와 무관하다. 개발원주차장에도 `B-06`이 있다. 그래서 칸은 항상 `(lot_id, space_id)`로 식별한다.
- 서버가 명부에 없는 칸 이름을 주면 그 주차장 목록에 추가하고 구역은 "미지정"으로 둔다.
- 이웃 칸(화재 브리핑의 주변 차량)은 **같은 글자 줄의 번호 ±1**이다. `A-06` 옆은 `A-05`이고 `B-01`이 아니다.

## 5. 아직 정해지지 않은 값

- [ ] 실제 배정 차량 번호(`assigned_car_ids`) — PM 결정 대기
- [ ] 전기차 미니카 번호와 색(`ev_vehicles`)
- [ ] 출구 이름·설비 값(`exit`, `exits`, `facility`)

## 6. 대화 에이전트 예산·모니터링 (v3.1, 환경변수)

`.env.example`의 고정 키 목록은 바꾸지 않았다. 아래 키는 필요할 때만 `.env`나 Compose 환경변수에 넣는다. 넣지 않으면 기본값을 쓴다.

| 키 | 기본 | 뜻 |
|---|---|---|
| `LLM_MAX_PER_SESSION_MIN` | 5 | (기존 키) 대화 에이전트는 **질문(턴) 단위**로 센다. 한 질문 안의 도구 선택·답 작성·고쳐 쓰기 호출은 1번으로 친다 |
| `LLM_MAX_PER_MIN`, `LLM_MAX_PER_DAY` | 30, 1000 | (기존 키) 대화 에이전트는 서버 전체 질문 수로 센다 |
| `LLM_MAX_INFLIGHT` | 4 | 서버 전체 동시 모델 호출 자리 |
| `LLM_SLOT_WAIT_SEC` | 1 | 자리가 없을 때 기다리는 최대 시간(초). 지나면 규칙 답으로 대체 |
| `LLM_TIMEOUT_SEC` | 3 | (기존 키) 도구를 고르는 모델 호출 1회 제한 시간 |
| `LLM_ANSWER_TIMEOUT_SEC` | 4 | 조회 결과를 읽고 답을 쓰는 모델 호출 1회 제한 시간 |
| `LLM_TURN_TOKEN_LIMIT` | 40000 | 한 질문의 입력+출력 토큰 상한(지시문·도구 설명이 호출마다 다시 들어감) |
| `LLM_REPAIR` | 1 | 근거 검사에 걸린 답을 이유와 함께 1번 고쳐 쓰게 함(0이면 바로 대체 답) |
| `MONITOR_BACKEND` | off | `off` / `local`(턴마다 JSON 한 줄) / `langfuse` |
| `MONITOR_LOCAL_PATH` | `.monitoring/turns.jsonl` | local 기록 파일(5MB에서 `.1`로 교체, Git 제외) |
| `MONITOR_CAPTURE_TEXT` | 1 | 질문·답 원문 기록 여부(0이면 글자 수만) |
| `MONITOR_SAMPLE_RATE` | 1 | 기록할 질문 비율(0~1) |
| `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_HOST` | 없음, 없음, `https://cloud.langfuse.com` | Langfuse 프로젝트 키와 주소. `requirements-monitoring.txt` 설치 필요 |

전체 요청 예산(`REQUEST_BUDGET_SEC`, 기본 6초)과 사용자 화면 제한(약 8초)은 그대로다. 실제 모델 응답 시간을 시연 PC에서 잰 뒤 필요하면 `REQUEST_BUDGET_SEC`를 7까지 올리는 것을 검토한다.
