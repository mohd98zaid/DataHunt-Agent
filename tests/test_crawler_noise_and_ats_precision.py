import pytest
from unittest.mock import patch, MagicMock

from datahunt.tools.search import (
    is_valid_job_url,
    canonicalize_url,
    unwrap_tracking_redirect_url,
    DirectATSSearchProvider,
    DISALLOWED_JOB_DOMAINS,
)
from datahunt.tools.fetch import FetchTool, is_actual_job_posting
from datahunt.models import SourceDocument, RetrievalStatus
from datahunt.agent.runtime import AgentRuntime
from datahunt.agent.state import AgentState
from datahunt.agents.query_understanding import JobSearchRequest


def test_unwrap_google_tracking_redirects():
    # 1. Google /goto with url parameter
    goto_url = "https://www.google.com/goto?gsessionid=abc123xyz&url=https%3A%2F%2Fboards.greenhouse.io%2Ftamara%2Fjobs%2F7654321"
    unwrapped = unwrap_tracking_redirect_url(goto_url)
    assert unwrapped == "https://boards.greenhouse.io/tamara/jobs/7654321"
    assert canonicalize_url(goto_url) == "https://boards.greenhouse.io/tamara/jobs/7654321"

    # 2. Google /url with q parameter
    google_url = "https://www.google.com/url?q=https%3A%2F%2Fjobs.lever.co%2Ftabby%2F456-uuid&sa=U&ved=0ahUKEw"
    unwrapped2 = unwrap_tracking_redirect_url(google_url)
    assert unwrapped2 == "https://jobs.lever.co/tabby/456-uuid"
    assert canonicalize_url(google_url) == "https://jobs.lever.co/tabby/456-uuid"

    # 3. Non-google URL remains untouched
    regular_url = "https://jobs.lever.co/tabby/456-uuid"
    assert unwrap_tracking_redirect_url(regular_url) == regular_url


def test_social_media_and_non_job_domains_rejected():
    social_urls = [
        "https://www.instagram.com/reel/DVRcjifj",
        "https://www.instagram.com/p/DQbWzthktbf",
        "https://facebook.com/groups/jobs/permalink/12345",
        "https://tiktok.com/@recruiter/video/987654",
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "https://x.com/company/status/123456789",
        "https://twitter.com/company/status/123456789",
        "https://reddit.com/r/cscareerquestions/comments/123",
        "https://investing.com/news/economy/saudi-arabia-ai-push-12345",
        "https://vision2030.ai/overview",
        "https://en.wikipedia.org/wiki/Artificial_intelligence_in_Saudi_Arabia",
    ]
    for url in social_urls:
        assert is_valid_job_url(url) is False, f"Expected {url} to be rejected by is_valid_job_url"

        # Fast drop in FetchTool
        fetcher = FetchTool()
        res = fetcher.execute(url=url)
        assert res.success is False
        assert "Disallowed non-job domain" in res.error_message


def test_fast_drop_expired_and_not_found_jobs():
    expired_urls = [
        "https://apply.workable.com/arabicai/?not_found=true",
        "https://job-boards.greenhouse.io/tamara?error=true",
        "https://boards.greenhouse.io/tamara/jobs/12345?error=true",
        "https://www.linkedin.com/jobs/view/123456?expired_jd_redirect=true",
        "https://company.com/careers/roles/123?job-closed=true",
        "https://company.com/jobs/view/404",
    ]
    for url in expired_urls:
        assert is_valid_job_url(url) is False, f"Expected {url} to be rejected as expired"

        fetcher = FetchTool()
        res = fetcher.execute(url=url)
        assert res.success is False
        assert any(sig in res.error_message.lower() for sig in ("expired", "closed", "not found"))


def test_fetch_redirect_fast_drop_on_workable_not_found():
    fetcher = FetchTool()

    # Mock redirect to workable ?not_found=true
    with patch("httpx.Client.send") as mock_send:
        mock_response = MagicMock()
        mock_response.is_redirect = True
        mock_response.headers = {"Location": "https://apply.workable.com/arabicai/?not_found=true"}
        mock_send.return_value = mock_response

        res = fetcher.execute("https://apply.workable.com/j/12345")
        assert res.success is False
        assert "expired" in res.error_message.lower() or "closed" in res.error_message.lower()


def test_fetch_redirect_fast_drop_on_greenhouse_error():
    fetcher = FetchTool()

    # Mock redirect to greenhouse ?error=true
    with patch("httpx.Client.send") as mock_send:
        mock_response = MagicMock()
        mock_response.is_redirect = True
        mock_response.headers = {"Location": "https://job-boards.greenhouse.io/tamara?error=true"}
        mock_send.return_value = mock_response

        res = fetcher.execute("https://job-boards.greenhouse.io/tamara/jobs/999999")
        assert res.success is False
        assert "expired" in res.error_message.lower() or "closed" in res.error_message.lower()


