---
name: smart-parking
description: "AI 스마트 주차장 통합관제 프로젝트의 오케스트레이터. 기능 요청을 담당 에이전트(integration-pm, vision-engineer, camera-roi-engineer, backend-engineer, billing-engineer, frontend-engineer, chatbot-engineer, qa-reviewer)에게 소유권 경계에 맞게 분배하고, 계약→목업→구현→검증 순서로 진행한다. '주차장 기능 개발', '점유 판정 붙여줘', 'API 만들어줘', '관제 화면', '결제 기능', '챗봇', '통합해줘', '모델 붙여줘' 등 이 저장소의 기능 개발 전반에 사용한다."
---

# Smart Parking Orchestrator

AI 스마트 주차장 기능 요청을 받아 **소유권 경계를 지키며** 에이전트 팀으로 개발한다. 기준 문서는 `CLAUDE.md`와 `docs/통합_개발운영지침서.md`.

## 에이전트 구성

| 에이전트 | 영역 | 의존 스킬 |
| --- | --- | --- |
| integration-pm | 작업 분해, 계약·스키마 변경 | api-contract |
| camera-roi-engineer | Frame Hub, ROI, 위치 매칭 | api-contract |
| vision-engineer | 차량/번호/LED 모델·어댑터 | vision-pipeline, api-contract |
| backend-engineer | FastAPI, DB, 상태 엔진, 시뮬레이터 | api-contract, parking-state-engine |
| billing-engineer | 요금, 모의 결제, 매출 | parking-state-engine |
| frontend-engineer | 사용자 웹, 관리자 관제 | api-contract |
| chatbot-engineer | LLM tool 챗봇 | api-contract |
| qa-reviewer | 테스트, 리뷰, 관문 점검 | integration-gate |

## 워크플로우

### Phase 1 — 준비 (오케스트레이터 직접)

1. 요청에서 추출: 대상 기능, 대상 주차장(A/B/C/D), 영향 영역(폴더), 관련 관문 날짜
2. `_workspace/` 생성(Git 제외), 요청 요약을 `_workspace/00_request.md`에 기록
3. 현재 구현 상태 확인: `backend/app/*`, `frontend/src`, `tests/`, `docs/API_CONTRACT.md`
4. 아래 "작업 규모별 모드"로 투입 에이전트 결정

### Phase 2 — 계약 확인 (integration-pm)

- 필요한 엔드포인트/이벤트/테이블이 `api-contract`에 있는지 확인
- 없거나 바꿔야 하면 `_workspace/change_request_*.md` 작성 → **사용자(PM)에게 승인 요청 후 진행**
- 의존 모듈이 미완성이면 목업 규격(예시 JSON, `file:` 프레임 소스, 시뮬레이터 이벤트)을 정의

### Phase 3 — 병렬 구현

| 순서 | 작업 | 담당 | 의존 |
| --- | --- | --- | --- |
| 1 | 계약·목업 확정 | integration-pm | — |
| 2a | 프레임 허브·ROI·매칭 | camera-roi-engineer | 1 |
| 2b | 모델 추론·어댑터 | vision-engineer | 1 (프레임은 파일로 대체 가능) |
| 2c | API·DB·상태 엔진 | backend-engineer | 1 |
| 2d | 요금·결제·매출 | billing-engineer | 1 |
| 2e | 화면 | frontend-engineer | 1 (목업) |
| 2f | 챗봇 | chatbot-engineer | 1 (목업) |
| 3 | 연결(목업 → 실제) | 해당 소유자 | 2x |
| 4 | 테스트·리뷰 | qa-reviewer | 3 |

각 에이전트는 **자기 소유 폴더만 수정**한다. 다른 영역 수정이 필요하면 변경 요청을 남기고 오케스트레이터가 해당 에이전트에 전달한다.

### Phase 4 — 검증·보고

1. qa-reviewer가 `integration-gate` 체크리스트 실행 (`pytest`, 서버 기동, `npm run build`)
2. 🔴 필수 수정 → 소유 에이전트 재작업 → 재검증 (최대 2회)
3. 사용자에게 보고: 변경 파일, 실행 명령과 실제 결과, 남은 목업/가정, 다음 관문까지 남은 작업
4. 브랜치·PR 생성은 사용자 요청 시에만. PR 본문은 `.github/PULL_REQUEST_TEMPLATE.md` 형식

## 작업 규모별 모드

| 요청 패턴 | 모드 | 투입 |
| --- | --- | --- |
| "A 주차장 영상→점유→화면" (11/06 관문) | 수직 통합 | camera-roi + vision + backend + frontend + qa |
| "API 추가/수정" | 백엔드 | integration-pm + backend + qa |
| "요금/결제/매출" | 결제 | billing + backend + qa |
| "화면 만들어줘" | 프론트 | frontend (+ backend 응답 예시) + qa |
| "모델 학습/성능 개선" | 비전 | vision (+ camera-roi) + qa |
| "챗봇" | 챗봇 | chatbot + backend + qa |
| "가상 주차장 C/D" | 시뮬레이션 | backend + frontend + qa |
| "PR 전 점검", "통합 점검" | 검증 | qa 단독 |

단순 수정(한 파일, 한 영역)은 에이전트 팀 없이 해당 에이전트 1명 또는 직접 처리한다.

## 에러 핸들링

| 상황 | 대응 |
| --- | --- |
| 카메라/모델 미준비 | `file:` 소스·샘플 이미지·목업 이벤트로 진행, 보고에 "목업" 명시 |
| 계약 충돌 | 구현 중단, change request 작성 후 사용자 확인 |
| 소유 영역 밖 수정 필요 | 변경 요청으로 전달, 임의 수정 금지 |
| 테스트 실패 | 로그 그대로 보고, 원인 영역의 에이전트가 수정 |
| 코드 동결(11/12) 이후 신규 기능 요청 | P0 장애가 아니면 거절하고 사유 설명 |
| OCR 성능 부족 | vision-engineer가 대체 마커안 + 근거 수치 제시 → PM 결정 |
