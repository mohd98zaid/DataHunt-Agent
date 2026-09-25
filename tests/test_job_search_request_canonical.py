"""
Unit and regression tests for canonical JobSearchRequest parsing, multi-location support,
title relevance matching, and URL canonicalization.
"""
import pytest
from datahunt.agents.query_understanding import JobSearchRequest, QueryUnderstandingAgent
from datahunt.agent.policies import (
    match_title_relevance,
    match_location,
    match_experience,
    MatchStatus,
)
from datahunt.tools.search import canonicalize_url
from datahunt.agent.runtime import AgentRuntime
from datahunt.agent.state import AgentState


def test_job_search_request_raw_query_default():
    """Verify JobSearchRequest can be instantiated without raw_query without validation errors."""
    req = JobSearchRequest(
        job_title="GenAI Engineer",
        location="Dubai",
        experience_min=2,
        experience_max=5,
    )
    assert req.raw_query == ""
    assert req.job_title == "GenAI Engineer"
    assert req.location == "Dubai"
    assert req.locations == ["Dubai"]


def test_multi_location_parsing_and_normalization():
    """Verify 'Saudi or UAE' is properly normalized to individual canonical locations."""
    req = JobSearchRequest(
        raw_query="GenAI Engineer in Saudi or UAE",
        job_title="GenAI Engineer",
        location="Saudi or UAE",
    )
    assert set(req.locations) == {"Saudi Arabia", "UAE"}
    assert req.location_operator == "OR"


def test_deterministic_query_understanding_multi_location():
    """Verify deterministic parser handles multi-location requests and experience bounds."""
    qu = QueryUnderstandingAgent(gemini_client=None)
    req = qu.understand("Find GenAI Engineer jobs in Saudi or UAE with 0 to 6 years experience")
    assert isinstance(req, JobSearchRequest)
    assert req.job_title == "GenAI Engineer"
    assert "Saudi Arabia" in req.locations
    assert "UAE" in req.locations
    assert req.location_operator == "OR"
    assert req.experience_min == 0
    assert req.experience_max == 6


def test_semantic_title_relevance_matching():
    """Verify GenAI/LLM family matches and anti-tracks strictly reject."""
    # Matches
    s1, score1, _ = match_title_relevance("GenAI Engineer", "Senior Generative AI Engineer")
    assert s1 == MatchStatus.MATCH
    assert score1 >= 0.85

    s2, score2, _ = match_title_relevance("GenAI Engineer", "LLM Research Engineer")
    assert s2 == MatchStatus.MATCH
    assert score2 >= 0.85

    s3, score3, _ = match_title_relevance("GenAI Engineer", "Staff AI Engineer")
    assert s3 == MatchStatus.MATCH
    assert score3 >= 0.85

    # Rejections (anti-tracks)
    s4, score4, _ = match_title_relevance("GenAI Engineer", "Senior Full Stack Engineer")
    assert s4 == MatchStatus.MISMATCH
    assert score4 == 0.0

    s5, score5, _ = match_title_relevance("GenAI Engineer", "Cybersecurity Analyst")
    assert s5 == MatchStatus.MISMATCH
    assert score5 == 0.0

    s6, score6, _ = match_title_relevance("GenAI Engineer", "Lead Accountant")
    assert s6 == MatchStatus.MISMATCH
    assert score6 == 0.0


def test_multi_location_matching_logic():
    """Verify match_location handles lists or multi-region strings."""
    req_locs = ["Saudi Arabia", "UAE"]

    # In Riyadh -> MATCH
    s1, _ = match_location(req_locs, "Riyadh, Saudi Arabia")
    assert s1 == MatchStatus.MATCH

    # In Dubai -> MATCH
    s2, _ = match_location(req_locs, "Dubai, United Arab Emirates")
    assert s2 == MatchStatus.MATCH

    # In Cairo -> MISMATCH
    s3, _ = match_location(req_locs, "Cairo, Egypt")
    assert s3 == MatchStatus.MISMATCH

    # In London -> MISMATCH
    s4, _ = match_location(req_locs, "London, United Kingdom")
    assert s4 == MatchStatus.MISMATCH


def test_experience_bound_matching():
    """Verify match_experience rejects when job requires more experience than requested max."""
    # Requested 0-6 years: job requires 7-10 years -> MISMATCH
    s1, _ = match_experience(req_min=0, req_max=6, job_min=7, job_max=10)
    assert s1 == MatchStatus.MISMATCH

    # Requested 0-6 years: job requires 3-5 years -> MATCH
    s2, _ = match_experience(req_min=0, req_max=6, job_min=3, job_max=5)
    assert s2 == MatchStatus.MATCH

    # Requested 0-6 years: job requires 5+ years -> MATCH
    s3, _ = match_experience(req_min=0, req_max=6, job_min=5, job_max=None)
    assert s3 == MatchStatus.MATCH


def test_canonicalize_url_transforms():
    """Verify URL canonicalization strips tracking queries, fragments, and standardizes casing."""
    u1 = "https://boards.greenhouse.io/techcorp/jobs/12345?utm_source=linkedin&utm_campaign=spring#app"
    assert canonicalize_url(u1) == "https://boards.greenhouse.io/techcorp/jobs/12345"

    u2 = "http://JOBS.LEVER.CO/ailabs/98765/?ref=xyz"
    assert canonicalize_url(u2) == "https://jobs.lever.co/ailabs/98765"


def test_candidate_triage_filters_junk_before_fetch():
    """Verify candidate triage helper rejects directory pages and foreign anchors."""
    runtime = AgentRuntime(
        client=None, search=None, fetch=None, extract=None, verify=None, dedupe=None, export=None
    )
    state = AgentState(
        request="GenAI Engineer in Saudi or UAE",
        run_id="run_triage",
        task_id="task_triage",
        mode="jobs",
        locations=["Saudi Arabia", "UAE"],
    )

    # Search directory URL -> should reject
    h1 = {"url": "https://www.linkedin.com/jobs/search?keywords=genai", "title": "Jobs"}
    assert runtime._is_promising_candidate(h1, state) is False

    # Sign in / account page -> should reject
    h2 = {"url": "https://boards.greenhouse.io/auth/login", "title": "Sign in to account"}
    assert runtime._is_promising_candidate(h2, state) is False

    # Foreign anchor with no gulf mention -> should reject
    h3 = {
        "url": "https://jobs.lever.co/company/job1",
        "title": "GenAI Engineer",
        "snippet": "Office located in Cairo, Egypt. Local applicants only.",
    }
    assert runtime._is_promising_candidate(h3, state) is False

    # Gulf position -> should accept
    h4 = {
        "url": "https://jobs.lever.co/company/job2",
        "title": "GenAI Engineer",
        "snippet": "Join our Dubai team working on LLM infrastructure.",
    }
    assert runtime._is_promising_candidate(h4, state) is True
