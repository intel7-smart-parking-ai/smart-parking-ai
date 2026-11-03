# PARK:ON 사용자 웹·관리자 관제 데모

김채린 담당 화면의 React + Docker 시연 버전입니다. 기존 팀 서버와 초기 프론트엔드를 보존하기 위해 `frontend/parkon-demo/` 안에서 독립 실행합니다.

## 집 컴퓨터에서 실행

Git과 Docker Desktop(Linux 컨테이너)을 설치하고 Docker Desktop을 실행한 뒤 PowerShell에서 실행합니다.

```powershell
git clone --branch chore/parkon-react-docker-demo-20261103 https://github.com/intel7-smart-parking-ai/smart-parking-ai.git
cd smart-parking-ai/frontend/parkon-demo
.\scripts\start.ps1
```

브라우저에서 <http://127.0.0.1:8080/>을 엽니다. API 문서는 <http://127.0.0.1:8000/docs>입니다. PowerShell 실행 정책으로 스크립트가 차단되면 해당 스크립트 내용을 확인한 뒤 `powershell -ExecutionPolicy Bypass -File .\scripts\start.ps1`로 실행할 수 있습니다.

이미 저장소가 있다면 기존 작업을 보존한 뒤 `git fetch origin`, `git switch chore/parkon-react-docker-demo-20261103`로 브랜치를 선택하고 위 데모 폴더로 이동합니다. `.env`는 첫 실행 시 임의 DB 비밀번호로 자동 생성됩니다. 교육장 DB 데이터와 비밀번호는 저장소에 포함되지 않으므로 집에서는 새 시연 DB가 만들어집니다.

## 포함 기능

- A·B 각 12면, C·D 각 10면: 총 44면 지도, 큰 주차칸 번호, 초록색 전기차·파란색 장애인 구역 표시.
- 사용자 차량 위치·요금 조회·모의 결제·영수증, 관리자 결제 취소·기간별 매출·경보 확인/해제.
- 라이트/다크 테마, 모바일 화면, React 19/Vite 7, Nginx, FastAPI, PostgreSQL.
- 서버 멱등 처리와 DB 제약으로 중복 결제 방지, 결제·취소·경보 영속 저장.

## 공식 서버와의 경계

`backend/`는 UI 개발을 위한 독립 목업 서버입니다. 팀 공통 `backend/`와 공식 API/DB 계약을 변경하지 않습니다. 요금은 `server_fixture`로 표시되는 고정 110분 샘플(A 5,000원/B 6,000원/C 2,600원/D 3,200원)입니다. 프론트엔드는 서버 응답만 표시하며 요금을 재계산하지 않습니다.

윤명수 담당 공식 요금·매출 모듈은 아직 연결되지 않았습니다. 요금 정책은 사용자 제공 A/B/C/D 기준을 시연 데이터로 사용합니다. 면수와 응답 필드 역시 이 데모의 기준이며 최종 팀 계약 확정을 뜻하지 않습니다. 실제 PG·금전 거래, 인증/RBAC, AI 카메라, 챗봇은 연결되지 않았습니다. 사용자/관리자 전환은 시연용이며 접근 제어 기능이 아닙니다. 외부 공개 운영용 서버가 아닙니다.

팀 서버로 통합할 때는 PM·서버·요금 담당자와 응답 스키마, 오류/인증, 견적 식별자, 결제 취소와 매출 기준을 합의하고 `frontend/src/api.js` 및 배포 프록시를 연결해야 합니다. 목업 DB를 공식 데이터로 이관하지 않습니다.

## 실행 및 검증 문서

[DOCKER_GUIDE.md](DOCKER_GUIDE.md)에 개발 모드, 중지·재시작, 백업, 오류 대응, 공식 서버 연결 범위가 있습니다. 해당 문서의 '프로젝트 루트'는 이 데모 폴더입니다. 문서에 언급된 기존 `parking-ui/`와 Sites 배포본은 이전 별도 시연본이며 이 패키지에는 포함하지 않았습니다.

2026-11-03 로컬 검증: Docker 프로덕션 React 빌드 성공, Nginx 설정 검사 통과, HTTP 통합 점검 16개 통과, DB/API 재시작 후 결제 취소·경보 기록 유지 확인. 팀 기존 서버의 pytest는 이 패키지 검증에 포함하지 않았습니다. 카메라·공식 요금·챗봇 통합 관문은 미완료입니다.

```powershell
python scripts/smoke_test.py
docker compose restart db api
docker compose up -d --wait
python scripts/verify_persistence.py
```

테스트는 22번 차량의 모의 결제를 만들고 취소하며 경보 테스트 기록을 남깁니다. Docker 프로젝트 이름은 `parkon`이고 포트 8080/8000(개발 모드 5173)을 사용하므로 기존 `parkon` 데모나 같은 포트의 서버와 동시에 실행하지 않습니다. 변경을 되돌리려면 데모에서 `docker compose --profile dev stop`으로 중지하고 이전 브랜치로 전환합니다. DB 볼륨은 보존됩니다.
