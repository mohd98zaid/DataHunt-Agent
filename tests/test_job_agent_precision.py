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
    match_skills,
    TitleCategory,
    MatchStatus,
)
from datahunt.tools.dedupe import deduplicate_normalized_jobs
from datahunt.agent.state import AgentState, AgentStatus
from datahunt.agent.decision import DecisionEngine, AgentAction
from datahunt.agent.runtime import AgentRuntime


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
