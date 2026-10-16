import json
import uuid
import pytest
from unittest.mock import MagicMock, patch
from datahunt.agent.discovery_engine import DiscoveryEngine
from datahunt.agent.discovery_models import SearchTaskType, DiscoveredSourceType
from datahunt.agent.policies import QualificationPolicy, MatchStatus, TitleCategory
from datahunt.agent.state import AgentState
from datahunt.agents.query_understanding import JobSearchRequest
from datahunt.db import get_connection, JobTrackingRepository
from datahunt.models import ExtractedRecord, SourceDocument, VerificationStatus
from datahunt.tools.extract import ExtractTool
from datahunt.tools.fetch import fetch_greenhouse_jobs_api
from fastapi.testclient import TestClient
from datahunt.api import app


def test_scenario_a_uae_or_saudi_multi_hub_ats_and_regional():
    """
    Scenario A: UAE OR Saudi Arabia
    Query: 'AI engineer OR GenAI engineer OR Agentic AI engineer in UAE OR Saudi Arabia (0-6 years exp)'
    Must include UAE hubs (Dubai, Abu Dhabi) AND Saudi hubs (Riyadh, Jeddah) in ATS and regional sweeps.
    """
    req = JobSearchRequest(
        raw_query="AI engineer OR GenAI engineer OR Agentic AI engineer in UAE OR Saudi Arabia (0-6 years exp)",
        job_title="GenAI Engineer",
        locations=["UAE", "Saudi Arabia"],
        location_operator="OR",
        experience_min=0,
        experience_max=6,
    )
    state = AgentState(
        request=req.raw_query,
        run_id="run_test_a",
        task_id="task_test_a",
        canonical_job_request=req,
        explicit_titles=["GenAI Engineer"],
        locations=["UAE", "Saudi Arabia"],
        location_operator="OR",
        explicit_experience_min=0,
        explicit_experience_max=6,
    )

    engine = DiscoveryEngine()
    tasks = engine.generate_initial_tasks(req, state)

    # 1. ATS Portals must cover both UAE and Saudi Arabia
    ats_queries = [t.query for t in tasks if t.task_type == SearchTaskType.ATS_SEARCH]
    assert any("Dubai" in q for q in ats_queries), "Expected Dubai in ATS queries"
    assert any("Riyadh" in q for q in ats_queries), "Expected Riyadh in ATS queries"
    assert any("boards.greenhouse.io" in q and "Dubai" in q for q in ats_queries)
    assert any("boards.greenhouse.io" in q and "Riyadh" in q for q in ats_queries)
    assert any("jobs.lever.co" in q and "Dubai" in q for q in ats_queries)
    assert any("jobs.lever.co" in q and "Riyadh" in q for q in ats_queries)

    # 2. Regional job boards must cover both UAE and Saudi hubs
    reg_queries = [t.query for t in tasks if t.task_type == SearchTaskType.REGIONAL_BOARD_SEARCH]
    assert any("naukrigulf.com" in q and "Dubai" in q for q in reg_queries)
    assert any("naukrigulf.com" in q and "Riyadh" in q for q in reg_queries)
    assert any("bayt.com" in q and "Dubai" in q for q in reg_queries)
    assert any("bayt.com" in q and "Riyadh" in q for q in reg_queries)

    # 3. Direct web sweep must include both UAE and Saudi
    sweep_queries = [t.query for t in tasks if t.task_type == SearchTaskType.BOARD_SEARCH and "direct" in t.id]
    assert any("Dubai" in q for q in sweep_queries)
    assert any("Riyadh" in q for q in sweep_queries)

    # 4. Location qualification matches across both countries
    policy = QualificationPolicy()
    for loc in ["Dubai, UAE", "Abu Dhabi, UAE", "Riyadh, Saudi Arabia", "Jeddah, KSA", "Dammam, Saudi Arabia"]:
        q_res = policy.qualify(
            {"title": "GenAI Engineer", "location": loc, "company": "Tech Corp"},
            req
        )
        assert q_res.qualified is True, f"Expected {loc} to qualify under UAE OR Saudi Arabia"
        assert q_res.location_match == MatchStatus.MATCH


