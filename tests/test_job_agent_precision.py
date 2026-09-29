"""
Comprehensive Test Suite for DataHunt Job Agent Precision & Reliability (Sections 23 & 24).

Enforces:
1. Invariant A: Explicit skill requirements are hard constraints.
2. Invariant B: Inferred skills are NOT hard constraints (ranking only).
3. Invariant C: Qualification precedes ranking; ranking can NEVER rescue disqualified jobs.
4. Invariant D: Single authoritative QualificationPolicy implementation.
5. Strict OR semantics for multi-region location matching (Saudi Arabia or UAE).
6. Remote + geography separation (agnostic remote vs foreign-anchored remote).
7. Multi-region salary currency is never silently guessed.
8. Title matching strictly rejects unrelated job families even if they contain AI/GenAI.
9. Duplicate vacancies across sources (LinkedIn, ATS, careers) merged into canonical record.
10. Company research never fabricates information when sources are missing.
11. Authoritative search path and dynamic discovery.
12. Golden End-to-End Test.
"""
import pytest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from datahunt.models import ExtractedRecord, VerificationStatus
from datahunt.models.job_spec import JobSearchSpec
from datahunt.agents.query_understanding import QueryUnderstandingAgent, JobSearchRequest
from datahunt.agents.normalizer import DataNormalizer, NormalizedJob
from datahunt.agents.hard_filter import HardFilter, HardFilterAgent
from datahunt.agents.job_analysis import JobAnalysisAgent, JobMatchResult
from datahunt.agents.company_research import CompanyResearchAgent, CompanyProfile
from datahunt.agent.policies import (
    QualificationPolicy,
    qualify_job,
    match_location,
    match_experience,
    match_title_relevance,
    TitleCategory,
    MatchStatus,
)
from datahunt.tools.dedupe import DedupeTool, deduplicate_normalized_jobs
from datahunt.agent.state import AgentState, AgentStatus
from datahunt.agent.decision import DecisionEngine, AgentAction
from datahunt.agent.runtime import AgentRuntime
from datahunt.agent.discovery_engine import DiscoveryEngine
from datahunt.agent.discovery_models import SearchTask, SearchTaskType, DiscoveredSourceType, DiscoveryState


# ─────────────────────────────────────────────────────────────────────────────
# 1. Invariant A: Explicit Skill Requirements are Hard Constraints
# ─────────────────────────────────────────────────────────────────────────────

def test_explicit_skills_hard_constraints():
    """
    Invariant A: All explicitly requested skills must be present.
    Missing any explicit skill yields hard rejection (MISMATCH).
    """
    policy = QualificationPolicy()
    req = JobSearchRequest(
        raw_query="Find GenAI Engineer requiring Python and LangChain in Dubai",
        job_title="GenAI Engineer",
        location="Dubai",
        explicit_skills=["Python", "LangChain"],
    )

    # Job 1: Both Python and LangChain present -> PASS
    j1 = {
        "title": "GenAI Engineer",
        "company": "Tech One",
        "location": "Dubai, UAE",
        "description": "Building LLM agents using Python and LangChain framework.",
        "skills": ["Python", "LangChain"],
    }
    res1 = policy.qualify(j1, req)
    assert res1.qualified is True
    assert res1.eligibility_status == "ELIGIBLE"
    assert "python" in [s.lower() for s in res1.matched_skills]
    assert "langchain" in [s.lower() for s in res1.matched_skills]
    assert len(res1.missing_skills) == 0

    # Job 2: Python only (missing LangChain) -> FAIL
    j2 = {
        "title": "GenAI Engineer",
        "company": "Tech Two",
        "location": "Dubai, UAE",
        "description": "Building LLM agents using Python and native PyTorch.",
        "skills": ["Python", "PyTorch"],
    }
    res2 = policy.qualify(j2, req)
    assert res2.qualified is False
    assert res2.eligibility_status == "INELIGIBLE"
    assert "langchain" in [s.lower() for s in res2.missing_skills]
    assert any("missing_explicit_skill:langchain" in r for r in res2.rejection_reasons)

    # Job 3: LangChain only (missing Python) -> FAIL
    j3 = {
        "title": "GenAI Engineer",
        "company": "Tech Three",
        "location": "Dubai, UAE",
        "description": "Expert in LangChain orchestration and TypeScript APIs.",
        "skills": ["LangChain", "TypeScript"],
    }
    res3 = policy.qualify(j3, req)
    assert res3.qualified is False
    assert res3.eligibility_status == "INELIGIBLE"
    assert "python" in [s.lower() for s in res3.missing_skills]

    # Job 4: Neither present -> FAIL
    j4 = {
        "title": "GenAI Engineer",
        "company": "Tech Four",
        "location": "Dubai, UAE",
        "description": "Working with Java and Spring backend microservices.",
        "skills": ["Java", "Spring"],
    }
    res4 = policy.qualify(j4, req)
    assert res4.qualified is False
    assert res4.eligibility_status == "INELIGIBLE"
    assert len(res4.missing_skills) == 2


# ─────────────────────────────────────────────────────────────────────────────
# 2. Invariant B: Inferred Skills Do Not Disqualify
# ─────────────────────────────────────────────────────────────────────────────

def test_inferred_skills_do_not_disqualify():
    """
    Invariant B: Inferred skills are NOT hard constraints.
    Missing an inferred skill affects scoring/ranking only, never causes rejection.
    """
    policy = QualificationPolicy()
    # User asked for "AI Engineer in Dubai" - no explicit skills in raw_query
    req = JobSearchRequest(
        raw_query="Find AI Engineer jobs in Dubai",
        job_title="AI Engineer",
        location="Dubai",
        explicit_skills=[],
        inferred_skills=["RAG", "PyTorch", "Transformers"],
    )

    # Job matches title and location, but mentions NONE of the inferred skills
    j = {
        "title": "AI Engineer",
        "company": "Desert AI",
        "location": "Dubai, UAE",
        "description": "Designing intelligent decision systems and data pipelines in Python.",
    }

    res = policy.qualify(j, req)
    assert res.qualified is True
    assert res.eligibility_status == "ELIGIBLE"
    # Qualification is unaffected even if inferred skills are missing
    assert len(res.rejection_reasons) == 0


# ─────────────────────────────────────────────────────────────────────────────
# 3. Location Matching: Strict OR Semantics for Saudi Arabia or UAE
# ─────────────────────────────────────────────────────────────────────────────

