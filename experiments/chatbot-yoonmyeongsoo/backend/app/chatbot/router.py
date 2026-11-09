from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from typing import Optional, Dict, Any, List
from .engine import ChatEngine

router = APIRouter(prefix="/api/v1/chat", tags=["chatbot"])
engine = ChatEngine()

class ChatRequest(BaseModel):
    session_id: str = Field(..., description="대화 세션 ID")
    text: Optional[str] = Field(None, description="사용자 입력 문장")
    chip_id: Optional[str] = Field(None, description="클릭한 추천 칩 ID")
    request_id: Optional[str] = Field(None, description="클라이언트 요청 고유 ID (멱등성용)")
    vehicle_context: Optional[Dict[str, Any]] = Field(None, description="현재 선택된 차량 컨텍스트")

class AlertDemoRequest(BaseModel):
    lot_id: str = "A"
    space_id: str = "A-05"
    level: int = 2
    message: str = "화재 감지 경보 시연"

@router.post("", summary="챗봇 대화 질의응답")
async def chat_endpoint(req: ChatRequest):
    try:
        resp = engine.handle(
            session_id=req.session_id,
            text=req.text,
            chip_id=req.chip_id,
            request_id=req.request_id,
            vehicle_context=req.vehicle_context
        )
        return resp
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/catalog", summary="주차장 및 구역 메타데이터 카탈로그")
async def get_catalog():
    from .catalog import LOTS_CONFIG
    return {
        "lots": list(LOTS_CONFIG.values()),
        "zones": [
            {"key": "general", "name": "일반"},
            {"key": "ev", "name": "전기차"},
            {"key": "accessible", "name": "장애인"}
        ]
    }

@router.post("/demo/alert", summary="[시연용] 모의 화재 경보 발생/해제")
async def demo_alert(req: AlertDemoRequest):
    alerts = engine.adapter.get_alerts()
    if alerts:
        engine.adapter.clear_alerts()
        return {"status": "cleared", "message": "경보가 해제되었습니다."}
    else:
        new_alert = engine.adapter.set_mock_alert(
            lot_id=req.lot_id,
            space_id=req.space_id,
            level=req.level,
            message=req.message
        )
        return {"status": "active", "alert": new_alert}

@router.post("/demo/reset", summary="[시연용] 결제 내역 및 상태 리셋")
async def demo_reset():
    engine.adapter.paid_records.clear()
    engine.adapter.clear_alerts()
    engine.adapter.admin_calls.clear()
    return {"status": "ok", "message": "모든 시연 상태가 초기화되었습니다."}

@router.get("/admin/calls", summary="관리자 호출 목록 조회")
async def get_admin_calls():
    return {"calls": engine.adapter.admin_calls}

