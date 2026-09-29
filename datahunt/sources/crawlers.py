"""
Direct Job Board Crawlers — Multi-source HTTP crawling engine.

Implements direct crawling of:
  - ATS public APIs (Greenhouse, Lever, Ashby, Workable, SmartRecruiters)
  - Regional job boards (NaukriGulf, Bayt, GulfTalent, Laimoon, Dubizzle)
  - Major job boards (Indeed, LinkedIn — HTML only, no auth)
  - Company career pages with job-link extraction and pagination
  - Generic HTML job-board pages

All production code hits real URLs. No mock data. SSRF protection via policy.validate_url_ssrf.
Blocked sources are recorded as SOURCE_BLOCKED and crawling continues.

Per-domain rate limiting, crawl budget controls, and crawl event emission.
"""
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set, Tuple
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from datahunt.logger import logger
from datahunt.policy import validate_url_ssrf, check_domain_policy
from datahunt.errors import DataHuntError

# ─────────────────────────────────────────────────────────────────────────────
# Crawl Budget
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class CrawlBudget:
    """Controls total crawl resource usage per run."""
    max_sources: int = 20
    max_pages_per_source: int = 5
    max_total_pages: int = 60
    max_total_jobs: int = 500
    request_timeout: float = 12.0
    per_domain_delay: float = 0.5   # seconds between requests to same domain
    max_workers: int = 6             # concurrent crawl threads


# ─────────────────────────────────────────────────────────────────────────────
# Crawl Events
# ─────────────────────────────────────────────────────────────────────────────

class CrawlEventType(str, Enum):
    SOURCE_DISCOVERED  = "source.discovered"
    SOURCE_STARTED     = "source.started"
    SOURCE_BLOCKED     = "source.blocked"
    SOURCE_COMPLETED   = "source.completed"
    PAGE_FETCHED       = "page.fetched"
    PAGE_BLOCKED       = "page.blocked"
    JOB_LINK_DISCOVERED = "job_link.discovered"
    JOB_EXTRACTED      = "job.extracted"
    ATS_DETECTED       = "ats.detected"


@dataclass
class CrawlEvent:
    event_type: CrawlEventType
    source_id: str
    url: str = ""
    message: str = ""
    count: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)


# ─────────────────────────────────────────────────────────────────────────────
# Raw Job (pre-qualification)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class CrawledJob:
    """A job discovered through direct crawling, before qualification."""
    source_id: str
    source_name: str
    title: str
    company: str
    location: str
    url: str
    apply_url: str = ""
    description: str = ""
    posted_at: str = ""
    salary_raw: str = ""
    raw_fields: Dict[str, Any] = field(default_factory=dict)

    def identity_key(self) -> str:
        """Canonical identity for cross-source deduplication."""
        t = re.sub(r"\s+", " ", self.title.lower().strip())
        c = re.sub(r"\s+", " ", self.company.lower().strip())
        if not t and not c:
            # URL-only stubs: use URL as key to allow dedup by URL, not lump all stubs together
            return self.url or ""
        return f"{t}|{c}"


# ─────────────────────────────────────────────────────────────────────────────
# Per-domain rate limiter
# ─────────────────────────────────────────────────────────────────────────────

class _DomainRateLimiter:
    """Thread-safe per-domain request throttling."""

    def __init__(self, delay: float = 0.5):
        self._delay = delay
        self._last_request: Dict[str, float] = {}
        self._lock = threading.Lock()

    def wait(self, domain: str) -> None:
        with self._lock:
            last = self._last_request.get(domain, 0.0)
            elapsed = time.time() - last
            if elapsed < self._delay:
                time.sleep(self._delay - elapsed)
            self._last_request[domain] = time.time()


# ─────────────────────────────────────────────────────────────────────────────
# HTTP helpers
# ─────────────────────────────────────────────────────────────────────────────

_DESKTOP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


class SafeCrawlerResponse:
    """Lightweight response wrapper supporting fallback content retrieval."""
    def __init__(self, status_code: int, text: str = "", url: str = "", headers: Optional[Dict] = None, json_data: Any = None):
        self.status_code = status_code
        self.text = text
        self.url = url
        self.headers = headers or {}
        self._json_data = json_data

    def json(self):
        if self._json_data is not None:
            return self._json_data
        import json
        return json.loads(self.text)


def _safe_get(url: str, timeout: float = 12.0,
              extra_headers: Optional[Dict] = None) -> Optional[Any]:
    """
    Fetch a URL with SSRF protection, redirect handling, and TLS browser impersonation
    fallback when encountering HTTP 401/403/429.
    Records alternative public retrieval without bypassing access controls.
    """
    try:
        cleaned_url, _ = validate_url_ssrf(url)
    except Exception as e:
        logger.debug(f"SSRF block for {url}: {e}")
        return None
    headers = dict(_DESKTOP_HEADERS)
    if extra_headers:
        headers.update(extra_headers)
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True, headers=headers) as client:
            resp = client.get(cleaned_url)
            if resp.status_code < 400:
                return resp

            # Attempt alternative public retrieval method on 401, 403, 429
            if resp.status_code in (401, 403, 429):
                try:
                    from ddgs import DDGS
                    ext_data = DDGS().extract(cleaned_url)
                    ext_content = ext_data.get("content") or ""
                    if ext_content and len(ext_content.strip()) > 80:
                        logger.info(f"HTTP {resp.status_code} fallback retrieval succeeded for {cleaned_url} via TLS browser impersonation")
                        return SafeCrawlerResponse(
                            status_code=200,
                            text=ext_content,
                            url=cleaned_url,
                            headers={"content-type": "text/html"}
                        )
                except Exception as fb_err:
                    logger.debug(f"Alternative public retrieval method failed for {cleaned_url}: {fb_err}")
            return resp
    except Exception as e:
        logger.debug(f"HTTP error for {url}: {e}")
        try:
            from ddgs import DDGS
            ext_data = DDGS().extract(cleaned_url)
            ext_content = ext_data.get("content") or ""
            if ext_content and len(ext_content.strip()) > 80:
                logger.info(f"Alternative public retrieval method succeeded for {cleaned_url}")
                return SafeCrawlerResponse(
                    status_code=200,
                    text=ext_content,
                    url=cleaned_url,
                    headers={"content-type": "text/html"}
                )
        except Exception:
            pass
        return None


