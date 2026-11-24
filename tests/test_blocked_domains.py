import json
import pytest
from datetime import datetime, timezone
from fastapi.testclient import TestClient
from datahunt.db.connection import get_connection
from datahunt.db.user_repositories import BlockedDomainsRepository, UserPreferencesRepository
from datahunt.db.repositories import JobTrackingRepository
from datahunt.api import app


def test_normalize_domain():
    assert BlockedDomainsRepository.normalize_domain("https://jo.trabajo.org/") == "jo.trabajo.org"
    assert BlockedDomainsRepository.normalize_domain("http://www.trabajo.org/job/123") == "trabajo.org"
    assert BlockedDomainsRepository.normalize_domain("jo.trabajo.org") == "jo.trabajo.org"
    assert BlockedDomainsRepository.normalize_domain("https://jobs.lever.co/company?ref=1") == "jobs.lever.co"
    assert BlockedDomainsRepository.normalize_domain("example.com:8080/path") == "example.com"
    assert BlockedDomainsRepository.normalize_domain("") == ""


def test_blocked_domains_repo_crud():
    repo = BlockedDomainsRepository()
    test_dom = "testblockdomain123.org"
    
    # Clean up before
    repo.unblock_domain(test_dom)
    
    norm = repo.block_domain(f"https://{test_dom}/careers", "Spam aggregator")
    assert norm == test_dom
    
    blocked_list = repo.get_blocked_domain_strings()
    assert test_dom in blocked_list
    
    all_blocked = repo.list_blocked_domains()
    matching = [b for b in all_blocked if b["domain"] == test_dom]
    assert len(matching) == 1
    assert matching[0]["reason"] == "Spam aggregator"
    
    # Unblock
    unblocked = repo.unblock_domain(test_dom)
    assert unblocked is True
    assert test_dom not in repo.get_blocked_domain_strings()


def _ensure_fixture_record(record_id: str, title: str, company: str, url: str) -> None:
    now_str = datetime.now(timezone.utc).isoformat()
    conn = get_connection()
    try:
        # Ensure task and run exist to satisfy foreign keys
        conn.execute(
            """INSERT OR REPLACE INTO research_tasks (
                id, request_text, normalized_spec_json, policy_version, prompt_version,
                requested_output_format, max_records, status, created_at, updated_at
            ) VALUES ('task_fix_block', 'test', '{}', 'v1', 'v1', 'json', 10, 'completed', ?, ?);""",
            (now_str, now_str)
        )
        conn.execute(
            """INSERT OR REPLACE INTO research_runs (
                id, task_id, status, attempt, started_at, finished_at, budget_json, counters_json, warning_json, created_at, updated_at
            ) VALUES ('run_fix_block', 'task_fix_block', 'completed', 1, ?, ?, '{}', '{}', '[]', ?, ?);""",
            (now_str, now_str, now_str, now_str)
        )
        conn.execute(
            """INSERT OR REPLACE INTO extracted_records (
                id, run_id, record_type, canonical_url, fields_json, confidence, verification_status, created_at, updated_at
            ) VALUES (?, 'run_fix_block', 'job_listing', ?, ?, 0.95, 'verified', ?, ?);""",
            (record_id, url, json.dumps({"title": title, "company": company, "application_url": url, "salary": "$150k"}), now_str, now_str)
        )
        conn.commit()
    finally:
        conn.close()


def test_user_preferences_ignore_and_unignore():
    u_repo = UserPreferencesRepository()
    test_record_id = "test_rec_blocking_9999"
    _ensure_fixture_record(test_record_id, "Unique Lead Architect", "Unique Alpha Corp", "https://uniquealpha.com/job")
    
    u_repo.unignore_job(test_record_id)
    u_repo.ignore_job(test_record_id, reason="Not interested in role")
    
    ignored_ids = u_repo.get_ignored_job_ids()
    assert test_record_id in ignored_ids
    
    ignored_jobs = u_repo.get_ignored_jobs()
    matching = [j for j in ignored_jobs if j["record_id"] == test_record_id]
    assert len(matching) == 1
    assert matching[0]["reason"] == "Not interested in role"
    
    unignored = u_repo.unignore_job(test_record_id)
    assert unignored is True
    assert test_record_id not in u_repo.get_ignored_job_ids()


def test_job_tracker_domain_and_job_filtering():
    j_repo = JobTrackingRepository()
    b_repo = BlockedDomainsRepository()
    u_repo = UserPreferencesRepository()
    
    test_id = "test_rec_filter_standalone"
    test_domain = "trabajo-spam-board.com"
    test_url = f"https://{test_domain}/view/999"
    _ensure_fixture_record(test_id, "Standalone AI Specialist", "Standalone Corp", test_url)
    
    initial_total = j_repo.count_jobs()
    assert any(j["id"] == test_id for j in j_repo.list_jobs(limit=100))
    
    # 1. Test job ignore
    u_repo.ignore_job(test_id, "Test ignore")
    count_after_ignore = j_repo.count_jobs()
    assert count_after_ignore == initial_total - 1
    assert not any(j["id"] == test_id for j in j_repo.list_jobs(limit=100))
    
    u_repo.unignore_job(test_id)
    assert j_repo.count_jobs() == initial_total
    assert any(j["id"] == test_id for j in j_repo.list_jobs(limit=100))
    
    # 2. Test domain blocking
    b_repo.block_domain(test_domain, "Spam board")
    count_after_block = j_repo.count_jobs()
    assert count_after_block == initial_total - 1
    assert not any(j["id"] == test_id for j in j_repo.list_jobs(limit=100))
    
    b_repo.unblock_domain(test_domain)
    assert j_repo.count_jobs() == initial_total
    assert any(j["id"] == test_id for j in j_repo.list_jobs(limit=100))


def test_api_endpoints():
    client = TestClient(app)
    
    # 1. Block domain
    res = client.post("/api/blocked-domains", json={"domain": "https://jo.trabajo.org/", "reason": "Aggregator site"})
    assert res.status_code == 200
    assert res.json()["domain"] == "jo.trabajo.org"
    
    # 2. List blocked domains
    res = client.get("/api/blocked-domains")
    assert res.status_code == 200
    domains = [b["domain"] for b in res.json()["blocked_domains"]]
    assert "jo.trabajo.org" in domains
    
    # 3. Unblock domain
    res = client.delete("/api/blocked-domains/jo.trabajo.org")
    assert res.status_code == 200
    assert res.json()["unblocked"] is True
    
    # 4. List ignored jobs
    res = client.get("/api/jobs/ignored")
    assert res.status_code == 200
    assert "ignored_jobs" in res.json()
    
    # 5. Ignore & unignore job via API
    test_id = "test_api_rec_111"
    _ensure_fixture_record(test_id, "API Test Role", "API Test Employer", "https://api-test.com/job")
    
    res = client.post(f"/api/jobs/{test_id}/ignore", json={"reason": "Irrelevant"})
    assert res.status_code == 200
    assert res.json()["record_id"] == test_id
    
    res = client.post(f"/api/jobs/{test_id}/unignore")
    assert res.status_code == 200
    assert res.json()["unignored"] is True
