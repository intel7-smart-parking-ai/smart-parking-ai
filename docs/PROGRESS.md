# 진행 현황 (PROGRESS)

> 전체 개발 항목과 완료 여부를 한곳에서 확인한다. 상세 작업·담당 변경은 Jira(KAN)가 기준이다.
> 체크 기준: **☑ = main 병합 완료**, ◐ = 브랜치/PR 진행 중, ☐ = 미착수. "완료"는 실행 증거 + 테스트 + PR 병합까지 끝난 상태.
> 최종 갱신: 2026-11-03 (화) 11:00

## 요약

| 전체 항목 | ☑ 완료 | ◐ 진행 | ☐ 미착수 |
| :-: | :-: | :-: | :-: |
| 47 | 6 | 5 | 36 |

(2~12장의 최상위 체크 항목 기준. 하위 항목 제외)

---

## 1. 일정 관문

| 날짜 | 관문 | 상태 |
| --- | --- | :-: |
| 11/02 | API v1·폴더 경계 확정, Jira 등록 | ◐ API 데이터 규격 v0.2 정리 중 |
| 11/03 | 모형 실측, 카메라 위치 확정, ROI 캘리브레이션, OCR·LED 촬영 검증 | ◐ |
| 11/04 | 데이터 수집·라벨링, 서버 목업 API, UI 초기 화면 | ◐ 목업 API 일부 완료 |
| 11/05 | 모델 1차 결과, DB·요금 함수 | ◐ 요금 함수 완료(KAN-28) |
| **11/06** | **중간 점검①: A 주차장 영상 → 점유 → API → 화면** | ☐ |
| 11/09 | B 주차장 + 가상 C | ☐ |
| **11/10** | **운영 기능 통합: 모의 결제·매출·LLM·모바일/관리자** | ☐ |
| 11/11 | 성능·오류 테스트 | ☐ |
| **11/12** | **코드 동결·리허설** | ☐ |
| 11/13 | 최종 시연 | ☐ |

---

## 2. 공통 기반

- [x] 초기 실행 골격 (FastAPI health, React 화면) — PR #1
- [x] Claude Code 하네스 (에이전트·스킬·CLAUDE.md) — PR #2
- [ ] API 계약 v0.2 확정 (`docs/API_CONTRACT.md` 반영) — 김한별 ◐ 규격 정리 완료, 반영 PR 대기
- [ ] 진행 현황 문서 (`docs/PROGRESS.md`) — 김한별 ◐ 이 문서

## 3. EPIC 01 차량 탐지·주차칸 (1팀 김한별·박해준) — P0

- [ ] 데이터 수집·라벨링 (미니카 3대로 시작, 추가 구매 예정)
- [ ] 차량 탐지 모델 1차 학습·평가 (`backend/app/vision/vehicle`)
- [ ] vision-event 어댑터 (`vehicle_observed`, `space_empty_observed`)

## 4. EPIC 02 번호 스티커 OCR (2팀 우지원·김채린) — P0

- [ ] 스티커 가독성 확인 (카메라 높이 기준 픽셀 크기, 6/9 혼동)
- [ ] 선행 0 처리 결정 (스티커 `1` → `"01"` 어댑터 변환)
- [ ] OCR 모델 1차 (`backend/app/vision/plate`), 실패 시 대체 마커

## 5. EPIC 03 LED 모의 화재 (3팀 윤명수·서정범) — P0 시연 / P1 정교화

- [ ] LED 점등·소등 촬영 검증, 빨간 물체 오탐 확인
- [ ] LED 인식 모듈 (`backend/app/vision/fire`) — 첫 업로드(`71d81bb`)에 있던 코드 복원 여부 확인 필요
- [ ] `mock_fire_led_observed` 이벤트 연동

## 6. 카메라·ROI (우지원) — P0

- [ ] 카메라 위치 확정 (중앙 상단 vs 모서리 대각선)
- [ ] 카메라 허브 (`backend/app/camera`, 장치 독점·프레임 공유·재연결)
- [ ] 주차칸 ROI 등록 (`configs/parking_a.json`, `parking_b.json`) — 인쇄 판 구역 ↔ 주차장 A/B ID 매핑 확정 필요
- [ ] 탐지 결과 ↔ 주차칸 매칭 (`backend/app/roi`)

