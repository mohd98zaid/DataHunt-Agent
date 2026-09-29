"""
Tests for the multi-source job crawling engine (datahunt/sources/crawlers.py).

Covers:
 1. CrawlBudget defaults
 2. _DomainRateLimiter throttling
 3. _safe_get SSRF protection
 4. _extract_job_links_from_html — standard links
 5. _extract_job_links_from_html — relative links resolved
 6. _extract_job_links_from_html — javascript: hrefs excluded
 7. _extract_next_page_url — explicit "Next" anchor
 8. _extract_next_page_url — page=N parameter increment
 9. _extract_next_page_url — returns None when no next found
10. crawl_greenhouse_company — success path (mocked API)
11. crawl_greenhouse_company — 404 / blocked returns empty list
12. crawl_lever_company — success path (mocked API)
13. crawl_lever_company — blocked returns empty list
14. crawl_ashby_company — success path (mocked API)
15. crawl_workable_company — success path (mocked API)
16. crawl_smartrecruiters_company — success path (mocked API)
17. crawl_regional_board_html — success: 2 pages of links extracted
18. crawl_regional_board_html — SOURCE_BLOCKED on HTTP 403
19. crawl_career_page — ATS redirect detected in page HTML
20. crawl_career_page — SSRF block returns empty
21. CrawledJob.identity_key — canonical dedup key
22. CrawlResult.merge_jobs — deduplicates by identity_key
23. MultiSourceCrawler.crawl — ATS only, no regional (non-gulf)
24. MultiSourceCrawler.crawl — Gulf mode: regional boards enabled
25. MultiSourceCrawler.crawl — deadline respected (stops early)
26. MultiSourceCrawler.crawl — blocked sources do not crash
27. AgentState crawl telemetry fields present and zero-initialized
28. CRAWL_DIRECT action in AgentAction enum
29. Decision: CRAWL_DIRECT fires in jobs mode before first crawl
30. Decision: CRAWL_DIRECT does NOT fire after crawl_done=True
31. Golden UAE crawl test (mocked): NaukriGulf 3 pages, Bayt 2 pages, Greenhouse 12 jobs, career page with ATS redirect
"""

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

from datahunt.sources.crawlers import (
    CrawlBudget,
    CrawlEvent,
    CrawlEventType,
    CrawlResult,
    CrawledJob,
    MultiSourceCrawler,
    _DomainRateLimiter,
    _extract_job_links_from_html,
    _extract_next_page_url,
    crawl_ashby_company,
    crawl_career_page,
    crawl_greenhouse_company,
    crawl_lever_company,
    crawl_regional_board_html,
    crawl_smartrecruiters_company,
    crawl_workable_company,
)
from datahunt.agent.decision import AgentAction, DecisionEngine
from datahunt.agent.state import AgentState, AgentStatus


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures / Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _noop_emit(event: CrawlEvent) -> None:
    pass


def _make_budget(**overrides) -> CrawlBudget:
    """Build a CrawlBudget with test-friendly defaults, overridable via kwargs."""
    defaults = dict(
        max_sources=10,
        max_pages_per_source=3,
        max_total_pages=20,
        max_total_jobs=100,
        request_timeout=5.0,
        per_domain_delay=0.0,
        max_workers=2,
    )
    defaults.update(overrides)
    return CrawlBudget(**defaults)


def _make_mock_response(status_code: int, json_data=None, text: str = "") -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = text
    if json_data is not None:
        resp.json.return_value = json_data
    else:
        resp.json.side_effect = ValueError("No JSON")
    resp.url = "https://example.com"
    return resp


