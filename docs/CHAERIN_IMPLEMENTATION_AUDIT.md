# 김채린 통합 구현 확인 — 2026-11-04

검토한 GitHub main: `5ff9242`. 화면은 미병합 Draft PR #52 (`7489204`)에 구현되어 있어 그 브랜치를 기반으로 검증했다. main에 전체 구현이 완료된 상태는 아니었다. 팀 main 병합은 PM 리뷰 후 진행한다.

| 요청 | 확인 및 반영 |
| --- | --- |
| 1 현재 입차 | PR #52: `/sessions?status=active`, 별도 출차 이력 |
| 2 모의 표시 | PR #52: `is_mock`, `source=simulation/manual` 표시 |
| 3 라 4구역 지도 | PR #52: space_id 접두어 A/B/C/D; 64면. 모바일 도움말도 4구역 64면, 옛 L자형 10면 문구 없음 |
| 4 확인 필요 | PR #52: uncertain 별도 색상·문구, 빈자리 제외 |
| 5 경보 | PR #52: level 2 대피·결제 비활성·모의 표시. 통합 API에서도 결제 직전 경보 검사 |
| 6 KST | PR #52: Asia/Seoul 명시 변환 |
| 7 사용자 기능 | PR #52: 10~99 문자열 검증, 견적 화면 2분 초기화, 모의 결제·영수증. 서버 견적 TTL은 D-09의 300초 |
| 8 챗봇 화면 유지 | ChatPanel 및 챗봇 소유 코드 변경 없음. main의 PR #45 등록 유지, 통합 앱에도 동일 router 등록 |
| 9 Vite | PR #52: demo-api 8003, 관리자 8090 |
| 10 점유 엔진 통합 | 기존 create_demo_app / OccupancyExperiment 재사용. C/D 시뮬레이터는 인증된 vision-events로 관측 전송. 시뮬레이터는 세션·점유 테이블에 직접 쓰지 않음 |
| 11 main / Dockerfile / 환경변수 | `main.py`에 mock/demo/real 구성; API_HOST/API_PORT/VISION_TOKEN, 영속 SMART_PARKING_DB_PATH. 기존 mock 기본 동작은 토큰 없는 개발·회귀 호환용 |
| 12 LED 경보 | 시연 서버와 통합 서버 모두 `mock_fire_led_observed` → 기존 `ingest_led_fire_event()` |
| 13 entry_at | 칸 응답에 추가. uncertain은 활성 세션을 종료하지 않으며 통합 API에서 entry_at 보존 |
| 14 챗봇 | PR #45 main 병합 확인. 같은 API·DB 조회 구성. 실제 OpenRouter·음성·담당자 연결 검수는 별도 |
| 15 Docker | api/web/admin-auth 3서비스, SQLite 볼륨. 별도 시연 서버·PostgreSQL 없음 |
| 16 카메라 경로 | nginx 80에서 `/api/v1/internal/vision-events` POST와 Bearer 토큰을 API로 전달 |
| 17 웹 80 | 사용자 `/`, 관리자 `/admin/`. 호스트 8090은 관리자 게이트웨이 전용 |

## 검증과 한계

- 프론트 Node 테스트: 47 통과. team 모드 Vite 프로덕션 빌드 및 PWA 생성 성공.
- 백엔드 전체 pytest 최종 결과: **350 통과**, 실패 0, Starlette/httpx 사용 중단 예정 경고 1개. 인증·중복 이벤트·입출차·요금·결제·경보·재시작 복구, 확인 이벤트 저장 중단 복구, 영구 거절 이벤트 후 재시작 테스트 포함.
- 실제 `python -m backend.app.main` 기동: 임시 포트 18082에서 health 200, 4개 주차장, chat 및 vision-events 경로 등록 확인 후 종료.
- Docker 실행 파일이 이 PC에 없으므로 이미지 빌드·nginx 설정 검사·컨테이너 간 연결은 미실행. `deployment/check_gateway.py`는 Docker 설치 PC에서 실행할 검증 도구이며 이번 실행 결과로 주장하지 않는다.
- 실제 카메라 모델·번호 스티커 학습·OpenRouter 키 기반 LLM·음성 품질은 이번 통합 검증 범위가 아니다. 카메라·챗봇 담당자와 리허설이 필요하다.
- `plate_number`, `vehicle_number`, 시뮬레이터 API는 선택적 통합 확장이다. 실물 car_id에 가상 번호판을 넣지 않으며 기존 계약 필드를 변경하지 않는다. 팀 공통 계약 편입은 PM 검토 대상이다.

## 실행

Docker Desktop 실행 후 저장소 루트에서:

```powershell
python deployment/setup_local.py
docker compose up --build -d
```

setup은 관리자 계정을 입력받아 로컬 해시 파일과 임의 토큰 `.env`를 생성한다. 기존 파일은 덮어쓰지 않는다. `.env`와 `.local-access/`는 Git 제외 대상이다.

- 사용자: http://127.0.0.1/
- 관리자: http://127.0.0.1/admin/
- 다른 카메라 PC: `http://<서버 LAN IP>/api/v1/internal/vision-events`, 동일 VISION_TOKEN. 서버 방화벽에서 포트 80 접근 허용 필요.
- `PARKING_MODE=real`은 C/D 자동 관측과 모의 경보 버튼을 끈다. 변경 후 컨테이너를 다시 생성한다.
- 백업은 API를 중지한 상태에서 SQLite 볼륨 전체를 보관한다. `docker compose down`은 볼륨을 유지한다.

11/06 시연 서버는 기존처럼 별도 터미널에서 실행할 수 있다 (Docker API와 다른 격리 DB):

```powershell
$env:DATA_SOURCE="api"
$env:BACKEND_BASE_URL="http://127.0.0.1:8002"
python -m backend.app.demo_main
# 터미널 2: 같은 토큰 입력
python -m backend.app.simulation.run_parking_demo --short --hold
# 터미널 3
cd frontend
$env:VITE_DATA_SOURCE="team"
$env:API_PROXY_TARGET="http://127.0.0.1:8002"
npm ci
npm run dev
```

입차 후 A 빈자리 9/12, 10번 A-01 조회→요금→모의 결제→관리자 active 세션을 확인한다. hold를 중단한 뒤 `--short --depart --keep-empty`로 출차·세션 closed를 확인한다. VISION_TOKEN 환경변수를 지정하면 서버와 스크립트 모두 입력 대신 같은 값을 사용한다. 시연 서버 DB는 기존 임시 DB이며 종료 시 삭제된다. 통합 main/Docker는 영속 DB이다.

Docker 없이 통합 main을 실행하려면 `PARKING_MODE=demo`, ASCII 16자 이상 `VISION_TOKEN`, `DATA_SOURCE=api`, `BACKEND_BASE_URL=http://127.0.0.1:8000`을 설정하고 `python -m backend.app.main`을 실행한다. 토큰이 설정되면 기본 모드도 demo다. 수신기 메모리 상태를 공유하므로 **단일 프로세스 / workers=1**로 실행한다.

롤백: 해당 PR 커밋을 revert하고 컨테이너를 재빌드한다. DB·자격증명 파일은 보존한다.
