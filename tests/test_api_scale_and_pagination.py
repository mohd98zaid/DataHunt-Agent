"""
Scale and Pagination Verification Suite.
Validates with a deterministic dataset containing >= 4,250 matching jobs that:
- The API returns the correct total before pagination in X-Total-Count.
- Filters (such as run isolation and status) are applied before pagination.
- Repeated page requests do not skip or duplicate records across offsets.
- Full dataset (4,500 jobs) is completely retrieved across page boundaries.
- Run-scoped requests isolate results from general database jobs.
- Export endpoints include every matching result.
"""
import json
import uuid
import pytest
from datetime import datetime, timezone
from fastapi.testclient import TestClient

from datahunt.api import app
from datahunt.db import run_migrations, get_connection


@pytest.fixture(scope="module")
def seeded_client():
    run_migrations()
    client = TestClient(app)

    run_id_1 = f"scale_run_{uuid.uuid4().hex[:8]}"
    run_id_2 = f"scale_run_{uuid.uuid4().hex[:8]}"
    task_id_1 = f"task_{uuid.uuid4().hex[:8]}"
    task_id_2 = f"task_{uuid.uuid4().hex[:8]}"
    now_str = datetime.now(timezone.utc).isoformat()

    conn = get_connection()
    with conn:
        # Create parent tasks
        conn.execute(
            """
            INSERT OR IGNORE INTO research_tasks 
            (id, request_text, normalized_spec_json, policy_version, prompt_version, created_at, updated_at)
            VALUES (?, ?, '{"topic": "GenAI UAE"}', '1.0', '1.0', ?, ?);
            """,
            (task_id_1, "GenAI UAE 0-6 years", now_str, now_str)
        )
        conn.execute(
            """
            INSERT OR IGNORE INTO research_tasks 
            (id, request_text, normalized_spec_json, policy_version, prompt_version, created_at, updated_at)
            VALUES (?, ?, '{"topic": "GenAI India"}', '1.0', '1.0', ?, ?);
            """,
            (task_id_2, "GenAI India", now_str, now_str)
        )

        # Create parent runs
        conn.execute(
            """
            INSERT OR IGNORE INTO research_runs
            (id, task_id, status, budget_json, created_at, updated_at)
            VALUES (?, ?, 'completed', '{}', ?, ?);
            """,
            (run_id_1, task_id_1, now_str, now_str)
        )
        conn.execute(
            """
            INSERT OR IGNORE INTO research_runs
            (id, task_id, status, budget_json, created_at, updated_at)
            VALUES (?, ?, 'completed', '{}', ?, ?);
            """,
            (run_id_2, task_id_2, now_str, now_str)
        )

        # Create source doc
        doc_id = f"doc_{uuid.uuid4().hex[:8]}"
        conn.execute(
            """
            INSERT OR IGNORE INTO source_documents
            (id, run_id, requested_url, canonical_url, domain, source_type, created_at)
            VALUES (?, ?, 'https://example.com/jobs', 'https://example.com/jobs', 'example.com', 'web', ?);
            """,
            (doc_id, run_id_1, now_str)
        )

        # 1. Seed 4,500 deterministic jobs for run_1 (>= 4,250 requirement)
        records_run1 = []
        for i in range(4500):
            rec_id = f"rec_scale_r1_{i:05d}"
            fields = {
                "title": f"GenAI Engineer {i:05d}",
                "company": f"TechGlobal {i % 100:03d}",
                "location": "Dubai, UAE" if i % 2 == 0 else "Riyadh, Saudi Arabia",
                "salary": "$120,000",
                "application_url": f"https://example.com/jobs/r1/{i:05d}",
                "experience_min": 0,
                "experience_max": 6,
                "posted_at": "2026-03-01T10:00:00Z",
            }
            # Deterministic scraped_at ensures stable ordering
            scraped_time = f"2026-03-01T{10 + (i // 3600):02d}:{(i % 3600) // 60:02d}:{i % 60:02d}Z"
            records_run1.append((
                rec_id,
                run_id_1,
                doc_id,
                "job_listing",
                json.dumps(fields),
                f"https://example.com/jobs/r1/{i:05d}",
                f"ident_r1_{i:05d}",
                "verified",
                0.95,
                scraped_time,
                scraped_time,
            ))

        conn.executemany(
            """
            INSERT OR REPLACE INTO extracted_records
            (id, run_id, source_document_id, record_type, fields_json, canonical_url, identity_key, verification_status, confidence, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """,
            records_run1
        )

        # 2. Seed 500 separate jobs for run_2 to verify run scoping isolation
        records_run2 = []
        for i in range(500):
            rec_id = f"rec_scale_r2_{i:05d}"
            fields = {
                "title": f"India AI Engineer {i:05d}",
                "company": f"IndiaTech {i % 50:03d}",
                "location": "Bengaluru, India",
                "salary": "INR 25,00,000",
                "application_url": f"https://example.com/jobs/r2/{i:05d}",
                "experience_min": 2,
                "experience_max": 5,
                "posted_at": "2026-03-02T10:00:00Z",
            }
            scraped_time = f"2026-03-02T{10 + (i // 3600):02d}:{(i % 3600) // 60:02d}:{i % 60:02d}Z"
            records_run2.append((
                rec_id,
                run_id_2,
                doc_id,
                "job_listing",
                json.dumps(fields),
                f"https://example.com/jobs/r2/{i:05d}",
                f"ident_r2_{i:05d}",
                "verified",
                0.90,
                scraped_time,
                scraped_time,
            ))

        conn.executemany(
            """
            INSERT OR REPLACE INTO extracted_records
            (id, run_id, source_document_id, record_type, fields_json, canonical_url, identity_key, verification_status, confidence, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """,
            records_run2
        )

    yield {
        "client": client,
        "run_id_1": run_id_1,
        "run_id_2": run_id_2,
        "count_run_1": 4500,
        "count_run_2": 500,
        "total_jobs": 5000,
    }


