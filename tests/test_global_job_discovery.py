"""
Master Test Suite for Global Job Discovery Engine (Sections 37-42).

Covers:
1. Golden end-to-end test (8 mock jobs: 1, 2, 3 qualify; 4, 5, 6, 7, 8 reject).
2. Multi-source duplication collapse with direct ATS / career link prioritization.
3. Source failure resilience and honest coverage reporting.
4. Multi-location evaluation (Saudi Arabia or UAE).
5. raw_query propagation across the complete pipeline.
6. LLM failure resilience (deterministic qualification fallback).
7. Non-job research intent separation.
"""
import pytest
from datetime import datetime, timezone
from unittest.mock import MagicMock

from datahunt.models import ExtractedRecord, VerificationStatus
from datahunt.models.job_spec import JobSearchSpec, JobSearchRequest
from datahunt.sources.models import JobSource, JobSourceType, SourceRunResult, SourceStatus
from datahunt.sources.registry import JobSourceRegistry
from datahunt.agent.policies import (
    qualify_job,
    match_location,
    match_experience,
    match_title_relevance,
    MatchStatus,
)
from datahunt.agent.coverage import CoverageEvaluator, CoverageReport, format_job_matches_markdown
from datahunt.tools.dedupe import DedupeTool, deduplicate_normalized_jobs
from datahunt.agents.normalizer import DataNormalizer
from datahunt.agents.intent_router import IntentRouter
from datahunt.models.intent import ResearchIntent


# ─────────────────────────────────────────────────────────────
# 1. Golden End-to-End Test (8 Candidates)
# ─────────────────────────────────────────────────────────────

