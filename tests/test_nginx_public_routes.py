"""공개 nginx 설정(11/10 점검): 공용 요청 한도와 챗봇 알림 폴링 경로."""
import re
from pathlib import Path

CONF = (Path(__file__).resolve().parents[1] / "frontend" / "nginx.conf").read_text(encoding="utf-8")


def _block(prefix):
    start = CONF.index(prefix)
    return CONF[start:CONF.index("\n  }", start)]


def test_shared_request_limit_fits_many_phones_behind_one_ip():
    rate = int(re.search(r"zone=access_requests:\S+ rate=(\d+)r/s", CONF).group(1))
    burst = int(re.search(r"limit_req zone=access_requests burst=(\d+)", CONF).group(1))
    # 모바일 1대 약 2.4회/초 → 50대 + 키오스크·관리자 여유
    assert rate >= 200 and burst >= rate


def test_chat_updates_is_public_read_only():
    block = _block("location = /api/v1/chat/updates")
    assert "limit_except GET { deny all; }" in block
    assert "proxy_pass http://parking_api;" in block
    assert CONF.index("location = /api/v1/chat/updates") < CONF.index("location /api/ { return 403; }")


def test_only_chat_post_and_updates_are_public():
    chat_locations = re.findall(r"location\s+[=~]?\s*(\S*/api/v1/chat\S*)\s*\{", CONF)
    assert sorted(chat_locations) == ["/api/v1/chat", "/api/v1/chat/updates"]
