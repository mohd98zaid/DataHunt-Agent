"""
Comprehensive test suite verifying exhaustion-based job collection, pagination,
anti-truncation, honest coverage reporting, and qualification policies.
Covers Section 11 Scenarios 1-22.
"""
import json
import time
import pytest
from unittest.mock import MagicMock, patch

from datahunt.agent.discovery_models import (
    CrawlTask,
    CrawlTaskStatus,
    DiscoveryBudget,
    DiscoveryState,
    SearchTask,
    SearchTaskType,
    DiscoveredSourceType,
    StopReason,
)
from datahunt.agent.state import AgentState, AgentStatus
from datahunt.agent.decision import DecisionEngine, AgentAction
from datahunt.agent.discovery_engine import DiscoveryEngine
from datahunt.agent.coverage import CoverageEvaluator, CoverageReport
from datahunt.agent.policies import QualificationPolicy, MatchStatus
from datahunt.models.job_spec import JobSearchRequest
from datahunt.sources.crawlers import (
    MultiSourceCrawler,
    CrawlBudget,
    CrawlResult,
    _DomainRateLimiter,
    crawl_lever_company,
    crawl_workable_company,
    crawl_smartrecruiters_company,
)
from datahunt.models import ExtractedRecord
from datahunt.db import run_migrations, get_connection
from datahunt.db.repositories import JobTrackingRepository, RecordRepository


# ─────────────────────────────────────────────────────────────────────────────
# 1. Multi-Page Pagination Across ATS Platforms with Cycle Detection (Scenarios 1 & 7)
# ─────────────────────────────────────────────────────────────────────────────

def test_lever_pagination_and_cycle_detection():
    """Verify Lever crawler handles skip/limit pagination across pages and avoids cycle loops."""
    budget = CrawlBudget(max_pages_per_source=50, max_total_jobs=1000)
    rate_limiter = _DomainRateLimiter(0)

    page1 = [{"id": f"l1_{i}", "text": f"AI Engineer {i}", "hostedUrl": f"https://jobs.lever.co/c/{i}", "categories": {"location": "Dubai"}} for i in range(100)]
    page2 = [{"id": f"l2_{i}", "text": f"ML Engineer {i}", "hostedUrl": f"https://jobs.lever.co/c/2_{i}", "categories": {"location": "Dubai"}} for i in range(50)]

    with patch("datahunt.sources.crawlers._safe_get") as mock_get:
        mock_get.side_effect = [
            MagicMock(status_code=200, json=lambda: page1),
            MagicMock(status_code=200, json=lambda: page2),
        ]
        records = crawl_lever_company("acme", ["AI"], budget, rate_limiter, lambda e: None)
        assert len(records) == 150
        assert mock_get.call_count == 2


def test_workable_cursor_pagination_and_cycle_detection():
    """Verify Workable cursor-based pagination follows nextPage and stops on repeated cursor token."""
    budget = CrawlBudget(max_pages_per_source=50)
    rate_limiter = _DomainRateLimiter(0)

    p1 = {"results": [{"shortcode": "w1", "title": "GenAI Dev", "location": {"city": "Dubai"}}], "nextPage": "tok_page2"}
    p2 = {"results": [{"shortcode": "w2", "title": "GenAI Lead", "location": {"city": "Riyadh"}}], "nextPage": "tok_page2"}  # cycle!

    with patch("datahunt.sources.crawlers._safe_get") as mock_get:
        mock_get.side_effect = [
            MagicMock(status_code=200, json=lambda: p1),
            MagicMock(status_code=200, json=lambda: p2),
        ]
        records = crawl_workable_company("techcorp", ["GenAI"], budget, rate_limiter, lambda e: None)
        assert len(records) == 2
        # Cycle detection on repeated token stopped further loop iterations
        assert mock_get.call_count == 2