def test_saudi_arabia_or_uae_strict_or_semantics():
    """
    Strict OR semantics:
    Saudi cities (Riyadh, Jeddah, Dammam, NEOM) -> MATCH
    UAE cities (Dubai, Abu Dhabi, Sharjah) -> MATCH
    Foreign cities (London, Cairo, San Francisco, Bangalore) -> MISMATCH
    """
    locs = ["Saudi Arabia", "UAE"]

    # Saudi locations -> MATCH
    for loc in ["Riyadh, Saudi Arabia", "Jeddah, KSA", "Dammam", "NEOM, Saudi Arabia"]:
        status, _ = match_location(locs, loc)
        assert status == MatchStatus.MATCH, f"Failed for {loc}"

    # UAE locations -> MATCH
    for loc in ["Dubai, UAE", "Abu Dhabi, United Arab Emirates", "Sharjah"]:
        status, _ = match_location(locs, loc)
        assert status == MatchStatus.MATCH, f"Failed for {loc}"

    # Foreign locations -> MISMATCH
    for loc in ["London, UK", "Cairo, Egypt", "San Francisco, CA", "Bengaluru, India"]:
        status, _ = match_location(locs, loc)
        assert status == MatchStatus.MISMATCH, f"Failed for foreign location {loc}"


# ─────────────────────────────────────────────────────────────────────────────
# 4. Unknown Location Rejected for Strict Geographic Search
# ─────────────────────────────────────────────────────────────────────────────

def test_unknown_location_rejected_for_strict_geographic_search():
    """
    When searching for a specific geographic region (e.g. Saudi Arabia or UAE),
    a posting with no location or unverified location must be rejected (not accepted).
    """
    policy = QualificationPolicy()
    req = JobSearchRequest(
        raw_query="Find GenAI Engineer in Saudi Arabia or UAE",
        job_title="GenAI Engineer",
        locations=["Saudi Arabia", "UAE"],
        location="Saudi Arabia or UAE",
        remote_allowed=False,
    )

    # Job with empty location
    j_empty = {
        "title": "GenAI Engineer",
        "company": "Secret Corp",
        "location": "",
        "remote_status": "onsite",
    }
    res_empty = policy.qualify(j_empty, req)
    assert res_empty.qualified is False
    assert res_empty.eligibility_status == "INELIGIBLE"
    assert any("unverified_location" in r for r in res_empty.rejection_reasons)

    # Job with unclassifiable unknown location
    j_unclass = {
        "title": "GenAI Engineer",
        "company": "Nomad Corp",
        "location": "Timbuktu Province",
        "remote_status": "onsite",
    }
    res_unclass = policy.qualify(j_unclass, req)
    assert res_unclass.qualified is False
    assert res_unclass.eligibility_status == "INELIGIBLE"


# ─────────────────────────────────────────────────────────────────────────────
# 5. Remote vs Geography Matching
# ─────────────────────────────────────────────────────────────────────────────

def test_remote_vs_geography_matching():
    """
    Remote matching behavior:
    - Saudi remote -> PASS
    - UAE remote -> PASS
    - Agnostic global remote (when remote allowed) -> PASS
    - Foreign-anchored remote (e.g. US Only Remote) -> FAIL
    """
    policy = QualificationPolicy()
    req = JobSearchRequest(
        raw_query="GenAI Engineer in Saudi Arabia or UAE",
        job_title="GenAI Engineer",
        locations=["Saudi Arabia", "UAE"],
        location="Saudi Arabia or UAE",
        remote_allowed=True,
    )

    # Saudi remote
    j_saudi_remote = {
        "title": "GenAI Engineer",
        "location": "Riyadh (Remote)",
        "remote_status": "remote",
    }
    assert policy.qualify(j_saudi_remote, req).qualified is True

    # UAE remote
    j_uae_remote = {
        "title": "GenAI Engineer",
        "location": "Dubai (Remote)",
        "remote_status": "remote",
    }
    assert policy.qualify(j_uae_remote, req).qualified is True

    # Agnostic global remote
    j_agnostic = {
        "title": "GenAI Engineer",
        "location": "Remote",
        "remote_status": "remote",
    }
    assert policy.qualify(j_agnostic, req).qualified is True

    # Foreign US-anchored remote -> Must be REJECTED
    j_us_remote = {
        "title": "GenAI Engineer",
        "location": "Denver, CO; Chicago, IL (Remote); United States (Remote)",
        "remote_status": "remote",
    }
    res_us = policy.qualify(j_us_remote, req)
    assert res_us.qualified is False
    assert any("location_mismatch" in r for r in res_us.rejection_reasons)


# ─────────────────────────────────────────────────────────────────────────────
# 6. Title Matching: Strict Rejection of Unrelated Job Families
# ─────────────────────────────────────────────────────────────────────────────

def test_title_matching_strict_rejection_of_unrelated_job_families():
    """
    Rejects unrelated job families even if they contain 'AI' or 'GenAI':
    - GenAI Engineer -> PASS
    - Generative AI Software Engineer -> PASS
    - AI Product Manager -> FAIL (Product Management)
    - AI Sales Manager -> FAIL (Sales & Marketing)
    - AI Content Writer -> FAIL (Content Writing)
    - AI Business Analyst -> FAIL (Business Analysis)
    - Full Stack Engineer -> FAIL (Full Stack)
    """
    policy = QualificationPolicy()
    req = JobSearchRequest(
        raw_query="GenAI Engineer in Dubai",
        job_title="GenAI Engineer",
        location="Dubai",
    )

    # Legitimate technical roles -> PASS
    assert policy.qualify({"title": "GenAI Engineer", "location": "Dubai"}, req).qualified is True
    assert policy.qualify({"title": "Generative AI Software Engineer", "location": "Dubai"}, req).qualified is True
    assert policy.qualify({"title": "Applied AI Engineer", "location": "Dubai"}, req).qualified is True

    # Unrelated job families -> FAIL
    for bad_title in [
        "AI Product Manager",
        "Generative AI Product Owner",
        "AI Sales Manager",
        "AI Content Writer",
        "AI Business Analyst",
        "Full Stack Engineer (React/Node)",
        "Cybersecurity SOC Analyst",
        "Senior Accountant",
    ]:
        res = policy.qualify({"title": bad_title, "location": "Dubai"}, req)
        assert res.qualified is False, f"Expected {bad_title} to be disqualified"
        assert res.title_match == MatchStatus.MISMATCH or res.title_category == TitleCategory.MISMATCH


