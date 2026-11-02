"""시연 API/관리자 권한 경계. 팀 인증으로 교체할 수 있는 한 곳의 검사."""
import hmac
from ipaddress import ip_address
from . import config


class AccessDenied(Exception):
    def __init__(self, message, status=403):
        super().__init__(message)
        self.status = status


def require_admin(authorization, host, demo=False):
    if demo and not config.DEMO_ENABLED:
        raise AccessDenied("시연 기능은 비활성화되어 있습니다.", 404)
    if config.ADMIN_TOKEN:
        if not hmac.compare_digest(str(authorization or ""), "Bearer " + config.ADMIN_TOKEN):
            raise AccessDenied("관리자 접근 키를 확인해 주세요.", 401)
        return
    try:
        local = ip_address(host).is_loopback
    except ValueError:
        local = host == "testclient"  # FastAPI TestClient 전용
    if not local or config.DATA_SOURCE != "mock":
        raise AccessDenied("관리자 접근 키 설정이 필요합니다.")
