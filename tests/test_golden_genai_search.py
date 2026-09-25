"""
Golden end-to-end test verifying multi-location qualification, title relevance,
experience constraints, and canonical deduplication on 10 realistic job candidates.
"""
import pytest
from datetime import datetime, timezone
from datahunt.models import ExtractedRecord, VerificationStatus
from datahunt.agents.query_understanding import JobSearchRequest
from datahunt.agent.policies import (
    qualify_job,
    match_location,
    match_experience,
    match_title_relevance,
    MatchStatus,
)
from datahunt.tools.search import canonicalize_url
from datahunt.tools.dedupe import DedupeTool


@pytest.fixture
def canonical_request():
    return JobSearchRequest(
        raw_query="Find GenAI Engineer jobs in Saudi or UAE with 0 to 6 years experience",
        job_title="GenAI Engineer",
        locations=["Saudi Arabia", "UAE"],
        location_operator="OR",
        experience_min=0,
        experience_max=6,
        skills=["python", "llm"],
        remote_allowed=True,
    )


@pytest.fixture
def mock_candidates():
    now_iso = datetime.now(timezone.utc).isoformat()
    return [
        # 1. GenAI Engineer, Dubai, 3 yrs (QUALIFIED)
        ExtractedRecord(
            id="job_1",
            run_id="run_golden",
            record_type="job_listing",
            fields={
                "title": "GenAI Engineer",
                "company": "Tech Corp",
                "location": "Dubai, UAE",
                "experience_min": 3,
                "experience_max": 5,
                "description": "Building LLM solutions with python.",
            },
            canonical_url="https://boards.greenhouse.io/techcorp/jobs/101",
            verification_status=VerificationStatus.VERIFIED,
            created_at=now_iso,
        ),
        # 2. Generative AI Engineer, Riyadh, 5 yrs (QUALIFIED)
        ExtractedRecord(
            id="job_2",
            run_id="run_golden",
            record_type="job_listing",
            fields={
                "title": "Generative AI Engineer",
                "company": "AI Labs",
                "location": "Riyadh, Saudi Arabia",
                "experience_min": 5,
                "experience_max": 7,
                "description": "Generative AI foundation model development.",
            },
            canonical_url="https://jobs.lever.co/ailabs/202",
            verification_status=VerificationStatus.VERIFIED,
            created_at=now_iso,
        ),
        # 3. LLM Engineer, Abu Dhabi, 4 yrs (QUALIFIED)
        ExtractedRecord(
            id="job_3",
            run_id="run_golden",
            record_type="job_listing",
            fields={
                "title": "LLM Engineer",
                "company": "G42",
                "location": "Abu Dhabi, UAE",
                "experience_min": 4,
                "experience_max": 6,
                "description": "Training and deploying LLMs using PyTorch.",
            },
            canonical_url="https://jobs.ashbyhq.com/g42/303",
            verification_status=VerificationStatus.VERIFIED,
            created_at=now_iso,
        ),
        # 4. Full Stack Engineer, Dubai, 3 yrs (DISQUALIFIED: title mismatch)
        ExtractedRecord(
            id="job_4",
            run_id="run_golden",
            record_type="job_listing",
            fields={
                "title": "Full Stack Engineer",
                "company": "Web Solutions",
                "location": "Dubai, UAE",
                "experience_min": 3,
                "experience_max": 5,
                "description": "React and Node.js full stack web apps.",
            },
            canonical_url="https://boards.greenhouse.io/websol/404",
            verification_status=VerificationStatus.VERIFIED,
            created_at=now_iso,
        ),
        # 5. Cybersecurity Analyst, Riyadh, 4 yrs (DISQUALIFIED: title mismatch)
        ExtractedRecord(
            id="job_5",
            run_id="run_golden",
            record_type="job_listing",
            fields={
                "title": "Cybersecurity Analyst",
                "company": "SecureNet",
                "location": "Riyadh, Saudi Arabia",
                "experience_min": 4,
                "experience_max": 6,
                "description": "SOC analyst and threat hunting.",
            },
            canonical_url="https://jobs.lever.co/securenet/505",
            verification_status=VerificationStatus.VERIFIED,
            created_at=now_iso,
        ),
        # 6. GenAI Engineer, Cairo, 3 yrs (DISQUALIFIED: location mismatch)
        ExtractedRecord(
            id="job_6",
            run_id="run_golden",
            record_type="job_listing",
            fields={
                "title": "GenAI Engineer",
                "company": "Nile Tech",
                "location": "Cairo, Egypt",
                "experience_min": 3,
                "experience_max": 5,
                "description": "GenAI engineer based in Cairo office.",
            },
            canonical_url="https://jobs.lever.co/niletech/606",
            verification_status=VerificationStatus.VERIFIED,
            created_at=now_iso,
        ),
        # 7. GenAI Engineer, London, 2 yrs (DISQUALIFIED: location mismatch)
        ExtractedRecord(
            id="job_7",
            run_id="run_golden",
            record_type="job_listing",
            fields={
                "title": "GenAI Engineer",
                "company": "London AI",
                "location": "London, UK",
                "experience_min": 2,
                "experience_max": 4,
                "description": "GenAI research engineer in London.",
            },
            canonical_url="https://boards.greenhouse.io/londonai/707",
            verification_status=VerificationStatus.VERIFIED,
            created_at=now_iso,
        ),
        # 8. GenAI Engineer, Dubai (exact duplicate URL of job 1)
        ExtractedRecord(
            id="job_8",
            run_id="run_golden",
            record_type="job_listing",
            fields={
                "title": "GenAI Engineer",
                "company": "Tech Corp",
                "location": "Dubai, UAE",
                "experience_min": 3,
                "experience_max": 5,
                "description": "Building LLM solutions with python.",
            },
            canonical_url="https://boards.greenhouse.io/techcorp/jobs/101",
            verification_status=VerificationStatus.VERIFIED,
            created_at=now_iso,
        ),
        # 9. GenAI Engineer, Dubai (same canonical URL with UTM parameters)
        ExtractedRecord(
            id="job_9",
            run_id="run_golden",
            record_type="job_listing",
            fields={
                "title": "GenAI Engineer",
                "company": "Tech Corp",
                "location": "Dubai, UAE",
                "experience_min": 3,
                "experience_max": 5,
                "description": "Building LLM solutions with python.",
            },
            canonical_url="https://boards.greenhouse.io/techcorp/jobs/101?utm_source=linkedin&utm_medium=job_post&gh_src=xyz",
            verification_status=VerificationStatus.VERIFIED,
            created_at=now_iso,
        ),
        # 10. AI Engineer, Riyadh, 7 yrs (DISQUALIFIED: experience mismatch, requires 7 yrs, max allowed 6)
        ExtractedRecord(
            id="job_10",
            run_id="run_golden",
            record_type="job_listing",
            fields={
                "title": "AI Engineer",
                "company": "Desert AI",
                "location": "Riyadh, Saudi Arabia",
                "experience_min": 7,
                "experience_max": 10,
                "description": "Staff AI Engineer with 7+ years experience.",
            },
            canonical_url="https://jobs.ashbyhq.com/desertai/1010",
            verification_status=VerificationStatus.VERIFIED,
            created_at=now_iso,
        ),
    ]