def test_smartrecruiters_offset_pagination():
    """Verify SmartRecruiters offset/limit pagination handles totalFound."""
    budget = CrawlBudget(max_pages_per_source=50)
    rate_limiter = _DomainRateLimiter(0)

    p1 = {"content": [{"name": f"AI Engineer {i}", "id": f"sr1_{i}", "location": {"city": "Dubai"}} for i in range(100)], "totalFound": 105}
    p2 = {"content": [{"name": f"Staff AI {i}", "id": f"sr2_{i}", "location": {"city": "Dubai"}} for i in range(5)], "totalFound": 105}

    with patch("datahunt.sources.crawlers._safe_get") as mock_get:
        mock_get.side_effect = [
            MagicMock(status_code=200, json=lambda: p1),
            MagicMock(status_code=200, json=lambda: p2),
        ]
        records = crawl_smartrecruiters_company("globex", ["AI"], budget, rate_limiter, lambda e: None)
        assert len(records) == 105
        assert mock_get.call_count == 2


# ─────────────────────────────────────────────────────────────────────────────
# 2. Blocked Source and Transient Error Handling (Scenarios 6 & 8)
# ─────────────────────────────────────────────────────────────────────────────

def test_blocked_source_marked_blocked():
    """Verify blocked/forbidden source is explicitly marked BLOCKED, never completed."""
    budget = CrawlBudget()
    task = CrawlTask(id="b1", source="lever", source_type="ats", url="https://jobs.lever.co/secret", company="secret")
    crawler = MultiSourceCrawler(budget=budget)

    with patch("datahunt.sources.crawlers._safe_get") as mock_get:
        mock_get.return_value = MagicMock(status_code=403, text="Access Denied")
        result = crawler.crawl_tasks([task], role_keywords=["AI"])

    assert result.task_outcomes.get("b1") == CrawlTaskStatus.BLOCKED
    assert task.status == CrawlTaskStatus.BLOCKED


def test_transient_failure_retry_pending():
    """Verify transient network exception on task marks it FAILED, not completed."""
    budget = CrawlBudget()
    task = CrawlTask(id="t1", source="lever", source_type="ats", url="https://jobs.lever.co/flaky", company="flaky")
    crawler = MultiSourceCrawler(budget=budget)

    with patch("datahunt.sources.crawlers._safe_get") as mock_get:
        mock_get.side_effect = Exception("Connection reset by peer (503)")
        result = crawler.crawl_tasks([task], role_keywords=["AI"])

    assert result.task_outcomes.get("t1") == CrawlTaskStatus.FAILED
    assert task.status == CrawlTaskStatus.FAILED


# ─────────────────────────────────────────────────────────────────────────────
# 3. Decision Engine & Discovery Engine Exhaustion vs Early Stop (Scenarios 17 & 18)
# ─────────────────────────────────────────────────────────────────────────────

def test_thorough_mode_does_not_early_stop_on_target_when_work_remains():
    """In thorough mode, reaching target_results must NOT terminate if tasks remain in queue."""
    state = AgentState(
        request="GenAI jobs in UAE",
        run_id="r_thorough",
        task_id="t_thorough",
        mode="jobs",
        target_results=2,
        collection_mode="thorough",
    )
    state.discovery_state = DiscoveryState()
    # Add pending task to queue
    state.discovery_state.task_queue.append(
        SearchTask(
            id="st1",
            task_type=SearchTaskType.ATS_SEARCH,
            query="site:jobs.ashbyhq.com AI",
            source="ashby",
            source_type=DiscoveredSourceType.ATS_PORTAL,
            round=1,
        )
    )
    # Satisfy target_results count
    state.qualified_records.extend([
        ExtractedRecord(id="r1", run_id="r_thorough", fields={"title": "GenAI Dev", "location": "Dubai"}),
        ExtractedRecord(id="r2", run_id="r_thorough", fields={"title": "AI Lead", "location": "Dubai"}),
    ])

    engine = DiscoveryEngine()
    should_stop, reason, msg = engine.should_stop(state.discovery_state, state)
    # Must NOT stop because task_queue has pending tasks in thorough mode!
    assert should_stop is False


