"""점검 화면(D-67): 정적 파일과 nginx 503 경로가 함께 배포되는지 확인한다."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"


def test_maintenance_assets_exist_and_are_small():
    html = (FRONTEND / "maintenance.html").read_text(encoding="utf-8")
    assert "/assets/maintenance-mascot.png" in html
    assert "점검" in html
    assert (FRONTEND / "maintenance-mascot.png").stat().st_size < 200_000


def test_dockerfile_copies_maintenance_files_after_build_output():
    lines = (FRONTEND / "Dockerfile").read_text(encoding="utf-8").splitlines()
    dist = next(i for i, line in enumerate(lines) if "COPY --from=build /app/dist" in line)
    for target in ("/usr/share/nginx/html/maintenance.html", "/usr/share/nginx/html/assets/maintenance-mascot.png"):
        copy = next(i for i, line in enumerate(lines) if line.startswith("COPY") and line.endswith(target))
        assert copy > dist, "dist 복사가 점검 파일을 덮어쓰지 않도록 뒤에 복사해야 함"


def test_nginx_maintenance_only_affects_user_pages():
    conf = (FRONTEND / "nginx.conf").read_text(encoding="utf-8")
    assert "location = /maintenance.html { internal; }" in conf
    root_block = conf.split("  location / {", 1)[1].split("}\n", 2)
    assert "if (-f /tmp/parkon-maintenance) { return 503; }" in conf
    assert "error_page 503 /maintenance.html;" in conf
    # API·카메라 수신 경로는 점검 스위치와 무관 (location / 블록 안에서만 503)
    vision = conf.split("location = /api/v1/internal/vision-events", 1)[1].split("}", 1)[0]
    assert "parkon-maintenance" not in vision
    assert root_block
