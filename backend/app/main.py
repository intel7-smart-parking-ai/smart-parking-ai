"""공통 진입점: 기존 목업 호환 및 단일 이벤트·세션·요금 통합 앱."""

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


import os

def configured_app():
    mode=os.getenv('PARKING_MODE','demo' if os.getenv('VISION_TOKEN') else 'mock')
    if mode=='mock':return create_app()
    if mode not in ('demo','real'):raise ValueError('PARKING_MODE must be mock, demo or real')
    from backend.app.runtime import create_runtime_app
    return create_runtime_app(token=os.environ.get('VISION_TOKEN',''),mode=mode)

app = configured_app()

if __name__=='__main__':
    import uvicorn
    uvicorn.run(app,host=os.getenv('API_HOST','127.0.0.1'),port=int(os.getenv('API_PORT','8000')))