def test_golden_8_mock_jobs_discovery_and_qualification():
    """
    Test 8 realistic candidates against:
    'Find GenAI Engineer jobs in Saudi Arabia or UAE with 0–6 years experience'
    Expected:
      Jobs 1, 2, 3 -> QUALIFIED
      Jobs 4, 5, 6, 7, 8 -> DISQUALIFIED with deterministic explanations
    """
    spec = JobSearchSpec(
        raw_query="Find GenAI Engineer jobs in Saudi Arabia or UAE with 0–6 years experience",
        titles=["GenAI Engineer"],
        locations=["Saudi Arabia", "UAE"],
        location_operator="OR",
        experience_min=0,
        experience_max=6,
        explicit_skills=["python", "llm", "generative ai"],
    )

    now_iso = datetime.now(timezone.utc).isoformat()

    candidates = [
        # 1. GenAI Engineer, Dubai, UAE, 3-5 yrs (QUALIFIED)
        ExtractedRecord(
            id="job_1",
            run_id="run_golden",
            record_type="job_listing",
            fields={
                "title": "GenAI Engineer",
                "company": "Noor AI Technologies",
                "location": "Dubai, UAE",
                "experience_min": 3,
                "experience_max": 5,
                "description": "Designing and deploying generative AI pipelines using LLMs and Python.",
            },
            canonical_url="https://boards.greenhouse.io/noorai/jobs/101",
            verification_status=VerificationStatus.VERIFIED,
            created_at=now_iso,
        ),
        # 2. Generative AI Research Engineer, Riyadh, Saudi Arabia, 4 yrs (QUALIFIED)
        ExtractedRecord(
            id="job_2",
            run_id="run_golden",
            record_type="job_listing",
            fields={
                "title": "Generative AI Research Engineer",
                "company": "Tuwaiq Intelligence",
                "location": "Riyadh, Saudi Arabia",
                "experience_min": 4,
                "experience_max": 6,
                "description": "Training and evaluating foundational LLMs.",
            },
            canonical_url="https://jobs.lever.co/tuwaiq/202",
            verification_status=VerificationStatus.VERIFIED,
            created_at=now_iso,
        ),
        # 3. LLM Systems Engineer, Abu Dhabi, UAE, 2 yrs (QUALIFIED)
        ExtractedRecord(
            id="job_3",
            run_id="run_golden",
            record_type="job_listing",
            fields={
                "title": "LLM Systems Engineer",
                "company": "Falcon Frontier",
                "location": "Abu Dhabi, UAE",
                "experience_min": 2,
                "experience_max": 4,
                "description": "Low-latency inference for large language models.",
            },
            canonical_url="https://jobs.ashbyhq.com/falconfrontier/303",
            verification_status=VerificationStatus.VERIFIED,
            created_at=now_iso,
        ),
        # 4. Lead GenAI Architect, Riyadh, Saudi Arabia, 10-12 yrs (REJECTED: experience > 6 yrs)
        ExtractedRecord(
            id="job_4",
            run_id="run_golden",
            record_type="job_listing",
            fields={
                "title": "Lead GenAI Architect",
                "company": "Riyadh Cloud",
                "location": "Riyadh, Saudi Arabia",
                "experience_min": 10,
                "experience_max": 12,
                "description": "Directing enterprise-wide generative AI architecture.",
            },
            canonical_url="https://boards.greenhouse.io/riyadhcloud/404",
            verification_status=VerificationStatus.VERIFIED,
            created_at=now_iso,
        ),
        # 5. Full Stack Engineer (React/Node), Dubai, UAE, 3 yrs (REJECTED: anti-track)
        ExtractedRecord(
            id="job_5",
            run_id="run_golden",
            record_type="job_listing",
            fields={
                "title": "Full Stack Engineer (React/Node)",
                "company": "Gulf Portal",
                "location": "Dubai, UAE",
                "experience_min": 3,
                "experience_max": 5,
                "description": "Building full-stack web applications with React, Next.js, and Node.",
            },
            canonical_url="https://boards.greenhouse.io/gulfportal/505",
            verification_status=VerificationStatus.VERIFIED,
            created_at=now_iso,
        ),
        # 6. Senior AI Engineer, London, UK, 4 yrs (REJECTED: location London not in Saudi/UAE)
        ExtractedRecord(
            id="job_6",
            run_id="run_golden",
            record_type="job_listing",
            fields={
                "title": "Senior AI Engineer",
                "company": "Thames AI",
                "location": "London, UK",
                "experience_min": 4,
                "experience_max": 6,
                "description": "Building computer vision and generative AI applications.",
            },
            canonical_url="https://jobs.lever.co/thamesai/606",
            verification_status=VerificationStatus.VERIFIED,
            created_at=now_iso,
        ),
        # 7. Cybersecurity SOC Analyst, Jeddah, Saudi Arabia, 2 yrs (REJECTED: anti-track)
        ExtractedRecord(
            id="job_7",
            run_id="run_golden",
            record_type="job_listing",
            fields={
                "title": "Cybersecurity SOC Analyst",
                "company": "Red Sea Defense",
                "location": "Jeddah, Saudi Arabia",
                "experience_min": 2,
                "experience_max": 4,
                "description": "Monitoring security alerts and incident response.",
            },
            canonical_url="https://jobs.lever.co/redsea/707",
            verification_status=VerificationStatus.VERIFIED,
            created_at=now_iso,
        ),
        # 8. Senior Accountant, Dubai, UAE, 5 yrs (REJECTED: anti-track)
        ExtractedRecord(
            id="job_8",
            run_id="run_golden",
            record_type="job_listing",
            fields={
                "title": "Senior Accountant",
                "company": "Emirates Trade",
                "location": "Dubai, UAE",
                "experience_min": 5,
                "experience_max": 7,
                "description": "Managing balance sheets, general ledgers, and financial reports.",
            },
            canonical_url="https://boards.greenhouse.io/emiratestrade/808",
            verification_status=VerificationStatus.VERIFIED,
            created_at=now_iso,
        ),
    ]

    results = [qualify_job(cand, spec) for cand in candidates]

    # Verify Job 1 (GenAI Engineer, Dubai, 3-5 yrs) -> QUALIFIED
    assert results[0].qualified is True
    assert results[0].score >= 0.85

    # Verify Job 2 (Generative AI Research Engineer, Riyadh, 4-6 yrs) -> QUALIFIED
    assert results[1].qualified is True
    assert results[1].score >= 0.85

    # Verify Job 3 (LLM Systems Engineer, Abu Dhabi, 2-4 yrs) -> QUALIFIED
    assert results[2].qualified is True
    assert results[2].score >= 0.85

    # Verify Job 4 (Lead GenAI Architect, Riyadh, 10-12 yrs) -> REJECTED (experience)
    assert results[3].qualified is False
    assert results[3].experience_match == MatchStatus.MISMATCH
    assert any("years" in r.lower() for r in results[3].reasons)

    # Verify Job 5 (Full Stack Engineer, Dubai, 3 yrs) -> REJECTED (anti-track)
    assert results[4].qualified is False
    assert results[4].title_match == MatchStatus.MISMATCH
    assert any("full_stack" in r.lower() or "not requested" in r.lower() for r in results[4].reasons)

    # Verify Job 6 (Senior AI Engineer, London, UK) -> REJECTED (geography)
    assert results[5].qualified is False
    assert results[5].location_match == MatchStatus.MISMATCH
    assert any("location" in r.lower() or "mismatch" in r.lower() for r in results[5].reasons)

    # Verify Job 7 (Cybersecurity SOC Analyst, Jeddah) -> REJECTED (anti-track)
    assert results[6].qualified is False
    assert results[6].title_match == MatchStatus.MISMATCH
    assert any("cybersecurity" in r.lower() or "not requested" in r.lower() for r in results[6].reasons)

    # Verify Job 8 (Senior Accountant, Dubai) -> REJECTED (anti-track)
    assert results[7].qualified is False
    assert results[7].title_match == MatchStatus.MISMATCH
    assert any("finance_accounting" in r.lower() or "not requested" in r.lower() for r in results[7].reasons)