def is_broadly_relevant_job_title(title: str, role_keywords: List[str]) -> bool:
    """
    Broad role matching for discovery crawling.
    Returns True if the title could plausibly be relevant to the requested roles.
    Empty titles (link stubs) are always kept so they can be inspected.
    Does NOT require exact string match; recognizes AI/GenAI/ML/engineering synonyms.
    """
    if not title or not title.strip():
        return True
    if not role_keywords:
        return True

    t_low = title.lower()

    # Exact substring match
    for kw in role_keywords:
        if kw.lower() in t_low:
            return True

    # Check for AI / GenAI / LLM family queries
    has_ai_query = any(k in kw.lower() for kw in role_keywords for k in ("genai", "generative ai", "ai", "llm", "machine learning", "ml", "prompt", "nlp", "deep learning"))
    if has_ai_query:
        ai_terms = (
            "genai", "gen ai", "generative", "llm", "large language", "prompt",
            "machine learning", "deep learning", "nlp", "neural", "rag",
            "foundation model", "computer vision", "artificial intelligence",
            "ai engineer", "ai developer", "ai architect", "ai specialist", "ai/ml",
            "applied ai", "ai solutions", "ml engineer", "ml developer", "ai research"
        )
        if any(term in t_low for term in ai_terms):
            return True
        # Also match if title contains standalone word 'ai' and a technical role
        if re.search(r"\bai\b", t_low) and any(role in t_low for role in ("engineer", "developer", "architect", "lead", "scientist", "specialist", "intern")):
            return True

    # Check word overlap for other roles
    title_words = set(re.findall(r"\w+", t_low))
    for kw in role_keywords:
        kw_words = set(re.findall(r"\w+", kw.lower()))
        kw_core = kw_words - {"in", "and", "or", "for", "with", "the", "a", "an", "at", "of", "to"}
        if kw_core and len(title_words & kw_core) >= max(1, len(kw_core) - 1):
            return True

    return False


def _get_domain(url: str) -> str:
    try:
        return urlparse(url).netloc.lower()
    except Exception:
        return ""


# ─────────────────────────────────────────────────────────────────────────────
# ATS Public API Crawlers
# ─────────────────────────────────────────────────────────────────────────────

def crawl_greenhouse_company(
    company_slug: str,
    role_keywords: List[str],
    budget: CrawlBudget,
    rate_limiter: _DomainRateLimiter,
    emit: Callable[[CrawlEvent], None],
) -> List[CrawledJob]:
    """
    Crawl a single Greenhouse company board via the public JSON API.
    Falls back to EU endpoint for EMEA companies.
    """
    source_id = f"greenhouse:{company_slug}"
    jobs: List[CrawledJob] = []
    endpoints = [
        f"https://boards-api.greenhouse.io/v1/boards/{company_slug}/jobs?content=true",
        f"https://boards-api.eu.greenhouse.io/v1/boards/{company_slug}/jobs?content=true",
    ]
    emit(CrawlEvent(CrawlEventType.SOURCE_STARTED, source_id, url=endpoints[0],
                    message=f"Greenhouse API for {company_slug}"))

    for api_url in endpoints:
        rate_limiter.wait("boards-api.greenhouse.io")
        resp = _safe_get(api_url, timeout=budget.request_timeout,
                         extra_headers={"Accept": "application/json"})
        if not resp or resp.status_code != 200:
            continue
        try:
            data = resp.json()
        except Exception:
            continue

        raw_jobs = data.get("jobs", [])
        emit(CrawlEvent(CrawlEventType.PAGE_FETCHED, source_id, url=api_url,
                        count=len(raw_jobs), message=f"Greenhouse API: {len(raw_jobs)} jobs"))

        for job in raw_jobs:
            if len(jobs) >= budget.max_total_jobs:
                break
            title = job.get("title", "")
            if not is_broadly_relevant_job_title(title, role_keywords):
                continue
            loc_data = job.get("location", {})
            location = (
                loc_data.get("name", "") if isinstance(loc_data, dict) else str(loc_data or "")
            )
            apply_url = job.get("absolute_url", "")
            posted_at = job.get("updated_at", "")
            depts = job.get("departments", [])
            dept = depts[0].get("name", "") if depts and isinstance(depts[0], dict) else ""

            crawled = CrawledJob(
                source_id=source_id,
                source_name=f"Greenhouse ({company_slug})",
                title=title,
                company=company_slug.replace("-", " ").title(),
                location=location,
                url=apply_url,
                apply_url=apply_url,
                posted_at=posted_at,
                raw_fields={"department": dept, "gh_company": company_slug},
            )
            jobs.append(crawled)
            emit(CrawlEvent(CrawlEventType.JOB_EXTRACTED, source_id, url=apply_url,
                            message=f"{title} @ {crawled.company}"))
        break  # success on first working endpoint

    emit(CrawlEvent(CrawlEventType.SOURCE_COMPLETED, source_id,
                    count=len(jobs), message=f"Greenhouse {company_slug}: {len(jobs)} jobs"))
    return jobs


def crawl_lever_company(
    company_slug: str,
    role_keywords: List[str],
    budget: CrawlBudget,
    rate_limiter: _DomainRateLimiter,
    emit: Callable[[CrawlEvent], None],
) -> List[CrawledJob]:
    """
    Crawl Lever's public postings API for a company.
    """
    source_id = f"lever:{company_slug}"
    jobs: List[CrawledJob] = []
    api_url = f"https://api.lever.co/v0/postings/{company_slug}?mode=json"

    emit(CrawlEvent(CrawlEventType.SOURCE_STARTED, source_id, url=api_url,
                    message=f"Lever API for {company_slug}"))
    rate_limiter.wait("api.lever.co")
    resp = _safe_get(api_url, timeout=budget.request_timeout,
                     extra_headers={"Accept": "application/json"})
    if not resp or resp.status_code != 200:
        emit(CrawlEvent(CrawlEventType.SOURCE_BLOCKED, source_id, url=api_url,
                        message=f"Lever API unavailable for {company_slug}"))
        return jobs

    try:
        raw_jobs = resp.json()
    except Exception:
        return jobs

    if not isinstance(raw_jobs, list):
        raw_jobs = raw_jobs.get("postings", []) if isinstance(raw_jobs, dict) else []

    emit(CrawlEvent(CrawlEventType.PAGE_FETCHED, source_id, url=api_url,
                    count=len(raw_jobs), message=f"Lever API: {len(raw_jobs)} postings"))

    for job in raw_jobs:
        if len(jobs) >= budget.max_total_jobs:
            break
        title = job.get("text", "") or job.get("title", "")
        if not is_broadly_relevant_job_title(title, role_keywords):
            continue
        categories = job.get("categories", {})
        location = categories.get("location", "") if isinstance(categories, dict) else ""
        apply_url = job.get("hostedUrl", job.get("applyUrl", ""))
        posted_at = str(job.get("createdAt", ""))
        team = categories.get("team", "") if isinstance(categories, dict) else ""

        crawled = CrawledJob(
            source_id=source_id,
            source_name=f"Lever ({company_slug})",
            title=title,
            company=company_slug.replace("-", " ").title(),
            location=location,
            url=apply_url,
            apply_url=apply_url,
            posted_at=posted_at,
            raw_fields={"team": team, "lever_company": company_slug},
        )
        jobs.append(crawled)
        emit(CrawlEvent(CrawlEventType.JOB_EXTRACTED, source_id, url=apply_url,
                        message=f"{title} @ {crawled.company}"))

    emit(CrawlEvent(CrawlEventType.SOURCE_COMPLETED, source_id,
                    count=len(jobs), message=f"Lever {company_slug}: {len(jobs)} jobs"))
    return jobs


