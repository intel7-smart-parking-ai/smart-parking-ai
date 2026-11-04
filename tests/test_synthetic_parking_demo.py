from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from backend.app.demo_main import create_app
from backend.app.simulation.run_parking_demo import A_SPACES, DemoError, run_demo


@pytest.mark.parametrize(("short", "empty_during_entry"), [(False, 0), (True, 9)])
def test_a_spaces_enter_and_exit(short, empty_during_entry):
    time = [datetime(2026, 11, 6, tzinfo=timezone.utc)]
    token = "synthetic-test-token"
    output = []

    def advance(seconds):
        time[0] += timedelta(seconds=seconds)

    assert len({space.space_id for space in A_SPACES}) == 12
    with TestClient(create_app(token=token, clock=lambda: time[0])) as client:
        with pytest.raises(DemoError, match="인증 실패"):
            run_demo(client, "invalid-test-token", output=lambda _: None)
        run_demo(client, token, short=short, wait=advance, clock=lambda: time[0], output=output.append)
        body = client.get("/api/v1/parking-lots/A/spaces").json()
        assert set(body) == {"items", "next_cursor", "as_of"}
        assert len(body["items"]) == 12
        assert all(row["status"] == "empty" for row in body["items"])
        updated = {row["space_id"] for row in body["items"] if row["version"] == 7}
        assert updated == ({"A-01", "A-02", "A-03"} if short else {space.space_id for space in A_SPACES})
    assert [line.rsplit(" | ", 1)[-1] for line in output] == [
        "빈자리 12/12", f"빈자리 {empty_during_entry}/12", "빈자리 12/12",
    ]