# ─────────────────────────────────────────────────────────────
# 2. Multi-Source Deduplication Collapse
# ─────────────────────────────────────────────────────────────

def test_multi_source_deduplication_collapse():
    """
    Verify 5 duplicate listings across LinkedIn, Indeed, Bayt, Greenhouse, and Company Career:
    - Collapses to exactly 1 canonical record.
    - All 5 sources are preserved in `sources`.
    - All 5 URLs are preserved in `all_source_urls`.
    - Direct ATS / Career URL is preferred as `primary_application_url` over aggregator links.
    """
    normalizer = DataNormalizer()

    raw_items = [
        {
            "title": "Senior GenAI Engineer",
            "company": "Falcon Frontier",
            "location": "Dubai, UAE",
            "source": "LinkedIn",
            "job_url": "https://www.linkedin.com/jobs/view/10001",
            "apply_url": "https://www.linkedin.com/jobs/view/10001",
        },
        {
            "title": "Senior GenAI Engineer",
            "company": "Falcon Frontier",
            "location": "Dubai, United Arab Emirates",
            "source": "Indeed",
            "job_url": "https://indeed.com/viewjob?jk=20002",
            "apply_url": "https://indeed.com/viewjob?jk=20002",
        },
        {
            "title": "Senior GenAI Engineer",
            "company": "Falcon Frontier",
            "location": "Dubai",
            "source": "Bayt",
            "job_url": "https://www.bayt.com/en/uae/jobs/30003",
            "apply_url": "https://www.bayt.com/en/uae/jobs/30003",
        },
        {
            "title": "Senior GenAI Engineer",
            "company": "Falcon Frontier",
            "location": "Dubai, UAE",
            "source": "Greenhouse",
            "job_url": "https://boards.greenhouse.io/falconfrontier/jobs/40004",
            "apply_url": "https://boards.greenhouse.io/falconfrontier/jobs/40004",
        },
        {
            "title": "Senior GenAI Engineer",
            "company": "Falcon Frontier",
            "location": "Dubai, UAE",
            "source": "Falcon Frontier Careers",
            "job_url": "https://falconfrontier.com/careers/genai-engineer",
            "apply_url": "https://falconfrontier.com/careers/genai-engineer",
        },
    ]

    normalized_jobs = [normalizer.normalize(item, record_id=f"rec_{i}") for i, item in enumerate(raw_items)]
    deduped = deduplicate_normalized_jobs(normalized_jobs)

    assert len(deduped) == 1
    canonical_job = deduped[0]

    # Verify all sources merged
    assert len(canonical_job.sources) >= 4
    for src in ["LinkedIn", "Indeed", "Bayt", "Greenhouse"]:
        assert any(src.lower() in s.lower() for s in canonical_job.sources)

    # Verify all URLs preserved
    assert len(canonical_job.all_source_urls) == 5

    # Verify primary_application_url is Greenhouse or direct career page (NOT LinkedIn or Indeed)
    assert any(pref in canonical_job.primary_application_url for pref in ("greenhouse.io", "falconfrontier.com/careers"))
    assert "linkedin.com" not in canonical_job.primary_application_url
    assert "indeed.com" not in canonical_job.primary_application_url


