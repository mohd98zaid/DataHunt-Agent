import hashlib
import json
import re
import time
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse, urljoin
import httpx
from bs4 import BeautifulSoup

_fetch_cache: dict = {}
_fetch_cache_lock = threading.Lock()
_CACHE_TTL = 300  # 5 minutes within a run


from datahunt.config import settings
from datahunt.errors import DataHuntError, ErrorCode
from datahunt.logger import logger
from datahunt.models import RetrievalStatus, SourceDocument
from datahunt.policy import validate_url_ssrf, check_domain_policy
from datahunt.tools.base import ToolResult

def clean_html_to_text(html_content: str) -> tuple[str, str]:
    """
    Parse HTML, extract title, preserve technical code fences, and extract clean text.
    Strips promotional sidebars, cookie banners, navigation menus, and advertisements.
    Returns (title, clean_text).
    """
    soup = BeautifulSoup(html_content, "html.parser")
    
    # Extract OpenGraph and metadata tags before decomposing
    meta_info = {}
    for meta in soup.find_all("meta"):
        prop = (meta.get("property") or meta.get("name") or "").lower()
        cnt = (meta.get("content") or "").strip()
        if not cnt:
            continue
        if prop in ("og:title", "twitter:title") and "title" not in meta_info:
            meta_info["title"] = cnt
        elif prop in ("og:description", "twitter:description", "description") and "description" not in meta_info:
            meta_info["description"] = cnt
        elif prop in ("og:url", "twitter:url") and "url" not in meta_info:
            meta_info["url"] = cnt

    # Extract title
    title = ""
    if soup.title and soup.title.string:
        title = soup.title.string.strip()
    elif meta_info.get("title"):
        title = meta_info["title"]
    elif soup.find("h1"):
        title = soup.find("h1").get_text().strip()

    # Extract JSON-LD schema (e.g. JobPosting, datePosted) before decomposing scripts
    structured_snippets = []
    for s in soup.find_all("script", type="application/ld+json"):
        content = (s.string or s.get_text() or "").strip()
        if content and any(k in content for k in ("JobPosting", "datePosted", "hiringOrganization")):
            structured_snippets.append(content)

    # 1. Decompose non-content and clutter tags
    for tag in soup(["script", "style", "nav", "footer", "header", "noscript", "svg", "iframe", "aside", "form"]):
        tag.decompose()

    # 2. Decompose elements matching ad, sidebar, cookie, and marketing clutter
    clutter_pattern = re.compile(
        r"(sidebar|ad-|ads-|advertisement|banner|newsletter|cookie|popup|modal|social-share|share-buttons|author-box|related-posts|comments|menu-main|navbar)",
        re.I
    )
    for el in soup.find_all(attrs={"class": clutter_pattern}):
        el.decompose()
    for el in soup.find_all(attrs={"id": clutter_pattern}):
        el.decompose()

    # 3. Preserve code snippets in markdown code fences before text extraction
    for pre in soup.find_all("pre"):
        code_text = pre.get_text().strip()
        if code_text:
            pre.replace_with(f"\n\n```python\n{code_text}\n```\n\n")

    # 4. Target primary content container if available
    main_el = soup.find(["main", "article"]) or soup.find(attrs={"role": "main"}) or soup.find(class_=re.compile(r"(content|markdown-body|documentation|post-content)", re.I))
    target_soup = main_el if (main_el and len(main_el.get_text().strip()) > 200) else soup

    text = target_soup.get_text(separator="\n")
    # Normalize multiple linebreaks and spaces
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    cleaned_text = "\n".join(lines)

    metadata_lines = []
    if meta_info:
        for k, v in meta_info.items():
            metadata_lines.append(f"{k.upper()}: {v}")
    metadata_header = ""
    if metadata_lines:
        metadata_header = "--- STRUCTURED PAGE METADATA ---\n" + "\n".join(metadata_lines) + "\n--- END PAGE METADATA ---\n\n"

    if structured_snippets:
        schema_header = "--- STRUCTURED JOB SCHEMA (JSON-LD) ---\n" + "\n".join(structured_snippets[:3]) + "\n--- END STRUCTURED SCHEMA ---\n\n"
        cleaned_text = schema_header + metadata_header + cleaned_text
    elif metadata_header:
        cleaned_text = metadata_header + cleaned_text

    return title, cleaned_text