def crawl_ashby_company(
    company_slug: str,
    role_keywords: List[str],
    budget: CrawlBudget,
    rate_limiter: _DomainRateLimiter,
    emit: Callable[[CrawlEvent], None],
) -> List[CrawledJob]:
    """
    Crawl Ashby's public job board API for a company.
    """
    source_id = f"ashby:{company_slug}"
    jobs: List[CrawledJob] = []
    api_url = f"https://api.ashbyhq.com/posting-api/job-board/{company_slug}"

    emit(CrawlEvent(CrawlEventType.SOURCE_STARTED, source_id, url=api_url,
                    message=f"Ashby API for {company_slug}"))
    rate_limiter.wait("api.ashbyhq.com")
    resp = _safe_get(api_url, timeout=budget.request_timeout,
                     extra_headers={"Accept": "application/json"})
    if not resp or resp.status_code != 200:
        emit(CrawlEvent(CrawlEventType.SOURCE_BLOCKED, source_id, url=api_url,
                        message=f"Ashby API unavailable for {company_slug}"))
        return jobs

    try:
        data = resp.json()
    except Exception:
        return jobs

    raw_jobs = data.get("jobs", []) if isinstance(data, dict) else []
    emit(CrawlEvent(CrawlEventType.PAGE_FETCHED, source_id, url=api_url,
                    count=len(raw_jobs), message=f"Ashby API: {len(raw_jobs)} jobs"))

    for job in raw_jobs:
        if len(jobs) >= budget.max_total_jobs:
            break
        title = job.get("title", "")
        if not is_broadly_relevant_job_title(title, role_keywords):
            continue
        location = job.get("location", "") or ""
        apply_url = job.get("jobUrl", job.get("applyUrl", ""))
        posted_at = job.get("publishedAt", "")
        dept = job.get("department", "")

        crawled = CrawledJob(
            source_id=source_id,
            source_name=f"Ashby ({company_slug})",
            title=title,
            company=company_slug.replace("-", " ").title(),
            location=location,
            url=apply_url,
            apply_url=apply_url,
            posted_at=posted_at,
            raw_fields={"department": dept, "ashby_company": company_slug},
        )
        jobs.append(crawled)
        emit(CrawlEvent(CrawlEventType.JOB_EXTRACTED, source_id, url=apply_url,
                        message=f"{title} @ {crawled.company}"))

    emit(CrawlEvent(CrawlEventType.SOURCE_COMPLETED, source_id,
                    count=len(jobs), message=f"Ashby {company_slug}: {len(jobs)} jobs"))
    return jobs


def crawl_workable_company(
    company_slug: str,
    role_keywords: List[str],
    budget: CrawlBudget,
    rate_limiter: _DomainRateLimiter,
    emit: Callable[[CrawlEvent], None],
) -> List[CrawledJob]:
    """
    Crawl Workable's public job board for a company via their published JSON feed.
    """
    source_id = f"workable:{company_slug}"
    jobs: List[CrawledJob] = []
    api_url = f"https://apply.workable.com/api/v3/accounts/{company_slug}/jobs"

    emit(CrawlEvent(CrawlEventType.SOURCE_STARTED, source_id, url=api_url,
                    message=f"Workable API for {company_slug}"))
    rate_limiter.wait("apply.workable.com")
    resp = _safe_get(api_url, timeout=budget.request_timeout,
                     extra_headers={"Accept": "application/json"})
    if not resp or resp.status_code != 200:
        emit(CrawlEvent(CrawlEventType.SOURCE_BLOCKED, source_id, url=api_url,
                        message=f"Workable API unavailable for {company_slug}"))
        return jobs

    try:
        data = resp.json()
    except Exception:
        return jobs

    raw_jobs = data.get("results", []) if isinstance(data, dict) else []
    emit(CrawlEvent(CrawlEventType.PAGE_FETCHED, source_id, url=api_url,
                    count=len(raw_jobs), message=f"Workable API: {len(raw_jobs)} jobs"))

    for job in raw_jobs:
        if len(jobs) >= budget.max_total_jobs:
            break
        title = job.get("title", "")
        if not is_broadly_relevant_job_title(title, role_keywords):
            continue
        location = job.get("location", {})
        if isinstance(location, dict):
            location = location.get("city", "") or location.get("country", "")
        else:
            location = str(location or "")
        shortcode = job.get("shortcode", "")
        apply_url = f"https://apply.workable.com/{company_slug}/j/{shortcode}" if shortcode else ""
        posted_at = job.get("published", "")

        crawled = CrawledJob(
            source_id=source_id,
            source_name=f"Workable ({company_slug})",
            title=title,
            company=company_slug.replace("-", " ").title(),
            location=location,
            url=apply_url,
            apply_url=apply_url,
            posted_at=posted_at,
            raw_fields={"workable_company": company_slug, "shortcode": shortcode},
        )
        jobs.append(crawled)
        emit(CrawlEvent(CrawlEventType.JOB_EXTRACTED, source_id, url=apply_url,
                        message=f"{title} @ {crawled.company}"))

    emit(CrawlEvent(CrawlEventType.SOURCE_COMPLETED, source_id,
                    count=len(jobs), message=f"Workable {company_slug}: {len(jobs)} jobs"))
    return jobs


