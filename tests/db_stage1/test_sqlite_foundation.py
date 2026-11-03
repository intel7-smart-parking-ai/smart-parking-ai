"""승인된 새 tmp_path DB만 생성한다. V1~V3·billing·실사용 DB 시험은 아니다."""

import os
import stat
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import inspect, select, text, update
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.exc import IntegrityError, InvalidRequestError
from sqlalchemy.schema import CreateTable

from backend.app.database.base import Base
from backend.app.database.models import ParkingLot, ParkingSpace
from backend.app.database.session import create_session_factory, create_test_engine, get_db
from backend.app.database.settings import DatabaseSafetyError, DatabaseSettings


def settings_for(root, path=None):
    target = path or root / f"kan17-verify-{uuid4().hex}.sqlite3"
    from sqlalchemy.engine import URL
    url = URL.create("sqlite", database=str(target)).render_as_string()
    return DatabaseSettings.from_env(test_root=root, environ={"DATABASE_URL": url})


@pytest.fixture
def database(tmp_path):
    settings = settings_for(tmp_path)
    engine = create_test_engine(settings)
    try:
        # 이 명시 호출은 자신이 새로 확보한 tmp_path DB의 두 표에만 실행한다.
        Base.metadata.create_all(engine)
        yield engine, create_session_factory(engine)
    finally:
        engine.dispose()


@pytest.mark.parametrize("values", [{}, {"DATABASE_URL": ""}, {"DATABASE_URL": "  "}])
def test_database_url_is_required(tmp_path, values):
    with pytest.raises(DatabaseSafetyError, match="미설정"):
        DatabaseSettings.from_env(test_root=tmp_path, environ=values)


@pytest.mark.parametrize("raw", [
    "sqlite:///data/smart_parking.db", "sqlite:///SMART_PARKING.DB",
    "sqlite:///:memory:", "sqlite://", "sqlite:///file:existing?uri=true",
    "sqlite:///C:/data/smart_parking.db", "sqlite:///C:/data/../data/smart_parking.db",
    "sqlite:///C:/DATA/SMART_~1.DB", "sqlite:///C:/data/smart_parking.db.",
    "sqlite:///C:/data/smart_parking.db:stream", "sqlite:///C:/data/%73mart_parking.db",
    "sqlite:///C:/data/$DATABASE_FILE", "sqlite:///C:/data/~/test.db",
    "sqlite:////server/share/smart_parking.db", "sqlite://host/data/smart_parking.db",
    "postgresql://example.invalid/db", "형식이-잘못된-URL",
])
def test_protected_relative_absolute_and_alias_urls_refused_before_connect(tmp_path, monkeypatch, raw):
    import sqlite3
    def forbidden(*args, **kwargs):
        raise AssertionError("거절 경로의 DB를 열면 안 됩니다.")
    monkeypatch.setattr(sqlite3, "connect", forbidden)
    with pytest.raises(DatabaseSafetyError):
        DatabaseSettings.from_env(test_root=tmp_path, environ={"DATABASE_URL": raw})
    assert list(tmp_path.iterdir()) == []