def is_actual_job_posting(doc: "SourceDocument") -> tuple[bool, str]:
    """
    Step 15-16: Detect whether a fetched document is an actual job posting.
    Rejects: category listings, login pages, expired postings, 404s, and error surfaces.
    """
    text = (doc.extracted_text or "").lower()
    title = (doc.title or "").lower()

    if len(text.strip()) < 120:
        return False, "Page content too sparse / empty"

    # 1. Error / Auth Rejections
    error_signals = (
        "404 not found", "page not found", "error 404", "access denied",
        "attention required! | cloudflare", "enable javascript", "please sign in",
        "log in to continue", "login to view", "session expired"
    )
    if any(sig in title for sig in error_signals) or any(sig in text[:400] for sig in error_signals):
        return False, "Page is an error, access denied, or login gate"

    # 2. Expired / Inactive Postings
    expired_signals = (
        "this job has expired", "job is no longer available",
        "this position has been closed", "position has been filled",
        "applications are now closed", "no longer accepting applications",
        "this listing has expired"
    )
    if any(sig in text for sig in expired_signals):
        return False, "Job posting has expired or closed"

    # 3. Aggregator Category / Search Result Form Rejection
    # If it has more than 10 separate job links and no primary role description
    if text.count("view job") > 8 or text.count("apply on company site") > 6:
        return False, "Page is an aggregator search listing, not an individual job posting"

    # 4. Positive Signals
    if "jobposting" in text or "dateposted" in text:
        return True, "Valid job posting (structured schema detected)"

    job_keywords = (
        "responsibilities", "requirements", "qualifications",
        "about the role", "who you are", "what you'll do", "what you will do",
        "apply now", "submit application", "apply for this job", "apply for this role",
        "compensation", "salary", "benefits", "years of experience"
    )
    positives = sum(1 for kw in job_keywords if kw in text)
    if positives >= 2:
        return True, f"Valid job posting ({positives} job content signals matched)"

    return True, "Assumed valid candidate document"


def fetch_greenhouse_jobs_api(company_slug: str, run_id: str) -> "ToolResult":
    """
    Fetch job listings from Greenhouse's public board JSON API.
    Bypasses JavaScript-rendered SPA board pages completely.
    Tries the standard endpoint first, then the EU endpoint for EMEA companies.

    Returns a ToolResult with data=SourceDocument where extracted_text is
    structured job listings in markdown format ready for deterministic extraction.
    """
    retrieved_at = datetime.now(timezone.utc).isoformat()
    endpoints = [
        f"https://boards-api.greenhouse.io/v1/boards/{company_slug}/jobs?content=true",
        f"https://boards-api.eu.greenhouse.io/v1/boards/{company_slug}/jobs?content=true",
    ]
    headers = {
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    }

    for api_url in endpoints:
        try:
            with httpx.Client(timeout=10.0, headers=headers, follow_redirects=True) as client:
                resp = client.get(api_url)
                if resp.status_code != 200:
                    continue
                data = resp.json()
                jobs = data.get("jobs", [])
                if not jobs:
                    continue

                # Convert job list to structured markdown for extraction
                md_lines = [f"# {company_slug.replace('-', ' ').title()} — Open Positions\n"]
                for job in jobs[:50]:
                    title = job.get("title", "")
                    job_url = job.get("absolute_url", "")
                    location = ""
                    loc_data = job.get("location", {})
                    if isinstance(loc_data, dict):
                        location = loc_data.get("name", "")
                    elif isinstance(loc_data, str):
                        location = loc_data
                    dept = ""
                    depts = job.get("departments", [])
                    if depts and isinstance(depts[0], dict):
                        dept = depts[0].get("name", "")
                    updated_at = job.get("updated_at", "")

                    md_lines.append(f"## {title}")
                    md_lines.append(f"Location: {location}")
                    if dept:
                        md_lines.append(f"Department: {dept}")
                    if updated_at:
                        md_lines.append(f"Posted: {updated_at}")
                    md_lines.append(f"Apply: {job_url}")
                    md_lines.append("")

                extracted_text = "\n".join(md_lines)
                content_hash = hashlib.sha256(extracted_text.encode()).hexdigest()
                board_url = f"https://boards.greenhouse.io/{company_slug}"
                doc = SourceDocument(
                    run_id=run_id,
                    requested_url=board_url,
                    final_url=api_url,
                    canonical_url=board_url,
                    domain=f"boards.greenhouse.io",
                    http_status=200,
                    content_type="application/json",
                    content_hash=content_hash,
                    title=f"{company_slug.replace('-', ' ').title()} Jobs",
                    extracted_text=extracted_text,
                    text_truncated=0,
                    policy_flags=["greenhouse_json_api"],
                    retrieval_status=RetrievalStatus.FETCHED,
                    retrieved_at=retrieved_at,
                )
                return ToolResult(success=True, data=doc)
        except Exception as e:
            logger.debug(f"Greenhouse JSON API failed for {company_slug} at {api_url}: {e}")
            continue

    return ToolResult(
        success=False,
        error_code=ErrorCode.FETCH_BLOCKED.value,
        error_message=f"Greenhouse JSON API unavailable for company slug: {company_slug}",
    )