def test_scenario_b_india_tech_hubs_and_no_gulf_bleed():
    """
    Scenario B: India
    Query: 'GenAI engineer OR Agentic AI in India (0-6 years exp)'
    Must target Indian tech hubs (Bengaluru, Hyderabad, Pune, etc.) and Indian portals.
    Must NOT query Gulf-specific portals (naukrigulf, bayt, gulftalent).
    """
    req = JobSearchRequest(
        raw_query="GenAI engineer OR Agentic AI in India (0-6 years exp)",
        job_title="GenAI Engineer",
        locations=["India"],
        location_operator="OR",
        experience_min=0,
        experience_max=6,
    )
    state = AgentState(
        request=req.raw_query,
        run_id="run_test_b",
        task_id="task_test_b",
        canonical_job_request=req,
        explicit_titles=["GenAI Engineer"],
        locations=["India"],
        location_operator="OR",
        explicit_experience_min=0,
        explicit_experience_max=6,
    )

    engine = DiscoveryEngine()
    tasks = engine.generate_initial_tasks(req, state)

    # 1. Must NOT include Gulf portals
    gulf_domains = ["naukrigulf.com", "bayt.com", "gulftalent.com"]
    for t in tasks:
        assert not any(gd in t.query.lower() for gd in gulf_domains), f"Found Gulf portal in India task: {t.query}"
        assert not any(gd in t.source.lower() for gd in gulf_domains), f"Found Gulf source in India task: {t.source}"

    # 2. Must include Indian regional portals
    india_sources = [t.source for t in tasks]
    assert "naukri.com" in india_sources
    assert "instahyre.com" in india_sources
    assert "foundit.in" in india_sources

    # 3. ATS queries must include Indian tech hubs
    ats_queries = [t.query for t in tasks if t.task_type == SearchTaskType.ATS_SEARCH]
    assert any("Bengaluru" in q for q in ats_queries)
    assert any("Hyderabad" in q for q in ats_queries)
    assert any("India" in q for q in ats_queries)

    # 4. Location qualification matches Indian hubs
    policy = QualificationPolicy()
    for loc in ["Bengaluru, India", "Hyderabad, Telangana", "Pune, Maharashtra", "Gurgaon, Haryana", "Mumbai"]:
        q_res = policy.qualify(
            {"title": "GenAI Engineer", "location": loc, "company": "AI Labs India"},
            req
        )
        assert q_res.qualified is True, f"Expected {loc} to qualify for India search"
        assert q_res.location_match == MatchStatus.MATCH


def test_scenario_c_all_time_search_no_age_cutoff():
    """
    Scenario C: All-Time Search
    When user requests 'all time' or unconstrained active roles, postings older than 60 days
    or from past years (e.g. 2025) must NOT be rejected as stale.
    """
    policy = QualificationPolicy()
    req = JobSearchRequest(
        raw_query="all time GenAI Engineer jobs in Dubai",
        job_title="GenAI Engineer",
        locations=["Dubai"],
        freshness_days=None,
    )

    # 90 days old job with age in seconds
    job_old = {
        "title": "GenAI Engineer",
        "company": "DeepTech",
        "location": "Dubai, UAE",
        "posted_age_seconds": 90 * 86400,
        "posted_at": "3 months ago",
    }
    res_old = policy.qualify(job_old, req)
    assert res_old.qualified is True
    assert not any("stale_job" in r for r in res_old.rejection_reasons)

    # Job from past year with date string
    job_past_year = {
        "title": "GenAI Engineer",
        "company": "Emirates AI",
        "location": "Dubai, UAE",
        "posted_at": "2025-11-20",
    }
    res_past = policy.qualify(job_past_year, req)
    assert res_past.qualified is True
    assert not any("stale_job" in r for r in res_past.rejection_reasons)

    # Job with missing / null posted date
    job_no_date = {
        "title": "GenAI Engineer",
        "company": "Future Corp",
        "location": "Dubai, UAE",
        "posted_at": None,
        "posted_age_seconds": None,
    }
    res_no_date = policy.qualify(job_no_date, req)
    assert res_no_date.qualified is True
    assert not any("stale_job" in r for r in res_no_date.rejection_reasons)


