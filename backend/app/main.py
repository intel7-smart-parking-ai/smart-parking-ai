"""프로젝트 공통 서버 진입점 — 업무 API는 담당자 협의 후 구현."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from fastapi import FastAPI

from backend.app.api.mock_queries import router as mock_queries_router

if TYPE_CHECKING:
    from sqlite3 import Connection

def health_check():
    return {'status': 'ok', 'service': 'smart-parking-api',
            'timestamp': datetime.now(timezone.utc).isoformat()}


def create_app(*, demo_billing_connection: Callable[[], Iterator[Connection]] | None = None) -> FastAPI:
    """Enable billing only when the approved demo launcher supplies an isolated connection."""
    application = FastAPI(
        title="AI Smart Parking API",
        description="AI 스마트 주차장 통합관제 - 초기 골격 (구현 전)",
        version="0.1.0",
    )
    application.add_api_route("/api/v1/health", health_check, methods=["GET"], tags=["system"])
    application.include_router(mock_queries_router)
    if demo_billing_connection is not None:
        from backend.app.api.billing_router import billing_router
        from backend.app.billing.db import get_db_conn

        application.dependency_overrides[get_db_conn] = demo_billing_connection
        application.include_router(billing_router)
    return application


app = create_app()


import os
if os.getenv("PARKING_RUNTIME") == "integrated":
    from backend.app.integrated import create_app as create_integrated_app
    app = create_integrated_app()
