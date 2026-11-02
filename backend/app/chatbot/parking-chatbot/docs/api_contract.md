# 팀 API 연결 계약

경로는 `chatbot/api_paths.json`에서 수정합니다. 응답 필드가 아래와 다르면 `ApiAdapter`에서 변환하고,
엔진에는 이 내부 형식으로 전달합니다. 시각 예시의 값은 고정 데이터가 아니라 실제 최신 시각이어야 합니다.
`.env`에 DATA_SOURCE=api, BACKEND_BASE_URL, 필요 시 BACKEND_TOKEN을 설정합니다.

## 조회 응답

| API | 내부 응답 형식 |
|---|---|
| occupancy | `{"lots":{"A":{"total":8,"empty":1}},"updated_at":"시간대 포함 ISO 시각"}` |
| location | `{"vehicle_no":"07","lot_id":"A","spot_no":7,"capacity":8,"entered_at":"ISO 시각","updated_at":"ISO 시각"}` |
| fee | `{"vehicle_no":"07","entered_at":"ISO 시각","minutes":58,"fee":1500,"fee_after_30":3000,"free_minutes":30,"paid":false,"updated_at":"ISO 시각"}` |
| alerts | `{"level":2,"version":10,"lot_id":"A","spot_no":6,"confidence":0.91,"started_at":"ISO 시각"}` |
| parked | `[{"vehicle_no":"07","lot_id":"A","spot_no":7}]` |

차량 위치/요금의 기록 없음은 JSON null 또는 HTTP 404입니다. 경보 없음은 `{"level":0,"version":11}`입니다.
경보 version은 상태 변경마다 올립니다. 백엔드 재시작 식별자가 있으면 source_epoch로 전달합니다.
갱신 시각은 timezone을 포함합니다. 빈자리는 0~total 정수, paid는 boolean입니다.
현재 주차장 ID A/B/C와 차량 01~24는 최종 모형 구성에 맞춰 config·chips·시연 배치를 조정합니다.

## 모의 결제 요청

POST payment 경로. Header: `Idempotency-Key: 작업키`와 선택 Bearer 인증.

```json
{"vehicle_no":"07","expected_amount":1500,"entered_at":"2026-11-01T09:00:00+09:00"}
```

서버는 차량의 현재 입차 건과 금액을 비교한 뒤 결제 상태 변경과 중복 키 기록을 같은 트랜잭션에서 처리해야 합니다.
같은 키/같은 요청은 기존 결과를 반환하고, 금액 또는 입차 건이 다르면 quote_changed를 반환합니다.
현재 코드의 입차 식별자는 entered_at입니다. 팀 DB에 고유 visit_id가 있으면 연결 시 그 ID를 기준으로 교체합니다.

| 결과 | 응답 |
|---|---|
| 완료 | `{"status":"paid","amount":1500,"paid_at":"ISO 시각"}` |
| 무료 | `{"status":"free","amount":0,"paid_at":"ISO 시각"}` |
| 이미 결제 | `{"status":"already_paid","paid_at":"ISO 시각"}` |
| 입차 기록 없음 | `{"status":"not_found"}` |
| 금액/입차 변경 | `{"status":"quote_changed"}` |

타임아웃은 성공/실패를 단정할 수 없습니다. 앱은 자동 재결제를 하지 않으며,
실서버 연결 시 결제 상태 조회 또는 관리자 확인으로 불확실 상태를 해소합니다.

## 챗봇 화면 프로토콜 변경

- POST chat: 기존 필드에 request_id, runtime_id, 확인 버튼의 confirmation_token을 추가했습니다.
- 같은 request_id와 같은 내용은 10분 이내/세션당 최근 100건 내에서 같은 작업 결과를 재사용합니다. 배너·차량·현재 확인과 버튼은 최신 상태로 다시 조합합니다.
- 다른 내용을 같은 request_id로 보내면 409입니다. 새 화면의 새 의도는 새 ID를 만듭니다.
- updates: cursor와 runtime_id를 전달합니다. 화면에 반영한 cursor만 다음 호출에 보냅니다.
- messages의 event_id로 화면 중복을 제거합니다. resync_required이면 현재 상태를 다시 조회합니다.
- 보고서 변경: runtime_id와 expected_revision이 필수입니다. 체크는 index와 checked를 보냅니다.
- 확정은 notes를 함께 보내 메모 저장과 확정을 한 번에 처리합니다. 충돌은 409입니다.

제공된 두 HTML은 새 계약에 맞춰 수정했습니다. 기존 팀 화면이 별도로 있다면 위 필드를 함께 적용합니다.

연결 후 `python tests/check_backend.py 07`을 실행하면 조회 API만 검사합니다. 키나 응답 원문을 출력하지 않습니다.

## 사용자 경험 확장

