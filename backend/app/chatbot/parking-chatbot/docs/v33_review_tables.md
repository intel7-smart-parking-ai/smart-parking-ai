# v3.3 후보 검토 — turn_guard 오탐·미탐 전체 표 (11/09)

같은 문장을 후보(`3b72325`)와 채택본에 넣은 결과. 기대 'pass'는 guard가 답하지 않고 엔진에 넘겨야 하는 문장.

## 후보 (feature/KAN-9-chatbot-v32-safety-port)

### 후보 · emergency

| 문장 | 기대 | 실제 | 판정 | 답 |
|---|---|---|:-:|---|
| 연기 나요 | emergency | emergency | ○ | 연기나 불이 보이거나 타는 냄새가 나면 즉시 안전한 곳으로 대피하세요. 안전한 곳에서 119에 신고하고 현장 관리자에게도 알리 |
| 거기 연기 나요 | emergency | emergency | ○ | 연기나 불이 보이거나 타는 냄새가 나면 즉시 안전한 곳으로 대피하세요. 안전한 곳에서 119에 신고하고 현장 관리자에게도 알리 |
| 주차장에 연기가 가득해 | emergency | emergency | ○ | 연기나 불이 보이거나 타는 냄새가 나면 즉시 안전한 곳으로 대피하세요. 안전한 곳에서 119에 신고하고 현장 관리자에게도 알리 |
| 타는 냄새 나 | emergency | emergency | ○ | 연기나 불이 보이거나 타는 냄새가 나면 즉시 안전한 곳으로 대피하세요. 안전한 곳에서 119에 신고하고 현장 관리자에게도 알리 |
| 불이 보여요 | emergency | emergency | ○ | 연기나 불이 보이거나 타는 냄새가 나면 즉시 안전한 곳으로 대피하세요. 안전한 곳에서 119에 신고하고 현장 관리자에게도 알리 |
| 불났어! | emergency | emergency | ○ | 연기나 불이 보이거나 타는 냄새가 나면 즉시 안전한 곳으로 대피하세요. 안전한 곳에서 119에 신고하고 현장 관리자에게도 알리 |
| 차에서 불이 번지고 있어 | emergency | emergency | ○ | 연기나 불이 보이거나 타는 냄새가 나면 즉시 안전한 곳으로 대피하세요. 안전한 곳에서 119에 신고하고 현장 관리자에게도 알리 |
| smoke in the garage | emergency | emergency | ○ | 연기나 불이 보이거나 타는 냄새가 나면 즉시 안전한 곳으로 대피하세요. 안전한 곳에서 119에 신고하고 현장 관리자에게도 알리 |
| 사람이 다쳤어요 | emergency | emergency | ○ | 다친 사람이 있으면 현장 안전을 먼저 확보하세요. 긴급한 부상이나 의식 저하가 있으면 즉시 119에 신고하고 현장 관리자에게  |
| 주차 연기할게 | pass | pass | ○ |  |
| 예약 연기 가능해? | pass | pass | ○ |  |
| 결제 연기 돼? | pass | pass | ○ |  |
| 출차 연기하고 싶어 | pass | pass | ○ |  |
| 연기해 줘 | pass | pass | ○ |  |
| 불 꺼져 있어 | pass | pass | ○ |  |
| 불났다는 거짓말이지? | pass | pass | ○ |  |
| 불났어? | pass | pass | ○ |  |
| 불 켜져 있어? | pass | pass | ○ |  |
| 연기 학원 근처 주차장 | pass | emergency | ✕ | 연기나 불이 보이거나 타는 냄새가 나면 즉시 안전한 곳으로 대피하세요. 안전한 곳에서 119에 신고하고 현장 관리자에게도 알리 |
| 배우 연기 좋더라 | pass | pass | ○ |  |
| 화재 경보 있어? | pass | pass | ○ |  |
| 빨간 불 깜빡여 | pass | rewrite→지금 경보 있어? | ○ |  |
| 담배 연기 때문에 창문 닫았어 | pass | pass | ○ |  |

오판 1/23

### 후보 · parking_permission vs 빈자리