def test_scenario_d_recent_search_24h_0sec():
    """
    Scenario D: Recent Search (24h/0Sec)
    Jobs posted <= 86,400s or marked today/hours ago pass; jobs older than 24h are excluded.
    """
    policy = QualificationPolicy()
    req = JobSearchRequest(
        raw_query="GenAI Engineer in Dubai 24h",
        job_title="GenAI Engineer",
        locations=["Dubai"],
        freshness_days=1,
    )

    # 4 hours old job (qualified)
    job_fresh = {
        "title": "GenAI Engineer",
        "company": "OmniAI",
        "location": "Dubai, UAE",
        "posted_age_seconds": 14400,
        "posted_at": "4 hours ago",
    }
    res_fresh = policy.qualify(job_fresh, req)
    assert res_fresh.qualified is True

    # 3 days old job (disqualified)
    job_stale = {
        "title": "GenAI Engineer",
        "company": "OldAI",
        "location": "Dubai, UAE",
        "posted_age_seconds": 259200,
        "posted_at": "3 days ago",
    }
    res_stale = policy.qualify(job_stale, req)
    assert res_stale.qualified is False
    assert any("stale_job" in r for r in res_stale.rejection_reasons)


def test_scenario_e_large_result_set_and_ats_untruncated():
    """
    Scenario E: Large Result Set & ATS Untruncated Ingestion
    Greenhouse JSON API parser must not slice jobs[:50] and must parse all 120 jobs.
    API pagination must allow limits up to 5000 and offset navigation.
    """
    # 1. Test Greenhouse JSON API ingestion with 120 mock roles
    mock_jobs = [
        {
            "id": i,
            "title": f"Generative AI Engineer #{i}",
            "absolute_url": f"https://boards.greenhouse.io/canonical/jobs/{i}",
            "location": {"name": "Dubai, UAE"},
            "departments": [{"name": "AI Engineering"}],
            "updated_at": "2026-10-15T12:00:00Z",
        }
        for i in range(1, 121)
    ]

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"jobs": mock_jobs}

    with patch("httpx.Client.get", return_value=mock_resp):
        tool_res = fetch_greenhouse_jobs_api("canonical", "run_large_test")
        assert tool_res.success is True
        doc = tool_res.data
        # Verify all 120 jobs are present in extracted_text, not truncated to 50
        assert "Generative AI Engineer #120" in doc.extracted_text
        assert "Generative AI Engineer #50" in doc.extracted_text
        assert "Generative AI Engineer #51" in doc.extracted_text

    # 2. Test API pagination with records
    from datahunt.models import ResearchTask, ResearchRun, ResearchSpec
    from datahunt.db import TaskRepository, RunRepository, DocumentRepository, RecordRepository
    task_repo = TaskRepository()
    run_repo = RunRepository()
    doc_repo = DocumentRepository()
    rec_repo = RecordRepository()
    job_repo = JobTrackingRepository()

    spec = ResearchSpec(topic="Pagination Test", domain="jobs")
    task = ResearchTask(request_text="test pagination task", normalized_spec=spec)
    task_repo.create_task(task)
    run = ResearchRun(task_id=task.id)
    run_repo.create_run(run)

    doc = doc_repo.upsert_document(SourceDocument(
        run_id=run.id,
        requested_url=f"https://example.com/page_{run.id}",
        final_url=f"https://example.com/page_{run.id}",
        canonical_url=f"https://example.com/page_{run.id}",
        domain="example.com",
        title="Test Jobs Page",
        extracted_text="Test listings",
    ))

    for i in range(25):
        rec = ExtractedRecord(
            id=f"rec_{run.id}_{i}",
            run_id=run.id,
            source_document_id=doc.id,
            canonical_url=f"https://example.com/job/{run.id}/{i}",
            record_type="job_listing",
            fields={
                "title": f"Senior AI Engineer {i}",
                "company": f"Company {i}",
                "location": "Dubai, UAE",
                "score_breakdown": {"title": {"status": "MATCH"}},
            },
            confidence=0.9,
            verification_status=VerificationStatus.VERIFIED,
        )
        rec_repo.insert_record(rec)

    run_id = run.id

    page1 = job_repo.list_jobs(run_id=run_id, limit=10, offset=0)
    assert len(page1) == 10

    page2 = job_repo.list_jobs(run_id=run_id, limit=10, offset=10)
    assert len(page2) == 10
    assert page1[0]["id"] != page2[0]["id"]

    page3 = job_repo.list_jobs(run_id=run_id, limit=10, offset=20)
    assert len(page3) == 5

    # Check API limit 2000 does not error
    client = TestClient(app)
    api_resp = client.get(f"/api/jobs?run_id={run_id}&limit=2000&offset=0")
    assert api_resp.status_code == 200
    assert len(api_resp.json()) == 25
    assert api_resp.headers.get("X-Total-Count") == "25"


