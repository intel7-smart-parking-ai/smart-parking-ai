"""검증 DB를 명시적으로 준비한다. import만으로 실행되는 작업은 없다."""

import os
import sqlite3
import stat
from collections.abc import Iterator

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

from backend.app.database.settings import DatabaseSafetyError, DatabaseSettings, validate_target


def create_test_engine(settings: DatabaseSettings) -> Engine:
    """기존 파일을 열지 않고 새 검증 파일을 확보한다. 스키마 생성은 안 한다."""
    target = validate_target(settings.url, settings.test_root)
    try:
        descriptor = os.open(target, os.O_CREAT | os.O_EXCL | os.O_RDWR | getattr(os, "O_BINARY", 0), 0o600)
    except OSError:
        raise DatabaseSafetyError("기존 파일 또는 생성 불가 경로는 열지 않습니다.") from None
    try:
        owned = os.fstat(descriptor)
    finally:
        os.close(descriptor)

    def connect_owned_file():
        validate_target(settings.url, settings.test_root)
        try:
            current = target.lstat()
        except OSError:
            raise DatabaseSafetyError("확보한 검증 파일이 없어 연결을 중단합니다.") from None
        if (
            not stat.S_ISREG(current.st_mode)
            or (current.st_dev, current.st_ino) != (owned.st_dev, owned.st_ino)
            or current.st_nlink != 1
            or getattr(current, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
        ):
            raise DatabaseSafetyError("교체되거나 별칭이 생긴 검증 파일은 열지 않습니다.")
        # mode=rw: 연결 시 파일이 사라져도 SQLite가 새 파일을 자동 생성하지 않는다.
        return sqlite3.connect(target.as_uri() + "?mode=rw", uri=True, check_same_thread=False)

    engine = create_engine(settings.url, creator=connect_owned_file, poolclass=NullPool, hide_parameters=True)

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, record):
        cursor = connection.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys=ON")
            if cursor.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
                raise DatabaseSafetyError("검증 연결에서 FK 강제를 활성화하지 못했습니다.")
        finally:
            cursor.close()

    return engine


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, close_resets_only=False)


def get_db(factory: sessionmaker[Session]) -> Iterator[Session]:
    """호출자가 commit을 결정한다. 예외 시 rollback, 모든 경로에서 close한다.

    FastAPI 미등록 검증용 공급자다. 정상 종료의 미커밋 내용도 close로 rollback된다.
    """
    database = factory()
    try:
        yield database
    except BaseException:
        database.rollback()
        raise
    finally:
        database.close()