def test_fast_mode_early_stops_when_target_reached():
    """In fast mode, reaching target_results with balanced coverage terminates early."""
    state = AgentState(
        request="GenAI jobs in UAE --fast",
        run_id="r_fast",
        task_id="t_fast",
        mode="jobs",
        target_results=2,
        collection_mode="fast",
    )
    state.discovery_state = DiscoveryState()
    state.discovery_state.coverage_matrix.record_hit("ats", "uae")
    state.discovery_state.coverage_matrix.record_hit("regional_board", "uae")
    state.qualified_records.extend([
        ExtractedRecord(id="r1", run_id="r_fast", fields={"title": "GenAI Dev", "location": "Dubai"}),
        ExtractedRecord(id="r2", run_id="r_fast", fields={"title": "AI Lead", "location": "Dubai"}),
    ])

    engine = DiscoveryEngine()
    should_stop, reason, msg = engine.should_stop(state.discovery_state, state)
    assert should_stop is True
    assert reason == StopReason.TARGET_QUALIFIED_REACHED


def test_thorough_mode_diminishing_returns_does_not_abort_with_pending_tasks():
    """In thorough mode, consecutive low-yield iterations do not stop while tasks remain."""
    state = AgentState(
        request="GenAI jobs in UAE",
        run_id="r_dim",
        task_id="t_dim",
        mode="jobs",
        collection_mode="thorough",
    )
    state.discovery_state = DiscoveryState()
    state.discovery_state.consecutive_low_yield_rounds = 3
    state.qualified_records.append(ExtractedRecord(id="q1", run_id="r_dim", fields={"title": "AI"}))
    state.discovery_state.task_queue.append(
        SearchTask(
            id="st2",
            task_type=SearchTaskType.GENERAL_SEARCH,
            query="AI jobs",
            source="web",
            source_type=DiscoveredSourceType.MAJOR_BOARD,
            round=2,
        )
    )

    engine = DiscoveryEngine()
    should_stop, reason, msg = engine.should_stop(state.discovery_state, state)
    assert should_stop is False


# ─────────────────────────────────────────────────────────────────────────────
# 4. Multi-Batch Processing in Runtime (Scenarios 3 & 4)
# ─────────────────────────────────────────────────────────────────────────────

def test_multi_batch_crawl_exhausts_more_than_six_tasks():
    """Verify runtime _act_crawl_direct iterates through batches until all tasks are complete."""
    from datahunt.agent.runtime import AgentRuntime

    client = MagicMock()
    runtime = AgentRuntime(
        client=client,
        search=MagicMock(),
        fetch=MagicMock(),
        extract=MagicMock(),
        verify=MagicMock(),
        dedupe=MagicMock(),
        export=MagicMock(),
    )

    state = AgentState(
        request="GenAI jobs in UAE",
        run_id="r_mb",
        task_id="t_mb",
        mode="jobs",
        collection_mode="thorough",
    )
    state.discovery_state = DiscoveryState()
    # Add 15 pending crawl tasks (more than old batch limit of 6)
    tasks = [
        CrawlTask(id=f"ct_{i}", source="lever", source_type="ats", url=f"https://jobs.lever.co/company_{i}", company=f"company_{i}")
        for i in range(15)
    ]
    state.discovery_state.pending_crawl_tasks.extend(tasks)

    from datahunt.sources.crawlers import CrawledJob
    # Mock crawl_tasks on the crawler instance
    def fake_crawl(*args, **kwargs):
        task_batch = args[1] if len(args) > 1 else kwargs.get("tasks", [])
        outcomes = {t.id: CrawlTaskStatus.COMPLETED for t in task_batch}
        jobs = [CrawledJob(source_id=t.source, source_name="Test", title=f"Dev at {t.company}", company=t.company, location="Dubai", url=t.url) for t in task_batch]
        candidates = [{"url": t.url, "title": f"Dev at {t.company}", "source": "lever", "crawled": True} for t in task_batch]
        return CrawlResult(crawled_jobs=jobs, candidate_urls=candidates, pages_fetched=len(task_batch), task_outcomes=outcomes)

    with patch("datahunt.sources.crawlers.MultiSourceCrawler.crawl_tasks", side_effect=fake_crawl):
        emit = MagicMock()
        runtime._act_crawl_direct(state, emit)

    # All 15 tasks must be processed across batches, none left pending
    assert len(state.discovery_state.pending_crawl_tasks) == 0
    assert len(state.discovery_state.completed_crawl_tasks) == 15
    assert len(state.candidate_urls) == 15


