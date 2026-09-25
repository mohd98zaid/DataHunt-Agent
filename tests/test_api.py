from fastapi.testclient import TestClient
from datahunt.api import app

client = TestClient(app)

def test_api_root():
    resp = client.get("/")
    assert resp.status_code == 200
    assert "DATAHUNT" in resp.text.upper()

def test_api_info():
    resp = client.get("/api/info")
    assert resp.status_code == 200
    data = resp.json()
    assert data["app"] == "DataHunt"
    assert data["status"] == "online"

def test_api_health():
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "healthy"
    assert data["database"] == "connected"
    assert "models_available" in data
    assert "gemini-3.8-flash" in data["models_available"]
    assert "gemini-flash-lite-latest" in data["models_available"]

def test_api_list_tasks():
    resp = client.get("/tasks")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)

def test_api_jobs_endpoints():
    # 1. Test listing jobs
    resp = client.get("/api/jobs")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)

    if data:
        rec_id = data[0]["id"]
        # 2. Test get job detail
        det_resp = client.get(f"/api/jobs/{rec_id}")
        assert det_resp.status_code == 200
        det = det_resp.json()
        assert det["id"] == rec_id

        # 3. Test patch job status
        patch_resp = client.patch(
            f"/api/jobs/{rec_id}",
            json={
                "applied_status": "applied",
                "interview_status": "screening",
                "notes": "Test application tracking"
            }
        )
        assert patch_resp.status_code == 200
        patched = patch_resp.json()
        assert patched["applied_status"] == "applied"
        assert patched["interview_status"] == "screening"
        assert patched["notes"] == "Test application tracking"


def test_api_jobs_delete_and_bulk_delete():
    from datahunt.db import get_connection, TaskRepository, RunRepository
    from datahunt.models import ResearchTask, ResearchRun, ResearchSpec
    import json
    from datetime import datetime, timezone

    task_repo = TaskRepository()
    run_repo = RunRepository()
    task = ResearchTask(request_text="Delete test task", normalized_spec=ResearchSpec(topic="Jobs"))
    task_repo.create_task(task)
    run = ResearchRun(task_id=task.id)
    run_repo.create_run(run)

    conn = get_connection()
    now_str = datetime.now(timezone.utc).isoformat()
    test_id_1 = "test_rec_del_1"
    test_id_2 = "test_rec_del_2"

    conn.execute(
        "INSERT OR REPLACE INTO extracted_records (id, run_id, record_type, canonical_url, fields_json, confidence, verification_status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);",
        (test_id_1, run.id, "job_listing", "https://example.com/job1", json.dumps({"title": "Del Job 1", "company": "Acme"}), 0.9, "verified", now_str, now_str)
    )
    conn.execute(
        "INSERT OR REPLACE INTO extracted_records (id, run_id, record_type, canonical_url, fields_json, confidence, verification_status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);",
        (test_id_2, run.id, "job_listing", "https://example.com/job2", json.dumps({"title": "Del Job 2", "company": "Beta"}), 0.95, "verified", now_str, now_str)
    )
    conn.commit()
    conn.close()

    # Verify they exist
    r1 = client.get(f"/api/jobs/{test_id_1}")
    assert r1.status_code == 200

    # Delete single job
    del_resp = client.delete(f"/api/jobs/{test_id_1}")
    assert del_resp.status_code == 200
    assert del_resp.json()["status"] == "deleted"

    # Confirm 404 after deletion
    r1_after = client.get(f"/api/jobs/{test_id_1}")
    assert r1_after.status_code == 404

    # Deleting again should return 404
    del_404 = client.delete(f"/api/jobs/{test_id_1}")
    assert del_404.status_code == 404

    # Bulk delete test
    bulk_resp = client.post("/api/jobs/bulk-delete", json={"record_ids": [test_id_2, "non_existent_id"]})
    assert bulk_resp.status_code == 200
    assert bulk_resp.json()["count"] >= 1

    # Confirm test_id_2 is gone
    r2_after = client.get(f"/api/jobs/{test_id_2}")
    assert r2_after.status_code == 404

def test_api_export_endpoints():
    import uuid
    from datahunt.models import ResearchRun, ResearchTask, ResearchSpec, RunBudget, RunStatus, TaskStatus
    from datahunt.db import TaskRepository, RunRepository
    
    t_repo = TaskRepository()
    r_repo = RunRepository()
    
    uid = uuid.uuid4().hex[:8]
    task_id = f"task_exp_{uid}"
    run_id = f"run_exp_{uid}"
    
    spec = ResearchSpec(topic="Export test")
    task = ResearchTask(id=task_id, request_text="Export test query", normalized_spec=spec, status=TaskStatus.COMPLETED)
    t_repo.create_task(task)
    run = ResearchRun(id=run_id, task_id=task.id, status=RunStatus.COMPLETED, budget=RunBudget())
    r_repo.create_run(run)
    
    # Test on-demand MD export
    md_res = client.get(f"/runs/{run_id}/export?format=md")
    assert md_res.status_code == 200
    assert "text/markdown" in md_res.headers.get("content-type", "")
    assert "# Export test query" in md_res.text
    
    # Test on-demand DOCX export
    docx_res = client.get(f"/runs/{run_id}/export?format=docx")
    assert docx_res.status_code == 200
    assert "wordprocessingml.document" in docx_res.headers.get("content-type", "")
    assert len(docx_res.content) > 1000


