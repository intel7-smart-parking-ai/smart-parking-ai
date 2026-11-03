"""명시적으로 구성하는 격리 실험 앱. 운영 main에 등록하지 않는다."""
from fastapi import Depends

from backend.app.api.occupancy_prototype import OccupancyExperiment
from backend.app.api.vision_event_receiver import create_experiment_app


def create_occupancy_app(*, store: OccupancyExperiment, authorize, clock):
    app = create_experiment_app(store=store, authorize=authorize)
    app.title = 'D5 합성 관측·점유 실험'

    def authorized(decision=Depends(authorize)):
        if decision is not True:
            from starlette.exceptions import HTTPException
            raise HTTPException(403)

    @app.get('/api/v1/parking-lots/{lot_id}/spaces', dependencies=[Depends(authorized)],
             description='메모리 합성 점유 실험. 실물·운영 DB 정보가 아닙니다.')
    def list_spaces(lot_id: str):
        now = clock()
        return {'items': store.spaces(lot_id, as_of=now), 'as_of': now,
                'data_source': 'memory-prototype', 'policy_status': 'experiment-only'}

    return app
