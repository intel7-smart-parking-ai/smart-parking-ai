"""Opt-in local preview of the chatbot mounted on the team app.

팀 파일(main.py, demo_main.py)은 고치지 않고, 챗봇 폴더 안에서 두 앱에 챗봇을 붙여 미리 실행해 본다.
"""
import os

from .router import router


def create_app():
    """Use the team's default app factory and include the chatbot routes."""
    from backend.app.main import create_app as create_team_app

    app = create_team_app()
    app.include_router(router)
    return app


def create_demo_app():
    """팀 통합 시연 서버(demo_main: 점유→세션→요금·결제)에 챗봇을 붙인다.

    시연 토큰은 PREVIEW_DEMO_TOKEN 환경변수에서 읽고, 없으면 터미널에서 입력받는다(ASCII 16자 이상).
    챗봇이 같은 서버를 조회하도록 BACKEND_BASE_URL을 실행 포트(예: http://127.0.0.1:8002)로 맞춘다.
    """
    from backend.app.demo_main import create_app as create_team_demo

    token = os.environ.get("PREVIEW_DEMO_TOKEN") or ""
    if not token:
        from getpass import getpass

        token = getpass("시연 토큰: ")
    app = create_team_demo(token=token)
    app.include_router(router)
    return app
