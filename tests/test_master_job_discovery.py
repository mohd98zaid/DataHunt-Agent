"""
Automated validation suite for Master Job Discovery overhaul.
Verifies Core Acceptance Scenarios A, B, C, D, E and Crawler Cursor Reliability.
"""
import json
import uuid
import pytest
from unittest.mock import MagicMock, patch

from datahunt.agents.query_understanding import QueryUnderstandingAgent, JobSearchRequest
from datahunt.models.job_spec import JobSearchSpec
from datahunt.agent.policies import (
    match_location, match_experience, match_title_relevance,
    QualificationPolicy, MatchStatus, TitleCategory
)
from datahunt.agent.state import AgentState
from datahunt.agent.runtime import AgentRuntime
from datahunt.agent.decision import DecisionEngine, AgentAction
from datahunt.agent.coverage import CoverageEvaluator
from datahunt.agent.discovery_models import (
    CrawlTask, CrawlTaskStatus, DiscoveryBudget, DiscoveryState,
    SearchTask, SearchTaskType, DiscoveredSourceType, StopReason
)
from datahunt.db.connection import get_connection
from datahunt.db.repositories import JobTrackingRepository
from datahunt.sources.crawlers import (
    crawl_lever_company, crawl_workable_company, crawl_smartrecruiters_company,
    CrawlBudget
)


# =============================================================================
# SCENARIO A: UAE and Saudi Arabia ("up to 6 years", "UAE/Saudi Arabia")
# =============================================================================

def test_scenario_a_query_parsing_uae_saudi_up_to_6_years():
    """Verify natural query parsing parses location alternative and experience maximum."""
    agent = QueryUnderstandingAgent(gemini_client=None)
    req = agent.understand("Find GenAI jobs in UAE/Saudi Arabia with up to 6 years of experience.")

    assert isinstance(req, JobSearchRequest)
    assert req.job_title == "GenAI Engineer"
    assert set(req.locations) == {"UAE", "Saudi Arabia"}
    assert req.location_operator == "OR"
    assert req.experience_min == 0
    assert req.experience_max == 6


def test_scenario_a_job_search_spec_normalization():
    """Verify JobSearchSpec synchronizes slash/comma locations and experience bounds."""
    spec = JobSearchSpec(
        raw_query="Find GenAI jobs in UAE/Saudi Arabia with up to 6 years of experience.",
        location="UAE/Saudi Arabia",
        experience_max=6,
    )
    assert set(spec.locations) == {"UAE", "Saudi Arabia"}
    assert spec.location_operator == "OR"
    assert spec.experience_max == 6


def test_scenario_a_location_matching_uae_or_saudi():
    """Verify match_location accepts Dubai (UAE) and Riyadh (Saudi Arabia) and rejects foreign."""
    s_dubai, r_dubai = match_location("UAE/Saudi Arabia", "Dubai, UAE")
    assert s_dubai == MatchStatus.MATCH

    s_riyadh, r_riyadh = match_location("UAE/Saudi Arabia", "Riyadh, Saudi Arabia")
    assert s_riyadh == MatchStatus.MATCH

    s_dammam, r_dammam = match_location(["UAE", "Saudi Arabia"], "Dammam, Eastern Province")
    assert s_dammam == MatchStatus.MATCH

    s_london, r_london = match_location("UAE/Saudi Arabia", "London, UK")
    assert s_london == MatchStatus.MISMATCH


def test_scenario_a_experience_maximum_bounds():
    """Verify experience maximum treats 6 years as upper bound, not minimum."""
    # 0-3 years -> Within 0-6 years budget -> MATCH
    s1, _ = match_experience(req_min=0, req_max=6, job_min=1, job_max=3)
    assert s1 == MatchStatus.MATCH

    # Exactly 6 years -> MATCH
    s2, _ = match_experience(req_min=0, req_max=6, job_min=4, job_max=6)
    assert s2 == MatchStatus.MATCH

    # Exceeds max 6 years (e.g. 7-10 years) -> MISMATCH
    s3, _ = match_experience(req_min=0, req_max=6, job_min=7, job_max=10)
    assert s3 == MatchStatus.MISMATCH

    # Unstated experience -> UNKNOWN (retained as unverified)
    s4, _ = match_experience(req_min=0, req_max=6, job_min=None, job_max=None)
    assert s4 == MatchStatus.UNKNOWN


