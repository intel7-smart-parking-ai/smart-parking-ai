"""Single local demo process for synthetic occupancy and mock billing."""
import sqlite3
from contextlib import asynccontextmanager, closing
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from backend.app.api.billing_router import billing_router
from backend.app.api.occupancy_query_demo import create_demo_app
from backend.app.billing.db import get_db_conn, init_schema, seed_defaults


def create_app(*, token: str, clock=lambda: datetime.now(timezone.utc)):
    app = create_demo_app(token=token, clock=clock, public_reads=True)
    app.title = "ParkFlow 로컬 통합 시연"

    @asynccontextmanager
    async def lifespan(application):
        with TemporaryDirectory(prefix="parkflow-single-demo-") as directory:
            path = Path(directory) / "billing.sqlite3"
            with closing(sqlite3.connect(path)) as conn, conn:
                init_schema(conn)
                seed_defaults(conn)
                conn.execute(
                    "INSERT INTO parking_sessions "
                    "(session_id, lot_id, space_id, car_id, entry_at, exit_at) VALUES (?,?,?,?,?,?)",
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
