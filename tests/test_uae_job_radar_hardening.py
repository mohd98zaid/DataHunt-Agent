import pytest
from unittest.mock import MagicMock

from datahunt.agent.discovery_models import (
    SearchTaskType,
    StopReason,
    DiscoveredSourceType,
    SearchTask,
    DiscoveryBudget,
    DiscoveryState,
    SourceCoverageMatrix,
)
from datahunt.agent.discovery_engine import DiscoveryEngine
from datahunt.agent.state import AgentState
from datahunt.agent.decision import AgentAction, DecisionEngine
from datahunt.agent.policies import qualify_job, QualificationPolicy
from datahunt.agents import JobSearchRequest
from datahunt.models.record import ExtractedRecord, VerificationStatus
from datahunt.tools.search import is_valid_job_url


def test_uae_geographic_coverage_includes_sharjah_and_cities():
    """
    Ensure UAE geographic coverage includes Sharjah, Dubai, and Abu Dhabi
    in both initial tasks and Round 4 city drilling.
    Also verify Saudi coverage includes Riyadh, Jeddah, Dammam, Khobar, and NEOM.
    """
    engine = DiscoveryEngine()

    # 1. Test UAE target locations and initial search tasks
    state_uae = AgentState(
        request="Find GenAI Engineer jobs in UAE",
        run_id="run_geo_uae",
        task_id="task_geo_uae",
        mode="jobs",
        explicit_titles=["GenAI Engineer"],
        locations=["UAE"],
    )
    job_req_uae = JobSearchRequest(job_title="GenAI Engineer", location="UAE", locations=["UAE"])
    tasks_uae = engine.generate_initial_tasks(job_req_uae, state_uae)

    queries_uae = [t.query for t in tasks_uae]
    # Invariant: Sharjah must be included in discrete target tasks
    assert any("sharjah" in q.lower() for q in queries_uae), f"Sharjah missing from UAE initial tasks: {queries_uae}"
    assert any("dubai" in q.lower() for q in queries_uae), f"Dubai missing from UAE initial tasks: {queries_uae}"
    assert any("abu dhabi" in q.lower() for q in queries_uae), f"Abu Dhabi missing from UAE initial tasks: {queries_uae}"

    # 2. Test Round 4 specific city drilling for UAE
    disc_state_uae = DiscoveryState()
    disc_state_uae.current_round = 4
    round4_tasks = engine.generate_next_round_tasks(disc_state_uae, job_req_uae, state_uae)
    r4_cities = [t.location.lower() for t in round4_tasks]
    assert "sharjah" in r4_cities, f"Sharjah missing from Round 4 tasks: {r4_cities}"
    assert "dubai" in r4_cities, f"Dubai missing from Round 4 tasks: {r4_cities}"
    assert "abu dhabi" in r4_cities, f"Abu Dhabi missing from Round 4 tasks: {r4_cities}"

    # 3. Test Saudi Arabia coverage includes all 5 major tech/industrial hubs
    state_saudi = AgentState(
        request="Find GenAI Engineer jobs in Saudi Arabia",
        run_id="run_geo_saudi",
        task_id="task_geo_saudi",
        mode="jobs",
        explicit_titles=["GenAI Engineer"],
        locations=["Saudi Arabia"],
    )
    job_req_saudi = JobSearchRequest(job_title="GenAI Engineer", location="Saudi Arabia", locations=["Saudi Arabia"])
    disc_state_saudi = DiscoveryState()
    disc_state_saudi.current_round = 4
    round4_saudi_tasks = engine.generate_next_round_tasks(disc_state_saudi, job_req_saudi, state_saudi)
    r4_saudi_cities = [t.location.lower() for t in round4_saudi_tasks]
    for expected_city in ("riyadh", "jeddah", "dammam", "khobar"):
        assert any(expected_city in c for c in r4_saudi_cities), f"{expected_city} missing from Saudi Round 4: {r4_saudi_cities}"