def test_golden_10_candidates_qualification(canonical_request, mock_candidates):
    """
    Verify qualification verdicts for all 10 mock candidates:
    - Exactly jobs 1, 2, 3 qualify.
    - Jobs 4, 5 rejected on title.
    - Jobs 6, 7 rejected on location.
    - Job 10 rejected on experience.
    """
    results = {}
    for cand in mock_candidates:
        results[cand.id] = qualify_job(cand, canonical_request)

    # 1. GenAI Engineer, Dubai, 3 yrs
    assert results["job_1"].qualified is True
    assert results["job_1"].title_match == MatchStatus.MATCH
    assert results["job_1"].location_match == MatchStatus.MATCH
    assert results["job_1"].experience_match == MatchStatus.MATCH

    # 2. Generative AI Engineer, Riyadh, 5 yrs
    assert results["job_2"].qualified is True
    assert results["job_2"].title_match == MatchStatus.MATCH
    assert results["job_2"].location_match == MatchStatus.MATCH
    assert results["job_2"].experience_match == MatchStatus.MATCH

    # 3. LLM Engineer, Abu Dhabi, 4 yrs
    assert results["job_3"].qualified is True
    assert results["job_3"].title_match == MatchStatus.MATCH
    assert results["job_3"].location_match == MatchStatus.MATCH
    assert results["job_3"].experience_match == MatchStatus.MATCH

    # 4. Full Stack Engineer (Dubai) -> Title Mismatch
    assert results["job_4"].qualified is False
    assert results["job_4"].title_match == MatchStatus.MISMATCH

    # 5. Cybersecurity Analyst (Riyadh) -> Title Mismatch
    assert results["job_5"].qualified is False
    assert results["job_5"].title_match == MatchStatus.MISMATCH

    # 6. GenAI Engineer (Cairo, Egypt) -> Location Mismatch
    assert results["job_6"].qualified is False
    assert results["job_6"].location_match == MatchStatus.MISMATCH

    # 7. GenAI Engineer (London, UK) -> Location Mismatch
    assert results["job_7"].qualified is False
    assert results["job_7"].location_match == MatchStatus.MISMATCH

    # 8 & 9 are qualified content-wise before deduplication
    assert results["job_8"].qualified is True
    assert results["job_9"].qualified is True

    # 10. AI Engineer (Riyadh, 7-10 yrs) -> Experience Mismatch (min 7 > max 6)
    assert results["job_10"].qualified is False
    assert results["job_10"].experience_match == MatchStatus.MISMATCH


