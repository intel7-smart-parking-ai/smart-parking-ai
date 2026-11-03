"""KAN-17 로컬 프로토타입 기반. 엔진 생성·DB 접속·DDL 부작용이 없다."""

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """개인 ParkFlow의 제약 명명 규칙을 재사용한다."""

    metadata = MetaData(naming_convention={
        "pk": "pk_%(table_name)s", "uq": "uq_%(table_name)s_%(column_0_name)s",
        "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
        "ck": "ck_%(table_name)s_%(constraint_name)s",
        "ix": "ix_%(table_name)s_%(column_0_name)s",
    })