chat/updates 응답에 vehicle_context가 추가됩니다. 상태는 unselected/parked/stale/not_found/unavailable입니다.
관리자 호출은 확인 없이 등록하며 미처리 중복 호출은 같은 문의를 반환합니다.
POST `/admin/calls/{id}/status`는 status(확인 중/처리 완료), expected_revision, runtime_id를 받습니다.
문의 상태 알림은 기존 큐를 사용합니다. actions는 알림 고유 버튼, chips는 현재 대화용 버튼입니다.
피드백 버튼 ID는 문의의 현재 revision과 소유 세션을 검사합니다.

## 2026-11-01 안정화 변경 (현재 계약)

- POST chat의 `session_id`와 GET updates의 `session_id`는 필수입니다. 생략하면 dev 서버는 400, FastAPI는 422를 반환합니다. 공용 `anon` 세션을 만들지 않습니다.
- 응답 `runtime_id`와 `state_version`은 화면 상태 순서를 식별합니다. 같은 runtime의 낮은 `state_version` 응답은 현재 배너·차량·버튼에 적용하지 않습니다. 작업 영수증은 과거 결과임을 표시하고 별도로 보여줄 수 있습니다.
- `request_id` 재사용은 작업을 재실행하지 않습니다. 동일 요청의 현재 화면 상태는 달라질 수 있으므로 전체 JSON의 바이트 일치를 요구하지 않습니다. `meta.replayed`가 재생임을 표시합니다.
- `event=start`는 미완료 확인을 취소합니다. 현재 선택 차량은 유지합니다. updates의 `chips`가 배열이면 하단 버튼을 갱신하고, null이면 현재 알림/대화 버튼을 유지합니다.
- Lv2는 미완료 결제 확인을 취소하며 새 모의 결제를 열지 않습니다. 확인 토큰은 재활용하지 않습니다. 실제 결제와 경보 발생의 완전한 원자성은 백엔드와의 공통 트랜잭션 없이는 보장하지 않습니다.
- 관리자 호출 결과에는 `incident_id`, `alert_level`이 추가됩니다. 명시한 문의 차량은 선택 차량과 다를 수 있으며, 같은 사건·차량의 진행 중 문의를 묶습니다.
- location/fee의 `vehicle_no`는 요청과 일치해야 합니다. 주차칸 범위는 현재 주차장 명부의 수용량을 따릅니다. 시각은 시간대 포함 ISO 문자열이며 제공된 updated_at은 노후·미래 시각을 검사합니다. 감지 started_at은 발생 시각이며 지속 경보의 신선도 기준으로 사용하지 않습니다.
- payment의 amount가 확인 금액과 다르면 확인 필요로 처리합니다. 선택적으로 반환한 vehicle_no/entered_at도 검사합니다. 후속 fee가 명시적으로 미결제이거나 입차 건이 다르면 정상 영수증을 보여주지 않습니다. 재조회 자체가 실패하면 이미 받은 결제 응답을 ‘상태 재확인 필요’로 남기고 자동 재결제하지 않습니다.
- `ADMIN_TOKEN`을 설정하면 관리자 호출 목록/상태 변경, 보고서 조회/변경, 통계 및 demo API에 `Authorization: Bearer <ADMIN_TOKEN>`이 필요합니다. 미설정은 mock·loopback 접속에만 허용합니다. 사용자 chat/updates는 이 키를 사용하지 않습니다. 세션 ID는 인증 수단이 아닙니다.
- `DEMO_ENABLED=0`이면 시연 API는 404입니다. DATA_SOURCE=api의 기본은 비활성입니다. 실제 팀 서비스에서는 팀 인증으로 관리자 검사를 교체하거나 프록시에서 동일 경계를 적용해야 합니다.
- 기본 외부 조회 예산은 요청당 6초, 개별 백엔드 호출은 최대 2초, 화면 fetch 제한은 8초입니다. 연결·본문 수신 단계의 지연과 처리 대기는 실제 환경에서 다시 측정해야 하며 엄격한 실시간 응답 보장은 아닙니다.
- API 모드 공용 경보 조회는 기본 최대 0.2초 동안 공유하며 같은 순간의 중복 조회를 합칩니다. Mock에서는 동시 조회만 공유합니다.
- 서버가 **관찰한** 경보 변경만 최근 100건 보관합니다. 과거 이벤트는 `meta.historical=true`로 표시하며 현재 대피 명령처럼 재생하지 않습니다. 서버도 관찰하지 못한 사건은 백엔드 이벤트 이력 연동 전까지 복원할 수 없습니다.

## 4개 주차장·구역·목적지 확장 — PROVISIONAL(API v1 확정 필요)

2026-11-01 구현 계약이며 아래 추가 필드는 팀 백엔드와 아직 합의되지 않았습니다.
기존 경로와 필드는 유지합니다. 모의 환경 기본값은 `chatbot/lots.json`이며
`PARKING_SETTINGS_PATH`로 다른 JSON을 지정할 수 있습니다.
기본 A/B는 실물, C/D는 가상, 각 8칸입니다. 이 수량·구역 배정은 임시값입니다.

### 주차장 명부와 점유 응답

기존 occupancy 응답의 `lots`에 아래 선택 필드를 추가합니다.