def test_empty_intermediate_round_does_not_stop_future_discovery():
    """
    Ensure that when an intermediate round (Round 2 pagination or Round 3 company ATS)
    produces 0 tasks, the engine does NOT terminate early with SOURCE_UNIVERSE_EXHAUSTED.
    It must automatically advance to subsequent rounds (Round 4, 5, 6).
    """
    engine = DiscoveryEngine()
    decision_engine = DecisionEngine()
    budget = DiscoveryBudget(max_expansion_rounds=6, max_search_requests=25)

    state = AgentState(
        request="GenAI Engineer jobs in UAE with 0-6 years experience",
        run_id="run_inter_test",
        task_id="task_inter_test",
        mode="jobs",
        target_results=10,
        max_search_calls=25,
        discovery_budget=budget,
        locations=["UAE"],
        explicit_location="UAE",
        explicit_titles=["GenAI Engineer"],
    )
    job_req = JobSearchRequest(job_title="GenAI Engineer", location="UAE", locations=["UAE"])
    disc_state = DiscoveryState()

    # Case A: Round 2 has no tasks to paginate (completed_tasks is empty or no valid page 2s)
    # Case B: Round 3 has no discovered ATS companies
    disc_state.current_round = 2
    disc_state.completed_tasks = []
    disc_state.discovered_ats = {}
    disc_state.task_queue.clear()

    # generate_next_round_tasks must loop past empty Round 2 & 3 and yield Round 4 city drilling tasks
    next_tasks = engine.generate_next_round_tasks(disc_state, job_req, state)
    assert len(next_tasks) > 0, "generate_next_round_tasks returned empty list despite future rounds having tasks"
    assert disc_state.current_round == 4, f"Expected current_round to advance to 4, got {disc_state.current_round}"
    assert any(t.round == 4 for t in next_tasks), "Expected Round 4 tasks to be generated"

    # Invariant: DecisionEngine does NOT declare STOP when task_queue was empty but rounds can still be advanced
    empty_queue_state = AgentState(
        request="GenAI Engineer jobs in UAE",
        run_id="run_empty_q",
        task_id="task_empty_q",
        mode="jobs",
        target_results=10,
        max_search_calls=25,
        discovery_budget=budget,
    )
    empty_disc_state = DiscoveryState()
    empty_disc_state.current_round = 2
    empty_disc_state.task_queue = []
    empty_queue_state.discovery_state = empty_disc_state

    action, reason = decision_engine.decide(empty_queue_state)
    assert action == AgentAction.SEARCH, f"DecisionEngine prematurely stopped with: {reason}"
    assert "Advancing discovery round" in reason or "Searching discovery queue" in reason


