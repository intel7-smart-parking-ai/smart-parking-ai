"""1단계 검증 전용 설정. 기본 URL·.env 로딩·엔진/DB 접속은 없다."""

import os
import re
import stat
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError


class DatabaseSafetyError(RuntimeError):
    """검증 범위 밖 설정/경로. URL 원문이나 비밀값은 오류에 싣지 않는다."""


def reject_linked_directories(directory: Path) -> None:
    """허용 디렉터리와 조상에 심볼릭 링크·junction/reparse가 있으면 거절한다."""
    for path in (directory, *directory.parents):
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode) or (
            getattr(metadata, "st_file_attributes", 0)
            & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
        ):
            raise DatabaseSafetyError("경로 별칭이 있는 검증 디렉터리는 허용하지 않습니다.")


def validate_target(url: URL, test_root: Path) -> Path:
    """호출자가 승인한 pytest 임시 디렉터리의 직접 자식 파일만 허용한다."""
    if url.drivername not in {"sqlite", "sqlite+pysqlite"}:
        raise DatabaseSafetyError("이번 단계는 파일 기반 SQLite 검증만 허용합니다.")
    if any(value is not None for value in (url.host, url.port, url.username, url.password)) or url.query:
        raise DatabaseSafetyError("호스트·인증·URI 옵션이 있는 URL은 허용하지 않습니다.")
    database = url.database or ""
    if not database or database == ":memory:" or database.startswith(("//", "\\\\", "file:")):
        raise DatabaseSafetyError("명시적인 임시 SQLite 파일 경로가 필요합니다.")
    if any(character in database for character in "%$~?#"):
        raise DatabaseSafetyError("별칭 또는 인코딩 경로는 허용하지 않습니다.")
    parts = database.replace("\\", "/").split("/")
    for part in parts:
        if part in {".", ".."} or part.rstrip(" .") != part:
            raise DatabaseSafetyError("점 또는 끝 공백을 포함한 경로는 허용하지 않습니다.")
        if ":" in part and not re.fullmatch(r"[A-Za-z]:", part):
            raise DatabaseSafetyError("장치 또는 대체 스트림 경로는 허용하지 않습니다.")
    path = Path(database)
    root = Path(test_root)
    if not path.is_absolute() or not root.is_absolute():
        raise DatabaseSafetyError("DB와 허용 임시 디렉터리는 절대 경로여야 합니다.")
    if path.name.casefold() == "smart_parking.db":
        raise DatabaseSafetyError("billing 실사용 DB 파일은 허용하지 않습니다.")
    if not re.fullmatch(r"kan17-verify-[0-9a-f]{32}\.sqlite3", path.name):
        raise DatabaseSafetyError("검증 전용 UUID 파일 이름이 필요합니다.")
    # 외부 경로는 resolve/stat 전에 거절하여 실사용 파일을 조사하지 않는다.
    if path.parent != root:
        raise DatabaseSafetyError("승인된 임시 디렉터리 밖 DB는 허용하지 않습니다.")
    try:
        reject_linked_directories(root)
        if not root.is_dir() or root.resolve(strict=True) != root:
            raise DatabaseSafetyError("실제 임시 디렉터리만 허용합니다.")
    except OSError:
        raise DatabaseSafetyError("임시 디렉터리를 안전하게 확인할 수 없습니다.") from None
    return path


@dataclass(frozen=True)
class DatabaseSettings:
    url: URL = field(repr=False)
    test_root: Path

    @classmethod
    def from_env(cls, *, test_root: Path, environ: Mapping[str, str] | None = None):
        values = os.environ if environ is None else environ
        raw = values.get("DATABASE_URL", "")
        if not raw or not raw.strip():
            raise DatabaseSafetyError("DATABASE_URL 미설정: 검증을 실행하지 않습니다.")
        try:
            url = make_url(raw)
            validate_target(url, test_root)
        except DatabaseSafetyError:
            raise
        except (ArgumentError, TypeError, ValueError):
            raise DatabaseSafetyError("검증용 DATABASE_URL 형식이 올바르지 않습니다.") from None
        return cls(url=url, test_root=Path(test_root))
