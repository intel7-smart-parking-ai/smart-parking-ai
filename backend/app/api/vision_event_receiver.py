"""운영 main에 등록하지 않는 Step 2 실험 라우터. 인증 콜백 주입이 필수다."""

from collections.abc import Callable
from typing import Literal

from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException

from backend.app.api.vision_event_schema import VisionEvent
from backend.app.api.vision_event_store import InMemoryEventStore, Receipt


class IngestResponse(BaseModel):
    event_id: str
    disposition: Literal["accepted", "duplicate"]


class IngestError(BaseModel):
    code: str
    message: str
    details: list[object] = Field(default_factory=list)


class IngestErrorResponse(BaseModel):
    error: IngestError


def error_response(status: int, code: str, message: str, details=None) -> JSONResponse:
    body = IngestErrorResponse(error=IngestError(
        code=code, message=message, details=[] if details is None else details))
    return JSONResponse(status_code=status, content=body.model_dump())


def create_router(*, store: InMemoryEventStore, authorize: Callable) -> APIRouter:
    if not callable(authorize):
        raise ValueError("허용 여부를 확인하는 실험용 인증 콜백이 필요합니다.")
    def require_authorization(decision=Depends(authorize)):
        if decision is not True:
            raise HTTPException(403, "명시적으로 허용된 시험 호출이 아닙니다.")

    router = APIRouter(prefix="/api/v1/internal", dependencies=[Depends(require_authorization)],
                       tags=["vision-event 임시 실험·운영 연결 금지"])

    @router.post("/vision-events", response_model=IngestResponse, status_code=202,
                 responses={200: {"model": IngestResponse},
                            409: {"model": IngestErrorResponse},
                            422: {"model": IngestErrorResponse},
                            503: {"model": IngestErrorResponse}},
                 description="메모리 접수만 수행합니다. 응답·충돌·인증은 팀 미확정 실험 규칙입니다.")
    def receive(event: VisionEvent):
        outcome = store.receive(event)
        if outcome is Receipt.CONFLICT:
            return error_response(409, "EVENT_ID_CONFLICT", "같은 event_id의 내용이 다릅니다.")
        if outcome is Receipt.FULL:
            return error_response(503, "EXPERIMENT_STORE_FULL", "실험용 접수함의 한도에 도달했습니다.")
        body = IngestResponse(event_id=event.event_id, disposition=outcome.value)
        return JSONResponse(status_code=202 if outcome is Receipt.ACCEPTED else 200,
                            content=body.model_dump())

    return router


def create_experiment_app(*, store: InMemoryEventStore, authorize: Callable) -> FastAPI:
    """자동 생성 app·토큰·기본 허용 정책이 없다. 테스트에서만 명시적으로 구성한다."""
    app = FastAPI(title="vision-event Step 2 실험 전용", version="0.0.0",
                  description="팀 미승인 입력·멱등 접수 실험. 운영 배포 금지.")

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, exc: RequestValidationError):
        # 요청 원문·민감한 입력·직렬화 불가능한 ctx를 오류 응답에 복제하지 않는다.
        details = [{"loc": list(e["loc"]), "type": e["type"], "message": e["msg"]}
                   for e in exc.errors()]
        return error_response(422, "VALIDATION_ERROR", "관측 요청 형식이 올바르지 않습니다.", details)

    @app.exception_handler(HTTPException)
    async def rejected_request(request: Request, exc: HTTPException):
        code = {401: "UNAUTHORIZED", 403: "FORBIDDEN"}.get(exc.status_code, "HTTP_ERROR")
        response = error_response(exc.status_code, code, "요청이 허용되지 않았습니다.")
        if exc.headers:
            response.headers.update(exc.headers)
        return response

    app.include_router(create_router(store=store, authorize=authorize))
    return app
