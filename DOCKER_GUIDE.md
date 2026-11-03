> 현재 실행 기준: `scripts/start.ps1`으로 API·DB와 관리자 인증을 준비한 뒤 웹을 실행합니다. 사용자 화면은 `/`, 앱은 `/?app=1`, 관리자 로그인은 `/admin/login`입니다. 초기 계정은 로컬 `.local-access/로그인정보.txt`에서 확인하며 Git에는 포함하지 않습니다. 사용자·관리자 권한 및 외부 접속 상태는 [PRIVATE_ACCESS.md](PRIVATE_ACCESS.md)를 우선 참고하세요. 매출은 달력/표 보기와 CSV 다운로드를 제공합니다. 아래의 초기 구축 기록과 다른 경우 이 안내를 우선합니다.

> 최신 안내: 사용자 화면은 최종 4개 주차장(실물 1·2번, 가상 3·4번, 총 50면)으로 통일했습니다. 기존 ?source=team 주소도 같은 화면입니다. 팀 공통 서버의 /api/v1 경로는 유지하고, 사용자 화면은 공식 통합 전까지 /demo-api/v1 목업을 사용합니다.

> 2026-11-03 업데이트: 현재 실행 폴더는 이 저장소 루트입니다. frontend/가 기본 React 화면, backend/가 팀 서버, dev/parkon-api/가 독립 목업입니다. 기본 화면은 http://127.0.0.1:8080/?source=demo 이며 팀 조회는 ?source=team 으로 선택합니다. 아래 초기 구축 기록보다 [PDF 도면](docs/PDF_LAYOUTS.md), [차량 검색](docs/VEHICLE_NUMBER_SEARCH.md), [결제 수단](docs/MOCK_PAYMENT_METHODS.md)의 최신 설명을 우선합니다.

# PARK:ON Docker 실행·개발 안내

작성일: 2026-11-02. 이 파일이 현재 로컬 Docker 환경의 실행 기준입니다.

## 1. 접속 주소

| 주소 | 용도 |
| --- | --- |
| http://127.0.0.1:8080 | React 프로덕션 빌드 + Nginx |
| http://127.0.0.1:5173 | React + Vite 개발 화면, 소스 수정 자동 반영 |
| http://127.0.0.1:8000/docs | FastAPI 대화형 API 문서 |
| http://127.0.0.1:8000/api/v1/health | API·PostgreSQL 연결 상태 |

이 PC에서는 localhost가 IPv6(::1)를 먼저 조회해 접속이 지연될 수 있으므로 위의 127.0.0.1 주소를 사용합니다.

기본 구성은 web/api/db 3개 서비스입니다. 추가 `dev` 프로필은 frontend-dev 서비스를 실행하며 같은 API와 DB를 사용합니다. 모든 공개 포트는 이 PC의 127.0.0.1에만 바인딩됩니다. DB 포트는 호스트에 공개하지 않습니다.

기존 `parking-ui/`와 Sites 배포본은 보존했습니다. 이번 React + Docker 버전은 루트의 `frontend/`, `backend/`, `compose.yaml`을 사용합니다. Sites 링크는 이 로컬 Docker 서버를 자동으로 가리키지 않습니다.

## 2. 시작·상태·중지

프로젝트 루트에서 PowerShell로 실행합니다.

```powershell
# 첫 실행: .env가 없으면 임의 DB 비밀번호 생성 후 빌드/시작
.\scripts\start.ps1

# 기본 서비스 시작
docker compose up -d --build --wait

# Vite 개발 화면 추가 시작
docker compose --profile dev up -d --build --wait frontend-dev

# 전체 상태
docker compose --profile dev ps

# 로그
docker compose logs --tail 100 api
docker compose logs --tail 100 web
docker compose logs --tail 100 db
docker compose logs --tail 100 frontend-dev

# 실행 중지: 컨테이너와 DB 데이터를 유지
docker compose --profile dev stop

# 재시작
docker compose up -d --wait
docker compose --profile dev up -d --wait frontend-dev
```

`.env`에는 로컬 DB 접속 비밀번호가 있고 Git에서 제외됩니다. `.env.example`은 형식 참고용입니다. 기존 데이터 볼륨을 재사용할 때 `.env`의 비밀번호를 임의로 바꾸지 마세요. PostgreSQL의 초기 사용자 비밀번호는 기존 볼륨을 다시 시작한다고 자동 변경되지 않습니다.

`docker compose down`은 컨테이너/네트워크를 제거하지만 이름 있는 DB 볼륨은 남깁니다. `down -v`는 DB 볼륨까지 삭제하므로 일반 종료에 사용하지 않습니다. 시스템 전체 prune 명령은 이 프로젝트 운영에 필요하지 않습니다.

