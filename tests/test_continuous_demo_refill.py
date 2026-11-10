"""연속 시연: 재시작 때 C·D를 예전 차로 복구하지 않고, 그 시간대 평균 점유율로 새로 채워 이어간다 (PM 11/09 방식 2).

- 재시작 전 C·D 주차 차량은 서버 관측 경로로 출차 처리되고(세션 closed + 자동 모의 결제 1건)
- 새 차량은 세션이 남지 않은 칸에만 놓이고, 서버가 시뮬레이션 관측을 확정할 때만 세션이 된다
- 한 칸에 활성 세션은 1개뿐, 보충 이력(gap-*)은 재시작 시각 전에 모두 출차
"""
from contextlib import closing
from datetime import datetime, timedelta
from time import monotonic, sleep

from fastapi.testclient import TestClient

from backend.app.runtime import create_runtime_app
from backend.app.simulation import demo_history


def _wait(predicate, seconds=15):
    deadline = monotonic() + seconds
    while monotonic() < deadline:
        if predicate():
            return True
        sleep(.2)
    return predicate()


def _active(conn, lot):
    return {row["space_id"]: row["session_id"] for row in conn.execute(
        "SELECT space_id, session_id FROM parking_sessions WHERE lot_id=? AND status='active'", (lot,))}


def _seeded(conn, lot):
    return {row["space_id"] for row in conn.execute(
        "SELECT space_id FROM simulation_targets WHERE lot_id=? AND occupied=1", (lot,))}


def test_restart_refills_virtual_lots_instead_of_restoring(monkeypatch, tmp_path):
    monkeypatch.setenv("PARKON_CONTINUOUS_DEMO", "1")
    start = datetime(2026, 11, 7, tzinfo=demo_history.KST)
    monkeypatch.setattr(demo_history, "START", start)
    now = [start + timedelta(days=1, hours=14)]
    database = tmp_path / "refill.sqlite3"

    def app():
        started = monotonic()
        return create_runtime_app(token="synthetic-demo-token", database=database,
                                  clock=lambda: now[0] + timedelta(seconds=monotonic() - started))

    first = app()
    with TestClient(first), closing(first.state.pipeline.connection()) as conn:
        low, high = demo_history.REFILL_BOUNDS
        for lot in "CD":
            seeded = _seeded(conn, lot)
            assert round(low * 64) <= len(seeded) <= round(high * 64)
        # 첫 시작에도 바로 적당히 찬 상태: 서버가 관측을 확정해 세션이 생긴다.
        assert _wait(lambda: set(_active(conn, "C")) == _seeded(conn, "C"))
        before = _active(conn, "C")
        assert before

    now[0] += timedelta(hours=3)
    restarted = app()
    restart_at = now[0]
    with TestClient(restarted), closing(restarted.state.pipeline.connection()) as conn:
        seeded = _seeded(conn, "C")
        # 새 차는 예전 세션이 남은 칸에 놓이지 않는다.
        assert not seeded & set(before)
        assert seeded
        # 예전 차는 서버 관측으로 출차, 새 차는 서버 확정으로 입차.
        # 대기 중에도 시뮬레이터가 대상 칸을 바꿀 수 있어, 그 시점의 대상 칸과 비교한다.
        assert _wait(lambda: set(_active(conn, "C")) == _seeded(conn, "C"))
        old = conn.execute(
            "SELECT status, exit_at FROM parking_sessions WHERE session_id IN (%s)"
            % ",".join("?" * len(before)), tuple(before.values())).fetchall()
        assert {row["status"] for row in old} == {"closed"}
        assert all(row["exit_at"] >= restart_at.isoformat() for row in old)
        demo_history.checkpoint(conn, restart_at + timedelta(seconds=20))
        demo_history.checkpoint(conn, restart_at + timedelta(seconds=20))
        paid = dict(conn.execute(
            "SELECT session_id, count(*) FROM mock_payments WHERE session_id IN (%s) GROUP BY session_id"
            % ",".join("?" * len(before)), tuple(before.values())))
        assert paid == {sid: 1 for sid in before.values()}
        # 한 칸에 활성 세션 1개, 보충 이력은 재시작 전에 모두 끝남.
        assert conn.execute(
            "SELECT count(*) FROM (SELECT lot_id, space_id FROM parking_sessions WHERE status='active'"
            " GROUP BY lot_id, space_id HAVING count(*) > 1)").fetchone()[0] == 0
        assert conn.execute(
            "SELECT count(*) FROM parking_sessions WHERE session_id GLOB 'gap-*' AND exit_at > ?",
            (restart_at.isoformat(),)).fetchone()[0] == 0
        # 가상 번호판 형식과 등록 차량 번호 회피
        plates = [row[0] for row in conn.execute(
            "SELECT plate_number FROM simulation_targets WHERE occupied=1")]
        assert all(p and p[-4:].isdigit() for p in plates)
        assert not {p[-4:] for p in plates} & set(demo_history.REGISTERED_VEHICLES)