```json
{"lots":{"D":{"name":"D 주차장","kind":"virtual","total":8,"empty":6,
  "zones":{"normal":{"total":6,"empty":4},"ev":{"total":1,"empty":1},"disabled":{"total":1,"empty":1}},
  "entry_allowed":true,"restriction_reason":""}},"updated_at":"시간대 포함 ISO 시각"}
```

- 검증을 통과한 최신 API 응답의 주차장 ID·순서·명칭·수용량을 우선합니다. 변경은 서버 로그에 남깁니다. 위 예시처럼 D만 반환하면 현재 명부도 D만 남으므로 전체 목록을 반환해야 합니다.
- ID는 영문자로 시작하는 영문·숫자·밑줄·하이픈 1~32자입니다. 기존 차량·경보의 주차장과 칸 번호는 현재 명부 기준으로 검사합니다.
- `kind`는 real/virtual입니다. 기존 API가 생략하면 설정값을 사용하고, 신규 ID에 설정이 없으면 unknown입니다.
- zones는 선택입니다. 미제공은 0이 아니며 전체 빈자리로 안내합니다. 해당 구역의 목적지 확정·구역 대기 알림은 수치를 확인할 때까지 실행하지 않습니다. 구역 수치 합계는 전체를 초과할 수 없습니다.
- `entry_allowed` 미제공은 기존 동작과 호환되도록 true로 취급합니다. false는 운영 제한이며 경보와 별도로 표시합니다. 실제 허용 여부를 이 기본값으로 확정하려면 팀 합의가 필요합니다.
- UI 설정 GET `/api/v1/chat/config`는 `{lots:[{lot_id,name,kind,capacity}],zones:[{key,name}],source}`를 반환합니다. API 모드는 명부를 먼저 조회합니다. 사용자 화면용이며 관리자 키가 필요하지 않습니다.

### 사용자 응답과 목적지 감시

chat/updates 응답의 추가 필드:

```json
{"destination":{"lot_id":"D","zone":"ev","kind":"virtual","set_at":"시간대 포함 ISO 시각"},
 "catalog":{"lots":[],"zones":[],"source":"backend"}}
```

목적지가 없으면 destination은 null입니다. catalog의 실제 배열은 위 설정 응답과 같습니다.
목적지는 세션별 메모리 상태이며 기본 1,800초(`DESTINATION_TTL_SEC`) 뒤 만료합니다.
입차가 확인된 선택 차량, 명시적 취소, 만료 시 감시를 종료합니다.
차량을 아직 선택하지 않은 세션은 실제 입차 주체를 알 수 없어 자동 종료할 수 없습니다.

선택 시 최신 점유·경보·운영 상태를 재검사합니다. 만차·Lv2·운영 제한으로 바뀌면
기존 알림 큐에 변경 시 한 번 알립니다. 동일 상태의 반복 폴링에는 재알림하지 않습니다.
대안은 같은 구역, 이용 가능, 빈자리 양수인 곳 중 빈자리 내림차순·명부 순서로 최대 두 곳입니다.
없으면 대기 버튼을 제공합니다. 이용자가 선택해야 목적지가 바뀝니다.
예약·지도·이동 경로·장애인 자격·충전 과금은 제공하지 않습니다.

구역 키 normal/ev/disabled와 별칭은 설정에 있습니다. 직전 빈자리 조회에만
주차장·구역 문맥을 최대 300초(`QUERY_CONTEXT_TTL_SEC`) 유지하고 다른 의도에서는 지웁니다.
구역 대기는 목적지와 별개이며 취소·만료·입차가 대기 등록을 자동 취소하지 않습니다.
선택 구역 안내와 실제 차량 위치·경보 위치를 서로 바꾸지 않습니다.

### 11/02 연결 회의 체크리스트 — 박해준·김한별·김채린 공동 확인

| 확인 항목 | 결정·검증할 내용 |
|---|---|
| 명부 | 전체 목록 응답 여부, 실제 ID·명칭·순서·수용량, A/B 실물·C/D 가상 구분 |
| 구역 | normal/ev/disabled 키, 칸 배치, 전체·구역 합계, 미검출·미제공 구분 |
| 운영 | entry_allowed와 restriction_reason 제공 주체, 미제공 기본값, 경보와의 우선순위 |
| 신선도 | updated_at·시간대·갱신 주기·허용 지연, 장애 시 stale 처리 |
| 입차 | 차량 번호·입차 건·주차 완료 판정의 출처, 목적지 감시 종료 시점 |
| 화면 | destination/catalog 추가 필드 수용, 알림 cursor, 동시 화면 응답 순서 |
| 인증·실행 | BACKEND_BASE_URL·조회 키·ADMIN_TOKEN, CORS/프록시, 단일 서버 프로세스 |
| 실연동 검증 | 4개 명부 → 구역 조회 → 목적지 → 만차/경보/제한 → 사용자 대안 선택 → 입차 종료 |

이 체크리스트는 담당 업무를 새로 배정한 것이 아닙니다. 팀에서 실제 API 담당자와 최종 확인합니다.