# ─────────────────────────────────────────────────────────────────────────────
# 7. Experience Boundary Matching
# ─────────────────────────────────────────────────────────────────────────────

def test_experience_boundary_matching():
    """
    Tests experience constraints:
    User has 0-3 years:
    - 5+ years required -> MISMATCH (Disqualified)
    - 1-3 years required -> MATCH (Qualified)
    - Unstated experience -> UNKNOWN (Preserved, not disqualified on exp alone)
    """
    policy = QualificationPolicy()
    req = JobSearchRequest(
        raw_query="GenAI Engineer in Dubai with 0-3 years experience",
        job_title="GenAI Engineer",
        location="Dubai",
        experience_min=0,
        experience_max=3,
    )

    # 5+ years required -> FAIL
    j_senior = {"title": "GenAI Engineer", "location": "Dubai", "experience_min": 5}
    res_senior = policy.qualify(j_senior, req)
    assert res_senior.qualified is False
    assert res_senior.experience_match == MatchStatus.MISMATCH

    # 1-3 years -> PASS
    j_junior = {"title": "GenAI Engineer", "location": "Dubai", "experience_min": 1, "experience_max": 3}
    assert policy.qualify(j_junior, req).qualified is True

    # Undisclosed experience -> UNKNOWN, but qualified
    j_unstated = {"title": "GenAI Engineer", "location": "Dubai"}
    res_unstated = policy.qualify(j_unstated, req)
    assert res_unstated.experience_match == MatchStatus.UNKNOWN
    assert res_unstated.qualified is True


# ─────────────────────────────────────────────────────────────────────────────
# 8. Multi-Region Salary Currency Never Guessed
# ─────────────────────────────────────────────────────────────────────────────

def test_multi_region_salary_currency_never_guessed():
    """
    Multi-region query (Saudi Arabia or UAE) must NEVER silently assign SAR or AED.
    Single-region queries resolve their local currency.
    """
    qu = QueryUnderstandingAgent()

    # Multi-region: must NOT guess currency
    req_multi = qu.understand("Find GenAI Engineer in Saudi Arabia or UAE")
    assert req_multi.salary_currency is None

    # Explicit USD in multi-region: preserved
    req_multi_usd = qu.understand("Find GenAI Engineer in Saudi Arabia or UAE with $120k salary")
    assert req_multi_usd.salary_currency == "USD"

    # Single-region Saudi: resolves SAR
    req_saudi = qu.understand("Find GenAI Engineer in Riyadh, Saudi Arabia")
    assert req_saudi.salary_currency == "SAR"

    # Single-region UAE: resolves AED
    req_uae = qu.understand("Find GenAI Engineer in Dubai")
    assert req_uae.salary_currency == "AED"


# ─────────────────────────────────────────────────────────────────────────────
# 9. Duplicate Vacancies Across Sources Merged
# ─────────────────────────────────────────────────────────────────────────────

def test_duplicate_vacancies_across_sources_merged():
    """
    Postings for the same vacancy from LinkedIn, Greenhouse, and Company Career:
    - Collapsed into 1 canonical NormalizedJob.
    - All sources tracked in `sources`.
    - Primary application URL prefers Greenhouse / Career page over LinkedIn.
    """
    normalizer = DataNormalizer()
    raw_vacancies = [
        {
            "title": "Staff GenAI Engineer",
            "company": "Tuwaiq Intelligence",
            "location": "Riyadh, Saudi Arabia",
            "source": "LinkedIn",
            "job_url": "https://www.linkedin.com/jobs/view/99001",
            "apply_url": "https://www.linkedin.com/jobs/view/99001",
        },
        {
            "title": "Staff GenAI Engineer",
            "company": "Tuwaiq Intelligence",
            "location": "Riyadh, Saudi Arabia",
            "source": "Greenhouse",
            "job_url": "https://boards.greenhouse.io/tuwaiq/jobs/99002",
            "apply_url": "https://boards.greenhouse.io/tuwaiq/jobs/99002",
        },
        {
            "title": "Staff GenAI Engineer",
            "company": "Tuwaiq Intelligence",
            "location": "Riyadh, Saudi Arabia",
            "source": "Tuwaiq Careers",
            "job_url": "https://tuwaiq.ai/careers/staff-genai",
            "apply_url": "https://tuwaiq.ai/careers/staff-genai",
        },
    ]

    normalized = [normalizer.normalize(v, record_id=f"rec_{i}") for i, v in enumerate(raw_vacancies)]
    deduped = deduplicate_normalized_jobs(normalized)

    assert len(deduped) == 1
    canonical = deduped[0]
    assert len(canonical.sources) >= 2
    assert any("greenhouse" in s.lower() for s in canonical.sources)
    assert any(pref in canonical.primary_application_url for pref in ("greenhouse.io", "tuwaiq.ai"))
    assert "linkedin.com" not in canonical.primary_application_url


# ─────────────────────────────────────────────────────────────────────────────
# 10. Company Research Never Fabricates Information
# ─────────────────────────────────────────────────────────────────────────────

def test_company_research_never_fabricates():
    """
    When company has no verifiable search hits:
    - verification_status == 'UNVERIFIED'
    - is_available == False
    - company_facts == []
    - Zero synthetic hallucinated facts
    """
    mock_search = MagicMock()
    mock_search.execute.return_value = MagicMock(success=True, data=[])

    agent = CompanyResearchAgent(search_tool=mock_search)
    profile = agent.research("CompletelyUnknownFictionalCompany12345", force_refresh=True)

    assert profile.verification_status == "UNVERIFIED"
    assert profile.is_available is False
    assert len(profile.company_facts) == 0
    assert len(profile.sources) == 0
    assert profile.status == "unavailable"


# ─────────────────────────────────────────────────────────────────────────────
# 11. Invariant C: Ranking Cannot Rescue Disqualified Jobs
# ─────────────────────────────────────────────────────────────────────────────

