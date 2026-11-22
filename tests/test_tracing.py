import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi.testclient import TestClient

from datahunt.tracing import traceable, query_project_runs, retrieve_run_details, query_project_threads
from datahunt.api import app


def test_traceable_decorator_passthrough():
    @traceable(run_type="chain", name="TestFunction")
    def sample_func(x: int, y: int) -> int:
        return x + y

    assert sample_func(3, 4) == 7


@pytest.mark.asyncio
async def test_query_project_runs_unconfigured():
    with patch("datahunt.config.settings.LANGSMITH_API_KEY", ""):
        runs = await query_project_runs(project_name="NonExistentProject", limit=5)
        assert isinstance(runs, list)


@pytest.mark.asyncio
async def test_retrieve_run_details_unconfigured():
    with patch("datahunt.config.settings.LANGSMITH_API_KEY", ""):
        res = await retrieve_run_details(run_id="fake-id", project_name="NonExistentProject")
        assert res is None


@pytest.mark.asyncio
async def test_query_project_threads_unconfigured():
    with patch("datahunt.config.settings.LANGSMITH_API_KEY", ""):
        threads = await query_project_threads(project_name="NonExistentProject", limit=5)
        assert isinstance(threads, list)


def test_telemetry_api_routes():
    client = TestClient(app)

    # 1. /telemetry/runs
    resp = client.get("/telemetry/runs?limit=5")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "success"
    assert "runs" in data
    assert "project" in data

    # 2. /telemetry/threads
    resp_th = client.get("/telemetry/threads?limit=5")
    assert resp_th.status_code == 200
    data_th = resp_th.json()
    assert data_th["status"] == "success"
    assert "threads" in data_th