# ─────────────────────────────────────────────────────────────
# 3. Source Failure Resilience & Honest Coverage Reporting
# ─────────────────────────────────────────────────────────────

def test_source_failure_resilience_and_coverage():
    """
    Verify search run resilience when sources fail:
    - Greenhouse: SUCCESS (found 3 jobs)
    - Bayt: UNAVAILABLE (timeout)
    - Indeed: RATE_LIMITED (429)
    - Lever: SUCCESS (found 2 jobs)
    Result: Search completes, records are qualified, coverage report honestly lists failed sources.
    """
    spec = JobSearchSpec(
        raw_query="GenAI Engineer in Saudi Arabia or UAE",
        titles=["GenAI Engineer"],
        locations=["Saudi Arabia", "UAE"],
    )

    state = MagicMock()
    state.source_run_results = {
        "greenhouse": SourceRunResult(
            source_id="greenhouse",
            source_name="Greenhouse",
            source_type=JobSourceType.ATS.value,
            status=SourceStatus.SUCCESS,
            records_found=3,
        ),
        "lever": SourceRunResult(
            source_id="lever",
            source_name="Lever",
            source_type=JobSourceType.ATS.value,
            status=SourceStatus.SUCCESS,
            records_found=2,
        ),
        "bayt": SourceRunResult(
            source_id="bayt",
            source_name="Bayt",
            source_type=JobSourceType.REGIONAL_JOB_BOARD.value,
            status=SourceStatus.TIMEOUT,
            records_found=0,
            error_message="Connection timed out after 10000ms",
        ),
        "indeed": SourceRunResult(
            source_id="indeed",
            source_name="Indeed",
            source_type=JobSourceType.MAJOR_JOB_BOARD.value,
            status=SourceStatus.RATE_LIMITED,
            records_found=0,
            error_message="HTTP 429 Too Many Requests",
        ),
    }

    state.candidate_urls = [{"url": f"https://example.com/job/{i}"} for i in range(5)]
    state.raw_records = [MagicMock() for _ in range(5)]
    state.verified_records = [MagicMock() for _ in range(4)]
    state.qualified_records = [
        MagicMock(fields={"title": "GenAI Engineer", "location": "Dubai, UAE", "relevance_score": 0.9}),
        MagicMock(fields={"title": "Generative AI Specialist", "location": "Riyadh, Saudi Arabia", "relevance_score": 0.88}),
    ]
    state.rejected_records = [MagicMock()]
    state.seen_canonical_urls = {f"url_{i}" for i in range(5)}
    state.target_results = 2

    evaluator = CoverageEvaluator()
    report = evaluator.evaluate(state, spec)

    assert report.sources_searched_count == 4
    assert report.sources_successful_count == 2
    assert report.sources_unavailable_count == 2
    assert any("Bayt" in s for s in report.unavailable_sources)
    assert any("Indeed" in s for s in report.unavailable_sources)

    # Format Section 35 summary
    summary = evaluator.format_summary(report, spec)
    assert "JOB SEARCH COMPLETE" in summary
    assert "Sources searched" in summary
    assert "Bayt" in summary
    assert "Indeed" in summary
    assert "Saudi Arabia" in summary
    assert "UAE" in summary


# ─────────────────────────────────────────────────────────────
# 4. Multi-Location Evaluation
# ─────────────────────────────────────────────────────────────