def test_scenario_a_semantic_title_with_description_support():
    """Verify listings with generic engineering titles qualify when description proves GenAI duties."""
    pol = QualificationPolicy()
    req = JobSearchRequest(
        raw_query="Find GenAI jobs in UAE/Saudi Arabia with up to 6 years of experience.",
        job_title="GenAI Engineer",
        locations=["UAE", "Saudi Arabia"],
        location_operator="OR",
        experience_min=0,
        experience_max=6,
    )

    # Generic title "Software Engineer" with strong GenAI responsibilities in JD
    job_candidate = {
        "title": "Software Engineer",
        "company": "DeepTech AI",
        "location": "Dubai, UAE",
        "experience_min": 2,
        "experience_max": 5,
        "description": "Develop and deploy GenAI agents using LLMs, LangChain, RAG pipelines, and prompt engineering.",
    }
    result = pol.qualify(job_candidate, req)
    assert result.qualified is True
    assert result.eligibility_status == "ELIGIBLE"
    assert result.title_match == MatchStatus.MATCH

    # Unstated experience is preserved as unverified without disqualifying
    job_unknown_exp = {
        "title": "Applied AI Engineer",
        "company": "Riyadh Robotics",
        "location": "Riyadh, Saudi Arabia",
        "description": "Work on LLMs and foundation models.",
    }
    result2 = pol.qualify(job_unknown_exp, req)
    assert result2.qualified is True
    assert result2.experience_verified is False
    assert any("unverified" in w.lower() for w in result2.warnings)


# =============================================================================
# SCENARIO B: India Nationwide Search
# =============================================================================

def test_scenario_b_india_multi_city_matching():
    """Verify India search matches across Indian tech hubs and nationwide remote listings."""
    # Bangalore / Bengaluru
    s_blr, _ = match_location("India", "Bengaluru, Karnataka")
    assert s_blr == MatchStatus.MATCH

    # Hyderabad
    s_hyd, _ = match_location("India", "Hyderabad, Telangana")
    assert s_hyd == MatchStatus.MATCH

    # Pune
    s_pune, _ = match_location("India", "Pune, Maharashtra")
    assert s_pune == MatchStatus.MATCH

    # Gurgaon / Delhi NCR
    s_del, _ = match_location("India", "Gurgaon, Haryana, India")
    assert s_del == MatchStatus.MATCH

    # Remote in India
    s_rem, _ = match_location("India", "Remote, India", remote_ok=True)
    assert s_rem == MatchStatus.MATCH

    # Foreign US remote must be rejected
    s_us, _ = match_location("India", "Denver, CO (Remote)", remote_ok=True)
    assert s_us == MatchStatus.MISMATCH


# =============================================================================
# SCENARIO C: 0-Sec / Live Discovery Refresh
# =============================================================================

def test_scenario_c_0sec_live_discovery():
    """Verify 0sec does not reject jobs because posting age is not literally current second."""
    pol = QualificationPolicy()
    req = JobSearchRequest(
        raw_query="Find GenAI Engineer in Dubai 0sec",
        job_title="GenAI Engineer",
        location="Dubai",
        freshness_days=30,
    )

    # Job posted 2 hours ago (7200s) -> Qualifies in 0-sec live mode
    cand1 = {
        "title": "GenAI Engineer",
        "company": "Gulf Innovations",
        "location": "Dubai, UAE",
        "posted_age_seconds": 7200,
        "posted_at": "2 hours ago",
    }
    res1 = pol.qualify(cand1, req)
    assert res1.qualified is True

    # Job with unverified posting timestamp -> Preserved in 0-sec live mode
    cand2 = {
        "title": "GenAI Engineer",
        "company": "Gulf Innovations",
        "location": "Dubai, UAE",
        "posted_at": None,
    }
    res2 = pol.qualify(cand2, req)
    assert res2.qualified is True


# =============================================================================
# SCENARIO D: Last 24 Hours Rolling UTC Window
# =============================================================================

