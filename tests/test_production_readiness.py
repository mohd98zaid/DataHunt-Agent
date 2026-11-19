from unittest.mock import patch

import pytest

from fastapi.testclient import TestClient

from datahunt.api import app
from datahunt.config import Settings


def test_api_key_protects_settings_and_task_routes_but_not_health(monkeypatch):
    from datahunt import api

    monkeypatch.setattr(api.settings, "API_KEY", "production-secret")
    client = TestClient(app)

    assert client.get("/health").status_code == 200
    assert client.get("/api/settings").status_code == 401
    assert client.get("/api/settings", headers={"X-API-Key": "production-secret"}).status_code == 200
    assert client.get("/tasks").status_code == 401
    assert client.get("/tasks", headers={"Authorization": "Bearer production-secret"}).status_code == 200

    with client.websocket_connect("/ws/agent-stream") as websocket:
        with pytest.raises(Exception):
            websocket.receive_text()

    with client.websocket_connect("/ws/agent-stream", headers={"X-API-Key": "production-secret"}) as websocket:
        websocket.send_json({"action": "ping"})
        assert websocket.receive_json()["event"] == "pong"


def test_health_returns_service_unavailable_when_database_is_down(monkeypatch):
    from datahunt import api

    monkeypatch.setattr(api.settings, "API_KEY", "")
    with patch.object(api, "get_connection", side_effect=RuntimeError("database unavailable")):
        response = TestClient(app).get("/health")

    assert response.status_code == 503
    assert response.json()["status"] == "degraded"


def test_secret_settings_are_not_persisted_from_settings_api(monkeypatch, tmp_path):
    settings = Settings()
    settings.DATABASE_PATH = tmp_path / "settings.sqlite3"
    monkeypatch.setattr("datahunt.config.Path", lambda value: tmp_path / ".env" if str(value) == ".env" else value)

    result = settings.update_and_persist({
        "nvidia_api_key": "should-not-be-stored",
        "gemini_api_key": "should-not-be-stored",
        "freehire_api_key": "should-not-be-stored",
        "max_concurrent_runs": 3,
    })

    assert settings.MAX_CONCURRENT_RUNS == 3
    assert not settings.is_nvidia_configured
    assert not settings.is_gemini_configured
    assert result["max_concurrent_runs"] == 3


def test_production_image_excludes_local_runtime_data():
    from pathlib import Path

    dockerignore = Path(".dockerignore").read_text(encoding="utf-8")
    assert ".env" in dockerignore
    assert "*.sqlite3" in dockerignore
    assert "exports/*" in dockerignore
