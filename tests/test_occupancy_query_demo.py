import json
from datetime import datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.api.occupancy_query_demo import create_demo_app

POST = "/api/v1/internal/vision-events"
LOTS = "/api/v1/parking-lots"
SPACES = f"{LOTS}/A/spaces"
LOCATION = "/api/v1/vehicles/10/location"


def test_vision_to_kan16_demo():
    fixture = Path(__file__).parent / "fixtures" / "vision_event_sequences.json"
    sample = json.loads(fixture.read_text("utf-8"))["normal"][0]
    start = datetime.fromisoformat(sample["observed_at"])
    token = "synthetic-test-token"
    clock = [start]

    with TestClient(create_demo_app(token=token, clock=lambda: clock[0])) as client:
        assert client.post(POST, json=sample).status_code == 403
        for path in (LOTS, SPACES, LOCATION):
            assert client.get(path).status_code == 403
        client.headers["Authorization"] = f"Bearer {token}"

        def verify(status, empty_count):
            response = client.get(SPACES)
            assert response.headers["X-ParkFlow-Data-Source"] == "mock"
            spaces = response.json()
            assert set(spaces) == {"items", "as_of", "next_cursor"}
            row = next(item for item in spaces["items"] if item["space_id"] == "A-01")
            assert row["status"] == status
            lots = client.get(LOTS).json()
            assert [lot["total_spaces"] for lot in lots["items"]] == [12, 16, 12, 64]
            assert lots["items"][0]["empty_spaces"] == empty_count
            assert sum(item["status"] == "empty" for item in spaces["items"]) == empty_count
            return row

        verify("empty", 12)
        for tick in range(7):
            clock[0] = start + timedelta(seconds=tick)
            event = sample | {"event_id": f"query-demo-{tick}", "source": "simulation",
                              "observed_at": clock[0].isoformat()}
            if tick >= 3:
                event |= {"event_type": "space_empty_observed", "car_id": None, "bbox_xyxy": None}
            assert client.post(POST, json=event).status_code == 202
            if tick == 2:
                occupied = verify("occupied", 11)
                assert occupied["car_id"] == "10"
                assert client.post(POST, json=event).status_code == 200
                assert verify("occupied", 11) == occupied
                location = client.get(LOCATION).json()
                assert (location["space_id"], location["session_id"]) == ("A-01", None)
            if tick == 3:
                verify("uncertain", 11)
        verify("empty", 12)
        assert client.get(LOCATION).json()["error"]["code"] == "VEHICLE_NOT_FOUND"