def test_scenario_d_24h_rolling_window_and_unverified_preservation():
    """Verify 24h rolling filter retains recent jobs, rejects >24h, and preserves unverified dates."""
    pol = QualificationPolicy()
    req = JobSearchRequest(
        raw_query="Find GenAI Engineer jobs in UAE 24h",
        job_title="GenAI Engineer",
        location="UAE",
        freshness_days=1,
    )

    # 1. Posted 4 hours ago (14400s) -> Qualified
    job_recent = {
        "title": "GenAI Engineer",
        "company": "Tech Corp",
        "location": "Dubai, UAE",
        "posted_age_seconds": 14400,
    }
    res_recent = pol.qualify(job_recent, req)
    assert res_recent.qualified is True

    # 2. Posted 48 hours ago (172800s) -> Disqualified (stale_job)
    job_old = {
        "title": "GenAI Engineer",
        "company": "Tech Corp",
        "location": "Dubai, UAE",
        "posted_age_seconds": 172800,
    }
    res_old = pol.qualify(job_old, req)
    assert res_old.qualified is False
    assert any("stale_job" in r for r in res_old.rejection_reasons)

    # 3. Posting date unknown -> Preserved in unverified recency category, not treated as stale
    job_unknown = {
        "title": "GenAI Engineer",
        "company": "Tech Corp",
        "location": "Dubai, UAE",
        "posted_age_seconds": None,
        "posted_at": None,
    }
    res_unknown = pol.qualify(job_unknown, req)
    assert res_unknown.qualified is False
    assert any("unverified_date" in r for r in res_unknown.rejection_reasons)
    assert not any("stale_job" in r for r in res_unknown.rejection_reasons)


# =============================================================================
# SCENARIO E: All Time Mode
# =============================================================================

def test_scenario_e_all_time_no_age_cutoff():
    """Verify all_time mode does not drop older listings based on search snippets or past dates."""
    pol = QualificationPolicy()
    req = JobSearchRequest(
        raw_query="Find GenAI Engineer jobs in Dubai All time",
        job_title="GenAI Engineer",
        location="Dubai",
        freshness_days=0,
    )

    # Job posted 90 days ago -> Preserved in all-time mode
    job_90d = {
        "title": "GenAI Engineer",
        "company": "AI Pioneers",
        "location": "Dubai, UAE",
        "posted_age_seconds": 90 * 86400,
        "posted_at": "3 months ago",
        "is_active": True,
    }
    res = pol.qualify(job_90d, req)
    assert res.qualified is True

    # Runtime candidate triage does not discard past snippets in all-time mode
    runtime = AgentRuntime(
        client=MagicMock(),
        search=MagicMock(),
        fetch=MagicMock(),
        extract=MagicMock(),
        verify=MagicMock(),
        dedupe=MagicMock(),
        export=MagicMock()
    )
    state = AgentState(
        request="Find GenAI Engineer jobs in Dubai All time",
        run_id="run_test_e",
        task_id="task_test_e",
        mode="jobs",
    )
    state.collection_mode = "thorough"

    hit_old_snippet = {
        "url": "https://boards.greenhouse.io/company/jobs/12345",
        "title": "GenAI Engineer - Dubai",
        "snippet": "Job opening posted 6 months ago in Dubai. Seeking LLM specialist.",
    }
    assert runtime._is_promising_candidate(hit_old_snippet, state) is True


# =============================================================================
# CRAWLER PAGINATION & CURSOR RETRY RELIABILITY
# =============================================================================

def test_lever_cursor_not_consumed_on_network_failure():
    """Verify Lever crawler cursor is not marked seen if HTTP request fails."""
    events = []
    task = CrawlTask(
        id="task_lev_1",
        source="lever",
        source_type="ats",
        company="test-co",
        url="https://api.lever.co/v0/postings/test-co",
        metadata={"seen_skips": []}
    )

    with patch("datahunt.sources.crawlers._safe_get") as mock_get:
        # First call fails (network timeout/error)
        mock_get.return_value = None

        crawl_lever_company(
            company_slug="test-co",
            role_keywords=["AI"],
            budget=CrawlBudget(max_pages_per_source=2),
            rate_limiter=MagicMock(),
            emit=events.append,
            task=task,
        )

        # Cursor skip 0 must NOT be in seen_skips because request failed
        assert 0 not in task.metadata.get("seen_skips", [])


def test_workable_token_not_consumed_on_network_failure():
    """Verify Workable crawler token is not marked seen if HTTP request fails."""
    events = []
    task = CrawlTask(
        id="task_work_1",
        source="workable",
        source_type="ats",
        company="test-co",
        url="https://apply.workable.com/api/v3/accounts/test-co/jobs",
        metadata={"next_token": "token_abc123", "seen_tokens": []}
    )

    with patch("datahunt.sources.crawlers._safe_get") as mock_get:
        mock_get.return_value = None

        crawl_workable_company(
            company_slug="test-co",
            role_keywords=["AI"],
            budget=CrawlBudget(max_pages_per_source=2),
            rate_limiter=MagicMock(),
            emit=events.append,
            task=task,
        )

        # The failed token must NOT be marked seen
        assert "token_abc123" not in task.metadata.get("seen_tokens", [])