def _is_greenhouse_board_index(url: str) -> str:
    """
    Detect if a URL is a Greenhouse board index page (not a specific job page).
    Returns the company slug if it's a board index, or empty string if it's a specific job.
    A board index has the form: boards.greenhouse.io/<slug> or job-boards.greenhouse.io/<slug>
    without a /jobs/<id> path segment.
    """
    parsed = urlparse(url.lower())
    hostname = parsed.hostname or ""
    path = parsed.path.rstrip("/")
    query = parsed.query

    if "greenhouse.io" not in hostname:
        return ""
    # Reject error pages
    if "error=true" in query:
        return ""
    # Specific job pages have /jobs/<numeric_id> or ?gh_jid=<id>
    if re.search(r"/jobs/\d+", path) or re.search(r"gh_jid=\d+", query):
        return ""
    # Board index pages: /boards.greenhouse.io/<slug> or /job-boards.greenhouse.io/<slug>
    segments = [s for s in path.split("/") if s]
    # A board-index URL has exactly 1 path segment (the company slug), possibly with /embed or /embed/job_board
    if segments and not re.search(r"/jobs/", path):
        slug = segments[0]
        # Exclude embed params — /embed/job_board is still a board index
        slug = slug.replace("embed", "").strip("-_")
        if slug:
            return slug
    return ""


_PREFETCHED_JOB_DOCS: Dict[str, tuple[str, str]] = {}


def store_prefetched_job_document(url: str, title: str, text: str) -> None:
    """
    Store rich job document content fetched directly from public APIs (e.g. RemoteOK, Remotive).
    Allows FetchTool to retrieve full job text with 100% reliability and zero HTTP 403 blocks.
    """
    if url and text:
        _PREFETCHED_JOB_DOCS[url.strip()] = (title.strip(), text.strip())