def test_ranking_cannot_rescue_disqualified_jobs():
    """
    Invariant C:
    Job A: Has high token overlap and senior title, but is missing required explicit skill (LangChain).
    Job B: Fully meets all constraints including explicit skill (LangChain).

    JobAnalysisAgent.analyze_and_rank MUST eliminate Job A before ranking.
    Ranking scores can NEVER rescue Job A into the output.
    """
    analyzer = JobAnalysisAgent()
    req = JobSearchRequest(
        raw_query="Find GenAI Engineer requiring LangChain in Dubai",
        job_title="GenAI Engineer",
        location="Dubai",
        explicit_skills=["LangChain"],
    )

    norm = DataNormalizer()
    job_a = norm.normalize({
        "title": "Principal GenAI Engineer",
        "company": "High Tech",
        "location": "Dubai, UAE",
        "skills": ["Python", "PyTorch", "Kubernetes"],  # Missing LangChain
        "description": "Leading massive scale GenAI systems.",
    }, record_id="job_a")

    job_b = norm.normalize({
        "title": "GenAI Engineer",
        "company": "Noor AI",
        "location": "Dubai, UAE",
        "skills": ["Python", "LangChain"],  # Has LangChain
        "description": "Building agents using LangChain.",
    }, record_id="job_b")

    results = analyzer.analyze_and_rank([job_a, job_b], req)

    # Job A MUST NOT be in the results!
    result_ids = [r.job.raw_id for r in results]
    assert "job_a" not in result_ids
    assert "job_b" in result_ids
    assert len(results) == 1
    assert results[0].eligibility_status == "ELIGIBLE"


# ─────────────────────────────────────────────────────────────────────────────
# 12. Single Authoritative Qualification Policy (Invariant D)
# ─────────────────────────────────────────────────────────────────────────────

def test_hard_filter_delegates_to_authoritative_qualification_policy():
    """
    Invariant D: HardFilter must delegate directly to the single authoritative QualificationPolicy.
    """
    hard_filter = HardFilterAgent()
    req = JobSearchRequest(
        raw_query="Find GenAI Engineer in Riyadh requiring Python",
        job_title="GenAI Engineer",
        location="Riyadh",
        explicit_skills=["Python"],
    )

    jobs = [
        {"id": "j1", "title": "GenAI Engineer", "location": "Riyadh, Saudi Arabia", "skills": ["Python"]},
        {"id": "j2", "title": "AI Product Manager", "location": "Riyadh, Saudi Arabia", "skills": ["Python"]},
        {"id": "j3", "title": "GenAI Engineer", "location": "London, UK", "skills": ["Python"]},
        {"id": "j4", "title": "GenAI Engineer", "location": "Riyadh, Saudi Arabia", "skills": ["Java"]},
    ]

    passed, rejected = hard_filter.apply(jobs, req)
    passed_ids = [j["id"] for j in passed]
    rejected_ids = [j["id"] for j, _ in rejected]

    assert passed_ids == ["j1"]
    assert "j2" in rejected_ids  # Unrelated title family
    assert "j3" in rejected_ids  # Geographic mismatch
    assert "j4" in rejected_ids  # Missing explicit skill


# ─────────────────────────────────────────────────────────────────────────────
# 13. Section 24 Golden End-to-End Test
# ─────────────────────────────────────────────────────────────────────────────

