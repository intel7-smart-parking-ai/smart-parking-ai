"""팀 계약 초안의 관측 데이터 검증용. 라우터·DB·상태 판정과 독립적이다."""
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictStr, field_validator, model_validator

FiniteCoordinate = Annotated[float, Field(strict=True, ge=0, allow_inf_nan=False)]


class VisionEvent(BaseModel):
    """Step 1 초안. 유효한 관측도 입차·출차 확정이나 중복 제거를 뜻하지 않는다."""
    model_config = ConfigDict(extra='forbid')

    event_id: StrictStr
    event_type: Literal['vehicle_observed', 'space_empty_observed', 'plate_observed', 'mock_fire_led_observed']
    source: Literal['vision', 'simulation', 'manual']
    lot_id: Literal['A', 'B', 'C', 'D']
    camera_id: StrictStr | None
    space_id: StrictStr | None
    car_id: Annotated[StrictStr, Field(pattern=r'^(0[1-9]|1[0-9]|2[0-4])$')] | None
    confidence: Annotated[float, Field(strict=True, ge=0, le=1, allow_inf_nan=False)]
    bbox_xyxy: tuple[FiniteCoordinate, FiniteCoordinate, FiniteCoordinate, FiniteCoordinate] | None
    observed_at: datetime
    frame_id: StrictStr | None = None

    @field_validator('event_id', 'camera_id', 'space_id', 'frame_id')
    @classmethod
    def nonempty_identifier(cls, value):
        if value is not None and not value.strip():
            raise ValueError('식별자는 빈 문자열일 수 없습니다.')
        return value

    @field_validator('observed_at', mode='before')
    @classmethod
    def timestamp_input(cls, value):
        if not isinstance(value, (str, datetime)):
            raise ValueError('시각은 시간대 포함 ISO 문자열 또는 datetime이어야 합니다.')
        return value

    @field_validator('observed_at')
    @classmethod
    def timezone_required(cls, value):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError('시간대 없는 observed_at은 허용하지 않습니다.')
        return value

    @model_validator(mode='after')
    def geometry_and_virtual_camera(self):
        if self.bbox_xyxy is not None:
            x1, y1, x2, y2 = self.bbox_xyxy
            if x2 <= x1 or y2 <= y1:
                raise ValueError('bbox_xyxy는 양의 너비·높이를 가져야 합니다.')
        if self.lot_id in {'C', 'D'} and self.camera_id is not None:
            raise ValueError('팀 초안에서 가상 주차장 camera_id는 null입니다.')
        return self