class FetchTool:
    name = "fetch_page"
    description = "Safely fetch a public web page with strict SSRF defenses and size limits."


    def __init__(
        self,
        connect_timeout: float = 4.0,
        read_timeout: float = 8.0,
        total_timeout: float = 12.0,
        max_bytes: int = 2_000_000,
        max_redirects: int = 3,
        mock_responses: Optional[Dict[str, str]] = None
    ):
        self.connect_timeout = connect_timeout
        self.read_timeout = read_timeout
        self.total_timeout = total_timeout
        self.max_bytes = max_bytes
        self.max_redirects = max_redirects
        self.mock_responses = mock_responses or {}

    def execute(
        self,
        url: str,
        run_id: str = "run_default",
        allowed_domains: Optional[List[str]] = None,
        blocked_domains: Optional[List[str]] = None,
        render_js: bool = False
    ) -> ToolResult:
        retrieved_at = datetime.now(timezone.utc).isoformat()

        # Route Greenhouse board-index URLs to public JSON API (avoids JS/SPA fetching empty pages)
        gh_slug = _is_greenhouse_board_index(url)
        if gh_slug:
            logger.info(f"Routing Greenhouse board index '{url}' to JSON API for slug '{gh_slug}'")
            return fetch_greenhouse_jobs_api(gh_slug, run_id)

        # Check pre-fetched API job documents (e.g. from RemoteOK, Remotive)
        if url in _PREFETCHED_JOB_DOCS:
            cached_title, cached_text = _PREFETCHED_JOB_DOCS[url]
            content_hash = hashlib.sha256(cached_text.encode("utf-8")).hexdigest()
            doc = SourceDocument(
                run_id=run_id,
                requested_url=url,
                final_url=url,
                canonical_url=url,
                domain=urlparse(url).netloc,
                http_status=200,
                content_type="text/markdown",
                content_hash=content_hash,
                title=cached_title,
                extracted_text=cached_text[:settings.MAX_EXTRACTED_CHARS],
                text_truncated=1 if len(cached_text) > settings.MAX_EXTRACTED_CHARS else 0,
                policy_flags=["prefetched_api_document"],
                retrieval_status=RetrievalStatus.FETCHED,
                retrieved_at=retrieved_at
            )
            return ToolResult(success=True, data=doc)

        # Check mock responses first (for fast unit testing and offline runs)
        if url in self.mock_responses:

            mock_html = self.mock_responses[url]
            title, text = clean_html_to_text(mock_html)
            content_hash = hashlib.sha256(mock_html.encode("utf-8")).hexdigest()
            doc = SourceDocument(
                run_id=run_id,
                requested_url=url,
                final_url=url,
                canonical_url=url,
                domain=urlparse(url).netloc,
                http_status=200,
                content_type="text/html",
                content_hash=content_hash,
                title=title,
                extracted_text=text[:settings.MAX_EXTRACTED_CHARS],
                text_truncated=1 if len(text) > settings.MAX_EXTRACTED_CHARS else 0,
                retrieval_status=RetrievalStatus.FETCHED,
                retrieved_at=retrieved_at
            )
            return ToolResult(success=True, data=doc)

        current_url = url
        redirect_count = 0
        policy_flags = []

        # Check in-memory URL cache before making a live HTTP request
        cache_key = url
        with _fetch_cache_lock:
            cached = _fetch_cache.get(cache_key)
            if cached and time.time() - cached['ts'] < _CACHE_TTL:
                logger.debug(f"Cache hit for {url}")
                return cached['result']

        try:
            while redirect_count <= self.max_redirects:
                # 1. Strict SSRF check before resolving or connecting to target
                cleaned_url, validated_ip = validate_url_ssrf(current_url)
                hostname = urlparse(cleaned_url).hostname
                check_domain_policy(hostname, allowed_domains, blocked_domains)

                # 2. Perform safe HTTP request with modern desktop browser headers
                timeout = httpx.Timeout(self.total_timeout, connect=self.connect_timeout, read=self.read_timeout)
                headers = {
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
                    "Accept-Language": "en-US,en;q=0.9",
                    "Sec-Ch-Ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
                    "Sec-Ch-Ua-Mobile": "?0",
                    "Sec-Ch-Ua-Platform": '"Windows"',
                    "Sec-Fetch-Dest": "document",
                    "Sec-Fetch-Mode": "navigate",
                    "Sec-Fetch-Site": "none",
                    "Sec-Fetch-User": "?1",
                    "Upgrade-Insecure-Requests": "1"
                }
                
                with httpx.Client(timeout=timeout, follow_redirects=False, headers=headers) as client:
                    response = client.get(cleaned_url)

                    # Check for redirect
                    if response.is_redirect:
                        redirect_count += 1
                        location = response.headers.get("Location")
                        if not location:
                            raise DataHuntError(
                                ErrorCode.FETCH_BLOCKED,
                                user_message="Redirect missing Location header.",
                                operator_message=f"Missing Location on redirect from {cleaned_url}"
                            )
                        current_url = urljoin(cleaned_url, location)
                        logger.info(f"Following redirect {redirect_count}/{self.max_redirects} to: {current_url}")

                        # Intercept Greenhouse ?error=true redirects — the job is on an EU or
                        # private board. Extract the company slug and fetch via the JSON API instead.
                        parsed_redir = urlparse(current_url)
                        if (
                            "greenhouse.io" in (parsed_redir.hostname or "")
                            and "error=" in (parsed_redir.query or "")
                        ):
                            # Extract company slug from the path: /canonical → "canonical"
                            path_segs = [s for s in parsed_redir.path.strip("/").split("/") if s]
                            if path_segs:
                                gh_slug = path_segs[0]
                                logger.info(
                                    f"Greenhouse redirect error intercepted for '{gh_slug}'. "
                                    f"Routing to JSON API."
                                )
                                return fetch_greenhouse_jobs_api(gh_slug, run_id)
                        continue


                    # Check status code
                    status_code = response.status_code
                    if status_code >= 400:
                        # Attempt TLS browser impersonation fallback on blocked or anti-bot endpoints
                        if status_code in (401, 403, 429):
                            try:
                                from ddgs import DDGS
                                ext_data = DDGS().extract(cleaned_url)
                                ext_content = ext_data.get("content") or ""
                                if ext_content and len(ext_content.strip()) > 80:
                                    logger.info(f"Successfully bypassed HTTP {status_code} block on {cleaned_url} via TLS browser impersonation")
                                    first_line = ext_content.splitlines()[0].strip("# ") if ext_content.splitlines() else "Document"
                                    title = first_line[:120]
                                    content_hash = hashlib.sha256(ext_content.encode("utf-8")).hexdigest()
                                    truncated = 1 if len(ext_content) > settings.MAX_EXTRACTED_CHARS else 0
                                    bounded_text = ext_content[:settings.MAX_EXTRACTED_CHARS]
                                    doc = SourceDocument(
                                        run_id=run_id,
                                        requested_url=url,
                                        final_url=cleaned_url,
                                        canonical_url=cleaned_url,
                                        domain=urlparse(cleaned_url).netloc,
                                        http_status=200,
                                        content_type="text/markdown",
                                        content_hash=content_hash,
                                        title=title,
                                        extracted_text=bounded_text,
                                        text_truncated=truncated,
                                        policy_flags=["tls_impersonation_fallback"],
                                        retrieval_status=RetrievalStatus.FETCHED,
                                        retrieved_at=retrieved_at
                                    )
                                    return ToolResult(success=True, data=doc)
                            except Exception as fb_exc:
                                logger.debug(f"TLS extract fallback failed on {cleaned_url}: {fb_exc}")

                        return ToolResult(
                            success=False,
                            error_code=ErrorCode.FETCH_BLOCKED.value if status_code in (401, 403) else ErrorCode.FETCH_TIMEOUT.value,
                            error_message=f"HTTP {status_code} from source",
                            metadata={"status_code": status_code, "url": cleaned_url}
                        )

                    # Check Content-Type
                    content_type = response.headers.get("Content-Type", "").lower()
                    if not any(t in content_type for t in ("text/", "html", "json", "xml")):
                        return ToolResult(
                            success=False,
                            error_code=ErrorCode.FETCH_UNSUPPORTED_TYPE.value,
                            error_message=f"Unsupported content type: {content_type}",
                            metadata={"content_type": content_type}
                        )

                    # Read body with max_bytes enforcement
                    body_bytes = response.content
                    if len(body_bytes) > self.max_bytes:
                        body_bytes = body_bytes[:self.max_bytes]
                        policy_flags.append("truncated_bytes")

                    content_hash = hashlib.sha256(body_bytes).hexdigest()
                    html_text = body_bytes.decode(response.encoding or "utf-8", errors="replace")
                    title, clean_text = clean_html_to_text(html_text)
                    
                    truncated = 1 if len(clean_text) > settings.MAX_EXTRACTED_CHARS else 0
                    bounded_text = clean_text[:settings.MAX_EXTRACTED_CHARS]

                    doc = SourceDocument(
                        run_id=run_id,
                        requested_url=url,
                        final_url=cleaned_url,
                        canonical_url=cleaned_url,
                        domain=urlparse(cleaned_url).netloc,
                        http_status=status_code,
                        content_type=content_type,
                        content_hash=content_hash,
                        title=title,
                        extracted_text=bounded_text,
                        text_truncated=truncated,
                        policy_flags=policy_flags,
                        retrieval_status=RetrievalStatus.FETCHED,
                        retrieved_at=retrieved_at
                    )
                    result = ToolResult(success=True, data=doc)
                    with _fetch_cache_lock:
                        _fetch_cache[cache_key] = {'result': result, 'ts': time.time()}
                        if len(_fetch_cache) > 200:
                            oldest = min(_fetch_cache.items(), key=lambda x: x[1]['ts'])
                            del _fetch_cache[oldest[0]]
                    return result

            return ToolResult(
                success=False,
                error_code=ErrorCode.FETCH_BLOCKED.value,
                error_message="Too many redirects.",
                metadata={"url": url}
            )

        except DataHuntError as dhe:
            return ToolResult(
                success=False,
                error_code=dhe.code.value,
                error_message=dhe.user_message,
                metadata={"url": url, "operator_message": dhe.operator_message}
            )
        except httpx.TimeoutException as te:
            return ToolResult(
                success=False,
                error_code=ErrorCode.FETCH_TIMEOUT.value,
                error_message=f"Connection timed out: {te}",
                metadata={"url": url}
            )
        except Exception as e:
            logger.error(f"Error fetching URL {url}: {e}")
            return ToolResult(
                success=False,
                error_code=ErrorCode.INTERNAL_ERROR.value,
                error_message=f"Failed to fetch source: {e}",
                metadata={"url": url}
            )


def clear_fetch_cache() -> None:
    """Clear the in-run fetch cache (useful for testing and between runs)."""
    global _fetch_cache
    with _fetch_cache_lock:
        _fetch_cache = {}