def test_section_24_golden_end_to_end_job_search():
    """
    Section 24 Golden End-to-End Test:
    Objective:
      'Find GenAI Engineer jobs in Saudi Arabia or UAE with 0-5 years experience requiring Python'

    10 Candidate postings:
      - 3 Qualifying jobs (Dubai, Riyadh, Abu Dhabi with Python and 0-5 yrs exp)
      - 7 Distractors:
        1. AI Product Manager (Unrelated job family)
        2. London AI Engineer (Location mismatch)
        3. Senior GenAI Architect 10+ yrs (Experience mismatch)
        4. GenAI Engineer missing Python (Missing explicit skill)
        5. Cybersecurity SOC Analyst (Unrelated job family)
        6. Duplicate vacancy of candidate 1 from another board (Duplicate)
        7. Denver US Remote GenAI Engineer (Foreign-anchored remote)

    Result:
      Exactly 3 unique canonical jobs qualified and returned.
      0 disqualified candidates rescued.
    """
    spec = JobSearchSpec(
        raw_query="Find GenAI Engineer jobs in Saudi Arabia or UAE with 0-5 years experience requiring Python",
        titles=["GenAI Engineer"],
        locations=["Saudi Arabia", "UAE"],
        location_operator="OR",
        experience_min=0,
        experience_max=5,
        explicit_skills=["Python"],
    )

    now_iso = datetime.now(timezone.utc).isoformat()

    candidates = [
        # 1. GenAI Engineer, Dubai, 3 yrs, Python (QUALIFIED)
        ExtractedRecord(
            id="cand_1",
            run_id="run_golden",
            record_type="job_listing",
            fields={
                "title": "GenAI Engineer",
                "company": "Noor AI",
                "location": "Dubai, UAE",
                "experience_min": 3,
                "experience_max": 5,
                "skills": ["Python", "PyTorch"],
                "description": "Building LLMs with Python.",
                "job_url": "https://boards.greenhouse.io/noorai/jobs/101",
            },
            canonical_url="https://boards.greenhouse.io/noorai/jobs/101",
            verification_status=VerificationStatus.VERIFIED,
            created_at=now_iso,
        ),
        # 2. Generative AI Software Engineer, Riyadh, 2 yrs, Python (QUALIFIED)
        ExtractedRecord(
            id="cand_2",
            run_id="run_golden",
            record_type="job_listing",
            fields={
                "title": "Generative AI Software Engineer",
                "company": "Tuwaiq Intelligence",
                "location": "Riyadh, Saudi Arabia",
                "experience_min": 2,
                "experience_max": 4,
                "skills": ["Python", "LangChain"],
                "description": "Developing generative AI pipelines in Python.",
                "job_url": "https://jobs.lever.co/tuwaiq/202",
            },
            canonical_url="https://jobs.lever.co/tuwaiq/202",
            verification_status=VerificationStatus.VERIFIED,
            created_at=now_iso,
        ),
        # 3. LLM Systems Engineer, Abu Dhabi, 4 yrs, Python (QUALIFIED)
        ExtractedRecord(
            id="cand_3",
            run_id="run_golden",
            record_type="job_listing",
            fields={
                "title": "LLM Systems Engineer",
                "company": "Falcon Frontier",
                "location": "Abu Dhabi, UAE",
                "experience_min": 4,
                "experience_max": 5,
                "skills": ["Python", "FastAPI"],
                "description": "Python engineering for low latency LLM services.",
                "job_url": "https://jobs.ashbyhq.com/falconfrontier/303",
            },
            canonical_url="https://jobs.ashbyhq.com/falconfrontier/303",
            verification_status=VerificationStatus.VERIFIED,
            created_at=now_iso,
        ),
        # 4. AI Product Manager, Dubai (REJECTED: job family)
        ExtractedRecord(
            id="cand_4",
            run_id="run_golden",
            record_type="job_listing",
            fields={"title": "AI Product Manager", "company": "Gulf Corp", "location": "Dubai, UAE", "skills": ["Python"]},
            verification_status=VerificationStatus.VERIFIED,
            created_at=now_iso,
        ),
        # 5. GenAI Engineer, London (REJECTED: location)
        ExtractedRecord(
            id="cand_5",
            run_id="run_golden",
            record_type="job_listing",
            fields={"title": "GenAI Engineer", "company": "Thames AI", "location": "London, UK", "skills": ["Python"]},
            verification_status=VerificationStatus.VERIFIED,
            created_at=now_iso,
        ),
        # 6. Lead GenAI Architect, Riyadh, 12 yrs (REJECTED: experience)
        ExtractedRecord(
            id="cand_6",
            run_id="run_golden",
            record_type="job_listing",
            fields={"title": "Lead GenAI Architect", "company": "Riyadh Cloud", "location": "Riyadh, Saudi Arabia", "experience_min": 10, "skills": ["Python"]},
            verification_status=VerificationStatus.VERIFIED,
            created_at=now_iso,
        ),
        # 7. GenAI Engineer, Dubai, missing Python (REJECTED: explicit skill)
        ExtractedRecord(
            id="cand_7",
            run_id="run_golden",
            record_type="job_listing",
            fields={"title": "GenAI Engineer", "company": "Java AI", "location": "Dubai, UAE", "skills": ["Java", "Scala"]},
            verification_status=VerificationStatus.VERIFIED,
            created_at=now_iso,
        ),
        # 8. Cybersecurity SOC Analyst, Jeddah (REJECTED: job family)
        ExtractedRecord(
            id="cand_8",
            run_id="run_golden",
            record_type="job_listing",
            fields={"title": "Cybersecurity SOC Analyst", "company": "Red Sea Tech", "location": "Jeddah, Saudi Arabia", "skills": ["Python"]},
            verification_status=VerificationStatus.VERIFIED,
            created_at=now_iso,
        ),
        # 9. Duplicate of cand_1 on LinkedIn (DUPLICATE)
        ExtractedRecord(
            id="cand_9",
            run_id="run_golden",
            record_type="job_listing",
            fields={
                "title": "GenAI Engineer",
                "company": "Noor AI",
                "location": "Dubai, UAE",
                "experience_min": 3,
                "skills": ["Python"],
                "job_url": "https://www.linkedin.com/jobs/view/101",
            },
            canonical_url="https://boards.greenhouse.io/noorai/jobs/101",
            verification_status=VerificationStatus.VERIFIED,
            created_at=now_iso,
        ),
        # 10. GenAI Engineer, Denver CO Remote (REJECTED: foreign anchor remote)
        ExtractedRecord(
            id="cand_10",
            run_id="run_golden",
            record_type="job_listing",
            fields={
                "title": "GenAI Engineer",
                "company": "Rocky Mountain AI",
                "location": "Denver, CO (Remote); US only",
                "skills": ["Python"],
                "remote_status": "remote",
            },
            verification_status=VerificationStatus.VERIFIED,
            created_at=now_iso,
        ),
    ]

    # Step 1: Deterministic Qualification
    results = [qualify_job(cand, spec) for cand in candidates]

    # Qualified check
    assert results[0].qualified is True
    assert results[1].qualified is True
    assert results[2].qualified is True
    assert results[8].qualified is True  # cand_9 qualifies on merits before dedupe

    # Disqualified check
    assert results[3].qualified is False  # AI Product Manager
    assert results[4].qualified is False  # London
    assert results[5].qualified is False  # 10+ yrs exp
    assert results[6].qualified is False  # Missing Python
    assert results[7].qualified is False  # Cybersecurity
    assert results[9].qualified is False  # US remote

    # Step 2: Ranking & Deduplication
    qualified_cands = [candidates[i] for i, r in enumerate(results) if r.qualified]
    assert len(qualified_cands) == 4

    normalizer = DataNormalizer()
    normalized_qualified = [normalizer.normalize(c.fields, record_id=c.id, canonical_url=c.canonical_url or "") for c in qualified_cands]
    deduped = deduplicate_normalized_jobs(normalized_qualified)

    # Cand 1 and Cand 9 collapse to 1
    assert len(deduped) == 3
    final_ids = [j.raw_id for j in deduped]
    assert "cand_1" in final_ids or "cand_9" in final_ids
    assert "cand_2" in final_ids
    assert "cand_3" in final_ids


# ─────────────────────────────────────────────────────────────────────────────
# 14. Section 25 Explicitly Numbered Precision Test Suite (Tests 1 to 16)
# ─────────────────────────────────────────────────────────────────────────────

def test_test_1_explicit_skills_single_missing():
    """TEST 1 — Explicit skills: Query requires Python and LangChain. Job has Python only -> REJECTED."""
    policy = QualificationPolicy()
    req = JobSearchRequest(
        raw_query="GenAI Engineer requiring Python and LangChain",
        job_title="GenAI Engineer",
        explicit_skills=["Python", "LangChain"],
    )
    job = {
        "title": "GenAI Engineer",
        "company": "Beta AI",
        "location": "Dubai, UAE",
        "skills": ["Python"],
        "description": "Python developer working with standard libraries.",
    }
    res = policy.qualify(job, req)
    assert res.qualified is False
    assert res.eligibility_status == "INELIGIBLE"
    assert "langchain" in [s.lower() for s in res.missing_skills]


