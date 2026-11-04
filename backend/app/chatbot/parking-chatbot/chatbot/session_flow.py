"""Confirmation states and their single-use, expiring records."""
from dataclasses import dataclass, field
from enum import Enum
import time
import uuid


class Flow(str, Enum):
    IDLE = "idle"
    ASK_VEHICLE = "ask_vehicle"
    CONFIRM_VEHICLE = "confirm_vehicle"
    CONFIRM_ACTION = "confirm_action"


@dataclass
class Pending:
    flow: Flow
    intent: str | None = None
    params: dict = field(default_factory=dict)
    car_id: str | None = None
    token: str = field(default_factory=lambda: uuid.uuid4().hex)
    expires_at: float = field(default_factory=lambda: time.monotonic() + 120)