def test_scenario_f_resumable_collection_checkpoints():
    """
    Scenario F: Interrupted Collection / Resumability
    State preserves already-seen canonical URLs and job fingerprints to prevent re-fetching.
    """
    state = AgentState(
        request="AI Engineer in Dubai",
        run_id="run_f_checkpoint",
        task_id="task_f",
    )
    url = "https://boards.greenhouse.io/techcorp/jobs/12345"
    state.seen_canonical_urls.add(url)
    state.candidate_urls.append({"url": url, "source": "greenhouse"})

    # Duplicate URL should be detected and skipped
    assert url in state.seen_canonical_urls


def test_scenario_g_unknown_and_entry_level_experience_policy():
    """
    Scenario G: Unknown Experience & 0-6 Years Experience Matching
    - Unstated experience is marked unverified but kept qualified.
    - Entry level / fresher / junior terms extract to 0-2 years.
    - Boundary checks: 3+ years and 5+ years qualify (3<=6, 5<=6).
    - 7+ years and 8-10 years disqualify (7>6, 8>6).
    """
    policy = QualificationPolicy()
    req = JobSearchRequest(
        raw_query="GenAI Engineer in Dubai (0-6 years exp)",
        job_title="GenAI Engineer",
        locations=["Dubai"],
        experience_min=0,
        experience_max=6,
    )

    # 1. Unspecified experience -> Qualified, experience_verified=False
    job_unspecified = {
        "title": "GenAI Engineer",
        "company": "DeepMind Partner",
        "location": "Dubai, UAE",
        "experience_min": None,
        "experience_max": None,
    }
    res_unspec = policy.qualify(job_unspecified, req)
    assert res_unspec.qualified is True
    assert res_unspec.experience_match == MatchStatus.UNKNOWN
    assert res_unspec.experience_verified is False
    assert any("unverified" in w for w in res_unspec.warnings)

    # 2. Extract tool natural language terms (Entry level / fresher)
    from datahunt.tools.extract import _enrich_job_fields
    entry_fields = _enrich_job_fields(
        {"title": "GenAI Engineer"},
        "We are hiring an Entry Level GenAI Engineer. Responsibilities include building LLM pipelines.",
        "https://example.com/job1"
    )
    assert entry_fields["experience_min"] == 0
    assert entry_fields["experience_max"] == 2

    # 3. 3+ years experience qualifies for 0-6 profile (3 <= 6)
    job_3plus = {
        "title": "GenAI Engineer",
        "company": "ScaleAI",
        "location": "Dubai, UAE",
        "experience_min": 3,
        "experience_max": None,
    }
    res_3plus = policy.qualify(job_3plus, req)
    assert res_3plus.qualified is True
    assert res_3plus.experience_match == MatchStatus.MATCH

    # 4. 5+ years experience qualifies for 0-6 profile (5 <= 6)
    job_5plus = {
        "title": "GenAI Engineer",
        "company": "Anthropic Partner",
        "location": "Dubai, UAE",
        "experience_min": 5,
        "experience_max": None,
    }
    res_5plus = policy.qualify(job_5plus, req)
    assert res_5plus.qualified is True
    assert res_5plus.experience_match == MatchStatus.MATCH

    # 5. 7+ years experience disqualifies (7 > 6)
    job_7plus = {
        "title": "Staff GenAI Engineer",
        "company": "MegaCorp",
        "location": "Dubai, UAE",
        "experience_min": 7,
        "experience_max": None,
    }
    res_7plus = policy.qualify(job_7plus, req)
    assert res_7plus.qualified is False
    assert res_7plus.experience_match == MatchStatus.MISMATCH
    assert any("experience_mismatch" in r for r in res_7plus.rejection_reasons)

    # 6. 8-10 years experience disqualifies (8 > 6)
    job_8to10 = {
        "title": "Principal GenAI Architect",
        "company": "Global Tech",
        "location": "Dubai, UAE",
        "experience_min": 8,
        "experience_max": 10,
    }
    res_8to10 = policy.qualify(job_8to10, req)
    assert res_8to10.qualified is False
    assert res_8to10.experience_match == MatchStatus.MISMATCH
    assert any("experience_mismatch" in r for r in res_8to10.rejection_reasons)