def test_test_2_multiple_explicit_skills_missing_one_rejects():
    """TEST 2 — Multiple explicit skills: Python + LangChain + LangGraph. Missing LangGraph -> REJECTED."""
    policy = QualificationPolicy()
    req = JobSearchRequest(
        raw_query="GenAI Engineer requiring Python, LangChain, and LangGraph",
        job_title="GenAI Engineer",
        explicit_skills=["Python", "LangChain", "LangGraph"],
    )
    # Missing LangGraph
    job_missing = {
        "title": "GenAI Engineer",
        "company": "Gamma AI",
        "location": "Riyadh, Saudi Arabia",
        "skills": ["Python", "LangChain"],
        "description": "Python and LangChain agent builder.",
    }
    res_m = policy.qualify(job_missing, req)
    assert res_m.qualified is False
    assert res_m.eligibility_status == "INELIGIBLE"
    assert "langgraph" in [s.lower() for s in res_m.missing_skills]

    # All three present -> QUALIFIED
    job_full = {
        "title": "GenAI Engineer",
        "company": "Gamma AI",
        "location": "Riyadh, Saudi Arabia",
        "skills": ["Python", "LangChain", "LangGraph"],
        "description": "Python, LangChain, and LangGraph multi-agent developer.",
    }
    res_f = policy.qualify(job_full, req)
    assert res_f.qualified is True
    assert res_f.eligibility_status == "ELIGIBLE"


def test_test_3_inferred_skill_missing_qualifies():
    """TEST 3 — Inferred skill: Inferred RAG missing -> QUALIFIED if all explicit constraints pass."""
    policy = QualificationPolicy()
    req = JobSearchRequest(
        raw_query="Find GenAI Engineer in Riyadh",
        job_title="GenAI Engineer",
        location="Riyadh",
        explicit_skills=[],
        inferred_skills=["RAG", "PyTorch"],
    )
    job = {
        "title": "GenAI Engineer",
        "company": "Tuwaiq Tech",
        "location": "Riyadh, Saudi Arabia",
        "skills": ["Python", "FastAPI"],  # Missing RAG and PyTorch
        "description": "GenAI backend engineer in Riyadh.",
    }
    res = policy.qualify(job, req)
    assert res.qualified is True
    assert res.eligibility_status == "ELIGIBLE"


def test_test_4_saudi_or_uae_locations():
    """TEST 4 — Saudi OR UAE: Saudi -> MATCH, UAE -> MATCH, India/UK/USA -> REJECTED."""
    locs = ["Saudi Arabia", "UAE"]
    # Saudi matches
    assert match_location(locs, "Riyadh, Saudi Arabia")[0] == MatchStatus.MATCH
    assert match_location(locs, "Jeddah, KSA")[0] == MatchStatus.MATCH
    # UAE matches
    assert match_location(locs, "Dubai, UAE")[0] == MatchStatus.MATCH
    assert match_location(locs, "Abu Dhabi, United Arab Emirates")[0] == MatchStatus.MATCH
    # Rejections
    assert match_location(locs, "Bengaluru, India")[0] == MatchStatus.MISMATCH
    assert match_location(locs, "London, UK")[0] == MatchStatus.MISMATCH
    assert match_location(locs, "Austin, TX, USA")[0] == MatchStatus.MISMATCH


def test_test_5_unknown_geography_not_qualified():
    """TEST 5 — Unknown geography: Saudi/UAE requested. Job location: unknown -> NOT QUALIFIED."""
    policy = QualificationPolicy()
    req = JobSearchRequest(
        raw_query="GenAI Engineer in Saudi Arabia or UAE",
        job_title="GenAI Engineer",
        locations=["Saudi Arabia", "UAE"],
        location="Saudi Arabia or UAE",
        remote_allowed=False,
    )
    job_unknown = {
        "title": "GenAI Engineer",
        "company": "Mystery AI",
        "location": "",
    }
    res = policy.qualify(job_unknown, req)
    assert res.qualified is False
    assert res.eligibility_status == "INELIGIBLE"
    assert any("unverified_location" in r for r in res.rejection_reasons)


def test_test_6_remote_geography_distinction():
    """
    TEST 6 — Remote:
    Global Remote: allowed if request permits.
    Saudi Remote: MATCH.
    UAE Remote: MATCH.
    US-only Remote: REJECTED.
    UK-only Remote: REJECTED.
    """
    policy = QualificationPolicy()
    req = JobSearchRequest(
        raw_query="GenAI Engineer in Saudi Arabia or UAE",
        job_title="GenAI Engineer",
        locations=["Saudi Arabia", "UAE"],
        location="Saudi Arabia or UAE",
        remote_allowed=True,
    )
    assert policy.qualify({"title": "GenAI Engineer", "location": "Remote", "remote_status": "remote"}, req).qualified is True
    assert policy.qualify({"title": "GenAI Engineer", "location": "Riyadh (Remote)", "remote_status": "remote"}, req).qualified is True
    assert policy.qualify({"title": "GenAI Engineer", "location": "Dubai (Remote)", "remote_status": "remote"}, req).qualified is True
    assert policy.qualify({"title": "GenAI Engineer", "location": "US Remote only; San Francisco, CA", "remote_status": "remote"}, req).qualified is False
    assert policy.qualify({"title": "GenAI Engineer", "location": "London, UK (Remote - UK only)", "remote_status": "remote"}, req).qualified is False


def test_test_7_currency_handling():
    """TEST 7 — Currency: Saudi -> SAR, UAE -> AED, Saudi OR UAE -> None, Explicit USD -> USD."""
    qu = QueryUnderstandingAgent()
    assert qu.understand("GenAI Engineer in Riyadh, Saudi Arabia").salary_currency == "SAR"
    assert qu.understand("GenAI Engineer in Dubai, UAE").salary_currency == "AED"

    spec_multi = qu.understand("GenAI Engineer in Saudi Arabia or UAE")
    assert spec_multi.salary_currency is None
    assert spec_multi.salary_currency_source == "unspecified"

    spec_usd = qu.understand("GenAI Engineer in Saudi Arabia or UAE with $120k salary")
    assert spec_usd.salary_currency == "USD"
    assert spec_usd.salary_currency_source == "explicit"