| 문장 | 기대 | 실제 | 판정 | 답 |
|---|---|---|:-:|---|
| 장애인 칸 가능한 곳 있어? | pass | pass | ○ |  |
| 전기차 칸 어디 있어? | pass | pass | ○ |  |
| 충전 자리 몇 개 남았어? | pass | pass | ○ |  |
| 장애인 자리 있어? | pass | pass | ○ |  |
| 전기차 구역 남은 자리 | pass | pass | ○ |  |
| 일반 칸 비어 있는 곳? | pass | pass | ○ |  |
| 부산역 장애인 칸 주차장? | pass | pass | ○ |  |
| 일반차인데 일반 칸 대도 돼? | parking_permission | parking_permission | ○ | 전기차를 포함해 일반 주차칸에 주차할 수 있어요. 현장에 별도 이용 제한이 있으면 표지판을 따라 주세요. |
| 일반차인데 전기차 칸 대도 돼? | parking_permission | parking_permission | ○ | 전기차 전용 칸에는 빈칸이어도 주차할 수 없어요. |
| 일반차인데 장애인 칸 대도 돼? | parking_permission | parking_permission | ○ | 장애인 전용 칸에 주차할 수 없어요. |
| 전기차인데 일반 칸 대도 돼? | parking_permission | parking_permission | ○ | 전기차를 포함해 일반 주차칸에 주차할 수 있어요. 현장에 별도 이용 제한이 있으면 표지판을 따라 주세요. |
| 전기차인데 전기차 칸 대도 돼? | parking_permission | parking_permission | ○ | 전기차 전용 칸에 주차할 수 있어요. |
| 전기차인데 장애인 칸 대도 돼? | parking_permission | parking_permission | ○ | 장애인 전용 칸에 주차할 수 없어요. |
| 장애인 차량인데 일반 칸 대도 돼? | parking_permission | parking_permission | ○ | 전기차를 포함해 일반 주차칸에 주차할 수 있어요. 현장에 별도 이용 제한이 있으면 표지판을 따라 주세요. |
| 장애인 차량인데 전기차 칸 대도 돼? | parking_permission | parking_permission | ○ | 전기차 전용 칸에는 빈칸이어도 주차할 수 없어요. |
| 장애인 차량인데 장애인 칸 대도 돼? | parking_permission | parking_permission | ○ | 장애인 전용 칸은 장애인 주차표지가 있고 보행상 장애인이 탑승한 차량만 이용할 수 있어요. 잠깐이라도 이 조건을 충족하지 않으 |
| 5분만 장애인 칸 대도 돼? | parking_permission | parking_permission | ○ | 장애인 전용 칸은 장애인 주차표지가 있고 보행상 장애인이 탑승한 차량만 이용할 수 있어요. 잠깐이라도 이 조건을 충족하지 않으 |
| 충전 칸에 잠깐 세워도 괜찮아? | parking_permission | parking_permission | ○ | 전기차 전용 칸은 해당 전용 칸의 이용 조건을 충족한 차량만 주차할 수 있어요. 일반차는 빈칸이어도 이용할 수 없어요. |
| 일반차도 전기차 전용 칸에 주차할 수 있어? | parking_permission | parking_permission | ○ | 전기차 전용 칸에는 빈칸이어도 주차할 수 없어요. |
| 장애인 칸 비었는데 대도 돼? | parking_permission | parking_permission | ○ | 장애인 전용 칸은 장애인 주차표지가 있고 보행상 장애인이 탑승한 차량만 이용할 수 있어요. 잠깐이라도 이 조건을 충족하지 않으 |
| 23번 차 결제 돼? | pass | pass | ○ |  |
| 전기차 충전 자리 결제 가능해? | pass | pass | ○ |  |

오판 0/22

### 후보 · 없는 칸

