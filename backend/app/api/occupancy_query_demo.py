"""Loopback-only synthetic Vision/D5 demo with KAN-16-shaped reads."""
from datetime import datetime, timezone
from secrets import compare_digest
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException

from backend.app.api.mock_queries import (
    LotList, LotSummary, MockRoute, SpaceList, SpaceResponse, VehicleLocation, not_found,
)
from backend.app.api.occupancy_prototype import ExperimentPolicy, OccupancyExperiment
from backend.app.api.vision_event_receiver import create_experiment_app
from backend.app.simulation.mock_data import LOT_NAMES, LOT_TYPES, SPACES

Source = Literal["vision", "simulation", "manual"]


class DemoSpace(SpaceResponse):
    source: Source | None = None
    is_mock: bool | None = None
    session_id: str | None = None
    entry_at: str | None = None
    zone: str | None = None
    vehicle_number: str | None = None
    plate_number: str | None = None


class DemoSpaceList(SpaceList):
    items: list[DemoSpace]


class DemoLocation(VehicleLocation):
    source: Source


def create_demo_app(*, token: str, clock=lambda: datetime.now(timezone.utc),
                    public_reads: bool = False, on_confirmed=None, max_events: int = 1000,
                    store_factory=OccupancyExperiment, enrich_space=None):
    if len(token) < 16 or not token.isascii():
        raise ValueError("시연 토큰은 ASCII 16자 이상이어야 합니다.")

    def authorize(authorization: str = Header(default="")):
        return compare_digest(authorization.encode(), f"Bearer {token}".encode())

    def require_access(decision=Depends(authorize)):
        if decision is not True:
            raise HTTPException(403)

    layout = {"lots": [
        {"lot_id": lot, "spaces": [
            {"space_id": space.space_id, "space_type": space.space_type}
            for space in SPACES if space.lot_id == lot
        ]} for lot in LOT_TYPES
    ]}
    # 기존 D5 격리 시험과 같은 실험 설정을 사용한다.
    store = store_factory(layout=layout, on_confirmed=on_confirmed, max_events=max_events, policy=ExperimentPolicy(
        occupy_seconds=2, empty_seconds=3, max_gap_seconds=2,
        stale_seconds=10, min_confidence=.8, initial_status="empty",
    ))
    app = create_experiment_app(store=store, authorize=authorize)
    app.title = "D5 합성 Vision · KAN-16 조회 시연"
    app.state.occupancy_store = store
    router = APIRouter(prefix="/api/v1", route_class=MockRoute,
                       dependencies=[] if public_reads else [Depends(require_access)])

    @router.get("/parking-lots", response_model=LotList)
    def list_lots():
        now = clock()
        items = []
        for lot, lot_type in LOT_TYPES.items():
            rows = store.spaces(lot, as_of=now)
            items.append(LotSummary(
                lot_id=lot, name=LOT_NAMES[lot], type=lot_type, total_spaces=len(rows),
                empty_spaces=sum(row["status"] == "empty" for row in rows),
                uncertain_spaces=sum(row["status"] == "uncertain" for row in rows),
                unavailable_spaces=sum(row["status"] == "unavailable" for row in rows),
            ))
        return LotList(items=items, as_of=now.isoformat())

    @router.get("/parking-lots/{lot_id}/spaces", response_model=DemoSpaceList)
    def list_spaces(lot_id: str):
        if lot_id not in LOT_TYPES:
            return not_found("PARKING_LOT_NOT_FOUND", "시연 주차장이 없습니다.")
        now = clock()
        items = []
        for row in store.spaces(lot_id, as_of=now):
            if enrich_space is not None:
                row = enrich_space(row)
            observed = row["observed_at"]
            items.append(DemoSpace(**(row | {
                "observed_at": observed.isoformat() if observed is not None else None,
                "confidence": None,
            })))
        return DemoSpaceList(items=items, as_of=now.isoformat())

    @router.get("/vehicles/{car_id}/location", response_model=DemoLocation)
    def read_location(car_id: str):
        now = clock()
        matches = [
            row for lot in LOT_TYPES for row in store.spaces(lot, as_of=now)
            if row["status"] == "occupied" and row["car_id"] == car_id
        ]
        if len(matches) != 1:
            return not_found("VEHICLE_NOT_FOUND", "단일 차량 위치를 확인할 수 없습니다.")
        row = matches[0]
        return DemoLocation(**(row | {
            "session_id": row.get("session_id"), "confidence": None,
            "observed_at": row["observed_at"].isoformat(),
        }))

    app.include_router(router)
    return app


if __name__ == "__main__":
    from getpass import getpass

    import uvicorn

    uvicorn.run(create_demo_app(token=getpass("시연 토큰: ")), host="127.0.0.1", port=8002)