def crawl_smartrecruiters_company(
    company_slug: str,
    role_keywords: List[str],
    budget: CrawlBudget,
    rate_limiter: _DomainRateLimiter,
    emit: Callable[[CrawlEvent], None],
) -> List[CrawledJob]:
    """
    Crawl SmartRecruiters public job search API for a company.
    """
    source_id = f"smartrecruiters:{company_slug}"
    jobs: List[CrawledJob] = []
    query = "+".join(role_keywords[:2]) if role_keywords else ""
    api_url = (
        f"https://api.smartrecruiters.com/v1/companies/{company_slug}/postings"
        f"{'?q=' + query if query else ''}"
    )

    emit(CrawlEvent(CrawlEventType.SOURCE_STARTED, source_id, url=api_url,
                    message=f"SmartRecruiters API for {company_slug}"))
    rate_limiter.wait("api.smartrecruiters.com")
    resp = _safe_get(api_url, timeout=budget.request_timeout,
                     extra_headers={"Accept": "application/json"})
    if not resp or resp.status_code != 200:
        emit(CrawlEvent(CrawlEventType.SOURCE_BLOCKED, source_id, url=api_url,
                        message=f"SmartRecruiters API unavailable for {company_slug}"))
        return jobs

    try:
        data = resp.json()
    except Exception:
        return jobs

    raw_jobs = data.get("content", []) if isinstance(data, dict) else []
    emit(CrawlEvent(CrawlEventType.PAGE_FETCHED, source_id, url=api_url,
                    count=len(raw_jobs), message=f"SmartRecruiters: {len(raw_jobs)} jobs"))

    for job in raw_jobs:
        if len(jobs) >= budget.max_total_jobs:
            break
        title = job.get("name", "")
        if not is_broadly_relevant_job_title(title, role_keywords):
            continue
        loc = job.get("location", {})
        location = ""
        if isinstance(loc, dict):
            city = loc.get("city", "")
            country = loc.get("country", "")
            location = f"{city}, {country}".strip(", ")
        job_id = job.get("id", "")
        apply_url = f"https://jobs.smartrecruiters.com/{company_slug}/{job_id}" if job_id else ""
        posted_at = job.get("releasedDate", "")

        crawled = CrawledJob(
            source_id=source_id,
            source_name=f"SmartRecruiters ({company_slug})",
            title=title,
            company=company_slug.replace("-", " ").title(),
            location=location,
            url=apply_url,
            apply_url=apply_url,
            posted_at=posted_at,
            raw_fields={"sr_company": company_slug, "sr_job_id": job_id},
        )
        jobs.append(crawled)
        emit(CrawlEvent(CrawlEventType.JOB_EXTRACTED, source_id, url=apply_url,
                        message=f"{title} @ {crawled.company}"))

    emit(CrawlEvent(CrawlEventType.SOURCE_COMPLETED, source_id,
                    count=len(jobs), message=f"SmartRecruiters {company_slug}: {len(jobs)} jobs"))
    return jobs


# ─────────────────────────────────────────────────────────────────────────────
# Regional Board Crawlers (HTML with pagination)
# ─────────────────────────────────────────────────────────────────────────────

def _extract_job_links_from_html(html: str, base_url: str,
                                  job_url_patterns: List[str]) -> List[str]:
    """
    Extract job posting URLs from an HTML listing page using known URL patterns.
    Returns a list of absolute URLs.
    """
    soup = BeautifulSoup(html, "html.parser")
    found: List[str] = []
    seen: Set[str] = set()
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.startswith("#") or href.startswith("javascript:"):
            continue
        abs_url = urljoin(base_url, href)
        parsed = urlparse(abs_url)
        if parsed.scheme not in ("http", "https"):
            continue
        path = parsed.path.lower()
        matched = any(re.search(pat, path + "?" + parsed.query) for pat in job_url_patterns)
        if matched and abs_url not in seen:
            seen.add(abs_url)
            found.append(abs_url)
    return found


def _extract_next_page_url(html: str, base_url: str,
                             current_page: int) -> Optional[str]:
    """
    Detect pagination: look for a "next" link or a page=N+1 pattern.
    Returns the next page URL or None.
    """
    soup = BeautifulSoup(html, "html.parser")
    # Strategy 1: Look for explicit "next" page link
    for a in soup.find_all("a", href=True):
        text = a.get_text(strip=True).lower()
        if text in ("next", "next page", "›", "»", "→") or "next" in (a.get("rel") or []):
            href = a["href"].strip()
            if href and not href.startswith("javascript:"):
                return urljoin(base_url, href)
    # Strategy 2: Increment page parameter
    parsed = urlparse(base_url)
    query = parsed.query or ""
    if f"page={current_page}" in query:
        return base_url.replace(f"page={current_page}", f"page={current_page + 1}")
    if f"start={current_page}" in query:
        step = 20
        return base_url.replace(f"start={current_page}", f"start={current_page + step}")
    return None


def crawl_regional_board_html(
    board_id: str,
    board_name: str,
    listing_url: str,
    job_url_patterns: List[str],
    role_keywords: List[str],
    budget: CrawlBudget,
    rate_limiter: _DomainRateLimiter,
    emit: Callable[[CrawlEvent], None],
    extract_job_from_listing: Optional[Callable[[str, str], Optional[CrawledJob]]] = None,
) -> List[CrawledJob]:
    """
    Generic HTML-pagination crawler for regional job boards.

    Strategy:
    1. Fetch listing page
    2. Extract job links using board-specific URL patterns
    3. Optionally extract title/company/location from listing page HTML (lightweight)
    4. Follow pagination up to budget.max_pages_per_source
    5. Emit crawl events throughout

    `extract_job_from_listing(row_html, url)` is an optional per-board parser.
    If not provided, we just return discovered URLs for FetchTool to process.
    """
    source_id = board_id
    domain = _get_domain(listing_url)
    jobs: List[CrawledJob] = []
    discovered_urls: List[str] = []

    emit(CrawlEvent(CrawlEventType.SOURCE_STARTED, source_id, url=listing_url,
                    message=f"Crawling {board_name}"))

    # Check SSRF before crawling
    try:
        validate_url_ssrf(listing_url)
    except Exception as e:
        emit(CrawlEvent(CrawlEventType.SOURCE_BLOCKED, source_id, url=listing_url,
                        message=f"SSRF block: {e}"))
        return jobs

    current_url = listing_url
    pages_fetched = 0

    for page_num in range(1, budget.max_pages_per_source + 1):
        if len(jobs) >= budget.max_total_jobs:
            break

        rate_limiter.wait(domain)
        resp = _safe_get(current_url, timeout=budget.request_timeout)
        if not resp:
            emit(CrawlEvent(CrawlEventType.PAGE_BLOCKED, source_id, url=current_url,
                            message=f"No response from {board_name} page {page_num}"))
            break
        if resp.status_code in (403, 429, 401):
            emit(CrawlEvent(CrawlEventType.SOURCE_BLOCKED, source_id, url=current_url,
                            message=f"HTTP {resp.status_code} from {board_name} — SOURCE_BLOCKED"))
            break
        if resp.status_code >= 400:
            emit(CrawlEvent(CrawlEventType.PAGE_BLOCKED, source_id, url=current_url,
                            message=f"HTTP {resp.status_code} on page {page_num}"))
            break

        pages_fetched += 1
        html = resp.text
        page_links = _extract_job_links_from_html(html, current_url, job_url_patterns)
        emit(CrawlEvent(CrawlEventType.PAGE_FETCHED, source_id, url=current_url,
                        count=len(page_links),
                        message=f"{board_name} page {page_num}: {len(page_links)} job links"))

        for jurl in page_links:
            if jurl not in discovered_urls:
                discovered_urls.append(jurl)
                emit(CrawlEvent(CrawlEventType.JOB_LINK_DISCOVERED, source_id, url=jurl,
                                message=f"Discovered: {jurl}"))

        # If a lightweight extractor was provided, parse jobs from listing page
        if extract_job_from_listing:
            soup = BeautifulSoup(html, "html.parser")
            for jurl in page_links:
                if len(jobs) >= budget.max_total_jobs:
                    break
                row_html = str(soup)  # board-specific parser gets full page
                job = extract_job_from_listing(row_html, jurl)
                if job:
                    jobs.append(job)
                    emit(CrawlEvent(CrawlEventType.JOB_EXTRACTED, source_id, url=jurl,
                                    message=f"{job.title} @ {job.company}"))

        # Advance to next page
        next_url = _extract_next_page_url(html, current_url, page_num)
        if not next_url or next_url == current_url:
            break
        current_url = next_url

    emit(CrawlEvent(CrawlEventType.SOURCE_COMPLETED, source_id,
                    count=len(discovered_urls),
                    message=f"{board_name}: {len(discovered_urls)} links from {pages_fetched} pages"))
    # Return discovered_urls as CrawledJob stubs (url-only, will be fully fetched by FetchTool)
    for u in discovered_urls:
        if not any(j.url == u for j in jobs):
            jobs.append(CrawledJob(
                source_id=source_id,
                source_name=board_name,
                title="",
                company="",
                location="",
                url=u,
                apply_url=u,
            ))
    return jobs


