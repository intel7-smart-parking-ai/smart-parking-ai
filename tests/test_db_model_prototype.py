"""DB 없는 모델 구조 시험. PR #8의 키/기본값과 1단계 호환 타입을 검증한다.

SQLA metadata/mapper만 확인하며 실제 DB의 제약 강제·DDL 실행을 검증하지 않는다.
"""

import subprocess
import sys

import pytest
from sqlalchemy import Boolean, CheckConstraint, DateTime, Integer, JSON, String, UniqueConstraint, inspect
from sqlalchemy.orm import configure_mappers

from backend.app.database.base import Base
from backend.app.database.models import ParkingLot, ParkingSpace


def test_import_and_mapping():
    configure_mappers()
    assert inspect(ParkingLot).local_table is Base.metadata.tables["parking_lots"]
    assert inspect(ParkingSpace).local_table is Base.metadata.tables["parking_spaces"]
    assert set(Base.metadata.tables) == {"parking_lots", "parking_spaces"}
    assert [t.name for t in Base.metadata.sorted_tables] == ["parking_lots", "parking_spaces"]


def test_primary_and_foreign_keys():
    lot, space = ParkingLot.__table__, ParkingSpace.__table__
    assert list(lot.primary_key.columns.keys()) == ["lot_id"]
    assert lot.primary_key.name == "pk_parking_lots"
    assert list(space.primary_key.columns.keys()) == ["lot_id", "space_id"]
    assert space.primary_key.name == "pk_parking_spaces"
    assert not lot.foreign_keys
    fk, = space.foreign_keys
    assert fk.parent.name == "lot_id" and fk.column is lot.c.lot_id
    assert fk.constraint.name == "fk_parking_spaces_lot_id_parking_lots"
    assert fk.ondelete == fk.onupdate == "RESTRICT"
    # 원본 두 표에는 별도의 UNIQUE 제약이나 보조 인덱스가 없다. 복합 PK를 유지한다.
    for table in (lot, space):
        assert not any(isinstance(c, UniqueConstraint) for c in table.constraints)
        assert not table.indexes


@pytest.mark.parametrize("model,expected", [
    (ParkingLot, {
        "ck_parking_lots_lot_id_nonempty": "trim(lot_id) <> ''",
        "ck_parking_lots_name_nonempty": "trim(name) <> ''",
        "ck_parking_lots_type": "type IN ('real','virtual')",
    }),
    (ParkingSpace, {
        "ck_parking_spaces_space_id_nonempty": "trim(space_id) <> ''",
        "ck_parking_spaces_roi_version": "roi_version >= 0",
    }),
])
def test_checks_preserved_with_portable_trim(model, expected):
    assert {c.name: str(c.sqltext) for c in model.__table__.constraints
            if isinstance(c, CheckConstraint)} == expected


@pytest.mark.parametrize("model,expected", [
    (ParkingLot, {
        "lot_id": (String, 16, False, None),
        "name": (String, 100, False, None),
        "type": (String, 8, False, None),
        "active": (Boolean, None, False, "true"),
        "created_at": (DateTime, None, False, "now()"),
    }),
    (ParkingSpace, {
        "lot_id": (String, 16, False, None),
        "space_id": (String, 32, False, None),
        "roi_polygon": (JSON, None, True, None),
        "roi_version": (Integer, None, False, "0"),
        "enabled": (Boolean, None, False, "true"),
    }),
])
def test_columns_match_initial_0001(model, expected):
    assert set(model.__table__.columns.keys()) == set(expected)
    for name, (type_class, length, nullable, default) in expected.items():
        column = model.__table__.c[name]
        assert isinstance(column.type, type_class)
        assert getattr(column.type, "length", None) == length
        assert column.nullable is nullable
        assert column.default is None
        assert (str(column.server_default.arg) if column.server_default else None) == default
    if model is ParkingLot:
        assert model.__table__.c.created_at.type.timezone is True
    else:
        assert model.__table__.c.roi_polygon.type.none_as_null is True


def test_only_available_relationship_is_mapped():
    configure_mappers()
    assert set(inspect(ParkingLot).relationships.keys()) == {"spaces"}
    relation = inspect(ParkingLot).relationships["spaces"]
    assert relation.mapper.class_ is ParkingSpace
    assert relation.viewonly is True and relation.uselist is True
    assert set(inspect(ParkingSpace).relationships.keys()) == set()
    assert len(Base.registry.mappers) == 2


def test_fresh_import_and_mapping_without_database_or_ddl():
    # 기존 프로세스 import 캐시 없이 접속/DDL 금지 상태로 처음부터 매핑한다.
    code = r'''
import importlib.abc
import socket
import sys
import sqlalchemy
from sqlalchemy import MetaData
from sqlalchemy.engine import Engine
def forbidden(*args, **kwargs):
    raise AssertionError("DB 접속·엔진 생성·DDL은 이 시험에서 금지합니다.")
sqlalchemy.create_engine = forbidden
Engine.connect = forbidden
MetaData.create_all = forbidden
MetaData.drop_all = forbidden
socket.create_connection = forbidden
socket.socket.connect = forbidden
class NoDatabaseDrivers(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'psycopg', 'psycopg2', 'sqlite3', 'asyncpg', 'alembic'}:
            raise AssertionError('DB 드라이버/Alembic import 금지')
sys.meta_path.insert(0, NoDatabaseDrivers())
from backend.app.database.models import ParkingLot, ParkingSpace
from sqlalchemy.orm import configure_mappers
configure_mappers()
assert len(ParkingLot.metadata.tables) == 2
'''
    result = subprocess.run([sys.executable, "-B", "-X", "utf8", "-c", code],
                            capture_output=True, text=True, encoding="utf-8")
    assert result.returncode == 0, result.stdout + result.stderr