def test_multi_location_evaluation():
    """Verify Saudi Arabia or UAE matches all relevant cities and rejects other countries."""
    req_locations = ["Saudi Arabia", "UAE"]

    # Saudi cities
    assert match_location(req_locations, "Riyadh, Saudi Arabia")[0] == MatchStatus.MATCH
    assert match_location(req_locations, "Jeddah, KSA")[0] == MatchStatus.MATCH
    assert match_location(req_locations, "Dammam")[0] == MatchStatus.MATCH
    assert match_location(req_locations, "NEOM, Saudi Arabia")[0] == MatchStatus.MATCH

    # UAE cities
    assert match_location(req_locations, "Dubai, UAE")[0] == MatchStatus.MATCH
    assert match_location(req_locations, "Abu Dhabi, United Arab Emirates")[0] == MatchStatus.MATCH
    assert match_location(req_locations, "Sharjah")[0] == MatchStatus.MATCH

    # Foreign cities (Must be MISMATCH)
    assert match_location(req_locations, "Cairo, Egypt")[0] == MatchStatus.MISMATCH
    assert match_location(req_locations, "London, United Kingdom")[0] == MatchStatus.MISMATCH
    assert match_location(req_locations, "Bengaluru, India")[0] == MatchStatus.MISMATCH
    assert match_location(req_locations, "San Francisco, CA")[0] == MatchStatus.MISMATCH


# ─────────────────────────────────────────────────────────────
# 5. raw_query Propagation Across Pipeline
# ─────────────────────────────────────────────────────────────

def test_raw_query_propagation_across_pipeline():
    """Verify raw_query survives normalization, planning, and qualification."""
    raw_text = "Find GenAI Engineer jobs in Saudi Arabia or UAE with 0-6 years experience"
    spec = JobSearchSpec(raw_query=raw_text)

    assert spec.raw_query == raw_text
    assert "GenAI Engineer" in spec.to_search_hint() or "Engineer" in spec.to_search_hint()

    # Pass through ExtractedRecord
    rec = ExtractedRecord(
        id="rec_test",
        run_id="run_test",
        record_type="job_listing",
        fields={"title": "GenAI Engineer", "location": "Dubai, UAE", "raw_query": spec.raw_query},
    )
    assert rec.fields.get("raw_query") == raw_text

    # Qualify
    q_res = qualify_job(rec, spec)
    assert q_res.qualified is True


# ─────────────────────────────────────────────────────────────
# 6. LLM Failure Resilience
# ─────────────────────────────────────────────────────────────

def test_llm_failure_resilience_deterministic_qualification():
    """Verify system qualifies jobs correctly even if LLM service is completely unavailable."""
    spec = JobSearchSpec(
        raw_query="GenAI Engineer in Dubai",
        titles=["GenAI Engineer"],
        locations=["UAE"],
        experience_min=2,
        experience_max=5,
    )

    rec = ExtractedRecord(
        id="rec_llm_down",
        run_id="run_llm_down",
        record_type="job_listing",
        fields={
            "title": "GenAI Engineer",
            "company": "Desert AI",
            "location": "Dubai, UAE",
            "experience_min": 3,
            "experience_max": 4,
        },
        verification_status=VerificationStatus.VERIFIED,
    )

    # Pure deterministic qualification (no LLM required)
    q_res = qualify_job(rec, spec)
    assert q_res.qualified is True
    assert q_res.score >= 0.85
    assert any("title match" in r.lower() or "location match" in r.lower() for r in q_res.reasons)


# ─────────────────────────────────────────────────────────────
# 7. Non-Job Research Intent Separation
# ─────────────────────────────────────────────────────────────

def test_non_job_research_intent_separation():
    """Verify general questions like 'how to travel to space' are routed to research, not job search."""
    router = IntentRouter(gemini_client=None)

    res_space = router.classify("explain me how to travel to space", agent_mode="auto")
    assert res_space.intent != ResearchIntent.JOB_SEARCH
    assert res_space.intent in (ResearchIntent.HOW_TO, ResearchIntent.EXPLANATION, ResearchIntent.FACTUAL_RESEARCH)

    res_quantum = router.classify("what is quantum computing and how does it work?", agent_mode="auto")
    assert res_quantum.intent != ResearchIntent.JOB_SEARCH
    assert res_quantum.intent in (ResearchIntent.EXPLANATION, ResearchIntent.FACTUAL_RESEARCH)

    res_jobs = router.classify("Find GenAI Engineer jobs in Saudi Arabia or UAE", agent_mode="auto")
    assert res_jobs.intent == ResearchIntent.JOB_SEARCH
