"""
3팀 LED 모의 화재 관리자 경보 API 라우터 (EPIC 03)
담당자: 윤명수(대표) · 서정범
"""
from __future__ import annotations
from fastapi import APIRouter, Path
from fastapi.responses import JSONResponse

fire_router = APIRouter(prefix="/api/v1", tags=["3. 3팀 — LED 모의 화재 인식 (윤명수·서정범)"])

@fire_router.get("/alerts")
async def get_alerts() -> JSONResponse:
  from server import state
  return JSONResponse(status_code=200, content={"alerts": state.alerts})

@fire_router.post("/alerts/{id}/ack")
async def acknowledge_alert(
    id: str = Path(..., description="경보 ID (예: 'alt_0001')")
) -> JSONResponse:
  from server import state, toKstIsoString
  alert = next((a for a in state.alerts if a["alert_id"] == id), None)
  if not alert:
    return JSONResponse(status_code=404, content={"error_code": "ALERT_NOT_FOUND", "message": "경보를 찾을 수 없습니다."})
  alert["status"] = "acknowledged"
  alert["acknowledged_at"] = toKstIsoString(state.simTimeMs)
  return JSONResponse(status_code=200, content={"status": "acknowledged", "alert": alert})