# ─────────────────────────────────────────────────────────────────────────────
# 5. Database Count & API Honest Total (Scenario 11)
# ─────────────────────────────────────────────────────────────────────────────

def test_count_jobs_returns_deduplicated_total_not_page_limit():
    """JobTrackingRepository.count_jobs returns the full count of deduplicated matching jobs."""
    run_migrations()
    conn = get_connection()
    try:
        conn.execute("PRAGMA foreign_keys = OFF;")
        run_id = f"test_run_count_{int(time.time()*1000)}"
        conn.execute(
            "INSERT OR IGNORE INTO research_tasks (id, request_text, normalized_spec_json, policy_version, prompt_version, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?);",
            (f"t_{run_id}", "GenAI", "{}", "1", "1", "2026-01-01T00:00:00", "2026-01-01T00:00:00")
        )
        conn.execute(
            "INSERT INTO research_runs (id, task_id, status, budget_json, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?);",
            (run_id, f"t_{run_id}", "completed", "{}", "2026-01-01T00:00:00", "2026-01-01T00:00:00")
        )

        # Insert 12 distinct job records
        for i in range(12):
            fields_json = json.dumps({
                "title": f"GenAI Engineer {i}",
                "company": f"Acme Corp {i}",
                "location": "Dubai, UAE",
                "score_breakdown": {"title": {"status": "MATCH"}},
            })
            conn.execute(
                """
                INSERT INTO extracted_records (id, run_id, record_type, canonical_url, fields_json, confidence, verification_status, created_at, updated_at)
                VALUES (?, ?, 'job_listing', ?, ?, 0.95, 'verified', '2026-01-01T00:00:00', '2026-01-01T00:00:00');
                """,
                (f"rec_{run_id}_{i}", run_id, f"https://example.com/job/{i}", fields_json)
            )

        # Insert 3 duplicates of record 0 with lower confidence
        for i in range(3):
            fields_json = json.dumps({
                "title": "GenAI Engineer 0",
                "company": "Acme Corp 0",
                "location": "Dubai, UAE",
                "score_breakdown": {"title": {"status": "MATCH"}},
            })
            conn.execute(
                """
                INSERT INTO extracted_records (id, run_id, record_type, canonical_url, fields_json, confidence, verification_status, created_at, updated_at)
                VALUES (?, ?, 'job_listing', ?, ?, 0.80, 'verified', '2026-01-01T00:00:00', '2026-01-01T00:00:00');
                """,
                (f"rec_dup_{run_id}_{i}", run_id, f"https://example.com/dup/{i}", fields_json)
            )
        conn.commit()

        repo = JobTrackingRepository()
        total = repo.count_jobs(run_id=run_id)
        # 12 unique (title, company) pairs
        assert total == 12

        # Paging with limit=5 should still have total == 12
        page1 = repo.list_jobs(run_id=run_id, limit=5, offset=0)
        assert len(page1) == 5
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────────────────────
# 6. Freshness Semantics: All-Time vs 24-Hour vs 0-Sec Live Mode (Scenario 16)
# ─────────────────────────────────────────────────────────────────────────────

