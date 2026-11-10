"""혼잡도 예측 C+안: PM 가상 시연 이력(demo_hourly_usage) 기반 조회 전용 API (11/09)."""
from contextlib import closing
from datetime import datetime, timedelta

from fastapi.testclient import TestClient

from backend.app.runtime import create_runtime_app
from backend.app.simulation import demo_history


def _app(monkeypatch, tmp_path, now):
    monkeypatch.setenv("PARKON_CONTINUOUS_DEMO", "1")
    monkeypatch.setattr(demo_history, "START", now - timedelta(days=28))
    return create_runtime_app(token="synthetic-demo-token", database=tmp_path / "forecast.sqlite3",
                              clock=lambda: now)


def test_forecast_uses_demo_history_and_validates(monkeypatch, tmp_path):
    now = datetime(2026, 11, 9, 10, 0, tzinfo=demo_history.KST)
    app = _app(monkeypatch, tmp_path, now)
    with TestClient(app) as client:
        target = (now + timedelta(days=1)).replace(hour=15)
        response = client.get("/api/v1/congestion/forecast", params={"lot_id": "C", "at": target.isoformat()})
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["lot_id"] == "C" and body["is_mock"] is True and body["source"] == "demo_history"
        assert body["label"] == "가상 시연 이력 기준 예측"
        assert len(body["hours"]) == 24
        selected = body["selected"]
        assert datetime.fromisoformat(selected["at"]).hour == 15
        assert 0 <= selected["empty"] <= selected["capacity"] == 64
        assert selected["level"] in ("여유", "보통", "혼잡")
        assert body["mae_spaces"] >= 0 and body["validation_samples"] >= 24

        # 같은 이력에서 직접 계산한 값과 일치 (지어낸 숫자 아님)
        with closing(app.state.pipeline.connection()) as conn:
            direct = demo_history.forecast(conn, "C", target, now)
        assert direct["selected"] == selected


def test_forecast_rejects_out_of_scope_requests(monkeypatch, tmp_path):
    now = datetime(2026, 11, 9, 10, 0, tzinfo=demo_history.KST)
    app = _app(monkeypatch, tmp_path, now)
    with TestClient(app) as client:
        get = lambda lot, at: client.get("/api/v1/congestion/forecast", params={"lot_id": lot, "at": at})
        assert get("A", (now + timedelta(hours=3)).isoformat()).status_code == 422          # 실물 주차장
        assert get("C", (now - timedelta(hours=1)).isoformat()).status_code == 422          # 지난 시각
        assert get("C", (now + timedelta(days=8)).isoformat()).status_code == 422           # 7일 밖
        assert get("C", "2026-11-10T15:00:00").status_code == 422                           # 시간대 없음


def test_forecast_without_history_refuses(tmp_path):
    now = datetime(2026, 11, 9, 10, 0, tzinfo=demo_history.KST)
    app = create_runtime_app(token="synthetic-demo-token", database=tmp_path / "plain.sqlite3", clock=lambda: now)
    with TestClient(app) as client:
        response = client.get("/api/v1/congestion/forecast",
                              params={"lot_id": "D", "at": (now + timedelta(hours=5)).isoformat()})
        assert response.status_code == 422      # 이력 없는 DB에서는 예측하지 않음(숫자를 만들지 않음)
        with closing(app.state.pipeline.connection()) as conn:
            try:
                demo_history.forecast(conn, "D", now + timedelta(hours=5), now)
            except ValueError as exc:
                assert "기록이 부족" in str(exc)
            else:
                raise AssertionError("이력 없이 예측하면 안 됨")
