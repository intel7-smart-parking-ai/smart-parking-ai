# 카메라 제외 UI·서버 통합 결과

기준: 팀 main 683f0f29 및 PR #46 head 5f365275 경보 구현의 로컬 통합. PR #47은 문서 참고 자료이며 코드 병합이 아니다. 원격 main·PR 상태·Jira는 변경하지 않았다.

## 실행

```powershell
docker compose up -d --build --wait
```

- 사용자 웹: http://127.0.0.1:8090/
- 모바일: http://127.0.0.1:8090/?app=1
- 관리자: http://127.0.0.1:8090/admin/
- 기존 관리자 계정 파일을 기본 사용한다. 다른 PC에서는 ADMIN_HTPASSWD_PATH를 기존 계정의 htpasswd 파일 경로로 설정한다. 비밀번호 파일은 저장소에 넣지 않는다.
- API는 외부 포트를 열지 않고 nginx 뒤에서 실행한다. 관리자 조회·변경 경로는 기존 세션 인증을 사용한다.
- `PARKING_RUNTIME=integrated`일 때 `backend.app.main:app`이 통합 앱을 실행한다. 미설정시 기존 main 동작을 보존한다.
- `SMART_PARKING_DB_PATH=/data/smart_parking.db`, Docker `parking_data` 볼륨 보존. `docker compose down -v`는 이력을 삭제하므로 일반 중지는 `docker compose stop`을 사용한다.

## 반영 내용

- 사용자 웹·모바일·관리자 UI 통합, A12/B16/C12/D64면 및 D 4구역 지도.
- 팀 billing 계산·견적·모의 결제·취소·매출 구현 그대로 재사용. 별도 고정 샘플 요금·PostgreSQL 대체 계산은 최종 통합본에서 사용하지 않는다.
- 모바일 결제 수단 선택·모의 결제·영수증, 실제 결제 금액과 영수증 재조회.
- PR #46 경보 모듈을 재사용하여 관리자 모의 경보 생성 어댑터 연결. 카메라/LED 수신기 연결은 제외.
- 현재 세션 `status=active` 명시, 별도 active/closed 이력 화면, 칸별 `entry_at`과 모의 출처 표시.
- C/D 수동·자동 입출차와 이력, SQLite 세션·결제·경보·시뮬레이션 보존.
- 공통 validation 오류, 실제 금전 청구 없음·카메라 미연결 안내.

## 직접 실행한 검증

- 백엔드 전체: 342 passed (기존 팀 테스트 + PR46 경보 + 통합 E2E). Starlette TestClient 의존성 사용 중단 예고 경고 1개.
- 프론트엔드: 41 passed, production 빌드 성공.
- 통합 E2E: 가상 입차→위치·입차시각→서버 견적→결제→멱등 재요청→영수증→매출→취소→경보 생성/확인/해제→출차→재시작 후 이력 보존.
- 브라우저: 웹 스티커10 검색, 모바일 스티커10 조회→모의 카드→결제 완료 영수증 표시.
- 실제 컨테이너 재시작 전후 세션 5건·결제 1건 동일. 브라우저 검증에서 생성한 모의 결제는 테스트 이력으로 남아 있다.

## 범위

카메라·ROI·AI 인식 및 vision-event 수신기 연결, 챗봇 변경은 포함하지 않는다. A/B는 모의 초기 데이터이며 C/D는 서버 시뮬레이션이다. 팀의 기존 vision 이벤트·점유 엔진과 demo_main은 보존했으며, 통합 앱에서 실제 카메라 관측을 받고 있다고 표시하지 않는다. 기존 PostgreSQL 데모 DB는 이 SQLite DB로 자동 이전하지 않는다. 팀 소스의 billing/세션/경보 모듈과 연결하는 운영 어댑터는 `backend/app/integrated.py`에 분리했다.