def test_smartrecruiters_offset_not_consumed_on_network_failure():
    """Verify SmartRecruiters offset is not marked seen if HTTP request fails."""
    events = []
    task = CrawlTask(
        id="task_sr_1",
        source="smartrecruiters",
        source_type="ats",
        company="test-co",
        url="https://api.smartrecruiters.com/v1/companies/test-co/postings",
        metadata={"offset": 20, "seen_offsets": []}
    )

    with patch("datahunt.sources.crawlers._safe_get") as mock_get:
        mock_get.return_value = None

        crawl_smartrecruiters_company(
            company_slug="test-co",
            role_keywords=["AI"],
            budget=CrawlBudget(max_pages_per_source=2),
            rate_limiter=MagicMock(),
            emit=events.append,
            task=task,
        )

        # The failed offset must NOT be marked seen
        assert 20 not in task.metadata.get("seen_offsets", [])


# =============================================================================
# ADDITIONAL REQUIRED REGRESSION SCENARIOS (1-24)
# =============================================================================

def test_experience_parsing_all_expressions():
    """Verify deterministic parsing across all required experience expressions and bounds."""
    agent = QueryUnderstandingAgent(gemini_client=None)

    # Maximum 6 years -> exp_max=6, exp_min=0
    r1 = agent.understand("Find GenAI jobs in Dubai maximum 6 years experience")
    assert r1.experience_min == 0 and r1.experience_max == 6

    # At most 6 years -> exp_max=6, exp_min=0
    r2 = agent.understand("Find GenAI jobs in Dubai at most 6 years experience")
    assert r2.experience_min == 0 and r2.experience_max == 6

    # No more than 6 years -> exp_max=6, exp_min=0
    r3 = agent.understand("Find GenAI jobs in Dubai no more than 6 years experience")
    assert r3.experience_min == 0 and r3.experience_max == 6

    # At least 3 years -> exp_min=3, exp_max=None
    r4 = agent.understand("Find GenAI jobs in Dubai at least 3 years experience")
    assert r4.experience_min == 3 and r4.experience_max is None

    # Minimum 3 years -> exp_min=3, exp_max=None
    r5 = agent.understand("Find GenAI jobs in Dubai minimum 3 years experience")
    assert r5.experience_min == 3 and r5.experience_max is None

    # 2-6 years -> exp_min=2, exp_max=6
    r6 = agent.understand("Find GenAI jobs in Dubai 2-6 years experience")
    assert r6.experience_min == 2 and r6.experience_max == 6

    # Fresher -> exp_min=0, exp_max=1
    r7 = agent.understand("Find GenAI fresher jobs in Dubai")
    assert r7.experience_min == 0 and r7.experience_max == 1

    # Not specified -> None
    r8 = agent.understand("Find GenAI jobs in Dubai")
    assert r8.experience_min is None and r8.experience_max is None

    # Contradictory range: 6 to 2 years -> resolved to 2 to 6
    spec = JobSearchSpec(raw_query="GenAI Engineer", experience_min=6, experience_max=2)
    assert spec.experience_min == 2 and spec.experience_max == 6


def test_country_alternatives_and_location_variants():
    """Verify parsing and normalization of UAE/Saudi alternatives and India nationwide."""
    agent = QueryUnderstandingAgent(gemini_client=None)

    # UAE or Saudi Arabia
    r1 = agent.understand("Find GenAI jobs in UAE or Saudi Arabia")
    assert set(r1.locations) == {"UAE", "Saudi Arabia"}
    assert r1.location_operator == "OR"

    # UAE, Saudi Arabia (comma separated regions)
    r2 = agent.understand("Find GenAI jobs in UAE, Saudi Arabia")
    assert set(r2.locations) == {"UAE", "Saudi Arabia"}
    assert r2.location_operator == "OR"

    # United Arab Emirates or KSA
    r3 = agent.understand("Find GenAI jobs in United Arab Emirates or KSA")
    assert set(r3.locations) == {"UAE", "Saudi Arabia"}
    assert r3.location_operator == "OR"

    # GenAI jobs anywhere in India
    r4 = agent.understand("GenAI jobs anywhere in India with up to 6 years experience")
    assert r4.locations == ["India"]


