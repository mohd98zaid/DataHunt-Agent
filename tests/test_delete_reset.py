import pytest
from fastapi.testclient import TestClient
from datahunt.db import get_connection
from datahunt.db.repositories import JobTrackingRepository
from datahunt.api import app


def test_database_stats_endpoint():
    client = TestClient(app)
    resp = client.get("/api/database/stats")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "success"
    assert "stats" in data
    assert "size_mb" in data["stats"]
    assert "counts" in data["stats"]


def test_job_repo_delete_and_purge():
    repo = JobTrackingRepository()

    # 1. Test purge_all_data
    deleted = repo.purge_all_data()
    assert isinstance(deleted, dict)
    assert "extracted_records" in deleted

    # Confirm counts are 0
    stats = repo.get_database_stats()
    assert stats["counts"]["extracted_records"] == 0
    assert stats["counts"]["source_documents"] == 0
    assert stats["counts"]["research_runs"] == 0


def test_api_reset_endpoint():
    client = TestClient(app)
    resp = client.post("/api/jobs/reset")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "success"
    assert "counts" in data