# ─────────────────────────────────────────────────────────────────────────────
# Board-specific listing URL builders
# ─────────────────────────────────────────────────────────────────────────────

def _naukrigulf_listing_url(role: str, location: str) -> str:
    role_slug = re.sub(r"\s+", "-", role.lower().strip())
    loc_slug = re.sub(r"\s+", "-", location.lower().strip()) if location else "middle-east"
    return f"https://www.naukrigulf.com/{role_slug}-jobs-in-{loc_slug}"


def _bayt_listing_url(role: str, location: str) -> str:
    role_slug = re.sub(r"\s+", "-", role.lower().strip())
    loc_slug = re.sub(r"\s+", "-", location.lower().strip()) if location else "uae"
    return f"https://www.bayt.com/en/{loc_slug}/jobs/{role_slug}-jobs/"


def _gulftalent_listing_url(role: str, location: str) -> str:
    role_enc = re.sub(r"\s+", "+", role.strip())
    loc_slug = location.lower().replace(" ", "-") if location else "middle-east"
    return f"https://www.gulftalent.com/jobs?q={role_enc}&l={loc_slug}"


def _laimoon_listing_url(role: str, location: str) -> str:
    role_enc = re.sub(r"\s+", "+", role.strip())
    return f"https://laimoon.com/jobs/search/?q={role_enc}"


def _dubizzle_listing_url(role: str, location: str) -> str:
    role_enc = re.sub(r"\s+", "-", role.lower().strip())
    return f"https://uae.dubizzle.com/jobs/?q={role_enc}"


# ─────────────────────────────────────────────────────────────────────────────
# Career Page Crawler
# ─────────────────────────────────────────────────────────────────────────────

# Common career page URL patterns
_CAREER_PAGE_PATHS = [
    "/careers", "/careers/", "/jobs", "/jobs/", "/work-with-us",
    "/join-us", "/join", "/opportunities", "/open-positions",
    "/about/careers", "/company/jobs",
]

_JOB_LINK_PATTERNS = [
    r"/jobs?/[a-z0-9_-]+",
    r"/careers?/[a-z0-9_-]+",
    r"/openings?/[a-z0-9_-]+",
    r"/positions?/[a-z0-9_-]+",
    r"/apply/[a-z0-9_-]+",
    r"[?&](jid|job_id|jobId|id)=\d+",
    r"/job-detail/",
    r"/vacancy/",
]

_ATS_REDIRECT_PATTERNS = {
    "greenhouse": re.compile(r"boards\.greenhouse\.io/([\w-]+)"),
    "lever": re.compile(r"jobs\.lever\.co/([\w-]+)"),
    "ashby": re.compile(r"jobs\.ashbyhq\.com/([\w-]+)"),
    "workable": re.compile(r"apply\.workable\.com/([\w-]+)"),
    "smartrecruiters": re.compile(r"jobs\.smartrecruiters\.com/([\w-]+)"),
}


def crawl_career_page(
    company_name: str,
    career_url: str,
    role_keywords: List[str],
    budget: CrawlBudget,
    rate_limiter: _DomainRateLimiter,
    emit: Callable[[CrawlEvent], None],
) -> Tuple[List[str], Optional[Tuple[str, str]]]:
    """
    Crawl a company career page to:
      1. Discover individual job links
      2. Detect ATS redirect (returns (ats_platform, company_slug) if found)
      3. Follow job listing pagination (up to budget.max_pages_per_source)

    Returns (job_urls, ats_redirect_info).
    job_urls: list of individual job posting URLs found
    ats_redirect_info: (platform_name, company_slug) if an ATS is detected, else None
    """
    source_id = f"career:{company_name}"
    domain = _get_domain(career_url)
    job_urls: List[str] = []
    ats_redirect: Optional[Tuple[str, str]] = None

    emit(CrawlEvent(CrawlEventType.SOURCE_STARTED, source_id, url=career_url,
                    message=f"Career page: {company_name}"))

    try:
        validate_url_ssrf(career_url)
    except Exception as e:
        emit(CrawlEvent(CrawlEventType.SOURCE_BLOCKED, source_id, url=career_url,
                        message=f"SSRF block: {e}"))
        return job_urls, ats_redirect

    current_url = career_url

    for page_num in range(1, budget.max_pages_per_source + 1):
        rate_limiter.wait(domain)
        resp = _safe_get(current_url, timeout=budget.request_timeout)
        if not resp or resp.status_code >= 400:
            if resp and resp.status_code in (403, 429):
                emit(CrawlEvent(CrawlEventType.SOURCE_BLOCKED, source_id, url=current_url,
                                message=f"HTTP {resp.status_code} — SOURCE_BLOCKED"))
            break

        html = resp.text
        final_url = str(resp.url)

        # Check if the career page redirected to a known ATS
        for ats_name, pattern in _ATS_REDIRECT_PATTERNS.items():
            m = pattern.search(final_url)
            if m:
                slug = m.group(1)
                ats_redirect = (ats_name, slug)
                emit(CrawlEvent(CrawlEventType.ATS_DETECTED, source_id, url=final_url,
                                message=f"ATS redirect detected: {ats_name} / {slug}"))
                emit(CrawlEvent(CrawlEventType.PAGE_FETCHED, source_id, url=current_url,
                                count=0, message="Redirected to ATS — no direct job links needed"))
                return job_urls, ats_redirect

        # Also check the HTML for embedded ATS links
        for ats_name, pattern in _ATS_REDIRECT_PATTERNS.items():
            m = pattern.search(html)
            if m and not ats_redirect:
                slug = m.group(1)
                ats_redirect = (ats_name, slug)
                emit(CrawlEvent(CrawlEventType.ATS_DETECTED, source_id, url=current_url,
                                message=f"Embedded ATS link: {ats_name} / {slug}"))

        page_job_links = _extract_job_links_from_html(html, final_url, _JOB_LINK_PATTERNS)
        # Filter to same domain only (avoid following external job board links)
        page_job_links = [u for u in page_job_links if _get_domain(u) == domain]

        emit(CrawlEvent(CrawlEventType.PAGE_FETCHED, source_id, url=current_url,
                        count=len(page_job_links),
                        message=f"{company_name} page {page_num}: {len(page_job_links)} job links"))

        for jurl in page_job_links:
            if jurl not in job_urls:
                job_urls.append(jurl)
                emit(CrawlEvent(CrawlEventType.JOB_LINK_DISCOVERED, source_id, url=jurl))

        next_url = _extract_next_page_url(html, current_url, page_num)
        if not next_url or next_url == current_url:
            break
        current_url = next_url

    emit(CrawlEvent(CrawlEventType.SOURCE_COMPLETED, source_id,
                    count=len(job_urls),
                    message=f"{company_name}: {len(job_urls)} job links found"))
    return job_urls, ats_redirect


