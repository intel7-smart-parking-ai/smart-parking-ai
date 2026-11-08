# Windows 복구 실검증 · 2026-11-08 KST

## 복구 출처와 범위

- 이전 ChatGPT 대화 첨부 `1-chatbot-v3-offline-fix-20261108.bundle`을 읽어 별도 작업 경로에 복사했다. 원본 첨부와 Downloads 사본은 변경하지 않았다.
- 첨부와 Downloads 사본의 SHA256은 동일하다: `3d0bb907a43fb7ed4498df7e6e2ba8b5db33a8ff9ce993cc7f6e80337c04331b`.
- `git bundle verify` 성공, 선행 bundle이 필요 없는 전체 이력.
- 포함 ref는 `refs/heads/chore/chatbot-v3-recovery-20261107` 하나이며 커밋은 `dbb9fe09db7e3e22e908583e21c18e5043f6055a`.
- `8cc7fd4`는 bundle에 없다. 원격 작업 브랜치는 확인 당시 원본 `828e9e4caa9ad4a26bbae1e932daa3e64de2fd3b`, 백업 원격은 브랜치 없음이었다. 두 원격에서 축약 커밋을 조회했지만 찾지 못했다. 원본 GitHub commit API는 422, 백업은 404였다. 이는 커밋의 전 세계 부재를 증명하지 않는다.
- 최신 ZIP `parkon-place-smoke-backup-20261108.zip`은 이번 첨부나 확인한 로컬 다운로드에서 확보하지 못했다. 정확한 이후 커밋별 diff는 해당 백업 또는 전체 SHA/접근 가능한 ref가 있어야 복구할 수 있다.

## 인계 기록과 실제 파일 대조

| 항목 | 실제 복원 상태 |
|---|---|
| 독립 chatbot_v3, fixture preview, 34문항/채점기 | 존재 |
| 명시적 export 전용 Langfuse 코드 | 존재, 전송하지 않음 |
| strict 함수 출력, preference 규약, 실패 사용량 보존 | dbb9fe0에 존재 |
| 22/34 → 31/34 → 32/34 실제 평가 | 이번 bundle에서 결과를 복구하지 못함. 대화 인계 기록이며 재현 결과가 아님 |
| 차량 kind/value 묶음 개선 | 이 버전은 lookup_type/vehicle_value 별도 필드. 후속 개선 복원 안 됨 |
| 130개 관련 시험, 6문항 실제 의미 분류 4/6 | 이 bundle의 검증 결과가 아님 |
| 오타 2개 차단·정상 4개 허용 후속 서버 방어 | 후속 원본 복원 안 됨. 이번 장소 제한은 별도 신규 구현 |
| 요청별 장소 스키마 제한 | dbb9fe0에 없으며 이번 복구 후 새로 구현 |

HANDOFF.md, KEYLESS_PREVIEW.md, evaluation/ACCEPTANCE.md,
OBSERVABILITY_PREPARATION.md, REVIEW_2026-11-07.md,
MODEL_CONNECTION_2026-11-08.md를 실제 읽고 비교했다.

## 실행 환경과 원본 기준선

- Windows, Python 3.11.9 별도 가상환경, `backend/requirements-test.lock` 설치.
- `CHATBOT_V3_MODEL_MODE=fixture`. 유료 모델 요청과 Langfuse 외부 전송 없음. 비밀 파일을 읽거나 옮기지 않았다.
- 수정 전 dbb9fe0 별도 checkout에서 관련 6파일 **51 passed, 13 warnings**. Windows 기본 인코딩 영향 방지를 위해 이 기준선 실행에는 `PYTHONUTF8=1`을 사용했다.
- 실행 제한 안에서는 Python asyncio의 로컬 socketpair 생성이 멈췄다. 로컬 통신이 가능한 실행 조건에서 동일 시험이 완료됐다. 이를 앱 로직 장애로 수정하지 않았다.
- Node 24.19.0 / npm 11.17.0, frontend `npm ci --no-audit --no-fund`, `npm test` **78 passed**, `npm run build` 성공. 500 kB 초과 번들 경고가 남았다.
- 조회 통합 시험은 임시 SQLite 및 TestClient를 사용한다. 사용자의 기존 DB를 삭제/초기화하지 않았다. 상시 조회 서버는 실행하지 않았으므로 임의의 BACKEND_BASE_URL을 정상 서버라고 보고하지 않는다.

## Git 저장 구분

- 복원은 별도 로컬 저장소에서 수행했으며 main을 변경하지 않았다.
- 두 원격에 대한 작업 브랜치 `git push --dry-run`은 성공했다. 이는 쓰기 경로 예행 검사이며, 실제 저장 및 원격 SHA 일치는 최종 복구 보고서에 별도로 기록한다.
- 강제 push, 기존 원본/DB 삭제, 키/.env 추가는 수행하지 않는다.
- 원본 bundle, 신규 커밋 bundle, 다운로드용 소스 ZIP, 원격 저장은 각각 별도 결과물이다. ZIP만으로 Git 이력까지 복구됐다고 주장하지 않는다.

## 신규 변경 검증

- 관련 7개 Python 시험 파일: **102 passed, 13 warnings / 12.91초**.
- 마지막 전체 조회 부정 표현 assertion 추가 후 새 후보 시험 파일: **51 passed**.
- 기존 34문항 중 기대 lot_id가 명시된 11문항의 장소 후보 포함: **11/11**. 모델 정답률이 아니다.
- 기존 경고는 Starlette/httpx 사용 중단 예고이며 이번 작업에서 의존성 버전을 바꾸지 않았다.
- 전체 저장소 회귀 시험, 실제 모델 평가, 브라우저 화면 실기는 이번 결과에 포함하지 않는다.

신규 장소 후보 제한의 시험 범위와 한계는 `REQUEST_PLACE_SCHEMA_2026-11-08.md`를 참고한다. 이번 오프라인 검증을 실제 모델 정답률 또는 동일 모델 3회 반복 검증으로 집계하지 않는다.