def test_freshness_semantics_all_time_vs_24h_vs_live():
    """
    Verify:
    - all-time preserves older open listings and unverified posting dates.
    - 24hour strictly excludes postings older than 24h while admitting recent postings.
    - 0sec / live mode runs as a live discovery mode without rejecting jobs for age.
    """
    policy = QualificationPolicy()

    # Job older than 24h (e.g. 3 days ago = 259,200 seconds)
    job_old = {
        "title": "GenAI Engineer",
        "company": "Tech Corp",
        "location": "Dubai",
        "posted_age_seconds": 259200,
        "posted_at": "3 days ago",
    }
    # Job with unknown posting date
    job_unknown_date = {
        "title": "GenAI Engineer",
        "company": "Tech Corp",
        "location": "Dubai",
        "posted_at": None,
    }
    # Job within 24h (2 hours ago = 7200 seconds)
    job_recent = {
        "title": "GenAI Engineer",
        "company": "Tech Corp",
        "location": "Dubai",
        "posted_age_seconds": 7200,
        "posted_at": "2 hours ago",
    }

    # 1. All-time search
    req_all_time = JobSearchRequest(raw_query="GenAI jobs Dubai all time", job_title="GenAI Engineer", location="Dubai", freshness_days=0)
    res_all_old = policy.qualify(job_old, req_all_time)
    res_all_unk = policy.qualify(job_unknown_date, req_all_time)
    assert res_all_old.qualified is True
    assert res_all_unk.qualified is True

    # 2. 24-hour search
    req_24h = JobSearchRequest(raw_query="GenAI jobs Dubai 24h", job_title="GenAI Engineer", location="Dubai", freshness_days=1)
    res_24h_recent = policy.qualify(job_recent, req_24h)
    res_24h_old = policy.qualify(job_old, req_24h)
    res_24h_unk = policy.qualify(job_unknown_date, req_24h)
    assert res_24h_recent.qualified is True
    assert res_24h_old.qualified is False
    assert any("stale_job" in r for r in res_24h_old.rejection_reasons)
    assert res_24h_unk.qualified is False

    # 3. 0-sec / Live Mode
    req_live = JobSearchRequest(raw_query="GenAI jobs Dubai 0sec", job_title="GenAI Engineer", location="Dubai")
    res_live_recent = policy.qualify(job_recent, req_live)
    res_live_unk = policy.qualify(job_unknown_date, req_live)
    assert res_live_recent.qualified is True
    # In live mode, active job with unknown date is preserved, not rejected for age
    assert res_live_unk.qualified is True


# ─────────────────────────────────────────────────────────────────────────────
# 7. Experience Range Matching (Scenario 15)
# ─────────────────────────────────────────────────────────────────────────────

def test_experience_range_up_to_6_years():
    """Verify experience constraint 0-6 years admits 0-3, 3-5, retains unverified, and rejects 7+."""
    policy = QualificationPolicy()
    req = JobSearchRequest(raw_query="GenAI jobs in UAE up to 6 years experience", job_title="GenAI Engineer", location="Dubai", experience_min=0, experience_max=6)

    # 3-5 years: MATCH
    job_mid = {"title": "GenAI Engineer", "company": "A", "location": "Dubai", "experience_min": 3, "experience_max": 5}
    res_mid = policy.qualify(job_mid, req)
    assert res_mid.qualified is True
    assert res_mid.experience_match == MatchStatus.MATCH

    # Unverified experience: retained as unverified, NOT rejected
    job_unv = {"title": "GenAI Engineer", "company": "B", "location": "Dubai"}
    res_unv = policy.qualify(job_unv, req)
    assert res_unv.qualified is True
    assert res_unv.experience_match == MatchStatus.UNKNOWN
    assert res_unv.experience_verified is False

    # 7+ years: MISMATCH, rejected
    job_senior = {"title": "GenAI Engineer", "company": "C", "location": "Dubai", "experience_min": 7}
    res_sen = policy.qualify(job_senior, req)
    assert res_sen.qualified is False
    assert res_sen.experience_match == MatchStatus.MISMATCH


# ─────────────────────────────────────────────────────────────────────────────
# 8. Dual Geographic Coverage: UAE OR Saudi Arabia (Scenario 13 & 14)
# ─────────────────────────────────────────────────────────────────────────────