## 3. 서비스 구조

```text
브라우저 :8080 → Nginx
                 ├─ React 빌드 정적 파일
                 └─ /api/* → api:8000 FastAPI → db:5432 PostgreSQL

브라우저 :5173 → Vite 개발 서버 → /api/* → 같은 FastAPI/DB
```

Compose healthcheck로 DB 준비 → API 준비 → 웹 시작 순서를 확인합니다. 프런트엔드는 `/api/v1/...` 상대 경로만 호출하며 브라우저에서 Docker 내부 호스트명 `api` 또는 `db`로 직접 접속하지 않습니다.

PostgreSQL 볼륨은 `parkon_parkon_db`입니다. 결제/취소/경보는 브라우저 localStorage가 아닌 이 DB에 저장됩니다. 현재 50면과 샘플 세션/거래는 API 시작 시 충돌 없이 한 번 등록되고 재시작 때 기존 거래 상태를 덮어쓰지 않습니다. 스키마 초기화/버전 기록은 `dev/parkon-api/app/schema.sql`에 있습니다. 후속 구조 변경에는 별도 마이그레이션을 추가해야 합니다.

## 4. 코드 수정

| 위치 | 담당 |
| --- | --- |
| frontend/src/App.jsx | React 상태/화면/API 연결 |
| frontend/src/components.jsx | 지도, 주차칸, 차량 SVG, 상세 창, 검색 |
| frontend/src/api.js | HTTP API 어댑터/오류/타임아웃 |
| frontend/src/styles/ | 기존 밝은/어두운 디자인과 운영 화면 스타일 |
| frontend/vite.config.js | Vite 개발 프록시/HMR |
| frontend/nginx.conf | 프로덕션 웹/API 프록시 |
| backend/app/main.py | FastAPI 엔드포인트·DB 트랜잭션 |
| backend/app/db.py | DB 접속·초기 데이터 등록 |
| backend/app/billing.py | 서버 측 요금 제공자 경계·현재 샘플 견적 |
| backend/app/schema.sql | DB 스키마 |
| compose.yaml | 서비스/포트/환경/볼륨/상태 점검 |

프런트 소스를 수정할 때 5173 개발 화면에 자동 반영됩니다. 8080은 빌드된 화면이므로 갱신하려면 다음을 실행합니다.

```powershell
docker compose up -d --build --wait web
```

API 소스 수정 후에는 다음을 실행합니다.

```powershell
docker compose up -d --build --wait api
```

패키지를 추가할 때는 컨테이너의 npm을 사용할 수 있습니다.

```powershell
docker compose exec frontend-dev npm install 패키지이름
```

`frontend/package-lock.json`과 `backend/requirements.lock`은 검증된 의존성 버전입니다. 의존성을 변경하면 재빌드하고 검증합니다. Vite의 node_modules는 별도 개발 볼륨에 두어 Windows의 모듈 설치와 섞지 않습니다.

## 5. 요금·매출 SSOT와 현재 범위

공식 요금/매출 SSOT는 윤명수 님의 서버 모듈입니다. React에는 시간 기반 요금 계산이나 매출 합계 계산을 넣지 않았습니다.

현재는 공식 모듈 소스/API가 없어 `BILLING_PROVIDER=fixtures`로 실행됩니다. `backend/app/billing.py`가 110분 고정 샘플 견적을 반환합니다. A 5,000원, B 6,000원, C 2,600원, D 3,200원입니다. 초 단위 반올림/일일 상한을 실제 계산하는 공식 함수의 정확성을 이 시연으로 검증한 것은 아닙니다.

| 주차장 | 구성 | 기본료 | 추가료 | 일일 최대 |
| --- | --- | --- | --- | --- |
| A | 실물 12면 A-01~12 | 30분 1,000원 | 10분당 500원 | 10,000원 |
| B | 실물 12면 B-01~12 | 30분 1,200원 | 10분당 600원 | 12,000원 |
| C | 가상 10면 C-01~10 | 60분 1,000원 | 15분당 400원 | 8,000원 |
| D | 가상 10면 D-01~10 | 30분 800원 | 10분당 300원 | 7,000원 |

공식 모듈을 받으면 billing provider와 매출 집계 경계를 그 모듈 호출로 교체합니다. `calculate_parking_fee_pure`를 React에 복제하지 않습니다. 알 수 없는 provider 설정은 명시적으로 실패하며 임의의 공식 금액을 생성하지 않습니다.