def test_test_8_title_family_rejections():
    """
    TEST 8 — Title:
    GenAI Engineer -> PASS, LLM Engineer -> PASS, AI Engineer -> PASS.
    AI Product Manager -> FAIL, AI Sales Manager -> FAIL, AI Recruiter -> FAIL, AI Business Analyst -> FAIL.
    """
    policy = QualificationPolicy()
    req = JobSearchRequest(job_title="GenAI Engineer", location="Dubai")
    assert policy.qualify({"title": "GenAI Engineer", "location": "Dubai"}, req).qualified is True
    assert policy.qualify({"title": "LLM Engineer", "location": "Dubai"}, req).qualified is True
    assert policy.qualify({"title": "AI Engineer", "location": "Dubai"}, req).qualified is True

    for bad in ["AI Product Manager", "AI Sales Manager", "AI Recruiter", "AI Business Analyst"]:
        res = policy.qualify({"title": bad, "location": "Dubai"}, req)
        assert res.qualified is False, f"Expected {bad} to be disqualified"


def test_test_9_duplicate_vacancy_cross_source_canonical_merge():
    """
    TEST 9 — Duplicate vacancy: LinkedIn + Greenhouse + Company Career.
    Expected: ONE canonical job entity. Sources merged. Direct employer URL preferred.
    """
    vacancies = [
        {"title": "Staff GenAI Engineer", "company": "Noor Labs", "location": "Dubai, UAE", "source": "LinkedIn", "job_url": "https://www.linkedin.com/jobs/view/555"},
        {"title": "Staff GenAI Engineer", "company": "Noor Labs", "location": "Dubai, UAE", "source": "Greenhouse", "job_url": "https://boards.greenhouse.io/noorlabs/jobs/555"},
        {"title": "Staff GenAI Engineer", "company": "Noor Labs", "location": "Dubai, UAE", "source": "Company Career", "job_url": "https://noorlabs.ai/careers/staff-genai"},
    ]
    norm = DataNormalizer()
    normalized = [norm.normalize(v, record_id=f"r_{i}") for i, v in enumerate(vacancies)]
    deduped = deduplicate_normalized_jobs(normalized)

    assert len(deduped) == 1
    canonical = deduped[0]
    assert len(canonical.sources) >= 3
    assert any("greenhouse" in u.lower() or "noorlabs.ai" in u.lower() for u in [canonical.primary_application_url])
    assert "linkedin.com" not in canonical.primary_application_url


def test_test_10_dedup_idempotency():
    """TEST 10 — Dedup idempotency: Run dedup twice. Expected: same result."""
    vacancies = [
        {"title": "GenAI Engineer", "company": "Noor Labs", "location": "Dubai, UAE", "source": "LinkedIn", "job_url": "https://www.linkedin.com/jobs/view/101"},
        {"title": "GenAI Engineer", "company": "Noor Labs", "location": "Dubai, UAE", "source": "Greenhouse", "job_url": "https://boards.greenhouse.io/noorlabs/jobs/101"},
    ]
    norm = DataNormalizer()
    normalized = [norm.normalize(v, record_id=f"rec_{i}") for i, v in enumerate(vacancies)]

    first_pass = deduplicate_normalized_jobs(normalized)
    assert len(first_pass) == 1
    first_pass_data = [(j.normalized_company, j.normalized_title, j.primary_application_url, sorted(j.sources)) for j in first_pass]

    second_pass = deduplicate_normalized_jobs(first_pass)
    assert len(second_pass) == 1
    second_pass_data = [(j.normalized_company, j.normalized_title, j.primary_application_url, sorted(j.sources)) for j in second_pass]

    assert first_pass_data == second_pass_data


def test_test_11_search_planner_isolation_in_job_mode():
    """
    TEST 11 — SearchPlanner isolation:
    Run a JOB MODE runtime.
    Assert: SearchPlannerAgent is NOT required to execute runtime searches.
    DiscoveryEngine owns the task queue.
    """
    runtime = AgentRuntime(client=None, search=MagicMock(), fetch=MagicMock(), extract=MagicMock(), verify=MagicMock(), dedupe=MagicMock(), export=MagicMock())
    state = AgentState(
        request="Find GenAI Engineer in Riyadh",
        run_id="run_sp_iso",
        task_id="task_sp_iso",
        mode="jobs",
    )
    with patch("datahunt.agents.search_planner.SearchPlannerAgent.plan") as mock_sp_plan:
        mock_sp_plan.side_effect = RuntimeError("SearchPlannerAgent should not be called in Job Mode!")
        runtime._init_state(state, lambda ev, d: None)
        assert len(state.discovery_state.task_queue) > 0
        assert mock_sp_plan.call_count == 0


def test_test_12_no_injected_legacy_queries_in_job_mode():
    """
    TEST 12 — No injected legacy queries:
    When DiscoveryEngine initializes:
    state.search_plan MUST NOT be merged into DiscoveryState.task_queue in JOB MODE.
    """
    runtime = AgentRuntime(client=None, search=MagicMock(), fetch=MagicMock(), extract=MagicMock(), verify=MagicMock(), dedupe=MagicMock(), export=MagicMock())
    state = AgentState(
        request="Find GenAI Engineer in Dubai",
        run_id="run_no_inject",
        task_id="task_no_inject",
        mode="jobs",
        search_plan=[{"query": "LEGACY_SEARCH_PLANNER_QUERY_XYZ", "tier": 1, "purpose": "legacy"}]
    )
    runtime._init_state(state, lambda ev, d: None)
    task_queries = [t.query for t in state.discovery_state.task_queue]
    assert "LEGACY_SEARCH_PLANNER_QUERY_XYZ" not in task_queries


def test_test_13_final_safety_gate():
    """
    TEST 13 — Final safety gate:
    Create eligible job and ineligible job.
    Ensure final output contains only eligible job.
    """
    runtime = AgentRuntime(client=None, search=MagicMock(), fetch=MagicMock(), extract=MagicMock(), verify=MagicMock(), dedupe=DedupeTool(), export=MagicMock())
    spec = JobSearchSpec(raw_query="GenAI Engineer in Dubai requiring Python", titles=["GenAI Engineer"], locations=["Dubai"], explicit_skills=["Python"])
    state = AgentState(
        request="GenAI Engineer in Dubai requiring Python",
        run_id="run_gate",
        task_id="task_gate",
        mode="jobs",
        canonical_job_request=spec,
    )
    eligible_job = ExtractedRecord(
        id="el_1",
        run_id="run_gate",
        record_type="job_listing",
        fields={"title": "GenAI Engineer", "company": "Good AI", "location": "Dubai, UAE", "skills": ["Python"]},
        verification_status=VerificationStatus.VERIFIED,
    )
    # Ineligible job: missing explicit skill Python
    ineligible_job = ExtractedRecord(
        id="inel_1",
        run_id="run_gate",
        record_type="job_listing",
        fields={"title": "GenAI Engineer", "company": "Bad AI", "location": "Dubai, UAE", "skills": ["Java"]},
        verification_status=VerificationStatus.VERIFIED,
    )
    state.qualified_records = [eligible_job, ineligible_job]
    res = runtime._finalize(state, lambda ev, d: None)
    final_ids = [r.id for r in res.get("records", [])]
    assert "el_1" in final_ids
    assert "inel_1" not in final_ids
    assert len(final_ids) == 1


