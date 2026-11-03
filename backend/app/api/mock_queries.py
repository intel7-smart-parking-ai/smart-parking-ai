"""팀 계약 초안 기준 임시 조회 목업. 실제 DB·면수·관측 데이터가 아니다."""

from typing import Literal

from fastapi import APIRouter, Request
from fastapi.routing import APIRoute
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from backend.app.simulation.mock_data import LOT_TYPES, OBSERVED_AT, SPACES, STATUS


class LotSummary(BaseModel):
    lot_id: str
    name: str
    type: Literal["real", "virtual"]
    total_spaces: int
    empty_spaces: int
    uncertain_spaces: int
    unavailable_spaces: int


class LotList(BaseModel):
    items: list[LotSummary]
    next_cursor: str | None = None
    as_of: str


class SpaceResponse(BaseModel):
    space_id: str
    status: STATUS
    car_id: str | None
    observed_at: str | None
    confidence: float | None
    version: int


class SpaceList(BaseModel):
    items: list[SpaceResponse]
    next_cursor: str | None = None
    as_of: str


class VehicleLocation(BaseModel):
    car_id: str
    lot_id: str
    space_id: str
    session_id: str | None
    status: STATUS
    observed_at: str
    confidence: float | None


class MockError(BaseModel):
    code: str
    message: str
    # 팀 초안은 배열만 제시한다. 상세 항목 규격을 임의로 정하지 않는다.
    details: list[object] = Field(default_factory=list)


class MockErrorResponse(BaseModel):
    error: MockError


def not_found(code: str, message: str) -> JSONResponse:
    body = MockErrorResponse(error=MockError(code=code, message=message))
    return JSONResponse(status_code=404, content=body.model_dump())


MOCK_DESCRIPTION = (
    "팀 계약 초안 기준 임시 목업입니다. 최종 승인된 계약이 아닙니다. "
    "A/B는 실물 주차장의 합성 예시, C는 가상 주차장입니다. "
    "각 4칸과 차량 01/02/03은 테스트 데이터이며 실제 면수·차량·DB 시드가 아닙니다. "
    "시각은 고정 자료의 기준 시각이며 실시간 관측이 아닙니다. 세션 미생성으로 session_id는 null입니다."
)


class MockRoute(APIRoute):
    """이 라우터의 응답에만 목업 표시를 붙여 기존 health를 보존한다."""

    def get_route_handler(self):
        handler = super().get_route_handler()

        async def identify_mock(request: Request):
            response = await handler(request)
            response.headers["X-ParkFlow-Data-Source"] = "mock"
            return response

        return identify_mock


router = APIRouter(
    prefix="/api/v1",
    route_class=MockRoute,
    tags=["팀 계약 초안 기준 임시 목업·DB 미사용"],
)


@router.get("/parking-lots", response_model=LotList,
         description=MOCK_DESCRIPTION, summary="[목업] A/B/C 주차장 및 빈자리")
def list_lots():
    return LotList(items=[
        LotSummary(
            lot_id=lot_id, name=f"목업 주차장 {lot_id} (합성 예시)", type=lot_type,
            total_spaces=sum(s.lot_id == lot_id for s in SPACES),
            empty_spaces=sum(s.lot_id == lot_id and s.status == "empty" for s in SPACES),
            uncertain_spaces=sum(s.lot_id == lot_id and s.status == "uncertain" for s in SPACES),
            unavailable_spaces=sum(s.lot_id == lot_id and s.status == "unavailable" for s in SPACES),
        ) for lot_id, lot_type in LOT_TYPES.items()
    ], as_of=OBSERVED_AT)


@router.get("/parking-lots/{lot_id}/spaces", response_model=SpaceList,
         responses={404: {"model": MockErrorResponse}},
         description=MOCK_DESCRIPTION, summary="[목업] 주차장별 칸 상태")
def list_spaces(lot_id: str):
    if lot_id not in LOT_TYPES:
        return not_found("PARKING_LOT_NOT_FOUND", "목업 주차장이 없습니다.")
    return SpaceList(items=[
        SpaceResponse(space_id=s.space_id, status=s.status, car_id=s.car_id,
                      observed_at=None if s.status == "unavailable" else OBSERVED_AT,
                      confidence=s.confidence, version=0)
        for s in SPACES if s.lot_id == lot_id
    ], as_of=OBSERVED_AT)


@router.get("/vehicles/{car_id}/location", response_model=VehicleLocation,
         responses={404: {"model": MockErrorResponse}},
         description=MOCK_DESCRIPTION, summary="[목업] 가상 차량 현재 위치")
def read_location(car_id: str):
    for space in SPACES:
        if space.car_id == car_id:
            return VehicleLocation(car_id=car_id, lot_id=space.lot_id,
                                   space_id=space.space_id, session_id=None,
                                   status=space.status, observed_at=OBSERVED_AT,
                                   confidence=space.confidence)
    return not_found("VEHICLE_NOT_FOUND", "목업 차량이 없습니다.")