def test_confirmed_closed_job_rejection():
    """Verify jobs with confirmed closed or expired status are disqualified as inactive."""
    pol = QualificationPolicy()
    req = JobSearchRequest(raw_query="GenAI Engineer", job_title="GenAI Engineer", location="Dubai")

    # Explicit is_closed = True
    job_closed = {
        "title": "GenAI Engineer",
        "company": "Closed Corp",
        "location": "Dubai",
        "is_closed": True,
    }
    res1 = pol.qualify(job_closed, req)
    assert res1.qualified is False
    assert "inactive_posting" in res1.rejection_reasons

    # Status = "expired"
    job_expired = {
        "title": "GenAI Engineer",
        "company": "Expired Corp",
        "location": "Dubai",
        "status": "expired",
    }
    res2 = pol.qualify(job_expired, req)
    assert res2.qualified is False
    assert "inactive_posting" in res2.rejection_reasons


def test_crawler_page_retry_and_checkpoint_resumability():
    """Verify crawler handles failed first page, succeeds on retry, and respects checkpoints."""
    from datahunt.sources.crawlers import SafeCrawlerResponse

    events = []
    task = CrawlTask(
        id="task_lev_retry",
        source="lever",
        source_type="ats",
        company="test-co",
        url="https://api.lever.co/v0/postings/test-co",
        metadata={"skip": 100, "seen_skips": [0]}  # Resuming at skip 100 after skip 0
    )

    call_count = 0
    def mock_safe_get(url, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            # First attempt on skip 100 fails
            return None
        # Second attempt succeeds with a job posting
        return SafeCrawlerResponse(status_code=200, text=json.dumps([{"id": "job1", "text": "AI Engineer", "hostedUrl": "https://jobs.lever.co/test-co/1"}]))

    with patch("datahunt.sources.crawlers._safe_get", side_effect=mock_safe_get):
        # First crawl attempt (fails)
        crawl_lever_company(
            company_slug="test-co",
            role_keywords=["AI"],
            budget=CrawlBudget(max_pages_per_source=2),
            rate_limiter=MagicMock(),
            emit=events.append,
            task=task,
        )
        # Skip 100 must NOT be in seen_skips because the request failed
        assert 100 not in task.metadata["seen_skips"]

        # Retry crawl attempt (succeeds)
        crawl_lever_company(
            company_slug="test-co",
            role_keywords=["AI"],
            budget=CrawlBudget(max_pages_per_source=2),
            rate_limiter=MagicMock(),
            emit=events.append,
            task=task,
        )
        # Skip 100 is now committed after successful 200 response
        assert 100 in task.metadata["seen_skips"]


def test_thorough_mode_does_not_prematurely_stop_at_target():
    """Verify thorough mode continues discovery when tasks remain in queue, even if target met."""
    decision_engine = DecisionEngine()
    spec = JobSearchRequest(raw_query="GenAI jobs UAE", job_title="GenAI Engineer", location="UAE")
    state = AgentState(
        request="GenAI jobs UAE",
        run_id="r_thorough",
        task_id="t_thorough",
        mode="jobs",
        target_results=5,
    )
    state.collection_mode = "thorough"

    # Add 5 qualified records (target met)
    for i in range(5):
        from datahunt.models.record import ExtractedRecord
        state.qualified_records.append(
            ExtractedRecord(
                id=f"rec_thorough_{i}",
                task_id=state.task_id,
                run_id=state.run_id,
                record_type="job_listing",
                canonical_url=f"https://example.com/job/{i}",
                fields={"title": "GenAI Engineer", "company": f"Co {i}", "location": "Dubai"}
            )
        )

    # Active tasks remain in discovery queue
    state.discovery_state = DiscoveryState()
    state.discovery_state.task_queue.append(
        SearchTask(
            id="task_pending_1",
            task_type=SearchTaskType.BOARD_SEARCH,
            query='site:bayt.com "GenAI Engineer"',
            source="bayt.com",
            source_type=DiscoveredSourceType.REGIONAL_BOARD,
            priority=1,
            depth=1,
            round=1,
        )
    )

    action, reason = decision_engine.decide(state)
    # Must NOT stop; should execute SEARCH on pending discovery queue
    assert action == AgentAction.SEARCH
    assert "Searching discovery queue" in reason


def test_resource_budget_exhaustion_reports_partial_honest_status():
    """Verify CoverageEvaluator honestly reports 'partial' when search budget is exhausted."""
    evaluator = CoverageEvaluator()
    spec = JobSearchSpec(raw_query="GenAI jobs UAE", job_title="GenAI Engineer", locations=["UAE"])
    state = AgentState(request="GenAI jobs UAE", run_id="r_partial", task_id="t_partial", mode="jobs", locations=["UAE"])
    state.discovery_state = DiscoveryState()

    # Search calls exceed max budget
    state.search_calls = 150
    state.max_search_calls = 150
    # Pending work remains
    state.candidate_urls = ["https://example.com/job/unread"]

    report = evaluator.evaluate(state, spec)
    assert report.run_status == "partial"
    assert report.can_resume is True
    assert "max_search_calls" in report.budgets_reached


def test_synthetic_dataset_pagination_and_total_count_beyond_2000_records():
    """
    Verify X-Total-Count and stable deterministic pagination on a synthetic dataset of 2,500 records.
    Ensures zero duplicate or skipped rows across page boundaries.
    """
    conn = get_connection()
    task_id = f"task_synth_{uuid.uuid4().hex[:8]}"
    run_id = f"run_synth_{uuid.uuid4().hex[:8]}"
    total_records = 2500

    try:
        conn.execute("PRAGMA foreign_keys = OFF;")
        conn.execute(
            "INSERT INTO research_tasks (id, request_text, normalized_spec_json, policy_version, prompt_version, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?);",
            (task_id, "Synthetic Query", "{}", "1", "1", "2026-10-16T00:00:00", "2026-10-16T00:00:00")
        )
        conn.execute(
            "INSERT INTO research_runs (id, task_id, status, budget_json, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?);",
            (run_id, task_id, "completed", "{}", "2026-10-16T00:00:00", "2026-10-16T00:00:00")
        )

        # Bulk insert 2,500 distinct synthetic job records
        records_to_insert = []
        for i in range(total_records):
            rec_id = f"rec_synth_{run_id}_{i:04d}"
            # Format timestamp so each record has a deterministic second
            scraped_at = f"2026-10-16T12:{i // 60:02d}:{i % 60:02d}.000000Z"
            fields_json = json.dumps({
                "title": f"GenAI Engineer Specialist {i}",
                "company": f"Synthetic AI Enterprise {i}",
                "location": "Dubai, UAE",
                "salary": "120000",
                "employment_type": "full_time"
            })
            records_to_insert.append((rec_id, run_id, f"https://company{i}.com/job/{i}", fields_json, scraped_at, scraped_at))

        conn.executemany(
            """
            INSERT INTO extracted_records (id, run_id, record_type, canonical_url, fields_json, confidence, verification_status, created_at, updated_at)
            VALUES (?, ?, 'job_listing', ?, ?, 0.90, 'verified', ?, ?);
            """,
            records_to_insert
        )
        conn.commit()

        repo = JobTrackingRepository(conn_factory=get_connection)

        # 1. Verify exact total count
        count = repo.count_jobs(run_id=run_id)
        assert count == total_records

        # 2. Page 1: limit 1000, offset 0
        p1 = repo.list_jobs(run_id=run_id, limit=1000, offset=0)
        assert len(p1) == 1000

        # 3. Page 2: limit 1000, offset 1000
        p2 = repo.list_jobs(run_id=run_id, limit=1000, offset=1000)
        assert len(p2) == 1000

        # 4. Page 3: limit 1000, offset 2000 (remaining 500 records)
        p3 = repo.list_jobs(run_id=run_id, limit=1000, offset=2000)
        assert len(p3) == 500

        # 5. Verify ID uniqueness and no gaps or duplicates across page boundaries
        all_fetched_ids = [j["id"] for j in p1] + [j["id"] for j in p2] + [j["id"] for j in p3]
        assert len(all_fetched_ids) == total_records
        assert len(set(all_fetched_ids)) == total_records

        # 6. Verify deterministic ordering (scraped_at DESC, id ASC)
        for i in range(len(p1) - 1):
            assert p1[i]["scraped_at"] >= p1[i+1]["scraped_at"]
    finally:
        conn.execute("DELETE FROM extracted_records WHERE run_id = ?;", (run_id,))
        conn.execute("DELETE FROM research_runs WHERE id = ?;", (run_id,))
        conn.execute("DELETE FROM research_tasks WHERE id = ?;", (task_id,))
        conn.commit()
        conn.close()


def test_multi_source_deduplication():
    """Verify identical (title, company) postings across different URLs/domains are deduplicated."""
    conn = get_connection()
    task_id = f"task_dedup_{uuid.uuid4().hex[:8]}"
    run_id = f"run_dedup_{uuid.uuid4().hex[:8]}"
    try:
        conn.execute("PRAGMA foreign_keys = OFF;")
        conn.execute(
            "INSERT INTO research_tasks (id, request_text, normalized_spec_json, policy_version, prompt_version, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?);",
            (task_id, "Dedup Query", "{}", "1", "1", "2026-10-16T00:00:00", "2026-10-16T00:00:00")
        )
        conn.execute(
            "INSERT INTO research_runs (id, task_id, status, budget_json, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?);",
            (run_id, task_id, "completed", "{}", "2026-10-16T00:00:00", "2026-10-16T00:00:00")
        )

        # Same job posted to Greenhouse and Lever
        fields1 = json.dumps({"title": "Lead Foundation Model Researcher", "company": "GlobalAI", "location": "Dubai"})
        fields2 = json.dumps({"title": "Lead Foundation Model Researcher", "company": "GlobalAI", "location": "Dubai, UAE"})

        conn.execute(
            """
            INSERT INTO extracted_records (id, run_id, record_type, canonical_url, fields_json, confidence, verification_status, created_at, updated_at)
            VALUES (?, ?, 'job_listing', ?, ?, 0.85, 'verified', '2026-10-16T10:00:00', '2026-10-16T10:00:00');
            """,
            (f"rec_gh_{run_id}", run_id, "https://boards.greenhouse.io/globalai/jobs/1", fields1)
        )
        conn.execute(
            """
            INSERT INTO extracted_records (id, run_id, record_type, canonical_url, fields_json, confidence, verification_status, created_at, updated_at)
            VALUES (?, ?, 'job_listing', ?, ?, 0.95, 'verified', '2026-10-16T11:00:00', '2026-10-16T11:00:00');
            """,
            (f"rec_lev_{run_id}", run_id, "https://jobs.lever.co/globalai/jobs/1", fields2)
        )
        conn.commit()

        repo = JobTrackingRepository(conn_factory=get_connection)
        # Deduplicated total should be exactly 1
        assert repo.count_jobs(run_id=run_id) == 1

        jobs = repo.list_jobs(run_id=run_id)
        assert len(jobs) == 1
        # Favors higher confidence / newer record
        assert jobs[0]["confidence"] == 0.95
    finally:
        conn.execute("DELETE FROM extracted_records WHERE run_id = ?;", (run_id,))
        conn.execute("DELETE FROM research_runs WHERE id = ?;", (run_id,))
        conn.execute("DELETE FROM research_tasks WHERE id = ?;", (task_id,))
        conn.commit()
        conn.close()


def test_source_coverage_report_metrics():
    """Verify CoverageEvaluator correctly evaluates dual geographic yields and source metrics."""
    evaluator = CoverageEvaluator()
    spec = JobSearchSpec(raw_query="GenAI jobs in UAE or Saudi Arabia", job_title="GenAI Engineer", locations=["UAE", "Saudi Arabia"])
    state = AgentState(request=spec.raw_query, run_id="r_cov", task_id="t_cov", mode="jobs", locations=spec.locations)

    from datahunt.models.record import ExtractedRecord
    # Add UAE job
    state.qualified_records.append(
        ExtractedRecord(
            id="rec_uae", task_id=state.task_id, run_id=state.run_id,
            record_type="job_listing", canonical_url="https://a.com/1",
            fields={"title": "GenAI Engineer", "company": "A", "location": "Dubai, UAE"}
        )
    )
    # Add Saudi job
    state.qualified_records.append(
        ExtractedRecord(
            id="rec_saudi", task_id=state.task_id, run_id=state.run_id,
            record_type="job_listing", canonical_url="https://b.com/1",
            fields={"title": "GenAI Engineer", "company": "B", "location": "Riyadh, Saudi Arabia"}
        )
    )

    report = evaluator.evaluate(state, spec)
    assert report.location_coverage.get("UAE") == 1
    assert report.location_coverage.get("Saudi Arabia") == 1
    assert len(report.weak_areas) == 0
    assert report.is_sufficient is True