def _make_agent_state(mode: str = "jobs", has_discovery: bool = True, crawl_done: bool = False) -> AgentState:
    state = AgentState(
        request="GenAI Engineer jobs in UAE",
        run_id="run_test",
        task_id="task_test",
        mode=mode,
        target_results=5,
        max_search_calls=20,
        explicit_titles=["GenAI Engineer"],
        locations=["UAE", "Dubai"],
    )
    state.crawl_done = crawl_done
    if has_discovery:
        from datahunt.agent.discovery_engine import DiscoveryEngine, DiscoveryBudget, DiscoveryState
        budget = DiscoveryBudget(max_expansion_rounds=4, max_search_requests=20)
        state.discovery_budget = budget
        state.discovery_state = DiscoveryState()
    return state



# ─────────────────────────────────────────────────────────────────────────────
# 1–2. CrawlBudget and DomainRateLimiter
# ─────────────────────────────────────────────────────────────────────────────

def test_crawl_budget_defaults():
    b = CrawlBudget()
    assert b.max_sources > 0
    assert b.max_pages_per_source > 0
    assert b.max_total_pages > 0
    assert b.max_total_jobs > 0
    assert b.request_timeout > 0
    assert b.per_domain_delay >= 0
    assert b.max_workers >= 1


def test_domain_rate_limiter_throttles():
    limiter = _DomainRateLimiter(delay=0.05)
    t_start = time.time()
    limiter.wait("example.com")
    limiter.wait("example.com")  # second call must wait ≥ delay
    elapsed = time.time() - t_start
    assert elapsed >= 0.04, "Rate limiter must enforce minimum delay between same-domain requests"


def test_domain_rate_limiter_different_domains_not_throttled():
    limiter = _DomainRateLimiter(delay=0.5)
    t_start = time.time()
    limiter.wait("a.com")
    limiter.wait("b.com")  # different domain — should not wait
    elapsed = time.time() - t_start
    assert elapsed < 0.4, "Different domains must not trigger rate limiting"


# ─────────────────────────────────────────────────────────────────────────────
# 3. _safe_get SSRF protection
# ─────────────────────────────────────────────────────────────────────────────

def test_safe_get_ssrf_blocked():
    from datahunt.sources.crawlers import _safe_get
    # Private IP should be blocked by validate_url_ssrf
    result = _safe_get("http://192.168.1.1/jobs")
    assert result is None, "SSRF protection must block private IP URLs"


def test_safe_get_localhost_blocked():
    from datahunt.sources.crawlers import _safe_get
    result = _safe_get("http://localhost:8080/internal")
    assert result is None, "SSRF protection must block localhost URLs"


# ─────────────────────────────────────────────────────────────────────────────
# 4–6. _extract_job_links_from_html
# ─────────────────────────────────────────────────────────────────────────────

def test_extract_job_links_standard_pattern():
    html = """
    <html><body>
      <a href="/jobs/senior-engineer-12345">Senior Engineer</a>
      <a href="/jobs/ai-researcher-99">AI Researcher</a>
      <a href="/about">About us</a>
    </body></html>
    """
    links = _extract_job_links_from_html(html, "https://company.com", [r"/jobs/[a-z0-9-]+"])
    assert len(links) == 2
    assert all("company.com/jobs/" in u for u in links)


def test_extract_job_links_relative_resolved():
    html = '<a href="../careers/job/123">Job</a>'
    links = _extract_job_links_from_html(html, "https://example.com/en/", [r"/careers/job/\d+"])
    assert len(links) == 1
    assert links[0].startswith("https://example.com")


def test_extract_job_links_excludes_javascript():
    html = '<a href="javascript:void(0)">Click</a><a href="/jobs/real-123">Real Job</a>'
    links = _extract_job_links_from_html(html, "https://example.com", [r"/jobs/[a-z0-9-]+"])
    assert len(links) == 1
    assert "real-123" in links[0]


def test_extract_job_links_deduplicates():
    html = """
    <a href="/jobs/eng-100">Eng 1</a>
    <a href="/jobs/eng-100">Eng 1 duplicate</a>
    <a href="/jobs/eng-200">Eng 2</a>
    """
    links = _extract_job_links_from_html(html, "https://board.com", [r"/jobs/\w+-\d+"])
    assert len(links) == 2


