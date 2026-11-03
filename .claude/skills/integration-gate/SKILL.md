---
name: integration-gate
description: "통합 관문 및 PR 전 점검 체크리스트. 11/06 중간 점검(A 주차장 영상→점유→API→화면), 11/10 운영 기능 통합, 11/12 코드 동결·리허설의 인수 기준과, 모든 PR 전에 실행할 테스트·경계 위반 검사 명령을 제공한다. 'PR 올리기 전 점검', '통합 점검', '시연 준비 확인', '관문 체크' 시 사용한다."
---

# Integration Gate — 관문·PR 점검

qa-reviewer가 주로 사용한다. 결과는 실제 실행 출력으로만 판정한다.

## A. PR 전 공통 점검 (저장소 루트, PowerShell)

```powershell
.\.venv\Scripts\python.exe -m pytest tests/ -q
cd frontend; npm run build; cd ..
git status --short          # .env, 이미지/영상, *.pt/*.onnx, *.db 가 없는지
git diff --stat main...HEAD # 내 담당 폴더 밖 변경이 있는지
```

경계 위반 검색 (Grep):
- `VideoCapture` → `backend/app/camera` 및 `smart_parking_dataset_capture/` 외에서 나오면 🔴
- `backend/app/vision/**`에서 DB 세션/`session.add`/`INSERT` → 🔴
- `frontend/src/**`, `backend/app/chatbot/**`에서 요금 계산식(`extra_fee`, `unit_minutes` 연산) → 🔴
- `car_id`를 `int(`로 변환 → 🔴 (문자열 규칙 위반, 두 자리 형식 검증 누락)
- `실제 화재`, `fire detected` 같은 표기 → 🟡 "모의 화재"로

PR 본문: `.github/PULL_REQUEST_TEMPLATE.md` (Jira 링크, 영향 영역 체크, 실행/테스트 결과, 복구 방법).

## B. 11/06 중간 점검 ① — A 주차장 수직 통합

| # | 확인 | 방법 |
| --- | --- | --- |
| 1 | 카메라 A 프레임 수신 (또는 `file:` 녹화 재생) | Frame Hub 로그 FPS |
| 2 | 차량 탐지 → ROI 매칭 → vision-event 전송 | 서버 로그/`vision_events` |
| 3 | 점유 안정화 후 `GET /parking-lots/A/spaces` 반영 | curl/Swagger |
| 4 | 관리자 화면 주차칸 맵 색상 갱신 (≤5초) | 화면 녹화 |
| 5 | 차량 제거 시 `vacate_confirm_s` 후 empty | 시연 |
| 6 | 모델별 1차 평가 리포트 존재 | `_workspace/vision_*_eval.md` |

→ 이 날 성능 목표 수치와 카메라 위치 최종 합의.

## C. 11/10 운영 기능 통합

- B 주차장 + 가상 C가 같은 API로 조회됨 (`GET /parking-lots` 3개 이상)
- 차량 위치 검색 → 예상 요금 → 모의 결제 → 관리자 매출 반영 → 취소 시 차감
- LED 점등 → 경보 생성 → 관리자 확인(acknowledge)
- 챗봇: "A 빈자리?", "12번 차 어디?", "요금 얼마?" 가 API 값과 일치
- 사용자 모바일(360px) / 관리자 화면 모두 실데이터 연결

## D. 11/12 코드 동결·리허설

- 지침서 11장 시나리오 전부 실행 결과 기록 (가림, 재연결, 중복 결제, 오탐, OCR null)
- 신규 기능 금지, P0 장애만 수정
- 백업 시연: 녹화 영상 `file:` 소스로 전체 흐름 재생 가능
- 최종 제출물: 소스, README 실행 명령, 모델/데이터셋 위치·해시, 테스트 결과, 시연 영상

## 보고 형식 `_workspace/review_<날짜|주제>.md`

```
## 실행 결과
- pytest: N passed / M failed (로그 요약)
- build: 성공/실패
## 관문 체크
| # | 항목 | 결과 ✅/❌ | 증거 |
## 지적 사항
🔴 ... / 🟡 ... / 🟢 ...
```
