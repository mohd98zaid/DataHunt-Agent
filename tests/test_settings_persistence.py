import os
import sqlite3
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from datahunt.config import Settings, settings
from datahunt.db.settings_repository import SettingsRepository
from datahunt.api import app

@pytest.fixture
def temp_db(tmp_path):
    db_file = tmp_path / "test_settings.sqlite3"
    def _factory():
        conn = sqlite3.connect(str(db_file), timeout=5.0)
        conn.row_factory = sqlite3.Row
        return conn
    return _factory, db_file

def test_settings_repository_crud(temp_db):
    factory, _ = temp_db
    repo = SettingsRepository(conn_factory=factory)
    
    # Initially empty
    assert repo.get_all() == {}
    assert repo.get("MAX_RUN_SECONDS") is None

    # Set multiple settings
    repo.set_many({
        "MAX_RUN_SECONDS": "2400",
        "DRAIN_TIMEOUT_SECONDS": "900",
        "LLM_PROVIDER": "nvidia"
    })

    # Retrieve settings
    all_s = repo.get_all()
    assert all_s["MAX_RUN_SECONDS"] == "2400"
    assert all_s["DRAIN_TIMEOUT_SECONDS"] == "900"
    assert repo.get("LLM_PROVIDER") == "nvidia"

    # New repo instance reads same persisted data (simulating restart)
    repo2 = SettingsRepository(conn_factory=factory)
    assert repo2.get("MAX_RUN_SECONDS") == "2400"
    assert repo2.get("DRAIN_TIMEOUT_SECONDS") == "900"

    # Delete single key
    repo.delete("LLM_PROVIDER")
    assert repo.get("LLM_PROVIDER") is None
    assert repo.get("MAX_RUN_SECONDS") == "2400"

    # Clear all
    repo.clear_all()
    assert repo.get_all() == {}

def test_settings_update_and_persist_lifecycle(tmp_path, monkeypatch):
    test_db = tmp_path / "sys_settings.sqlite3"
    env_file = tmp_path / ".env"
    env_file.write_text("SOME_EXISTING_KEY=123\nMAX_RUN_SECONDS=1800\n", encoding="utf-8")

    # Point settings to temp DB
    s = Settings()
    s.DATABASE_PATH = test_db

    # Patch repo conn_factory to use test_db
    def _conn(db_path=None):
        conn = sqlite3.connect(str(test_db), timeout=5.0)
        conn.row_factory = sqlite3.Row
        return conn
    
    monkeypatch.setattr("datahunt.db.connection.get_connection", _conn)
    monkeypatch.setattr("datahunt.config.Path", lambda p: env_file if str(p) == ".env" else Path(p))

    # Update settings
    result = s.update_and_persist({
        "max_run_seconds": 3600,
        "drain_timeout_seconds": 1200,
        "max_records": 1000,
        "llm_provider": "gemini",
        "default_freshness": "24h",
        "default_collection_mode": "fast"
    })

    # In-memory values updated
    assert s.MAX_RUN_SECONDS == 3600
    assert s.DRAIN_TIMEOUT_SECONDS == 1200
    assert s.MAX_RECORDS == 1000
    assert s.LLM_PROVIDER == "gemini"
    assert s.DEFAULT_FRESHNESS == "24h"
    assert s.DEFAULT_COLLECTION_MODE == "fast"

    # Verify SQLite persistence
    repo = SettingsRepository(conn_factory=_conn)
    stored = repo.get_all()
    assert stored["MAX_RUN_SECONDS"] == "3600"
    assert stored["DRAIN_TIMEOUT_SECONDS"] == "1200"
    assert stored["MAX_RECORDS"] == "1000"

    # Simulate Cold Server Restart: Create a completely brand new Settings instance and load from DB
    cold_s = Settings()
    cold_s.DATABASE_PATH = test_db
    cold_s.load_from_db()

    assert cold_s.MAX_RUN_SECONDS == 3600
    assert cold_s.DRAIN_TIMEOUT_SECONDS == 1200
    assert cold_s.MAX_RECORDS == 1000
    assert cold_s.LLM_PROVIDER == "gemini"
    assert cold_s.DEFAULT_FRESHNESS == "24h"
    assert cold_s.DEFAULT_COLLECTION_MODE == "fast"

def test_api_settings_endpoints(monkeypatch, tmp_path):
    fake_env = tmp_path / ".env"
    monkeypatch.setattr(settings, "_sync_to_env_file", lambda updates: None)
    client = TestClient(app)

    # 1. GET /api/settings
    resp = client.get("/api/settings")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "success"
    assert "settings" in data
    assert "fields" in data
    assert len(data["fields"]) >= 5

    # 2. POST /api/settings - Update settings
    update_payload = {
        "settings": {
            "max_run_seconds": 2100,
            "drain_timeout_seconds": 750,
            "max_records": 250,
            "default_freshness": "all_time",
            "default_collection_mode": "thorough"
        }
    }
    resp_post = client.post("/api/settings", json=update_payload)
    assert resp_post.status_code == 200
    res_data = resp_post.json()
    assert res_data["status"] == "success"
    assert res_data["settings"]["max_run_seconds"] == 2100
    assert res_data["settings"]["drain_timeout_seconds"] == 750
    assert res_data["settings"]["max_records"] == 250
    assert res_data["settings"]["default_freshness"] == "all_time"
    assert res_data["settings"]["default_collection_mode"] == "thorough"

    # Verify settings reflection via GET
    resp_verify = client.get("/api/settings")
    assert resp_verify.status_code == 200
    cur_settings = resp_verify.json()["settings"]
    assert cur_settings["max_run_seconds"] == 2100
    assert cur_settings["drain_timeout_seconds"] == 750

    # 3. POST /api/settings/reset
    resp_reset = client.post("/api/settings/reset")
    assert resp_reset.status_code == 200
    reset_data = resp_reset.json()
    assert reset_data["status"] == "success"
    assert reset_data["settings"]["max_run_seconds"] == 1800
    assert reset_data["settings"]["drain_timeout_seconds"] == 600