현재 구현된 것은 로컬 시연용 모의 결제/취소와 SQL 매출 집계입니다. 실제 PG, 실제 돈의 이동, 실제 고객 인증, 카메라/AI, 챗봇은 연결하지 않았습니다. 사용자/관리자 버튼은 시연 모드 전환이며 인증이 아닙니다. 다른 PC나 인터넷에 공개하기 전에는 인증·권한·TLS와 공식 요금 모듈 연동이 필요합니다.

결제는 서버 견적 금액만 사용합니다. 클라이언트가 보낸 임의 금액은 사용하지 않습니다. idempotency_key와 DB 잠금/고유 제약으로 같은 세션의 동시 중복 결제를 방지합니다. 취소는 멱등 처리하며 원결제 기간의 순매출에서 차감합니다. 결제 완료와 출차는 별도입니다.

## 6. 화면 체험

1. 8080 또는 5173에서 사용자 → 요금·모의 결제로 이동합니다.
2. 차량 `07`을 조회해 A-12 위치와 5,000원 견적을 확인합니다. `12`는 B, `15`는 C, `19`는 D 샘플입니다.
3. 모의 결제 후 영수증을 확인합니다. 이미 결제 완료된 차량은 추가 결제를 하지 않습니다.
4. 관리자 → 매출 통계에서 일별/월별/주차장별 금액을 조회합니다. 화면은 서버가 집계한 결과를 표시합니다.
5. 관리자 → 입출차·결제에서 방금 결제를 취소하고 매출에서 차감됐는지 확인합니다.
6. 관리자 → 안전·경보 → LED 경보 시연 → 확인 처리 → 경보 해제 순서로 실행합니다.
7. 브라우저 새로고침 또는 API/DB 재시작 뒤에도 결제와 경보 이력이 남는지 확인합니다.

시연 시각은 2026-11-02 14:30으로 고정되어 있습니다. 매출 조회는 2026년 11월 또는 11월 2일로 테스트합니다. 15초마다 읽기 데이터를 갱신하며 입력/처리/상세 창 사용 중에는 자동 갱신을 미룹니다.

## 7. 검증

```powershell
# 실행 중인 Nginx→API→DB 통합 테스트
python scripts/smoke_test.py

# 위 테스트 이후 API/DB 재시작 후 영속성 확인
docker compose restart db api
docker compose up -d --wait
python scripts/verify_persistence.py
```

smoke 테스트는 테스트용 22번 차량에 모의 결제를 만들고 자신이 만든 거래를 취소합니다. 동시 중복 요청, 조작 금액 무시, 실패 재시도, 취소/집계, 경보 상태를 검증합니다. 테스트 기록은 DB에 남습니다. 22번 차량에 별도 미취소 거래가 있으면 먼저 그 거래 상황을 확인해야 합니다.

## 8. 백업

```powershell
New-Item -ItemType Directory -Force backups
docker compose exec -T db pg_dump -U parkon -d parkon -Fc -f /tmp/parkon-backup.dump
docker compose cp db:/tmp/parkon-backup.dump ./backups/parkon-backup.dump
```

`.env`도 별도로 안전하게 보관해야 같은 DB 연결 정보를 재사용할 수 있습니다. 백업 파일에는 테스트 거래와 경보 데이터가 포함됩니다.

## 9. 오류 대응

| 증상 | 확인·조치 |
| --- | --- |
| Docker 연결 오류 | Docker Desktop이 실행 중인지 확인 후 `docker version` 실행 |
| API 연결 중에서 멈춤 | `docker compose ps`, `docker compose logs --tail 100 api` 확인 |
| 8080은 열리지만 API 실패 | `/api/v1/health` 확인, api/db 건강 상태와 Nginx 프록시 확인 |
| 5173 접속 실패 | dev 프로필의 frontend-dev를 시작했는지 확인 |
| 화면 수정이 8080에 안 보임 | 5173 개발 화면에서 확인하거나 web 이미지를 재빌드 |
| 요금이 시간에 따라 바뀌지 않음 | 현재 fixture는 110분 고정 샘플. 공식 서버 모듈 연동 필요 |
| 결제 완료 차량을 다시 결제할 수 없음 | 정상 중복 방지. 관리자에서 테스트 결제를 취소한 후 재조회 |
| DB 비밀번호 변경 후 접속 오류 | 기존 볼륨의 DB 비밀번호와 .env 값 일치 여부 확인. 볼륨 삭제로 우회하지 않음 |
| 매출 0원 | 조회 기간/주차장/취소 상태 확인. 시연 거래는 2026년 10~11월 기준 |

빌드와 실행은 로컬 Docker에서 수행하며 Sites에 별도로 배포하지 않습니다.