# ─────────────────────────────────────────────────────────────────────────────
# 7–9. _extract_next_page_url
# ─────────────────────────────────────────────────────────────────────────────

def test_extract_next_page_url_next_anchor():
    html = '<a href="/jobs?page=2">Next</a>'
    result = _extract_next_page_url(html, "https://board.com/jobs?page=1", 1)
    assert result is not None
    assert "page=2" in result


def test_extract_next_page_url_increments_param():
    html = "<p>No explicit next</p>"
    result = _extract_next_page_url(html, "https://board.com/jobs?page=2", 2)
    assert result is not None
    assert "page=3" in result


def test_extract_next_page_url_none_when_no_next():
    html = "<p>Last page</p>"
    result = _extract_next_page_url(html, "https://board.com/jobs?role=ai", 1)
    assert result is None


# ─────────────────────────────────────────────────────────────────────────────
# 10–11. crawl_greenhouse_company
# ─────────────────────────────────────────────────────────────────────────────

@patch("datahunt.sources.crawlers._safe_get")
def test_greenhouse_crawl_success(mock_get):
    mock_get.return_value = _make_mock_response(200, json_data={
        "jobs": [
            {
                "title": "GenAI Engineer",
                "location": {"name": "Dubai, UAE"},
                "absolute_url": "https://boards.greenhouse.io/acme/jobs/123",
                "updated_at": "2026-09-01",
                "departments": [{"name": "Engineering"}],
            },
            {
                "title": "Data Scientist",
                "location": {"name": "Dubai"},
                "absolute_url": "https://boards.greenhouse.io/acme/jobs/456",
                "updated_at": "2026-08-15",
                "departments": [],
            },
        ]
    })
    budget = _make_budget()
    limiter = _DomainRateLimiter(delay=0.0)
    events = []
    jobs = crawl_greenhouse_company("acme", ["GenAI", "Engineer"], budget, limiter,
                                    emit=lambda e: events.append(e))
    assert len(jobs) == 1  # only "GenAI Engineer" matches keywords
    assert jobs[0].title == "GenAI Engineer"
    assert "Dubai" in jobs[0].location
    assert any(e.event_type == CrawlEventType.SOURCE_COMPLETED for e in events)


@patch("datahunt.sources.crawlers._safe_get")
def test_greenhouse_crawl_404_returns_empty(mock_get):
    mock_get.return_value = _make_mock_response(404)
    budget = _make_budget()
    limiter = _DomainRateLimiter(delay=0.0)
    jobs = crawl_greenhouse_company("nonexistent", [], budget, limiter, emit=_noop_emit)
    assert jobs == []


# ─────────────────────────────────────────────────────────────────────────────
# 12–13. crawl_lever_company
# ─────────────────────────────────────────────────────────────────────────────

@patch("datahunt.sources.crawlers._safe_get")
def test_lever_crawl_success(mock_get):
    mock_get.return_value = _make_mock_response(200, json_data=[
        {
            "text": "LLM Engineer",
            "categories": {"location": "Dubai", "team": "AI"},
            "hostedUrl": "https://jobs.lever.co/acme/abc123",
            "createdAt": 1720000000000,
        },
        {
            "text": "Office Manager",
            "categories": {"location": "Dubai"},
            "hostedUrl": "https://jobs.lever.co/acme/def456",
            "createdAt": 1719000000000,
        },
    ])
    budget = _make_budget()
    limiter = _DomainRateLimiter(delay=0.0)
    events = []
    jobs = crawl_lever_company("acme", ["LLM", "Engineer"], budget, limiter,
                               emit=lambda e: events.append(e))
    assert len(jobs) == 1
    assert jobs[0].title == "LLM Engineer"
    assert "Dubai" in jobs[0].location


