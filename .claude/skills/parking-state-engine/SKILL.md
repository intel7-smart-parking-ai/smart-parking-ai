---
name: parking-state-engine
description: "주차 상태 확정 규칙: vision-event를 시간적으로 안정화해 주차칸 점유를 확정하고, 입차·출차·칸 이동 세션을 관리하며, 요금 계산과 멱등 모의 결제·취소·매출 집계를 처리하는 도메인 규칙과 테스트 시나리오. 상태 엔진, 입출차, 세션, 요금, 결제, 매출 로직을 구현하거나 리뷰할 때 사용한다."
---

# Parking State Engine — 상태 확정·세션·요금·결제 규칙

backend-engineer(상태·세션)와 billing-engineer(요금·결제)가 공유한다. 원칙: **AI는 관측, 서버가 확정.**

## 1. 주차칸 상태 안정화

각 `(lot_id, space_id)`마다 최근 관측 창을 유지한다. 기본값은 설정으로 빼고 11/06 점검 후 조정.

| 파라미터 | 기본 | 의미 |
| --- | --- | --- |
| `occupy_confirm_s` | 3초 | 연속 occupied 관측 지속 시 occupied 확정 |
| `vacate_confirm_s` | 10초 | 연속 empty 관측 지속 시 empty 확정 (가림 대비 더 길게) |
| `stale_after_s` | 15초 | 관측 없으면 `uncertain` (카메라 끊김) — 출차 처리 아님 |
| `min_confidence` | 0.5 | 미만 관측은 무시 또는 uncertain 근거로만 |

- 단일 프레임 미검출로 empty/출차 처리 금지
- 경계 걸침·두 칸 겹침 → `uncertain`
- 카메라 `unavailable` 시 해당 주차장 칸은 마지막 확정값 유지 + `uncertain` 표시

## 2. car_id 결합

- 점유 확정 칸에 OCR 관측이 들어오면 최빈값(최근 N회) 투표로 car_id 확정, 미달이면 `null` 유지
- 같은 car_id가 두 칸에서 동시 확정 → 중복 경보(`duplicate_car_id`), 관리자 수동 확정

## 3. 세션

| 사건 | 처리 |
| --- | --- |
| 주차장 내 해당 car_id(또는 미식별 차량)가 처음 확정 | 입차: session `active`, `entry_at` = 최초 안정 관측 시각 |
| 같은 car_id가 다른 칸으로 이동 | **세션 유지**, 위치만 갱신 |
| 주차장 전체에서 `vacate_confirm_s` 이상 미관측 | 출차: `exit_at`, status `closed`(결제 대기 또는 결제 완료) |
| 수동 보정 | `source="manual"`, audit_logs 기록 |

미식별 차량은 임시 `session` 생성 후 car_id 확정 시 연결. 가상 C/D는 시뮬레이터가 같은 이벤트 형식으로 입력.

## 4. 요금

```
minutes = ceil((exit_or_now - entry_at) / 1분)
fee = base_fee + ceil(max(0, minutes - base_minutes) / unit_minutes) * extra_fee   (base_minutes = unit_minutes 기본)
fee = min(fee, daily_cap)  (일 단위 상한, 자정 기준 분할은 정책 확정 후)
```
- 정책은 `(lot_id, effective_at)`로 조회, 입차 시점 정책 적용
- 금액 정수 KRW, 계산은 `billing` 단일 함수. 다른 곳에 복제 금지
- 실제 금액 미확정 → 시드값 사용 + "가정" 표기

## 5. 모의 결제

상태: `pending → paid → cancelled`, 실패 `rejected`
- `POST /mock-payments` 동일 `idempotency_key` 재요청 → **같은 payment 반환** (새로 만들지 않음), 다른 body면 409
- 이미 `paid`인 세션에 새 키로 결제 → 409 `ALREADY_PAID`
- 취소: `paid`만 가능, 재취소 409. 관리자 권한 확인, audit_logs 기록
- 매출 = Σ paid 금액 − Σ cancelled 금액, 결제 시점(`paid_at`) 기준 일·월, lot별·real/virtual 구분

## 6. 필수 테스트 시나리오

1. 1~2초 가림 → 상태 occupied 유지, 세션 유지
2. A-01 → A-02 이동 → 세션 1개 유지, 위치 갱신
3. 카메라 15초 끊김 → uncertain, 출차 없음
4. OCR null → 세션은 생성, car_id null, 이후 확정 시 연결
5. 같은 키 결제 2회 → payment 1건
6. 결제 → 취소 → 매출 0
7. 요금: 0분, unit 경계 정확히, 상한 초과
8. 빨간 미니카 프레임 → LED 경보 없음 (vision 평가와 함께)
