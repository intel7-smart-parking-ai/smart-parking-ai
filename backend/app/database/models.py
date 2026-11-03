"""KAN-17 검토용 두 모델. PostgreSQL과 팀 스키마의 최종 승인을 뜻하지 않는다.

개인 0d4ff4a의 컬럼·키·제약을 유지한다. 미이전 모델을 참조하는
ParkingLot.cameras/sessions, ParkingSpace.state 관계만 보류한다.
실제 면수·시드 데이터·엔진 생성·DB 접속·테이블 생성은 포함하지 않는다.
"""

from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Integer, String, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.database.base import Base


def ref(target: str) -> ForeignKey:
    return ForeignKey(target, ondelete="RESTRICT", onupdate="RESTRICT")


def nonempty(column):
    return CheckConstraint(f"btrim({column}) <> ''", name=f"{column}_nonempty")


class CreatedAt:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ParkingLot(CreatedAt, Base):
    __tablename__ = "parking_lots"
    __table_args__ = (
        nonempty("lot_id"), nonempty("name"),
        CheckConstraint("type IN ('real','virtual')", name="type"),
    )
    lot_id: Mapped[str] = mapped_column(String(16), primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    type: Mapped[str] = mapped_column(String(8))
    active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    spaces: Mapped[list["ParkingSpace"]] = relationship(viewonly=True)


class ParkingSpace(Base):
    __tablename__ = "parking_spaces"
    __table_args__ = (
        nonempty("space_id"), CheckConstraint("roi_version >= 0", name="roi_version"),
    )
    lot_id: Mapped[str] = mapped_column(String(16), ref("parking_lots.lot_id"), primary_key=True)
    space_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    roi_polygon: Mapped[dict | list | None] = mapped_column(JSONB(none_as_null=True))
    roi_version: Mapped[int] = mapped_column(Integer, server_default="0")
    enabled: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