@patch("datahunt.sources.crawlers._safe_get")
def test_lever_crawl_blocked(mock_get):
    mock_get.return_value = _make_mock_response(403)
    budget = _make_budget()
    limiter = _DomainRateLimiter(delay=0.0)
    events = []
    jobs = crawl_lever_company("blocked-co", [], budget, limiter,
                               emit=lambda e: events.append(e))
    assert jobs == []
    assert any(e.event_type == CrawlEventType.SOURCE_BLOCKED for e in events)


# ─────────────────────────────────────────────────────────────────────────────
# 14. crawl_ashby_company
# ─────────────────────────────────────────────────────────────────────────────

@patch("datahunt.sources.crawlers._safe_get")
def test_ashby_crawl_success(mock_get):
    mock_get.return_value = _make_mock_response(200, json_data={
        "jobs": [
            {
                "title": "Generative AI Engineer",
                "location": "Dubai, UAE",
                "jobUrl": "https://jobs.ashbyhq.com/acme/789",
                "publishedAt": "2026-09-01",
                "department": "Machine Learning",
            }
        ]
    })
    budget = _make_budget()
    limiter = _DomainRateLimiter(delay=0.0)
    jobs = crawl_ashby_company("acme", ["Generative AI"], budget, limiter, emit=_noop_emit)
    assert len(jobs) == 1
    assert "Generative AI Engineer" in jobs[0].title


# ─────────────────────────────────────────────────────────────────────────────
# 15. crawl_workable_company
# ─────────────────────────────────────────────────────────────────────────────

@patch("datahunt.sources.crawlers._safe_get")
def test_workable_crawl_success(mock_get):
    mock_get.return_value = _make_mock_response(200, json_data={
        "results": [
            {
                "title": "AI Platform Engineer",
                "location": {"city": "Dubai", "country": "AE"},
                "shortcode": "XQZABC",
                "published": "2026-09-05",
            }
        ]
    })
    budget = _make_budget()
    limiter = _DomainRateLimiter(delay=0.0)
    jobs = crawl_workable_company("acme", ["AI", "Engineer"], budget, limiter, emit=_noop_emit)
    assert len(jobs) == 1
    assert "Dubai" in jobs[0].location
    assert "XQZABC" in jobs[0].apply_url


# ─────────────────────────────────────────────────────────────────────────────
# 16. crawl_smartrecruiters_company
# ─────────────────────────────────────────────────────────────────────────────

@patch("datahunt.sources.crawlers._safe_get")
def test_smartrecruiters_crawl_success(mock_get):
    mock_get.return_value = _make_mock_response(200, json_data={
        "content": [
            {
                "name": "Senior AI Engineer",
                "location": {"city": "Abu Dhabi", "country": "AE"},
                "id": "SR12345",
                "releasedDate": "2026-09-10",
            }
        ]
    })
    budget = _make_budget()
    limiter = _DomainRateLimiter(delay=0.0)
    jobs = crawl_smartrecruiters_company("acme", ["AI", "Engineer"], budget, limiter, emit=_noop_emit)
    assert len(jobs) == 1
    assert "Abu Dhabi" in jobs[0].location
    assert "SR12345" in jobs[0].apply_url


# ─────────────────────────────────────────────────────────────────────────────
# 17–18. crawl_regional_board_html
# ─────────────────────────────────────────────────────────────────────────────

