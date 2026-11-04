"""Verify the chatbot endpoint is mounted on the isolated SQLite demo app."""

from fastapi.testclient import TestClient

from backend.app.demo_main import create_app


def test_chatbot_route_is_registered_without_changing_existing_demo_routes():
    with TestClient(create_app(token="synthetic-test-token")) as client:
        paths = client.get("/openapi.json").json()["paths"]
        assert "/api/v1/chat" in paths
        assert "post" in paths["/api/v1/chat"]
        assert client.post("/api/v1/chat", json={}).status_code == 422
        assert client.get("/api/v1/parking-lots").status_code == 200
