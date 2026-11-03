# PARK:ON 사용자·관리자 분리

## 접속 주소
- 사용자: http://127.0.0.1:8080/ (로그인 불필요)
- 사용자 모바일 앱: http://127.0.0.1:8080/?app=1
- 관리자 로그인: http://127.0.0.1:8080/admin/login
- 관리자 대시보드: http://127.0.0.1:8080/admin/ (미로그인 시 로그인 화면)

사용자 페이지에 관리자 선택/전환 버튼은 없습니다. 일반 방문자는 매출, 전체 결제 내역, 취소, 입차 세션 목록, 경보 조작 API를 사용할 수 없습니다. 사용자 모의 결제와 차량 위치/요금 조회는 기존 시연 기능으로 제공합니다. 사용자별 개인정보 인증까지 구현한 실결제 서비스는 아닙니다.

## 인증 구조
Nginx의 auth_request가 내부 admin-auth 서비스의 세션을 검사합니다. `/admin-api/demo/`, `/admin-api/team/`은 로그인한 관리자 전용입니다. 기존 공개 API 경로는 필요한 조회와 모의 결제만 명시적으로 허용하고 나머지는 403으로 차단합니다. 로컬과 외부 모두 같은 권한 검사를 적용합니다.

아이디는 admin입니다. 지정한 비밀번호는 `.local-access/로그인정보.txt`에 있습니다. 해당 경로는 Git에서 제외됩니다. 비밀번호를 소스에 하드코딩하지 않습니다.

관리자는 8시간 유효한 HttpOnly, SameSite=Strict 세션 쿠키로 로그인합니다. HTTPS에서는 Secure도 적용합니다. 로그아웃, 비밀번호 변경, 인증 서비스 재시작 시 기존 세션은 사용할 수 없습니다. 로그인 실패 횟수 제한 및 관리자 변경 요청의 동일 출처 검사를 적용했습니다.

비밀번호 무작위 교체:
```powershell
python scripts/setup_private_access.py --rotate
```

## 검증
```powershell
python scripts/verify_private_access.py
python scripts/verify_private_access.py http://127.0.0.1:8082
```
사용자 조회, 관리자 API 비로그인 차단, 정상/오류 로그인, CSRF 차단, 로그아웃, 위조 쿠키 차단을 검사합니다. 8082는 동일한 화면을 제공하는 로컬 점검용 포트입니다.

## 외부 접속은 아직 미개방
Cloudflare HTTPS 터널 생성은 자동 승인 검토에서 차단됐으며 제3자 트래픽 중계에 대한 승인이 아직 없습니다. 승인 전에는 시작하지 마세요.

승인 후 사용할 구성은 준비되어 있습니다:
```powershell
docker compose --profile external up -d --no-deps tunnel
docker compose logs --tail 50 tunnel
```
생성된 HTTPS 주소의 `/`은 사용자용, `/admin/login`은 관리자 로그인입니다. 관리자 주소를 직접 입력해도 로그인이 필요합니다. 주소를 숨기는 방식에 의존하지 않습니다. 외부에서 로그인할 때는 HTTPS를 사용하세요.

외부 접속 중지:
```powershell
docker compose stop tunnel
```
임시 주소는 시연용으로 PC와 Docker가 켜져 있어야 합니다. 지속 운영에는 고정 도메인과 개별 관리자 계정 등을 별도로 구성해야 합니다.