| 문장 | 기대 | 실제 | 판정 | 답 |
|---|---|---|:-:|---|
| 부산역 B-30 비었어? | space_missing | space_missing | ○ | 부산역주차장에는 B-30 칸이 없어요. |
| 부산역 B-08 비었어? | pass | pass | ○ |  |
| 부산역 B-09 비었어? | space_missing | space_missing | ○ | 부산역주차장에는 B-09 칸이 없어요. |
| 부산역 A-08 비었어? | pass | pass | ○ |  |
| 개발원 A-06 비었어? | pass | pass | ○ |  |
| 개발원 A-07 비었어? | space_missing | space_missing | ○ | 개발원주차장에는 A-07 칸이 없어요. |
| 개발원 B-06 | pass | pass | ○ |  |
| 개발원 C-01 | space_missing | space_missing | ○ | 개발원주차장에는 C-01 칸이 없어요. |
| 김해공항 C-04 비었어? | pass | pass | ○ |  |
| 김해공항 C-05 비었어? | space_missing | space_missing | ○ | 김해공항 주차장에는 C-05 칸이 없어요. |
| 김해공항 A-24 | pass | pass | ○ |  |
| 김해공항 A-25 | space_missing | space_missing | ○ | 김해공항 주차장에는 A-25 칸이 없어요. |
| 김해공항 B-36 | pass | pass | ○ |  |
| 김해공항 B-37 | space_missing | space_missing | ○ | 김해공항 주차장에는 B-37 칸이 없어요. |
| 신세계 D-16 | pass | pass | ○ |  |
| 신세계 D-17 | space_missing | pass | ✕ |  |
| 신세계백화점 D-16 비었어? | pass | pass | ○ |  |
| 신세계백화점 D-17 비었어? | space_missing | space_missing | ○ | 신세계백화점주차장에는 D-17 칸이 없어요. |
| 공항 C-05 비었어? | space_missing | pass | ✕ |  |
| 백화점 D-17 비었어? | space_missing | pass | ✕ |  |
| B-30 비었어? | pass | pass | ○ |  |

오판 3/21

### 후보 · IDENTITY·인사

| 문장 | 기대 | 실제 | 판정 | 답 |
|---|---|---|:-:|---|
| 넌 이름이 뭐니 | self_intro | self_intro | ○ | 저는 PARK:ON 주차 안내 챗봇이에요. 빈자리, 내 차 위치, 요금·모의 결제, 모의 화재 경보 안내를 도와드려요. 아래  |
| 너 누구야 | self_intro | self_intro | ○ | 저는 PARK:ON 주차 안내 챗봇이에요. 빈자리, 내 차 위치, 요금·모의 결제, 모의 화재 경보 안내를 도와드려요. 아래  |
| 자기소개 해줘 | self_intro | self_intro | ○ | 저는 PARK:ON 주차 안내 챗봇이에요. 빈자리, 내 차 위치, 요금·모의 결제, 모의 화재 경보 안내를 도와드려요. 아래  |
| 안녕 | self_intro | self_intro | ○ | 저는 PARK:ON 주차 안내 챗봇이에요. 빈자리, 내 차 위치, 요금·모의 결제, 모의 화재 경보 안내를 도와드려요. 아래  |
| 안녕하세요 | self_intro | self_intro | ○ | 저는 PARK:ON 주차 안내 챗봇이에요. 빈자리, 내 차 위치, 요금·모의 결제, 모의 화재 경보 안내를 도와드려요. 아래  |
| who are you | self_intro | self_intro | ○ | 저는 PARK:ON 주차 안내 챗봇이에요. 빈자리, 내 차 위치, 요금·모의 결제, 모의 화재 경보 안내를 도와드려요. 아래  |
| 주차장 이름이 뭐야? | lot_names | self_intro | ✕ | 저는 PARK:ON 주차 안내 챗봇이에요. 빈자리, 내 차 위치, 요금·모의 결제, 모의 화재 경보 안내를 도와드려요. 아래  |
| 내 차 이름 뭐야 | pass | self_intro | ✕ | 저는 PARK:ON 주차 안내 챗봇이에요. 빈자리, 내 차 위치, 요금·모의 결제, 모의 화재 경보 안내를 도와드려요. 아래  |
| 안녕 23번 차 어디야 | pass | pass | ○ |  |
| 이 주차장 이름이 뭐야 | lot_names | self_intro | ✕ | 저는 PARK:ON 주차 안내 챗봇이에요. 빈자리, 내 차 위치, 요금·모의 결제, 모의 화재 경보 안내를 도와드려요. 아래  |
| 구역 이름이 뭐야 | pass | self_intro | ✕ | 저는 PARK:ON 주차 안내 챗봇이에요. 빈자리, 내 차 위치, 요금·모의 결제, 모의 화재 경보 안내를 도와드려요. 아래  |
| 요금제 이름이 뭐야 | pass | self_intro | ✕ | 저는 PARK:ON 주차 안내 챗봇이에요. 빈자리, 내 차 위치, 요금·모의 결제, 모의 화재 경보 안내를 도와드려요. 아래  |
| 안녕 빈자리 있어? | pass | pass | ○ |  |
| 챗봇 기능 뭐 있어? | pass | pass | ○ |  |
| 정체 구간 있어? | pass | pass | ○ |  |
| hi there, any spots? | pass | pass | ○ |  |
| 하이패스 돼? | pass | pass | ○ |  |
| 반가워 23번 차 요금 | pass | pass | ○ |  |
| 너는 뭐 하는 챗봇이야 | self_intro | self_intro | ○ | 저는 PARK:ON 주차 안내 챗봇이에요. 빈자리, 내 차 위치, 요금·모의 결제, 모의 화재 경보 안내를 도와드려요. 아래  |
| 이름이 뭐예요? | self_intro | self_intro | ○ | 저는 PARK:ON 주차 안내 챗봇이에요. 빈자리, 내 차 위치, 요금·모의 결제, 모의 화재 경보 안내를 도와드려요. 아래  |