def test_api_returns_correct_total_before_pagination(seeded_client):
    """
    Verify X-Total-Count accurately reports the full filtered dataset size
    before limit/offset are applied.
    """
    client = seeded_client["client"]
    run_id_1 = seeded_client["run_id_1"]

    # Limit = 10, total should still be 4,500
    res = client.get(f"/api/jobs?run_id={run_id_1}&limit=10&offset=0")
    assert res.status_code == 200
    assert "X-Total-Count" in res.headers
    total = int(res.headers["X-Total-Count"])
    assert total == 4500
    assert len(res.json()) == 10


def test_repeated_page_requests_do_not_skip_or_duplicate(seeded_client):
    """
    Verify that paginating through the entire 4,500 job dataset via the API
    (imitating the UI loop with batchSize=1000) yields all 4,500 jobs with:
    - Zero duplicate records
    - Zero skipped records
    - Strict set equivalence to seeded IDs
    """
    client = seeded_client["client"]
    run_id_1 = seeded_client["run_id_1"]
    batch_size = 1000
    offset = 0
    all_retrieved_jobs = []

    while True:
        res = client.get(f"/api/jobs?run_id={run_id_1}&limit={batch_size}&offset={offset}")
        assert res.status_code == 200
        batch = res.json()
        if not batch:
            break
        all_retrieved_jobs.extend(batch)
        offset += len(batch)
        if len(batch) < batch_size:
            break

    # Total count match
    assert len(all_retrieved_jobs) == 4500

    # Zero duplicates
    retrieved_ids = [j["id"] for j in all_retrieved_jobs]
    unique_ids = set(retrieved_ids)
    assert len(unique_ids) == 4500

    # Zero skipped records (all 4500 expected IDs present)
    expected_ids = {f"rec_scale_r1_{i:05d}" for i in range(4500)}
    assert unique_ids == expected_ids


def test_run_isolation_and_global_unscoped_dataset(seeded_client):
    """
    Verify that:
    1. Scoped search for run_2 returns exactly 500 jobs (no leakage from run_1).
    2. Unscoped query /api/jobs returns all 5,000 jobs across runs.
    """
    client = seeded_client["client"]
    run_id_2 = seeded_client["run_id_2"]

    # Scoped query for run 2
    res_r2 = client.get(f"/api/jobs?run_id={run_id_2}&limit=50")
    assert res_r2.status_code == 200
    assert int(res_r2.headers["X-Total-Count"]) == 500

    # Global unscoped query (includes seeded 5000 jobs plus any pre-existing jobs in db)
    res_global = client.get("/api/jobs?limit=50")
    assert res_global.status_code == 200
    assert int(res_global.headers["X-Total-Count"]) >= 5000


def test_export_includes_entire_matching_dataset(seeded_client):
    """
    Verify that export endpoints (/runs/{run_id}/records) return all 4,500 matching results
    without fixed batch truncation.
    """
    client = seeded_client["client"]
    run_id_1 = seeded_client["run_id_1"]

    res = client.get(f"/runs/{run_id_1}/records")
    assert res.status_code == 200
    records = res.json()
    assert len(records) == 4500
    assert records[0]["fields"]["title"].startswith("GenAI Engineer")