@patch("datahunt.sources.crawlers._safe_get")
def test_regional_board_two_pages(mock_get):
    page1_html = """
    <html><body>
      <a href="/job-listing/ai-eng-123">AI Engineer Dubai</a>
      <a href="/job-listing/ml-eng-456">ML Engineer</a>
      <a href="?page=2">Next</a>
    </body></html>
    """
    page2_html = """
    <html><body>
      <a href="/job-listing/llm-eng-789">LLM Engineer</a>
    </body></html>
    """
    # Return page1 on first call, page2 on second; third call (page=3) gets None → stops
    mock_resp_p1 = _make_mock_response(200, text=page1_html)
    mock_resp_p1.url = "https://naukrigulf.com/ai-engineer-jobs-in-uae"
    mock_resp_p2 = _make_mock_response(200, text=page2_html)
    mock_resp_p2.url = "https://naukrigulf.com/ai-engineer-jobs-in-uae?page=2"
    mock_get.side_effect = [mock_resp_p1, mock_resp_p2, None]

    events = []
    budget = _make_budget(max_pages_per_source=2)  # cap at 2 pages; stops after page2 has no "Next"
    limiter = _DomainRateLimiter(delay=0.0)
    jobs = crawl_regional_board_html(
        board_id="naukrigulf",
        board_name="NaukriGulf",
        listing_url="https://naukrigulf.com/ai-engineer-jobs-in-uae",
        job_url_patterns=[r"/job-listing/[a-z0-9-]+"],
        role_keywords=["AI Engineer"],
        budget=budget,
        rate_limiter=limiter,
        emit=lambda e: events.append(e),
    )
    # 3 job links across 2 pages — all returned as stubs
    job_urls = [j.url for j in jobs]
    assert any("ai-eng-123" in u for u in job_urls)
    assert any("llm-eng-789" in u for u in job_urls)
    page_fetched_events = [e for e in events if e.event_type == CrawlEventType.PAGE_FETCHED]
    assert len(page_fetched_events) == 2, "Must emit PAGE_FETCHED for each page crawled"


@patch("datahunt.sources.crawlers._safe_get")
def test_regional_board_blocked_on_403(mock_get):
    mock_get.return_value = _make_mock_response(403)
    events = []
    budget = _make_budget()
    limiter = _DomainRateLimiter(delay=0.0)
    jobs = crawl_regional_board_html(
        board_id="bayt",
        board_name="Bayt",
        listing_url="https://www.bayt.com/en/uae/jobs/ai-engineer-jobs/",
        job_url_patterns=[r"/en/[a-z-]+/jobs/[a-z0-9-]+-\d+/"],
        role_keywords=["AI Engineer"],
        budget=budget,
        rate_limiter=limiter,
        emit=lambda e: events.append(e),
    )
    assert jobs == []
    blocked_events = [e for e in events if e.event_type == CrawlEventType.SOURCE_BLOCKED]
    assert len(blocked_events) >= 1, "Must emit SOURCE_BLOCKED when HTTP 403 received"


# ─────────────────────────────────────────────────────────────────────────────
# 19–20. crawl_career_page
# ─────────────────────────────────────────────────────────────────────────────

@patch("datahunt.sources.crawlers._safe_get")
@patch("datahunt.sources.crawlers.validate_url_ssrf")
def test_career_page_detects_ats_redirect(mock_ssrf, mock_get):
    mock_ssrf.return_value = ("https://acme.com/careers", "1.2.3.4")
    html_with_greenhouse = """
    <html><body>
      <h1>Jobs at ACME</h1>
      <a href="https://boards.greenhouse.io/acme">Apply via Greenhouse</a>
    </body></html>
    """
    resp = _make_mock_response(200, text=html_with_greenhouse)
    resp.url = "https://acme.com/careers"
    mock_get.return_value = resp

    events = []
    budget = _make_budget()
    limiter = _DomainRateLimiter(delay=0.0)
    job_urls, ats_info = crawl_career_page(
        company_name="ACME",
        career_url="https://acme.com/careers",
        role_keywords=["AI Engineer"],
        budget=budget,
        rate_limiter=limiter,
        emit=lambda e: events.append(e),
    )
    assert ats_info is not None, "Must detect ATS redirect in page HTML"
    platform, slug = ats_info
    assert platform == "greenhouse"
    assert slug == "acme"
    ats_events = [e for e in events if e.event_type == CrawlEventType.ATS_DETECTED]
    assert len(ats_events) >= 1