오판 5/20

### 후보 · 그거 취소

| 문장 | 기대 | 실제 | 판정 | 답 |
|---|---|---|:-:|---|
| 그거 취소 | cancel_clarify | cancel_clarify | ○ | 어떤 요청을 취소할까요? 취소할 대상을 알려주세요. |
| 취소 | cancel_clarify | cancel_clarify | ○ | 어떤 요청을 취소할까요? 취소할 대상을 알려주세요. |
| 취소해줘 | cancel_clarify | cancel_clarify | ○ | 어떤 요청을 취소할까요? 취소할 대상을 알려주세요. |
| 결제 취소해줘 | pass | pass | ○ |  |
| 23번 결제 취소 | pass | pass | ○ |  |

오판 0/5

## 채택본 (수정 후)

### 채택본 · emergency

| 문장 | 기대 | 실제 | 판정 | 답 |
|---|---|---|:-:|---|
| 연기 나요 | emergency | emergency | ○ | 연기나 불이 보이거나 타는 냄새가 나면 즉시 안전한 곳으로 대피하세요. 안전한 곳에서 119에 신고하고 현장 관리자에게도 알리 |
| 거기 연기 나요 | emergency | emergency | ○ | 연기나 불이 보이거나 타는 냄새가 나면 즉시 안전한 곳으로 대피하세요. 안전한 곳에서 119에 신고하고 현장 관리자에게도 알리 |
| 주차장에 연기가 가득해 | emergency | emergency | ○ | 연기나 불이 보이거나 타는 냄새가 나면 즉시 안전한 곳으로 대피하세요. 안전한 곳에서 119에 신고하고 현장 관리자에게도 알리 |
| 타는 냄새 나 | emergency | emergency | ○ | 연기나 불이 보이거나 타는 냄새가 나면 즉시 안전한 곳으로 대피하세요. 안전한 곳에서 119에 신고하고 현장 관리자에게도 알리 |
| 불이 보여요 | emergency | emergency | ○ | 연기나 불이 보이거나 타는 냄새가 나면 즉시 안전한 곳으로 대피하세요. 안전한 곳에서 119에 신고하고 현장 관리자에게도 알리 |
| 불났어! | emergency | emergency | ○ | 연기나 불이 보이거나 타는 냄새가 나면 즉시 안전한 곳으로 대피하세요. 안전한 곳에서 119에 신고하고 현장 관리자에게도 알리 |
| 차에서 불이 번지고 있어 | emergency | emergency | ○ | 연기나 불이 보이거나 타는 냄새가 나면 즉시 안전한 곳으로 대피하세요. 안전한 곳에서 119에 신고하고 현장 관리자에게도 알리 |
| smoke in the garage | emergency | emergency | ○ | 연기나 불이 보이거나 타는 냄새가 나면 즉시 안전한 곳으로 대피하세요. 안전한 곳에서 119에 신고하고 현장 관리자에게도 알리 |
| 사람이 다쳤어요 | emergency | emergency | ○ | 다친 사람이 있으면 현장 안전을 먼저 확보하세요. 긴급한 부상이나 의식 저하가 있으면 즉시 119에 신고하고 현장 관리자에게  |
| 주차 연기할게 | pass | pass | ○ |  |
| 예약 연기 가능해? | pass | pass | ○ |  |
| 결제 연기 돼? | pass | pass | ○ |  |
| 출차 연기하고 싶어 | pass | pass | ○ |  |
| 연기해 줘 | pass | pass | ○ |  |
| 불 꺼져 있어 | pass | pass | ○ |  |
| 불났다는 거짓말이지? | pass | pass | ○ |  |
| 불났어? | pass | pass | ○ |  |
| 불 켜져 있어? | pass | pass | ○ |  |
| 연기 학원 근처 주차장 | pass | pass | ○ |  |
| 배우 연기 좋더라 | pass | pass | ○ |  |
| 화재 경보 있어? | pass | pass | ○ |  |
| 빨간 불 깜빡여 | pass | pass | ○ |  |
| 담배 연기 때문에 창문 닫았어 | pass | pass | ○ |  |

