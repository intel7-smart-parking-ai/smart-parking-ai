"""프로젝트 공통 서버 진입점 — 업무 API는 담당자 협의 후 구현."""

from datetime import datetime, timezone
from fastapi import FastAPI
from backend.app.api.billing_router import billing_router

app = FastAPI(
    title="AI Smart Parking API",
    description="AI 스마트 주차장 통합관제 - 초기 골격 (구현 전)",
    version="0.1.0",
)
app.include_router(billing_router)
@app.get('/api/v1/health', tags=['system'])
def health_check():
    return {'status': 'ok', 'service': 'smart-parking-api',
            'timestamp': datetime.now(timezone.utc).isoformat()}