def test_direct_ats_provider_strict_role_filtering_for_ai_query():
    provider = DirectATSSearchProvider()

    mock_tamara_jobs = [
        {"title": "Associate Data Scientist - Builders Program", "location": {"name": "Dubai, United Arab Emirates"}, "absolute_url": "https://boards.greenhouse.io/tamara/jobs/1"},
        {"title": "Senior Machine Learning Engineer", "location": {"name": "Riyadh, Saudi Arabia"}, "absolute_url": "https://boards.greenhouse.io/tamara/jobs/2"},
        {"title": "Customer Care Advisor (Voice)", "location": {"name": "Riyadh, Saudi Arabia"}, "absolute_url": "https://boards.greenhouse.io/tamara/jobs/3"},
        {"title": "Collections Advisor", "location": {"name": "Riyadh, Saudi Arabia"}, "absolute_url": "https://boards.greenhouse.io/tamara/jobs/4"},
        {"title": "Copy Writer", "location": {"name": "Riyadh, Saudi Arabia"}, "absolute_url": "https://boards.greenhouse.io/tamara/jobs/5"},
        {"title": "Compliance Officer", "location": {"name": "Riyadh, Saudi Arabia"}, "absolute_url": "https://boards.greenhouse.io/tamara/jobs/6"},
        {"title": "Accountant", "location": {"name": "Dubai, United Arab Emirates"}, "absolute_url": "https://boards.greenhouse.io/tamara/jobs/7"},
    ]

    with patch.object(provider, "_fetch_greenhouse_board", return_value=mock_tamara_jobs):
        hits = provider.search('site:boards.greenhouse.io/tamara "AI" "Saudi Arabia"')

        titles = [h.title for h in hits]
        # Only ML/AI titles should be included
        assert any("Machine Learning" in t for t in titles)
        # Support and operational non-AI roles must be strictly excluded
        assert not any("Customer Care" in t for t in titles)
        assert not any("Collections" in t for t in titles)
        assert not any("Copy Writer" in t for t in titles)
        assert not any("Compliance" in t for t in titles)
        assert not any("Accountant" in t for t in titles)


def test_runtime_candidate_triage_disqualifies_non_ai_roles_for_ai_search():
    runtime = object.__new__(AgentRuntime)
    state = AgentState(task_id="task_test_123", run_id="test_run", request="Find AI jobs in Saudi or UAE", mode="jobs")
    state.canonical_job_request = JobSearchRequest(
        raw_query="Find AI jobs in Saudi or UAE",
        job_title="AI Engineer",
        locations=["Saudi Arabia", "UAE"],
    )

    valid_hit = {
        "url": "https://boards.greenhouse.io/tamara/jobs/2",
        "title": "Senior AI / Machine Learning Engineer",
        "snippet": "Join our AI research team in Riyadh",
        "location": "Riyadh, Saudi Arabia",
    }
    assert runtime._is_promising_candidate(valid_hit, state) is True

    # Support / non-AI roles must be rejected during pre-fetch triage
    invalid_hits = [
        {"url": "https://boards.greenhouse.io/tamara/jobs/3", "title": "Customer Care Advisor (Voice)", "snippet": "Support customers in Riyadh", "location": "Riyadh, Saudi Arabia"},
        {"url": "https://boards.greenhouse.io/tamara/jobs/4", "title": "Collections Advisor", "snippet": "Manage customer collections", "location": "Riyadh, Saudi Arabia"},
        {"url": "https://boards.greenhouse.io/tamara/jobs/5", "title": "Copy Writer", "snippet": "Write marketing copies", "location": "Riyadh, Saudi Arabia"},
        {"url": "https://boards.greenhouse.io/tamara/jobs/6", "title": "Compliance Officer", "snippet": "Regulatory compliance", "location": "Riyadh, Saudi Arabia"},
        {"url": "https://boards.greenhouse.io/tamara/jobs/7", "title": "Staff Accountant", "snippet": "Corporate accounting", "location": "Riyadh, Saudi Arabia"},
        {"url": "https://instagram.com/reel/123", "title": "AI Hiring Reel", "snippet": "Check out our reel", "location": "Riyadh, Saudi Arabia"},
        {"url": "https://apply.workable.com/arabicai/?not_found=true", "title": "AI Engineer", "snippet": "Role not found", "location": "Riyadh, Saudi Arabia"},
    ]
    for hit in invalid_hits:
        assert runtime._is_promising_candidate(hit, state) is False, f"Expected {hit['title']} ({hit['url']}) to be rejected"