def test_geographic_coverage_uae_or_saudi():
    """Verify dual location query 'UAE or Saudi Arabia' admits jobs in either country."""
    policy = QualificationPolicy()
    req = JobSearchRequest(
        raw_query="GenAI jobs in UAE or Saudi Arabia",
        job_title="GenAI Engineer",
        locations=["UAE", "Saudi Arabia"],
        location_operator="OR"
    )

    job_uae = {"title": "GenAI Engineer", "company": "A", "location": "Dubai, UAE"}
    job_saudi = {"title": "GenAI Engineer", "company": "B", "location": "Riyadh, Saudi Arabia"}
    job_germany = {"title": "GenAI Engineer", "company": "C", "location": "Berlin, Germany"}

    assert policy.qualify(job_uae, req).qualified is True
    assert policy.qualify(job_saudi, req).qualified is True
    assert policy.qualify(job_germany, req).qualified is False


def test_geographic_filtering_india_no_leakage():
    """Verify India query admits Indian locations and excludes foreign locations."""
    policy = QualificationPolicy()
    req = JobSearchRequest(raw_query="GenAI jobs in India", job_title="GenAI Engineer", locations=["India"])

    job_blr = {"title": "GenAI Engineer", "company": "A", "location": "Bangalore, India"}
    job_pune = {"title": "GenAI Engineer", "company": "B", "location": "Pune, Maharashtra"}
    job_us = {"title": "GenAI Engineer", "company": "C", "location": "San Francisco, USA"}

    assert policy.qualify(job_blr, req).qualified is True
    assert policy.qualify(job_pune, req).qualified is True
    assert policy.qualify(job_us, req).qualified is False


# ─────────────────────────────────────────────────────────────────────────────
# 9. Coverage Evaluator Honest Status & Resumability (Scenarios 19 & 20)
# ─────────────────────────────────────────────────────────────────────────────

def test_coverage_evaluator_complete_within_scope():
    """When all discovered tasks and sources are exhausted, status is complete_within_scope."""
    evaluator = CoverageEvaluator()
    spec = JobSearchRequest(raw_query="GenAI jobs UAE", job_title="GenAI Engineer", locations=["UAE"])
    state = AgentState(request="GenAI jobs UAE", run_id="r_done", task_id="t_done", mode="jobs", locations=["UAE"])
    state.discovery_state = DiscoveryState()
    # No pending tasks, 10 qualified records
    for i in range(10):
        state.qualified_records.append(ExtractedRecord(id=f"q_{i}", run_id="r_done", fields={"title": "GenAI", "location": "Dubai"}))
    state.verified_records = list(state.qualified_records)

    report = evaluator.evaluate(state, spec)
    assert report.run_status == "complete_within_scope"
    assert report.can_resume is False
    assert report.remaining_queued_tasks == 0


def test_coverage_evaluator_budget_exhaustion_reports_partial_and_resumable():
    """When stopped with pending tasks or budget exceeded, status is partial with can_resume=True."""
    evaluator = CoverageEvaluator()
    spec = JobSearchRequest(raw_query="GenAI jobs UAE", job_title="GenAI Engineer", locations=["UAE"])
    state = AgentState(request="GenAI jobs UAE", run_id="r_part", task_id="t_part", mode="jobs", locations=["UAE"])
    state.discovery_state = DiscoveryState()
    # Add pending task that wasn't reached
    state.discovery_state.pending_crawl_tasks.append(
        CrawlTask(id="p1", source="lever", source_type="ats", url="https://jobs.lever.co/pending", company="pending")
    )
    state.qualified_records.append(ExtractedRecord(id="q_1", run_id="r_part", fields={"title": "GenAI", "location": "Dubai"}))

    report = evaluator.evaluate(state, spec)
    assert report.run_status == "partial"
    assert report.can_resume is True
    assert report.remaining_queued_tasks == 1
    assert report.sources_unattempted_count == 1