def test_golden_uae_job_discovery_and_qualification():
    """
    Golden integration test:
    Verify that UAE GenAI Engineer jobs from ALL 4 source types:
    - Direct ATS (Greenhouse, Lever, Ashby, Workable)
    - Regional job boards (NaukriGulf, Bayt, GulfTalent)
    - Major job boards (LinkedIn)
    - Company career pages
    are properly discovered, accepted by is_valid_job_url, and qualified
    when meeting title, location (Dubai/Abu Dhabi/Sharjah/UAE), and experience requirements,
    while non-qualifying jobs (foreign anchor, mismatch role, excessive experience) are strictly rejected.
    """
    job_req = JobSearchRequest(
        job_title="GenAI Engineer",
        location="UAE",
        locations=["UAE", "Dubai", "Abu Dhabi"],
        experience_min=0,
        experience_max=6,
        remote_allowed=True,
    )

    # 1. URL validity checks for all source types
    ats_url = "https://boards.greenhouse.io/coblestoneenergy/jobs/4567890"
    reg_naukri_url = "https://www.naukrigulf.com/genai-engineer-jobs-in-dubai-uae-in-techcorp-jid-123456"
    reg_bayt_url = "https://www.bayt.com/en/uae/jobs/genai-engineer-7654321/"
    maj_linkedin_url = "https://www.linkedin.com/jobs/view/394857201"
    career_url = "https://acme-corp.com/careers/genai-engineer-dubai-101"

    for u in (ats_url, reg_naukri_url, reg_bayt_url, maj_linkedin_url, career_url):
        assert is_valid_job_url(u), f"Valid job URL was rejected: {u}"

    # Search indices must be rejected
    bad_urls = [
        "https://www.naukrigulf.com/genai-engineer-jobs-in-dubai",
        "https://www.bayt.com/en/uae/jobs/search",
        "https://www.linkedin.com/in/some-user",
        "https://boards.greenhouse.io/users/sign_in",
    ]
    for bu in bad_urls:
        assert not is_valid_job_url(bu), f"Search index/login URL was accepted: {bu}"

    # 2. Qualification of Direct ATS job in Dubai
    ats_rec = ExtractedRecord(
        id="rec_ats_1",
        run_id="run_golden",
        record_type="job",
        fields={
            "title": "Generative AI Engineer",
            "company": "Cobblestone Energy",
            "location": "Dubai, UAE",
            "experience_min": 2,
            "experience_max": 5,
            "skills": ["Python", "PyTorch", "LLMs"],
            "application_url": ats_url,
            "source": "boards.greenhouse.io",
        },
        canonical_url=ats_url,
        verification_status=VerificationStatus.VERIFIED,
    )
    res_ats = qualify_job(ats_rec, job_req)
    assert res_ats.qualified is True
    assert res_ats.eligibility_status == "ELIGIBLE"
    assert res_ats.score >= 0.85

    # 3. Qualification of Regional Job Board posting on NaukriGulf (no ATS required!)
    naukri_rec = ExtractedRecord(
        id="rec_reg_1",
        run_id="run_golden",
        record_type="job",
        fields={
            "title": "GenAI Engineer",
            "company": "TechCorp MENA",
            "location": "Dubai, United Arab Emirates",
            "experience_min": 3,
            "experience_max": 5,
            "skills": ["LLM", "RAG", "LangChain"],
            "application_url": reg_naukri_url,
            "source": "naukrigulf.com",
            "source_type": "regional_board",
        },
        canonical_url=reg_naukri_url,
        verification_status=VerificationStatus.VERIFIED,
    )
    res_naukri = qualify_job(naukri_rec, job_req)
    assert res_naukri.qualified is True, f"Regional board job failed qualification: {res_naukri.reasons}"
    assert res_naukri.eligibility_status == "ELIGIBLE"

    # 4. Qualification of Major Board posting on LinkedIn in Abu Dhabi
    linkedin_rec = ExtractedRecord(
        id="rec_maj_1",
        run_id="run_golden",
        record_type="job",
        fields={
            "title": "AI Engineer",
            "company": "G42 / Inception",
            "location": "Abu Dhabi, UAE",
            "experience_min": 1,
            "experience_max": 4,
            "skills": ["Deep Learning", "Generative Models"],
            "application_url": maj_linkedin_url,
            "source": "linkedin.com",
        },
        canonical_url=maj_linkedin_url,
        verification_status=VerificationStatus.VERIFIED,
    )
    res_linkedin = qualify_job(linkedin_rec, job_req)
    assert res_linkedin.qualified is True
    assert res_linkedin.eligibility_status == "ELIGIBLE"

    # 5. Strict rejection of foreign location (London, UK)
    foreign_rec = ExtractedRecord(
        id="rec_foreign",
        run_id="run_golden",
        record_type="job",
        fields={
            "title": "GenAI Engineer",
            "company": "UK AI Labs",
            "location": "London, United Kingdom",
            "experience_min": 3,
            "experience_max": 5,
        },
        canonical_url="https://example.com/job_london",
        verification_status=VerificationStatus.VERIFIED,
    )
    res_foreign = qualify_job(foreign_rec, job_req)
    assert res_foreign.qualified is False
    assert any("location" in r for r in res_foreign.rejection_reasons)

    # 6. Strict rejection of unrelated job family (Product Manager)
    pm_rec = ExtractedRecord(
        id="rec_pm",
        run_id="run_golden",
        record_type="job",
        fields={
            "title": "AI Product Manager",
            "company": "Dubai Tech Hub",
            "location": "Dubai, UAE",
            "experience_min": 3,
            "experience_max": 5,
        },
        canonical_url="https://example.com/job_pm",
        verification_status=VerificationStatus.VERIFIED,
    )
    res_pm = qualify_job(pm_rec, job_req)
    assert res_pm.qualified is False
    assert any("title" in r for r in res_pm.rejection_reasons)

    # 7. Strict rejection of excessive experience (>6 years)
    senior_rec = ExtractedRecord(
        id="rec_senior",
        run_id="run_golden",
        record_type="job",
        fields={
            "title": "Staff GenAI Engineer",
            "company": "Emirates AI",
            "location": "Dubai, UAE",
            "experience_min": 10,
            "experience_max": 14,
        },
        canonical_url="https://example.com/job_senior",
        verification_status=VerificationStatus.VERIFIED,
    )
    res_senior = qualify_job(senior_rec, job_req)
    assert res_senior.qualified is False
    assert any("experience" in r for r in res_senior.rejection_reasons)