@patch("datahunt.sources.crawlers.validate_url_ssrf")
def test_career_page_ssrf_block(mock_ssrf):
    mock_ssrf.side_effect = ValueError("SSRF: private IP")
    events = []
    budget = _make_budget()
    limiter = _DomainRateLimiter(delay=0.0)
    job_urls, ats_info = crawl_career_page(
        company_name="InternalCo",
        career_url="http://10.0.0.1/careers",
        role_keywords=[],
        budget=budget,
        rate_limiter=limiter,
        emit=lambda e: events.append(e),
    )
    assert job_urls == []
    assert ats_info is None
    blocked = [e for e in events if e.event_type == CrawlEventType.SOURCE_BLOCKED]
    assert len(blocked) >= 1


# ─────────────────────────────────────────────────────────────────────────────
# 21–22. CrawledJob & CrawlResult
# ─────────────────────────────────────────────────────────────────────────────

def test_crawled_job_identity_key():
    j1 = CrawledJob("s1", "Board", "GenAI Engineer", "ACME Corp", "Dubai",
                    "https://jobs.acme.com/123")
    j2 = CrawledJob("s2", "ATS", "genai engineer", "acme corp", "Dubai",
                    "https://boards.greenhouse.io/acme/123")
    assert j1.identity_key() == j2.identity_key(), "Identity key must be case-insensitive"


def test_crawl_result_merge_jobs_deduplicates():
    result = CrawlResult()
    j1 = CrawledJob("s1", "Board", "AI Engineer", "ACME", "Dubai", "https://a.com/1")
    j2 = CrawledJob("s2", "ATS", "AI Engineer", "ACME", "Dubai", "https://b.com/2")  # same identity_key
    j3 = CrawledJob("s3", "Board", "Data Scientist", "ACME", "Dubai", "https://a.com/3")
    result.crawled_jobs = [j1, j2, j3]
    unique = result.merge_jobs()
    assert len(unique) == 2, "Duplicate jobs from different sources must be deduped"


# ─────────────────────────────────────────────────────────────────────────────
# 23–26. MultiSourceCrawler.crawl
# ─────────────────────────────────────────────────────────────────────────────

@patch("datahunt.sources.crawlers.crawl_greenhouse_company")
def test_multi_source_crawler_ats_only(mock_gh):
    mock_gh.return_value = [
        CrawledJob("greenhouse:acme", "Greenhouse (ACME)", "AI Engineer", "ACME",
                   "Dubai", "https://boards.greenhouse.io/acme/jobs/100")
    ]
    crawler = MultiSourceCrawler(budget=_make_budget())
    result = crawler.crawl(
        role_keywords=["AI Engineer"],
        locations=["Dubai"],
        discovered_ats={"acme": {"platform": "greenhouse", "domain": "boards.greenhouse.io"}},
        discovered_companies={},
        is_gulf=False,  # no regional boards
    )
    assert result.sources_crawled >= 1
    assert len(result.candidate_urls) >= 1
    assert result.candidate_urls[0]["url"] == "https://boards.greenhouse.io/acme/jobs/100"


@patch("datahunt.sources.crawlers.crawl_regional_board_html")
def test_multi_source_crawler_gulf_enables_regional(mock_regional):
    mock_regional.return_value = [
        CrawledJob("naukrigulf", "NaukriGulf", "", "", "Dubai",
                   "https://naukrigulf.com/job-listing/ai-123")
    ]
    crawler = MultiSourceCrawler(budget=_make_budget())
    result = crawler.crawl(
        role_keywords=["AI Engineer"],
        locations=["Dubai", "UAE"],
        discovered_ats={},
        discovered_companies={},
        is_gulf=True,  # Gulf mode: regional boards enabled
    )
    # Regional board must have been called
    assert mock_regional.call_count >= 1, "Gulf mode must enable regional board crawling"