def test_real_environment_is_used_without_dotenv_or_billing_fallback(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("SMART_PARKING_DB_PATH", "data/smart_parking.db")
    with pytest.raises(DatabaseSafetyError):
        DatabaseSettings.from_env(test_root=tmp_path)
    settings = settings_for(tmp_path)
    monkeypatch.setenv("DATABASE_URL", settings.url.render_as_string())
    assert DatabaseSettings.from_env(test_root=tmp_path) == settings


def test_outside_approved_root_and_uri_query_are_refused(tmp_path):
    sibling = tmp_path / "outside"
    sibling.mkdir()
    with pytest.raises(DatabaseSafetyError):
        settings_for(tmp_path, sibling / f"kan17-verify-{uuid4().hex}.sqlite3")
    settings = settings_for(tmp_path)
    for suffix in ("?uri=true", "?mode=ro", "?mode=memory"):
        with pytest.raises(DatabaseSafetyError):
            DatabaseSettings.from_env(test_root=tmp_path,
                environ={"DATABASE_URL": settings.url.render_as_string() + suffix})


@pytest.mark.parametrize("name", ["smart_parking.db", "SMART_PARKING.DB", "ordinary.sqlite3"])
def test_protected_or_unmarked_name_inside_approved_root_is_refused(tmp_path, name):
    with pytest.raises(DatabaseSafetyError):
        settings_for(tmp_path, tmp_path / name)
    assert list(tmp_path.iterdir()) == []


def test_existing_file_is_never_opened_even_with_valid_test_name(tmp_path, monkeypatch):
    settings = settings_for(tmp_path)
    target = Path(settings.url.database)
    original = b"OWNED-TEST-SENTINEL"
    target.write_bytes(original)
    import sqlite3
    monkeypatch.setattr(sqlite3, "connect", lambda *a, **k: pytest.fail("기존 파일 연결 금지"))
    with pytest.raises(DatabaseSafetyError, match="기존 파일"):
        create_test_engine(settings)
    assert target.read_bytes() == original


def test_reparse_directory_metadata_is_refused(tmp_path, monkeypatch):
    # junction/symlink 생성 권한에 의존하지 않고 OS reparse 메타데이터를 주입한다.
    original_lstat = Path.lstat
    def marked(path, *args, **kwargs):
        if path == tmp_path:
            return SimpleNamespace(st_mode=stat.S_IFDIR,
                st_file_attributes=getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 1024))
        return original_lstat(path, *args, **kwargs)
    monkeypatch.setattr(Path, "lstat", marked)
    with pytest.raises(DatabaseSafetyError, match="별칭"):
        settings_for(tmp_path)


def test_new_file_replacement_and_hard_link_are_blocked(tmp_path):
    settings = settings_for(tmp_path)
    target = Path(settings.url.database)
    engine = create_test_engine(settings)
    try:
        alias = tmp_path / "owned-test-alias"
        os.link(target, alias)
        with pytest.raises(DatabaseSafetyError, match="별칭"):
            engine.connect()
        # 생성한 빈 검증 파일만 옮겨 파일 정체성 변경도 검증한다. 기존 DB는 대상이 아니다.
        target.rename(tmp_path / "owned-reserved-empty-file")
        target.write_bytes(b"OWNED-REPLACEMENT")
        with pytest.raises(DatabaseSafetyError, match="교체"):
            engine.connect()
        assert target.read_bytes() == b"OWNED-REPLACEMENT"
    finally:
        engine.dispose()


def test_foreign_keys_enabled_on_every_connection(database):
    engine, _ = database
    for _ in range(2):
        with engine.connect() as connection:
            assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1


def test_actual_keys_and_fk_restrict_delete(database):
    engine, factory = database
    metadata = inspect(engine)
    assert set(metadata.get_table_names()) == {"parking_lots", "parking_spaces"}
    assert metadata.get_pk_constraint("parking_spaces")["constrained_columns"] == ["lot_id", "space_id"]
    with factory() as session:
        session.add(ParkingSpace(lot_id="MISSING", space_id="01"))
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()
        session.add(ParkingLot(lot_id="A", name="검증 예시", type="real"))
        session.commit()
        session.add_all([ParkingSpace(lot_id="A", space_id="01"), ParkingSpace(lot_id="A", space_id="02")])
        session.commit()
        lot = session.get(ParkingLot, "A")
        assert {space.space_id for space in lot.spaces} == {"01", "02"}
        session.delete(lot)
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()
        session.add(ParkingSpace(lot_id="A", space_id="01"))
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()


def test_lot_pk_and_composite_space_key_and_restrict_update(database):
    _, factory = database
    with factory() as session:
        session.add_all([ParkingLot(lot_id=lot_id, name="검증", type="real") for lot_id in ("A", "B")])
        session.commit()
        session.add_all([ParkingSpace(lot_id=lot_id, space_id="01") for lot_id in ("A", "B")])
        session.commit()
        assert len(session.scalars(select(ParkingSpace)).all()) == 2
        with pytest.raises(IntegrityError):
            session.execute(update(ParkingLot).where(ParkingLot.lot_id == "A").values(lot_id="C"))
        session.rollback()
    with factory() as session:
        session.add(ParkingLot(lot_id="A", name="중복 검증", type="real"))
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()


@pytest.mark.parametrize("changes", [
    {"lot_id": " "}, {"name": " "}, {"type": "unsupported"},
])
def test_lot_checks_enforced(database, changes):
    _, factory = database
    with factory() as session:
        session.add(ParkingLot(**({"lot_id": "A", "name": "검증", "type": "real"} | changes)))
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()