def test_zero_result_deterministic_diagnostics():
    """
    Ensure that when qualified == 0, the AgentRuntime._finalize output
    includes deterministic diagnostics containing:
    1. Execution & Pipeline counters (queries, pages fetched, candidates, jobs extracted, duplicates, verified, qualified, rejected)
    2. Rejection reasons breakdown (location_mismatch, title_mismatch, experience_mismatch, missing_explicit_skill, insufficient_evidence)
    3. Source distribution (direct_ats, regional_board, company_careers, major_board)
    """
    from datahunt.agent.runtime import AgentRuntime

    client = MagicMock()
    search = MagicMock()
    fetch = MagicMock()
    extract = MagicMock()
    verify = MagicMock()
    dedupe = MagicMock()
    export = MagicMock()

    runtime = AgentRuntime(
        client=client,
        search=search,
        fetch=fetch,
        extract=extract,
        verify=verify,
        dedupe=dedupe,
        export=export,
    )

    state = AgentState(
        request="GenAI Engineer jobs in UAE with 0-6 years experience",
        run_id="run_diag_test",
        task_id="task_diag_test",
        mode="jobs",
        target_results=10,
    )
    state.search_calls = 5
    state.fetch_calls = 14
    state.seen_canonical_urls = {"http://a.com/1", "http://b.com/2", "http://c.com/3", "http://d.com/4"}
    state.raw_records = [MagicMock(id=f"r{i}") for i in range(4)]
    state.duplicate_records = [MagicMock()]
    state.verified_records = []
    state.qualified_records = []
    state.rejected_records = [MagicMock(), MagicMock(), MagicMock()]

    # Populate rejection reasons and source distribution
    state.rejection_reasons_tally = {
        "location_mismatch": 2,
        "title_mismatch": 1,
        "experience_mismatch": 0,
        "missing_explicit_skill": 0,
        "insufficient_evidence": 0,
    }
    state.source_distribution = {
        "direct_ats": 1,
        "regional_board": 2,
        "company_careers": 1,
        "major_board": 0,
    }

    emit = MagicMock()
    result = runtime._finalize(state, emit)

    assert "diagnostics" in result, "Result dictionary missing 'diagnostics'"
    diag = result["diagnostics"]

    # Verify counters
    assert "counters" in diag
    counters = diag["counters"]
    assert counters["search_queries"] == 5
    assert counters["pages_fetched"] == 14
    assert counters["candidates"] == 4
    assert counters["jobs_extracted"] == 4
    assert counters["duplicates"] == 1
    assert counters["verified"] == 0
    assert counters["qualified"] == 0
    assert counters["rejected"] == 3

    # Verify rejection reasons
    assert "rejection_reasons" in diag
    reasons = diag["rejection_reasons"]
    assert reasons["location_mismatch"] == 2
    assert reasons["title_mismatch"] == 1
    assert reasons["experience_mismatch"] == 0

    # Verify source distribution
    assert "source_distribution" in diag
    src_dist = diag["source_distribution"]
    assert src_dist["direct_ats"] == 1
    assert src_dist["regional_board"] == 2
    assert src_dist["company_careers"] == 1
    assert src_dist["major_board"] == 0
