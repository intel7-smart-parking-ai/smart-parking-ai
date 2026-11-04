"""Single local demo process for synthetic occupancy and mock billing."""
import sqlite3
from contextlib import asynccontextmanager, closing
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from fastapi import HTTPException

from backend.app.api.billing_router import billing_router
from backend.app.api.occupancy_query_demo import create_demo_app
from backend.app.billing.db import get_db_conn, init_schema, seed_defaults
from backend.app.database.parking_sessions import SessionError, enter, exit_session


def create_app(*, token: str, clock=lambda: datetime.now(timezone.utc)):
    def sync_session(row, observed_at):
        session_id = row.get("session_id")
        if row["status"] == "occupied" and session_id is not None:
            if row["car_id"] != row["session_car_id"]:
                raise HTTPException(409, "활성 세션의 차량 식별 변경은 확인이 필요합니다.")
            return
        if row["status"] == "empty" and session_id is None:
            return

        try:
            with closing(sqlite3.connect(app.state.billing_path)) as conn:
                if row["status"] == "occupied":
                    session = enter(
                        conn, session_id=str(uuid4()), lot_id=row["lot_id"],
                        space_id=row["space_id"], car_id=row["car_id"], entry_at=observed_at,
                    )
                    row.update(session_id=session.session_id, session_car_id=session.car_id)
                else:
                    exit_session(conn, session_id=session_id, exit_at=observed_at)
                    row.pop("session_id")
                    row.pop("session_car_id")
        except SessionError as exc:
            raise HTTPException(exc.status_code, str(exc)) from exc
        except sqlite3.Error as exc:
            raise HTTPException(503, "시연 세션 저장 실패. 동일 이벤트를 재시도하세요.") from exc

    app = create_demo_app(token=token, clock=clock, public_reads=True, on_confirmed=sync_session,
                          max_events=20_000)
    app.title = "ParkFlow 로컬 통합 시연"

    @asynccontextmanager
    async def lifespan(application):
        with TemporaryDirectory(prefix="parkflow-single-demo-") as directory:
            path = Path(directory) / "billing.sqlite3"
            with closing(sqlite3.connect(path)) as conn, conn:
                init_schema(conn)
                columns = {row[1] for row in conn.execute("PRAGMA table_info(parking_sessions)")}
                if "status" not in columns:
                    conn.execute(
                        "ALTER TABLE parking_sessions ADD COLUMN status TEXT "
                        "CHECK (status IN ('active', 'closed'))"
                    )
                seed_defaults(conn)
                conn.execute(
                    "INSERT INTO parking_sessions "
                    "(session_id, lot_id, space_id, car_id, entry_at, exit_at, status) "
                    "VALUES (?,?,?,?,?,?,'closed')",
                    ("ses_demo", "A", "A-01", "10", "2026-11-06T09:00:00+09:00",
                     "2026-11-06T09:30:00+09:00"),
                )
            application.state.billing_path = path
            yield

    def temporary_billing_connection():
        with closing(sqlite3.connect(app.state.billing_path, check_same_thread=False)) as conn:
            conn.row_factory = sqlite3.Row
            yield conn

    app.router.lifespan_context = lifespan
    app.dependency_overrides[get_db_conn] = temporary_billing_connection
    app.include_router(billing_router)
    return app


if __name__ == "__main__":
    from getpass import getpass

    import uvicorn

    uvicorn.run(create_app(token=getpass("시연 토큰: ")), host="127.0.0.1", port=8002)