def test_golden_canonical_url_deduplication():
    """Verify that canonicalize_url removes UTM tracking parameters and normalizes URLs."""
    raw_url = "https://boards.greenhouse.io/techcorp/jobs/101?utm_source=linkedin&utm_medium=feed&gh_src=xyz#app"
    clean_url = "https://boards.greenhouse.io/techcorp/jobs/101"
    assert canonicalize_url(raw_url) == clean_url


def test_golden_dedupe_and_qualification_pipeline(canonical_request, mock_candidates):
    """
    Full end-to-end pipeline test:
    1. Filter out disqualified candidates (title, location, experience).
    2. Deduplicate qualified candidates by canonical URL.
    3. Exactly 3 distinct qualified jobs remain!
    """
    qualified_records = []
    rejected_records = []

    for cand in mock_candidates:
        q_res = qualify_job(cand, canonical_request)
        if q_res.qualified:
            cand.confidence = q_res.score
            cand.fields["relevance_score"] = q_res.score
            cand.fields["score_breakdown"] = q_res.score_breakdown
            qualified_records.append(cand)
        else:
            rejected_records.append(cand)

    # Initially jobs 1, 2, 3, 8, 9 qualified (5 total, 2 are duplicates of job 1)
    assert len(qualified_records) == 5
    assert len(rejected_records) == 5

    # Run DedupeTool
    deduper = DedupeTool()
    dedupe_res = deduper.execute(qualified_records)
    assert dedupe_res.success is True

    unique_jobs = dedupe_res.data["unique_records"]
    assert len(unique_jobs) == 3

    unique_ids = {j.id for j in unique_jobs}
    # Job 1 must be kept, and duplicate jobs 8 & 9 dropped
    assert "job_1" in unique_ids
    assert "job_2" in unique_ids
    assert "job_3" in unique_ids
    assert "job_8" not in unique_ids
    assert "job_9" not in unique_ids