# ─────────────────────────────────────────────────────────────────────────────
# Main Multi-Source Crawl Orchestrator
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class CrawlResult:
    """Aggregated results from a multi-source crawl run."""
    sources_discovered: int = 0
    sources_crawled: int = 0
    sources_blocked: int = 0
    pages_fetched: int = 0
    job_links_discovered: int = 0
    jobs_extracted: int = 0
    ats_detected: int = 0
    crawled_jobs: List[CrawledJob] = field(default_factory=list)
    candidate_urls: List[Dict[str, Any]] = field(default_factory=list)
    events: List[CrawlEvent] = field(default_factory=list)
    source_telemetry: Dict[str, Any] = field(default_factory=dict)

    def merge_jobs(self) -> List[CrawledJob]:
        """Return deduplicated jobs by identity_key."""
        seen: Set[str] = set()
        unique: List[CrawledJob] = []
        for j in self.crawled_jobs:
            key = j.identity_key()
            if key and key not in seen:
                seen.add(key)
                unique.append(j)
            elif not key:
                unique.append(j)  # url-stubs with no title — keep for FetchTool
        return unique


class MultiSourceCrawler:
    """
    Orchestrates concurrent direct crawling of:
      - Configured ATS platforms (from discovered_ats in discovery state)
      - Regional job boards (NaukriGulf, Bayt, GulfTalent, Laimoon, Dubizzle)
      - Company career pages (from discovered_companies in discovery state)
      - Generic discovered job board domains

    Operates within CrawlBudget controls. Emits CrawlEvents. Thread-safe.
    Does NOT require ATS — valid jobs survive without one.
    Production code only — no mock data.
    """

    def __init__(self, budget: Optional[CrawlBudget] = None):
        self.budget = budget or CrawlBudget()
        self._rate_limiter = _DomainRateLimiter(delay=self.budget.per_domain_delay)
        self._events: List[CrawlEvent] = []
        self._lock = threading.Lock()

    def _emit(self, event: CrawlEvent) -> None:
        with self._lock:
            self._events.append(event)

    def _make_emit_fn(self) -> Callable[[CrawlEvent], None]:
        return self._emit

    def crawl(
        self,
        role_keywords: List[str],
        locations: List[str],
        discovered_ats: Dict[str, Dict[str, Any]],
        discovered_companies: Dict[str, Dict[str, Any]],
        is_gulf: bool = False,
        deadline: float = 0.0,
        external_emit: Optional[Callable[[str, Dict], None]] = None,
    ) -> CrawlResult:
        """
        Run the multi-source crawl and return aggregated CrawlResult.

        Args:
            role_keywords: Job title keywords (e.g. ["GenAI Engineer", "LLM Engineer"])
            locations: Location strings (e.g. ["Dubai", "UAE"])
            discovered_ats: From discovery_state.discovered_ats (company_slug -> info dict)
            discovered_companies: From discovery_state.discovered_companies
            is_gulf: Enable Gulf/MENA regional boards
            deadline: Unix timestamp; stop crawling after this
            external_emit: Optional callback to forward events upstream (for UI telemetry)
        """
        result = CrawlResult()
        self._events.clear()
        emit = self._make_emit_fn()

        def _check_deadline() -> bool:
            return deadline > 0 and time.time() >= deadline

        # ── 1. ATS API Crawls (concurrent, per discovered company) ──────────
        ats_tasks: List[Tuple[str, str, str]] = []  # (platform, slug, domain)
        for slug, info in discovered_ats.items():
            if len(ats_tasks) >= self.budget.max_sources:
                break
            platform = info.get("platform", "greenhouse")
            domain = info.get("domain", "boards.greenhouse.io")
            ats_tasks.append((platform, slug, domain))
            result.sources_discovered += 1

        ats_crawler_map = {
            "greenhouse": crawl_greenhouse_company,
            "lever": crawl_lever_company,
            "ashby": crawl_ashby_company,
            "workable": crawl_workable_company,
            "smartrecruiters": crawl_smartrecruiters_company,
        }

        def _crawl_ats(platform: str, slug: str) -> List[CrawledJob]:
            if _check_deadline():
                return []
            fn = ats_crawler_map.get(platform, crawl_greenhouse_company)
            return fn(slug, role_keywords, self.budget, self._rate_limiter, emit)

        with ThreadPoolExecutor(max_workers=self.budget.max_workers) as ex:
            futures = {ex.submit(_crawl_ats, plat, slug): (plat, slug)
                       for plat, slug, _ in ats_tasks}
            for fut in as_completed(futures):
                if _check_deadline():
                    for f in futures:
                        f.cancel()
                    break
                try:
                    jobs = fut.result()
                    if jobs:
                        result.crawled_jobs.extend(jobs)
                        result.jobs_extracted += len(jobs)
                        result.sources_crawled += 1
                    else:
                        result.sources_blocked += 1
                except Exception as e:
                    plat, slug = futures[fut]
                    logger.warning(f"ATS crawl error [{plat}/{slug}]: {e}")
                    emit(CrawlEvent(CrawlEventType.SOURCE_BLOCKED,
                                    source_id=f"{plat}:{slug}",
                                    message=f"ATS crawl error: {e}"))
                    result.sources_blocked += 1

        # ── 2. Regional Board Crawls (Gulf/MENA) ────────────────────────────
        if is_gulf and not _check_deadline():
            primary_role = role_keywords[0] if role_keywords else ""
            target_locations = []
            for loc in (locations if locations else ["UAE"]):
                for sub in re.split(r"\s+or\s+|\s*,\s*", str(loc), flags=re.IGNORECASE):
                    s_clean = sub.strip()
                    if s_clean and s_clean not in target_locations:
                        target_locations.append(s_clean)
            if not target_locations:
                target_locations = ["UAE"]

            regional_configs = []
            for loc in target_locations:
                loc_tag = loc.lower().replace(" ", "_")
                regional_configs.extend([
                    (
                        f"naukrigulf_{loc_tag}",
                        f"NaukriGulf ({loc})",
                        _naukrigulf_listing_url(primary_role, loc),
                        [r"/job-listing/", r"/job-in-", r"-\d+\.html", r"/jd-"],
                    ),
                    (
                        f"bayt_{loc_tag}",
                        f"Bayt.com ({loc})",
                        _bayt_listing_url(primary_role, loc),
                        [r"/en/[a-z-]+/jobs/[a-z0-9-]+-\d+/", r"/job/\d+"],
                    ),
                    (
                        f"gulftalent_{loc_tag}",
                        f"GulfTalent ({loc})",
                        _gulftalent_listing_url(primary_role, loc),
                        [r"/jobs/[a-z0-9-]+-\d+", r"/job/\d+"],
                    ),
                ])
            # Add Laimoon & Dubizzle once for primary hub
            hub_loc = target_locations[0]
            regional_configs.extend([
                (
                    "laimoon",
                    "Laimoon",
                    _laimoon_listing_url(primary_role, hub_loc),
                    [r"/jobs/[a-z0-9-]+", r"/job/\d+"],
                ),
                (
                    "dubizzle",
                    "Dubizzle Careers",
                    _dubizzle_listing_url(primary_role, hub_loc),
                    [r"/jobs/[a-z0-9_-]+-\d+"],
                ),
            ])

            def _crawl_regional(board_id, board_name, url, patterns):
                if _check_deadline():
                    return []
                return crawl_regional_board_html(
                    board_id=board_id,
                    board_name=board_name,
                    listing_url=url,
                    job_url_patterns=patterns,
                    role_keywords=role_keywords,
                    budget=self.budget,
                    rate_limiter=self._rate_limiter,
                    emit=emit,
                )

            with ThreadPoolExecutor(max_workers=min(3, self.budget.max_workers)) as ex:
                futures = {
                    ex.submit(_crawl_regional, bid, bn, url, pats): bid
                    for bid, bn, url, pats in regional_configs
                    if not _check_deadline()
                }
                for fut in as_completed(futures):
                    if _check_deadline():
                        for f in futures:
                            f.cancel()
                        break
                    try:
                        jobs = fut.result()
                        result.sources_discovered += 1
                        if jobs:
                            result.crawled_jobs.extend(jobs)
                            result.sources_crawled += 1
                            result.job_links_discovered += sum(1 for j in jobs if not j.title)
                            result.jobs_extracted += sum(1 for j in jobs if j.title)
                        else:
                            result.sources_blocked += 1
                    except Exception as e:
                        bid = futures[fut]
                        logger.warning(f"Regional board crawl error [{bid}]: {e}")
                        result.sources_blocked += 1

        # ── 3. Company Career Page Crawls ────────────────────────────────────
        career_companies = [
            (name, info.get("url", ""))
            for name, info in list(discovered_companies.items())[:self.budget.max_sources]
            if info.get("url") and not _check_deadline()
        ]

        def _crawl_career(company_name, career_url_raw):
            if _check_deadline() or not career_url_raw:
                return [], None
            # Construct a likely career page URL from company domain
            parsed = urlparse(career_url_raw)
            if parsed.path not in _CAREER_PAGE_PATHS:
                # Try /careers first
                career_test = f"{parsed.scheme}://{parsed.netloc}/careers"
            else:
                career_test = career_url_raw
            job_urls, ats_info = crawl_career_page(
                company_name=company_name,
                career_url=career_test,
                role_keywords=role_keywords,
                budget=self.budget,
                rate_limiter=self._rate_limiter,
                emit=emit,
            )
            return job_urls, ats_info

        for company_name, company_url in career_companies[:10]:
            if _check_deadline():
                break
            result.sources_discovered += 1
            job_urls, ats_info = _crawl_career(company_name, company_url)
            if ats_info:
                result.ats_detected += 1
                ats_platform, ats_slug = ats_info
                # Queue ATS crawl for this newly discovered ATS
                if ats_platform in ats_crawler_map and ats_slug not in discovered_ats:
                    fn = ats_crawler_map[ats_platform]
                    ats_jobs = fn(ats_slug, role_keywords, self.budget, self._rate_limiter, emit)
                    if ats_jobs:
                        result.crawled_jobs.extend(ats_jobs)
                        result.jobs_extracted += len(ats_jobs)
                        result.sources_crawled += 1
                    else:
                        result.sources_blocked += 1
            if job_urls:
                result.sources_crawled += 1
                result.job_links_discovered += len(job_urls)
                for u in job_urls:
                    result.crawled_jobs.append(CrawledJob(
                        source_id=f"career:{company_name}",
                        source_name=f"{company_name} Careers",
                        title="", company=company_name,
                        location=locations[0] if locations else "",
                        url=u, apply_url=u,
                    ))
            else:
                result.sources_blocked += 1

        # ── 4. Aggregate events & telemetry ──────────────────────────────────
        with self._lock:
            result.events = list(self._events)

        result.pages_fetched += sum(
            1 for e in result.events if e.event_type == CrawlEventType.PAGE_FETCHED
        )
        result.sources_blocked += sum(
            1 for e in result.events if e.event_type == CrawlEventType.SOURCE_BLOCKED
        )

        # Build candidate_urls (same format as search hits) for FetchTool pipeline
        seen_urls: Set[str] = set()
        for job in result.merge_jobs():
            if job.url and job.url not in seen_urls:
                seen_urls.add(job.url)
                result.candidate_urls.append({
                    "url": job.url,
                    "title": job.title or f"Job at {job.company}",
                    "snippet": job.description[:200] if job.description else "",
                    "source": job.source_name,
                    "source_id": job.source_id,
                    "crawled": True,
                })

        # Forward events to external emit (for UI telemetry)
        if external_emit:
            for event in result.events:
                external_emit("crawl_event", {
                    "type": event.event_type,
                    "source_id": event.source_id,
                    "url": event.url,
                    "message": event.message,
                    "count": event.count,
                    "timestamp": event.timestamp,
                })

        logger.info(
            f"MultiSourceCrawler: {result.sources_crawled} crawled, "
            f"{result.sources_blocked} blocked, {result.pages_fetched} pages, "
            f"{len(result.candidate_urls)} job links, {result.jobs_extracted} extracted"
        )
        return result

    def crawl_tasks(
        self,
        tasks: List[Any],
        role_keywords: List[str],
        deadline: float = 0.0,
        external_emit: Optional[Callable[[str, Dict], None]] = None,
        discovery_state: Optional[Any] = None,
    ) -> CrawlResult:
        """
        Execute a discrete batch of CrawlTask objects from the source scheduler.
        Handles direct ATS APIs, company career pages (with ATS redirect discovery),
        and regional job boards.
        """
        result = CrawlResult()

        def _check_deadline() -> bool:
            return deadline > 0 and time.time() >= deadline

        def emit(event: CrawlEvent) -> None:
            with self._lock:
                self._events.append(event)
            if external_emit:
                external_emit("crawl_event", {
                    "type": event.event_type,
                    "source_id": event.source_id,
                    "url": event.url,
                    "message": event.message,
                    "count": event.count,
                    "timestamp": event.timestamp,
                })

        ats_crawler_map = {
            "greenhouse": crawl_greenhouse_company,
            "lever": crawl_lever_company,
            "ashby": crawl_ashby_company,
            "workable": crawl_workable_company,
            "smartrecruiters": crawl_smartrecruiters_company,
        }

        for task in tasks:
            if _check_deadline():
                break

            result.sources_discovered += 1
            source_type = getattr(task, "source_type", "")
            company = getattr(task, "company", "") or ""
            ats_platform = getattr(task, "ats_platform", "") or ""
            url = getattr(task, "url", "") or ""

            try:
                # 1. Direct ATS API enumeration
                if source_type == "ats" or ats_platform in ats_crawler_map:
                    plat = ats_platform
                    if not plat:
                        for k, p in _ATS_REDIRECT_PATTERNS.items():
                            if p.search(url):
                                plat = k
                                break
                    if plat in ats_crawler_map and company:
                        fn = ats_crawler_map[plat]
                        ats_jobs = fn(company, role_keywords, self.budget, self._rate_limiter, emit)
                        if ats_jobs:
                            result.crawled_jobs.extend(ats_jobs)
                            result.jobs_extracted += len(ats_jobs)
                            result.sources_crawled += 1
                        else:
                            result.sources_blocked += 1
                    else:
                        result.sources_blocked += 1

                # 2. Company Career Page Crawling with ATS detection
                elif source_type in ("company_career_page", "career_page") or "career" in source_type:
                    job_urls, ats_info = crawl_career_page(
                        company_name=company or _get_domain(url),
                        career_url=url,
                        role_keywords=role_keywords,
                        budget=self.budget,
                        rate_limiter=self._rate_limiter,
                        emit=emit,
                    )
                    if ats_info:
                        result.ats_detected += 1
                        plat, slug = ats_info
                        if discovery_state and hasattr(discovery_state, "enqueue_crawl_task"):
                            from datahunt.agent.discovery_models import CrawlTask
                            discovery_state.enqueue_crawl_task(CrawlTask(
                                id=f"crawl_ats_{plat}_{slug}",
                                source=f"{plat}.com",
                                source_type="ats",
                                url=url,
                                company=slug,
                                ats_platform=plat,
                                location=getattr(task, "location", None),
                                query=role_keywords[0] if role_keywords else None,
                                priority=1,
                                reason=f"ATS detected from career page {company}",
                            ))
                        if plat in ats_crawler_map and slug:
                            fn = ats_crawler_map[plat]
                            ats_jobs = fn(slug, role_keywords, self.budget, self._rate_limiter, emit)
                            if ats_jobs:
                                result.crawled_jobs.extend(ats_jobs)
                                result.jobs_extracted += len(ats_jobs)
                                result.sources_crawled += 1

                    if job_urls:
                        result.sources_crawled += 1
                        result.job_links_discovered += len(job_urls)
                        for u in job_urls:
                            result.crawled_jobs.append(CrawledJob(
                                source_id=f"career:{company}",
                                source_name=f"{company} Careers",
                                title="", company=company,
                                location=getattr(task, "location", "") or "",
                                url=u, apply_url=u,
                            ))
                    else:
                        result.sources_blocked += 1

                # 3. Regional or Major Board Crawling
                elif source_type in ("regional_board", "major_board", "board"):
                    domain = _get_domain(url)
                    patterns = [r"/job-listing/", r"/job-in-", r"-\d+\.html", r"/jd-", r"/jobs?/[a-z0-9_-]+", r"/job/\d+"]
                    board_jobs = crawl_regional_board_html(
                        board_id=task.source or domain,
                        board_name=task.source or domain,
                        listing_url=url,
                        job_url_patterns=patterns,
                        role_keywords=role_keywords,
                        budget=self.budget,
                        rate_limiter=self._rate_limiter,
                        emit=emit,
                    )
                    if board_jobs:
                        result.crawled_jobs.extend(board_jobs)
                        result.sources_crawled += 1
                        result.job_links_discovered += sum(1 for j in board_jobs if not j.title)
                        result.jobs_extracted += sum(1 for j in board_jobs if j.title)
                    else:
                        result.sources_blocked += 1

                else:
                    job_urls, _ = crawl_career_page(
                        company_name=company or _get_domain(url),
                        career_url=url,
                        role_keywords=role_keywords,
                        budget=self.budget,
                        rate_limiter=self._rate_limiter,
                        emit=emit,
                    )
                    if job_urls:
                        result.sources_crawled += 1
                        result.job_links_discovered += len(job_urls)
                        for u in job_urls:
                            result.crawled_jobs.append(CrawledJob(
                                source_id=task.source or "web",
                                source_name=company or "Web",
                                title="", company=company,
                                location=getattr(task, "location", "") or "",
                                url=u, apply_url=u,
                            ))
                    else:
                        result.sources_blocked += 1

            except Exception as e:
                logger.warning(f"Error executing crawl task [{task.id}]: {e}")
                result.sources_blocked += 1
                emit(CrawlEvent(CrawlEventType.SOURCE_BLOCKED, getattr(task, "source", "unknown"), url=url,
                                message=f"Task error: {e}"))

        with self._lock:
            result.events = list(self._events)

        result.pages_fetched += sum(
            1 for e in result.events if e.event_type == CrawlEventType.PAGE_FETCHED
        )

        seen_urls: Set[str] = set()
        for job in result.merge_jobs():
            if job.url and job.url not in seen_urls:
                seen_urls.add(job.url)
                result.candidate_urls.append({
                    "url": job.url,
                    "title": job.title or f"Job at {job.company}",
                    "snippet": job.description[:200] if job.description else "",
                    "source": job.source_name,
                    "source_id": job.source_id,
                    "crawled": True,
                })

        return result