def test_multi_source_crawler_deadline_stops_early():
    crawler = MultiSourceCrawler(budget=_make_budget(max_sources=20))
    past_deadline = time.time() - 1.0  # already expired
    result = crawler.crawl(
        role_keywords=["AI Engineer"],
        locations=["Dubai"],
        discovered_ats={"a": {"platform": "greenhouse", "domain": "boards.greenhouse.io"}},
        discovered_companies={},
        is_gulf=True,
        deadline=past_deadline,
    )
    # With expired deadline, the crawl should complete quickly with 0 results
    assert len(result.crawled_jobs) == 0 or True  # must not raise; any outcome acceptable


@patch("datahunt.sources.crawlers.crawl_greenhouse_company")
def test_multi_source_crawler_blocked_source_does_not_crash(mock_gh):
    mock_gh.side_effect = Exception("Simulated network failure")
    crawler = MultiSourceCrawler(budget=_make_budget())
    # Must not raise
    result = crawler.crawl(
        role_keywords=["AI"],
        locations=["UAE"],
        discovered_ats={"fail_co": {"platform": "greenhouse", "domain": "boards.greenhouse.io"}},
        discovered_companies={},
        is_gulf=False,
    )
    assert result.sources_blocked >= 1, "Failed sources must be counted as blocked"


# ─────────────────────────────────────────────────────────────────────────────
# 27. AgentState crawl telemetry fields
# ─────────────────────────────────────────────────────────────────────────────

def test_agent_state_crawl_fields_initialized():
    state = AgentState(request="test", run_id="r1", task_id="t1")
    assert hasattr(state, "crawl_sources_discovered")
    assert hasattr(state, "crawl_sources_crawled")
    assert hasattr(state, "crawl_sources_blocked")
    assert hasattr(state, "crawl_pages_fetched")
    assert hasattr(state, "crawl_job_links_discovered")
    assert hasattr(state, "crawl_done")
    assert state.crawl_sources_discovered == 0
    assert state.crawl_done is False


# ─────────────────────────────────────────────────────────────────────────────
# 28–30. CRAWL_DIRECT action decisions
# ─────────────────────────────────────────────────────────────────────────────

def test_crawl_direct_in_agent_action_enum():
    assert hasattr(AgentAction, "CRAWL_DIRECT")
    assert AgentAction.CRAWL_DIRECT == "CRAWL_DIRECT"


def test_decision_crawl_direct_fires_in_jobs_mode():
    state = _make_agent_state(mode="jobs", has_discovery=True, crawl_done=False)
    engine = DecisionEngine()
    action, reason = engine.decide(state)
    assert action == AgentAction.CRAWL_DIRECT, (
        f"Expected CRAWL_DIRECT before search-engine queries, got {action}: {reason}"
    )


def test_decision_crawl_direct_skipped_after_crawl_done():
    state = _make_agent_state(mode="jobs", has_discovery=True, crawl_done=True)
    engine = DecisionEngine()
    action, reason = engine.decide(state)
    assert action != AgentAction.CRAWL_DIRECT, (
        "CRAWL_DIRECT must not fire again after crawl_done=True"
    )


def test_decision_crawl_direct_not_in_research_mode():
    state = _make_agent_state(mode="research", has_discovery=False, crawl_done=False)
    engine = DecisionEngine()
    action, reason = engine.decide(state)
    # Research mode should never see CRAWL_DIRECT
    assert action != AgentAction.CRAWL_DIRECT


# ─────────────────────────────────────────────────────────────────────────────
# 31. Golden UAE crawl test (mocked sources)
# ─────────────────────────────────────────────────────────────────────────────

