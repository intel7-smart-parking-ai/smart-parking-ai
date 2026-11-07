"""Strict API boundary models, reconstructed from the recorded v3 design."""
from datetime import datetime, timezone
from typing import Annotated, Literal
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

LotId = Literal['A', 'B', 'C', 'D']
SpaceType = Literal['general', 'ev', 'accessible']
SpaceStatus = Literal['empty', 'occupied', 'uncertain', 'unavailable']
StickerId = Annotated[str, Field(pattern=r'^[1-9][0-9]$')]
PlateSuffix = Annotated[str, Field(pattern=r'^[0-9]{4}$')]
SpaceId = Annotated[str, Field(pattern=r'^[A-Z]-[0-9]{2}$')]
Source = Literal['vision', 'simulation', 'manual']
Nonnegative = Annotated[int, Field(ge=0)]

class WireModel(BaseModel):
    model_config = ConfigDict(extra='ignore', strict=True)

class ToolError(WireModel):
    code: str
    message: str
    http_status: int | None = None

class ToolResult(WireModel):
    status: Literal['ok', 'not_found', 'ambiguous', 'unavailable', 'invalid_data', 'unsupported', 'invalid_input', 'conflict']
    data: dict | None = None
    error: ToolError | None = None
    source_refs: list[str] = Field(default_factory=list)
    retrieved_at: AwareDatetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    is_mock: bool | None = None

class Lot(WireModel):
    lot_id: LotId
    name: str
    type: Literal['real', 'virtual']
    total_spaces: Nonnegative
    empty_spaces: Nonnegative
    uncertain_spaces: Nonnegative
    unavailable_spaces: Nonnegative

    @model_validator(mode='after')
    def counts(self):
        if self.empty_spaces + self.uncertain_spaces + self.unavailable_spaces > self.total_spaces:
            raise ValueError('invalid lot counts')
        return self

class LotList(WireModel):
    items: list[Lot]
    as_of: AwareDatetime
    next_cursor: str | None = None

class Space(WireModel):
    space_id: SpaceId
    status: SpaceStatus
    space_type: SpaceType
    car_id: StickerId | None = None
    observed_at: AwareDatetime | None
    version: Nonnegative
    source: Source | None = None
    is_mock: bool | None = None
    session_id: str | None = None
    entry_at: AwareDatetime | None = None
    zone: str | None = None
    vehicle_number: PlateSuffix | None = None
    plate_number: str | None = None

class SpaceList(WireModel):
    items: list[Space]
    as_of: AwareDatetime
    next_cursor: str | None = None

    @model_validator(mode='after')
    def unique(self):
        if len({r.space_id for r in self.items}) != len(self.items):
            raise ValueError('duplicate space')
        return self

class Location(WireModel):
    lot_id: LotId
    space_id: SpaceId
    session_id: str | None = None
    car_id: StickerId | None = None
    vehicle_number: PlateSuffix | None = None
    plate_number: str | None = None
    entry_at: AwareDatetime | None = None
    status: Literal['occupied', 'uncertain']
    observed_at: AwareDatetime
    source: Source | None = None
    is_mock: bool | None = None

class PaymentReceipt(WireModel):
    payment_id: Annotated[str, Field(min_length=1)]
    session_id: Annotated[str, Field(min_length=1)]
    amount_krw: Nonnegative
    status: Literal['completed']
    payment_method: str
    created_at: AwareDatetime

class Fee(WireModel):
    session_id: Annotated[str, Field(min_length=1)]
    lot_id: LotId
    amount_krw: Nonnegative
    duration_minutes: Nonnegative
    target_at: AwareDatetime
    quote_id: Annotated[str, Field(min_length=1)]
    quote_expires_at: AwareDatetime
    payment_status: Literal['unpaid', 'completed', 'canceled']
    rate_policy: dict[str, int]
    fee_breakdown: dict[str, int]
    payment_blocked: bool | None = None
    payment: PaymentReceipt | None = None
    entry_at: AwareDatetime | None = None
    exit_at: AwareDatetime | None = None
    car_id: StickerId | None = None
    space_id: SpaceId | None = None

    @model_validator(mode='after')
    def receipt_matches(self):
        if self.payment and (self.payment.session_id != self.session_id or self.payment.amount_krw != self.amount_krw or self.payment_status != 'completed'):
            raise ValueError('receipt mismatch')
        return self

class RatePolicy(WireModel):
    lot_id: LotId
    base_minutes: Nonnegative
    base_fee: Nonnegative
    unit_minutes: Annotated[int, Field(gt=0)]
    extra_fee: Nonnegative
    daily_cap: Nonnegative

class PolicyList(WireModel):
    items: list[RatePolicy]
    as_of: AwareDatetime
    next_cursor: str | None = None

class Alert(WireModel):
    alert_id: str
    lot_id: LotId
    level: Nonnegative
    status: Literal['active', 'acknowledged', 'resolved']
    type: str
    source: Source
    is_mock: bool
    message: str
    detected_at: AwareDatetime

class AlertList(WireModel):
    items: list[Alert]
    total: Nonnegative
    as_of: AwareDatetime
    next_cursor: str | None = None