@pytest.mark.parametrize("changes", [{"space_id": " "}, {"roi_version": -1}])
def test_space_checks_enforced(database, changes):
    _, factory = database
    with factory() as session:
        session.add(ParkingLot(lot_id="A", name="검증", type="real"))
        session.commit()
        session.add(ParkingSpace(**({"lot_id": "A", "space_id": "01"} | changes)))
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()


@pytest.mark.parametrize("polygon", [{"points": [[0, 0], [1, 1]]}, [[0, 0], [1, 1]], None])
def test_json_and_defaults_round_trip(database, polygon):
    _, factory = database
    with factory() as session:
        session.add(ParkingLot(lot_id="C", name="가상 검증 예시", type="virtual"))
        session.commit()
        session.add(ParkingSpace(lot_id="C", space_id="01", roi_polygon=polygon))
        session.commit()
    with factory() as session:
        lot, space = session.get(ParkingLot, "C"), session.get(ParkingSpace, ("C", "01"))
        assert space.roi_polygon == polygon and space.roi_version == 0
        assert lot.active is True and space.enabled is True and lot.created_at is not None
        assert session.execute(text("SELECT roi_polygon IS NULL FROM parking_spaces")).scalar_one() == (polygon is None)


def test_get_db_explicit_commit_close_and_rollback(database):
    _, factory = database
    provider = get_db(factory)
    session = next(provider)
    session.add(ParkingLot(lot_id="A", name="저장 검증", type="real"))
    session.commit()
    provider.close()
    with pytest.raises(InvalidRequestError):
        session.execute(select(ParkingLot))
    provider = get_db(factory)
    session = next(provider)
    session.add(ParkingLot(lot_id="B", name="미저장 검증", type="real"))
    session.flush()
    with pytest.raises(RuntimeError, match="검증 예외"):
        provider.throw(RuntimeError("검증 예외"))
    with factory() as reader:
        assert reader.get(ParkingLot, "A") is not None
        assert reader.get(ParkingLot, "B") is None
    provider = get_db(factory)
    session = next(provider)
    session.add(ParkingLot(lot_id="C", name="정상 종료 미커밋", type="virtual"))
    session.flush()
    with pytest.raises(StopIteration):
        next(provider)
    with factory() as reader:
        assert reader.get(ParkingLot, "C") is None


def test_postgresql_types_compile_without_engine_or_driver():
    table = ParkingSpace.__table__
    assert str(table.c.roi_polygon.type.compile(dialect=sqlite.dialect())) == "JSON"
    assert str(table.c.roi_polygon.type.compile(dialect=postgresql.dialect())) == "JSONB"
    assert table.c.roi_polygon.type.dialect_impl(postgresql.dialect()).none_as_null is True
    assert "PRIMARY KEY (lot_id, space_id)" in str(CreateTable(table).compile(dialect=postgresql.dialect()))
    for table in Base.metadata.sorted_tables:
        ddl = str(CreateTable(table).compile(dialect=postgresql.dialect()))
        assert "btrim" not in ddl
    assert "TIMESTAMP WITH TIME ZONE" in str(CreateTable(ParkingLot.__table__).compile(dialect=postgresql.dialect()))


def test_import_has_no_engine_file_or_database_side_effects(tmp_path):
    script = r'''
import os, sqlite3, sqlalchemy, sys
from sqlalchemy import MetaData
from sqlalchemy.engine import Engine
def forbidden(*args, **kwargs):
    raise AssertionError('import 중 엔진/파일/접속/DDL 금지')
sqlite3.connect = forbidden
sqlalchemy.create_engine = forbidden
Engine.connect = forbidden
MetaData.create_all = forbidden
os.open = forbidden
from backend.app.database import settings, session, models
from sqlalchemy.orm import configure_mappers
configure_mappers()
assert len(models.Base.metadata.tables) == 2
'''
    result = subprocess.run([sys.executable, "-B", "-X", "utf8", "-c", script],
        capture_output=True, text=True, encoding="utf-8", env=os.environ | {"DATABASE_URL": "invalid-for-import"})
    assert result.returncode == 0, result.stdout + result.stderr