## 7. 백엔드·DB (박해준) — P0

- [x] 조회 목업 API 3종 (`/parking-lots`, `/spaces`, `/vehicles/{car_id}/location`) — KAN-16, PR #3
- [x] DB 통합 계획 문서 (PostgreSQL 제안) — KAN-17, PR #6
- [ ] DB 모델 프로토타입 (`parking_lots`, `parking_spaces`) — KAN-17 ◐ `feature/KAN-17-db-model-prototype` (sqlalchemy 의존성 추가됨)
- [ ] `GET /sessions` (lot_id·car_id·status 필터)
- [ ] `location.session_id` 실제 세션 연결
- [ ] vision-event 수집 `POST /internal/vision-events`
- [ ] 점유 안정화·입출차 세션 확정 (`backend/app/parking`)
- [ ] 경보 API (`/alerts`, acknowledge·resolve)
- [ ] `parking_lots` 테이블 정의 단일화 (billing·KAN-17·데모)
- [ ] 가상 주차장 C 시뮬레이터 (`backend/app/simulation`) — 11/09

## 8. 요금·모의 결제·매출 (윤명수) — P0

- [ ] 요금·결제·취소·매출 API — KAN-28 ◐ `feature/KAN-28-billing` (`cfd0191`), 병합 가능 판정, PR 대기
  - [x] 요금 계산 (기본·추가·일일 상한), 서버 단일 계산
  - [x] 모의 결제 (멱등키, 이중 결제 차단, 실서버 동시 요청 검증)
  - [x] 결제 취소·매출 차감, 일·월·주차장별 매출
  - [x] 결제 목록·요금 정책 API, 팀 공통 에러·목록 형식
  - [x] pytest 53 passed
- [ ] `main.py` 라우터 등록 (KAN-17 DB 확정 후 박해준과)
- [ ] `quote_id` 채택 여부 (PM 결정 대기)

## 9. 프론트엔드 (김채린) — P0

- [ ] PARK:ON 데모 PR — ◐ `chore/parkon-react-docker-demo-20261103` (미병합)
- [ ] API 데이터 규격 통일 (`canceled` 철자, 에러 형식, 요금 필드명, 목록 `items`)
- [ ] 사용자 모바일 웹: 빈자리·내 차 위치·요금·모의 결제
- [ ] 관리자 관제: 다중 주차장 현황·카메라·경보·일·월 매출

## 10. LLM 챗봇 (서정범) — P0 텍스트 / P2 STT

- [x] 챗봇 초본 — KAN-9, PR #4
- [ ] API 경로·필드 계약 맞춤 (`car_id`, `/sessions/{id}/fee`, `/mock-payments`)
- [ ] 실서버 연동 확인 (`DATA_SOURCE=api`)
- [ ] 폴더명 정리 (`parking-chatbot` → import 가능한 이름), 검증 로그 정리
- [ ] 음성인식(STT) 타당성 검토 — P2

## 11. 물리 모형·재료

- [x] 주차장 인쇄 판 디자인 (거의 확정)
- [ ] 미니카 추가 구매 (현재 3대 / 목표 24대)
- [ ] 빨간 LED·전지 홀더, 폼보드, 카메라 거치대 2개
- [ ] 번호 스티커 색 결정 (빨강·주황·분홍 제외)

## 12. 통합 검증

- [ ] 11/06 A 주차장 End-to-End 시연
- [ ] 11/10 운영 기능 통합 점검
- [ ] 11/11 성능·오류 테스트 (가림·오인식·재연결·중복 결제·경보 해제)
- [ ] 11/12 코드 동결·리허설

---

## 갱신 방법

1. 작업이 **main에 병합**되면 해당 줄을 `- [x]`로 바꾸고 PR 번호를 적는다.
2. 브랜치·PR 진행 중이면 ◐와 브랜치명을 적는다.
3. 맨 위 "최종 갱신" 시각과 요약 표 숫자를 함께 고친다.
4. 이 문서 변경은 `chore/progress-*` 브랜치 PR로 올린다 (main 직접 수정 금지).