오판 0/23

### 채택본 · parking_permission vs 빈자리

| 문장 | 기대 | 실제 | 판정 | 답 |
|---|---|---|:-:|---|
| 장애인 칸 가능한 곳 있어? | pass | pass | ○ |  |
| 전기차 칸 어디 있어? | pass | pass | ○ |  |
| 충전 자리 몇 개 남았어? | pass | pass | ○ |  |
| 장애인 자리 있어? | pass | pass | ○ |  |
| 전기차 구역 남은 자리 | pass | pass | ○ |  |
| 일반 칸 비어 있는 곳? | pass | pass | ○ |  |
| 부산역 장애인 칸 주차장? | pass | pass | ○ |  |
| 일반차인데 일반 칸 대도 돼? | parking_permission | parking_permission | ○ | 일반 차량은 일반 칸에 주차할 수 있어요. |
| 일반차인데 전기차 칸 대도 돼? | parking_permission | parking_permission | ○ | 일반 차량은 전기차·장애인 전용 칸에 주차할 수 없습니다. |
| 일반차인데 장애인 칸 대도 돼? | parking_permission | parking_permission | ○ | 일반 차량은 전기차·장애인 전용 칸에 주차할 수 없습니다. |
| 전기차인데 일반 칸 대도 돼? | parking_permission | parking_permission | ○ | 전기차의 일반 칸 이용 기준은 아직 안내 자료에 없어요. 현장 표지판을 따라 주세요. 일반 차량은 전기차·장애인 전용 칸에 주 |
| 전기차인데 전기차 칸 대도 돼? | parking_permission | parking_permission | ○ | 전기차 전용 칸은 전기차를 위한 칸이에요. 지금 빈자리는 '전기차 전용 칸 빈자리'로 확인할 수 있어요. |
| 전기차인데 장애인 칸 대도 돼? | parking_permission | parking_permission | ○ | 장애인 전용 칸은 장애인 차량을 위한 칸이에요. 다른 전용 차량의 이용 기준은 안내 자료에 없어서 현장 안내를 따라 주세요. |
| 장애인 차량인데 일반 칸 대도 돼? | parking_permission | parking_permission | ○ | 장애인 차량의 일반 칸 이용 기준은 아직 안내 자료에 없어요. 현장 표지판을 따라 주세요. 일반 차량은 전기차·장애인 전용 칸 |
| 장애인 차량인데 전기차 칸 대도 돼? | parking_permission | parking_permission | ○ | 전기차 전용 칸은 전기차를 위한 칸이에요. 다른 전용 차량의 이용 기준은 안내 자료에 없어서 현장 안내를 따라 주세요. |
| 장애인 차량인데 장애인 칸 대도 돼? | parking_permission | parking_permission | ○ | 장애인 전용 칸은 장애인 차량을 위한 칸이에요. 지금 빈자리는 '장애인 전용 칸 빈자리'로 확인할 수 있어요. |
| 5분만 장애인 칸 대도 돼? | parking_permission | parking_permission | ○ | 일반 차량은 전기차·장애인 전용 칸에 주차할 수 없습니다. 잠깐 세우는 것도 마찬가지예요. |
| 충전 칸에 잠깐 세워도 괜찮아? | parking_permission | parking_permission | ○ | 일반 차량은 전기차·장애인 전용 칸에 주차할 수 없습니다. 잠깐 세우는 것도 마찬가지예요. |
| 일반차도 전기차 전용 칸에 주차할 수 있어? | parking_permission | parking_permission | ○ | 일반 차량은 전기차·장애인 전용 칸에 주차할 수 없습니다. |
| 장애인 칸 비었는데 대도 돼? | parking_permission | parking_permission | ○ | 일반 차량은 전기차·장애인 전용 칸에 주차할 수 없습니다. |
| 23번 차 결제 돼? | pass | pass | ○ |  |
| 전기차 충전 자리 결제 가능해? | pass | pass | ○ |  |