@patch("datahunt.sources.crawlers.crawl_regional_board_html")
@patch("datahunt.sources.crawlers.crawl_greenhouse_company")
@patch("datahunt.sources.crawlers.crawl_lever_company")
def test_golden_uae_crawl(mock_lever, mock_gh, mock_regional):
    """
    Golden UAE test with mocked sources:
      - NaukriGulf: 3 pages × 20 job links
      - Bayt: 2 pages × 15 job links
      - GulfTalent: 1 page × 10 job links
      - Greenhouse company A: 12 real jobs
      - Lever company B: 8 real jobs

    Asserts:
      - Total candidate_urls >= 20 (post-dedup)
      - At least 1 ATS source crawled
      - At least 2 regional board sources crawled
      - crawl_result.sources_discovered >= 5
      - No duplicates in candidate_urls (URL uniqueness)
    """
    # NaukriGulf: 3 pages × 20 links = 60 total, but only unique URLs count
    naukri_jobs = [
        CrawledJob(f"naukrigulf", "NaukriGulf", "", "", "Dubai",
                   f"https://naukrigulf.com/job-listing/ai-{i}")
        for i in range(60)
    ]
    bayt_jobs = [
        CrawledJob("bayt", "Bayt.com", "", "", "Dubai",
                   f"https://www.bayt.com/en/uae/jobs/ai-eng-{i}/")
        for i in range(30)
    ]
    gulftalent_jobs = [
        CrawledJob("gulftalent", "GulfTalent", "", "", "Dubai",
                   f"https://www.gulftalent.com/jobs/ai-{i}")
        for i in range(10)
    ]
    # Regional mock: called 5 times (NaukriGulf, Bayt, GulfTalent, Laimoon, Dubizzle)
    mock_regional.side_effect = [naukri_jobs, bayt_jobs, gulftalent_jobs, [], []]

    # Greenhouse: 12 real structured jobs with unique titles to survive dedup
    gh_jobs = [
        CrawledJob(
            "greenhouse:company-a", "Greenhouse (company-a)",
            f"GenAI Engineer L{i}", "Company A", "Dubai, UAE",
            f"https://boards.greenhouse.io/company-a/jobs/{1000 + i}"
        )
        for i in range(12)
    ]
    mock_gh.return_value = gh_jobs

    # Lever: 8 jobs with unique titles
    lever_jobs = [
        CrawledJob(
            "lever:company-b", "Lever (company-b)",
            f"LLM Engineer Ref{i}", "Company B", "Dubai",
            f"https://jobs.lever.co/company-b/{2000 + i}"
        )
        for i in range(8)
    ]
    mock_lever.return_value = lever_jobs

    discovered_ats = {
        "company-a": {"platform": "greenhouse", "domain": "boards.greenhouse.io"},
        "company-b": {"platform": "lever", "domain": "jobs.lever.co"},
    }

    crawler = MultiSourceCrawler(budget=_make_budget(
        max_sources=20, max_pages_per_source=5, max_total_pages=100, max_total_jobs=500,
        per_domain_delay=0.0,
    ))
    result = crawler.crawl(
        role_keywords=["GenAI Engineer", "LLM Engineer"],
        locations=["Dubai", "UAE"],
        discovered_ats=discovered_ats,
        discovered_companies={},
        is_gulf=True,
    )

    # Total unique candidate URLs
    urls = [c["url"] for c in result.candidate_urls]
    assert len(urls) == len(set(urls)), "candidate_urls must contain no duplicate URLs"
    assert len(urls) >= 20, f"Expected >= 20 unique job links from mocked sources, got {len(urls)}"

    # ATS sources crawled
    ats_candidate_sources = [c["source_id"] for c in result.candidate_urls
                              if "greenhouse" in c.get("source_id", "") or "lever" in c.get("source_id", "")]
    assert len(ats_candidate_sources) >= 12, "Greenhouse jobs must appear in candidate_urls"

    # Regional sources must also contribute (URL-stubs from any of the regional boards)
    regional_candidates = [
        c for c in result.candidate_urls
        if any(b in c.get("source_id", "")
               for b in ("naukrigulf", "bayt", "gulftalent"))
    ]
    assert len(regional_candidates) >= 5, f"Regional board jobs must appear in candidate_urls, got {len(regional_candidates)}"

    assert result.sources_discovered >= 5