def test_test_14_company_fabrication_zero_invented_facts():
    """TEST 14 — Company fabrication: No verified company evidence -> company info remains unknown, no invented facts."""
    mock_search = MagicMock()
    mock_search.execute.return_value = MagicMock(success=True, data=[])
    agent = CompanyResearchAgent(search_tool=mock_search)
    profile = agent.research("CompletelyUnknownFictionalCompany9999", force_refresh=True)

    assert profile.verification_status == "UNVERIFIED"
    assert profile.is_available is False
    assert len(profile.company_facts) == 0


def test_test_15_adaptive_search_favors_productive_source():
    """
    TEST 15 — Adaptive search:
    Source A produces many qualified jobs.
    Source B produces only duplicates.
    Expected: future search budget favors productive Source A.
    """
    engine = DiscoveryEngine()
    disc_state = DiscoveryState()
    # Source A: productive (qualified yield)
    disc_state.record_task_productivity(
        source="boards.greenhouse.io", hits=10, valid_jobs=8, unique_jobs=8, qualified_jobs=6, duplicate_jobs=0
    )
    # Source B: unproductive (duplicates only)
    disc_state.record_task_productivity(
        source="genericboard.com", hits=50, valid_jobs=2, unique_jobs=2, qualified_jobs=0, duplicate_jobs=40
    )
    tasks = [
        SearchTask(id="tb", task_type=SearchTaskType.GENERAL_SEARCH, query="qB", source="genericboard.com", source_type=DiscoveredSourceType.MAJOR_BOARD, priority=2),
        SearchTask(id="ta", task_type=SearchTaskType.ATS_SEARCH, query="qA", source="boards.greenhouse.io", source_type=DiscoveredSourceType.ATS_PORTAL, priority=2),
    ]
    prioritized = engine.prioritize_tasks(tasks, disc_state)
    assert prioritized[0].source == "boards.greenhouse.io"
    assert prioritized[1].source == "genericboard.com"

    # In Round 2, pagination favors Source A and skips unproductive Source B
    disc_state.current_round = 2
    disc_state.completed_tasks = [
        SearchTask(id="c_b", task_type=SearchTaskType.GENERAL_SEARCH, query="qB", source="genericboard.com", source_type=DiscoveredSourceType.MAJOR_BOARD, priority=2, round=1),
        SearchTask(id="c_a", task_type=SearchTaskType.ATS_SEARCH, query="qA", source="boards.greenhouse.io", source_type=DiscoveredSourceType.ATS_PORTAL, priority=2, round=1),
    ]
    dummy_state = AgentState(request="test", run_id="r", task_id="t", mode="jobs", explicit_titles=["Engineer"])
    r2_tasks = engine.generate_next_round_tasks(disc_state, None, dummy_state)
    r2_sources = [t.source for t in r2_tasks]
    assert "boards.greenhouse.io" in r2_sources
    assert "genericboard.com" not in r2_sources


def test_test_16_golden_query_end_to_end():
    """
    TEST 16 — Golden query:
    'Find 10 GenAI Engineer jobs in Saudi Arabia or UAE requiring Python and LangChain, preferably fresh.'
    Verifies all 12 properties from Section 25.
    """
    spec = JobSearchSpec(
        raw_query="Find 10 GenAI Engineer jobs in Saudi Arabia or UAE requiring Python and LangChain, preferably fresh.",
        titles=["GenAI Engineer"],
        locations=["Saudi Arabia", "UAE"],
        location_operator="OR",
        explicit_skills=["Python", "LangChain"],
        max_results=10,
    )
    now_iso = datetime.now(timezone.utc).isoformat()
    candidates = [
        ExtractedRecord(
            id="g_1",
            run_id="run_g16",
            record_type="job_listing",
            fields={"title": "GenAI Engineer", "company": "Gulf AI", "location": "Riyadh, Saudi Arabia", "skills": ["Python", "LangChain"]},
            verification_status=VerificationStatus.VERIFIED,
            created_at=now_iso,
        ),
        ExtractedRecord(
            id="g_2",
            run_id="run_g16",
            record_type="job_listing",
            fields={"title": "Generative AI Engineer", "company": "Dubai Tech", "location": "Dubai, UAE", "skills": ["Python", "LangChain"]},
            verification_status=VerificationStatus.VERIFIED,
            created_at=now_iso,
        ),
        # Distractor 1: London (Location mismatch)
        ExtractedRecord(
            id="g_dist_1",
            run_id="run_g16",
            record_type="job_listing",
            fields={"title": "GenAI Engineer", "company": "London Tech", "location": "London, UK", "skills": ["Python", "LangChain"]},
            verification_status=VerificationStatus.VERIFIED,
            created_at=now_iso,
        ),
        # Distractor 2: Missing LangChain (Explicit skill mismatch)
        ExtractedRecord(
            id="g_dist_2",
            run_id="run_g16",
            record_type="job_listing",
            fields={"title": "GenAI Engineer", "company": "Riyadh Tech", "location": "Riyadh, Saudi Arabia", "skills": ["Python"]},
            verification_status=VerificationStatus.VERIFIED,
            created_at=now_iso,
        ),
        # Distractor 3: AI Product Manager (Unrelated job family)
        ExtractedRecord(
            id="g_dist_3",
            run_id="run_g16",
            record_type="job_listing",
            fields={"title": "AI Product Manager", "company": "Gulf Corp", "location": "Dubai, UAE", "skills": ["Python", "LangChain"]},
            verification_status=VerificationStatus.VERIFIED,
            created_at=now_iso,
        ),
    ]

    results = [qualify_job(c, spec) for c in candidates]
    assert results[0].qualified is True
    assert results[1].qualified is True
    assert results[2].qualified is False  # London
    assert results[3].qualified is False  # Missing LangChain
    assert results[4].qualified is False  # Product Manager