오판 0/22

### 채택본 · 없는 칸

| 문장 | 기대 | 실제 | 판정 | 답 |
|---|---|---|:-:|---|
| 부산역 B-30 비었어? | space_missing | space_missing | ○ | 부산역주차장에는 B-30 칸이 없어요. 칸 번호를 다시 확인해 주세요. |
| 부산역 B-08 비었어? | pass | pass | ○ |  |
| 부산역 B-09 비었어? | space_missing | space_missing | ○ | 부산역주차장에는 B-09 칸이 없어요. 칸 번호를 다시 확인해 주세요. |
| 부산역 A-08 비었어? | pass | pass | ○ |  |
| 개발원 A-06 비었어? | pass | pass | ○ |  |
| 개발원 A-07 비었어? | space_missing | space_missing | ○ | 개발원주차장에는 A-07 칸이 없어요. 칸 번호를 다시 확인해 주세요. |
| 개발원 B-06 | pass | pass | ○ |  |
| 개발원 C-01 | space_missing | space_missing | ○ | 개발원주차장에는 C-01 칸이 없어요. 칸 번호를 다시 확인해 주세요. |
| 김해공항 C-04 비었어? | pass | pass | ○ |  |
| 김해공항 C-05 비었어? | space_missing | space_missing | ○ | 김해공항 주차장에는 C-05 칸이 없어요. 칸 번호를 다시 확인해 주세요. |
| 김해공항 A-24 | pass | pass | ○ |  |
| 김해공항 A-25 | space_missing | space_missing | ○ | 김해공항 주차장에는 A-25 칸이 없어요. 칸 번호를 다시 확인해 주세요. |
| 김해공항 B-36 | pass | pass | ○ |  |
| 김해공항 B-37 | space_missing | space_missing | ○ | 김해공항 주차장에는 B-37 칸이 없어요. 칸 번호를 다시 확인해 주세요. |
| 신세계 D-16 | pass | pass | ○ |  |
| 신세계 D-17 | space_missing | space_missing | ○ | 신세계백화점주차장에는 D-17 칸이 없어요. 칸 번호를 다시 확인해 주세요. |
| 신세계백화점 D-16 비었어? | pass | pass | ○ |  |
| 신세계백화점 D-17 비었어? | space_missing | space_missing | ○ | 신세계백화점주차장에는 D-17 칸이 없어요. 칸 번호를 다시 확인해 주세요. |
| 공항 C-05 비었어? | space_missing | space_missing | ○ | 김해공항 주차장에는 C-05 칸이 없어요. 칸 번호를 다시 확인해 주세요. |
| 백화점 D-17 비었어? | space_missing | space_missing | ○ | 신세계백화점주차장에는 D-17 칸이 없어요. 칸 번호를 다시 확인해 주세요. |
| B-30 비었어? | pass | pass | ○ |  |

오판 0/21

### 채택본 · IDENTITY·인사

| 문장 | 기대 | 실제 | 판정 | 답 |
|---|---|---|:-:|---|
| 넌 이름이 뭐니 | self_intro | self_intro | ○ | 저는 PARK:ON 주차 안내 챗봇이에요. 빈자리, 내 차 위치, 요금·모의 결제, 모의 화재 경보 안내를 도와드려요. 아래  |
| 너 누구야 | self_intro | self_intro | ○ | 저는 PARK:ON 주차 안내 챗봇이에요. 빈자리, 내 차 위치, 요금·모의 결제, 모의 화재 경보 안내를 도와드려요. 아래  |
| 자기소개 해줘 | self_intro | self_intro | ○ | 저는 PARK:ON 주차 안내 챗봇이에요. 빈자리, 내 차 위치, 요금·모의 결제, 모의 화재 경보 안내를 도와드려요. 아래  |
| 안녕 | self_intro | self_intro | ○ | 저는 PARK:ON 주차 안내 챗봇이에요. 빈자리, 내 차 위치, 요금·모의 결제, 모의 화재 경보 안내를 도와드려요. 아래  |
| 안녕하세요 | self_intro | self_intro | ○ | 저는 PARK:ON 주차 안내 챗봇이에요. 빈자리, 내 차 위치, 요금·모의 결제, 모의 화재 경보 안내를 도와드려요. 아래  |
| who are you | self_intro | self_intro | ○ | 저는 PARK:ON 주차 안내 챗봇이에요. 빈자리, 내 차 위치, 요금·모의 결제, 모의 화재 경보 안내를 도와드려요. 아래  |
| 주차장 이름이 뭐야? | lot_names | lot_names | ○ | 안내할 수 있는 주차장은 개발원주차장·부산역주차장·김해공항 주차장·신세계백화점주차장, 모두 4곳이에요. |
| 내 차 이름 뭐야 | pass | pass | ○ |  |
| 안녕 23번 차 어디야 | pass | pass | ○ |  |
| 이 주차장 이름이 뭐야 | lot_names | lot_names | ○ | 안내할 수 있는 주차장은 개발원주차장·부산역주차장·김해공항 주차장·신세계백화점주차장, 모두 4곳이에요. |
| 구역 이름이 뭐야 | pass | pass | ○ |  |
| 요금제 이름이 뭐야 | pass | pass | ○ |  |
| 안녕 빈자리 있어? | pass | pass | ○ |  |
| 챗봇 기능 뭐 있어? | pass | pass | ○ |  |
| 정체 구간 있어? | pass | pass | ○ |  |
| hi there, any spots? | pass | pass | ○ |  |
| 하이패스 돼? | pass | pass | ○ |  |
| 반가워 23번 차 요금 | pass | pass | ○ |  |
| 너는 뭐 하는 챗봇이야 | self_intro | self_intro | ○ | 저는 PARK:ON 주차 안내 챗봇이에요. 빈자리, 내 차 위치, 요금·모의 결제, 모의 화재 경보 안내를 도와드려요. 아래  |
| 이름이 뭐예요? | self_intro | self_intro | ○ | 저는 PARK:ON 주차 안내 챗봇이에요. 빈자리, 내 차 위치, 요금·모의 결제, 모의 화재 경보 안내를 도와드려요. 아래  |

오판 0/20

### 채택본 · 그거 취소

| 문장 | 기대 | 실제 | 판정 | 답 |
|---|---|---|:-:|---|
| 그거 취소 | cancel_clarify | cancel_clarify | ○ | 어떤 요청을 취소할까요? 취소할 대상을 알려주세요. |
| 취소 | cancel_clarify | cancel_clarify | ○ | 어떤 요청을 취소할까요? 취소할 대상을 알려주세요. |
| 취소해줘 | cancel_clarify | cancel_clarify | ○ | 어떤 요청을 취소할까요? 취소할 대상을 알려주세요. |
| 결제 취소해줘 | pass | pass | ○ |  |
| 23번 결제 취소 | pass | pass | ○ |  |

오판 0/5
