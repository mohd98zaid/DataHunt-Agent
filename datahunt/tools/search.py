import hashlib
import re
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Protocol
from urllib.parse import urlparse, unquote
import httpx
from bs4 import BeautifulSoup
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock

from datahunt.errors import DataHuntError, ErrorCode
from datahunt.logger import logger
from datahunt.policy import check_domain_policy
from datahunt.tools.base import ToolResult

def unwrap_tracking_redirect_url(url: str) -> str:
    """
    Unwrap search engine tracking redirects (e.g. google.com/goto?url=... or google.com/url?q=...)
    to the direct destination URL before normalization and crawling.
    """
    if not url or not isinstance(url, str):
        return ""
    u = url.strip()
    try:
        from urllib.parse import urlparse, parse_qs, unquote
        parsed = urlparse(u)
        hostname = (parsed.hostname or "").lower()
        if "google." in hostname or hostname.endswith(".google.com"):
            if any(p in parsed.path for p in ("/url", "/goto", "/search")):
                qs = parse_qs(parsed.query)
                for param in ("url", "q", "target"):
                    vals = qs.get(param)
                    if vals and vals[0]:
                        unq = unquote(vals[0])
                        if unq.startswith("http://") or unq.startswith("https://"):
                            return unq
    except Exception:
        pass
    return u


DISALLOWED_JOB_DOMAINS = {
    "instagram.com", "facebook.com", "tiktok.com", "youtube.com",
    "twitter.com", "x.com", "reddit.com", "pinterest.com", "threads.net",
    "snapchat.com", "investing.com", "vision2030.ai", "wikipedia.org",
    "quora.com", "medium.com", "news.google.com"
}


def canonicalize_url(url: str) -> str:
    """
    Produce a canonical, normalized URL for deduplication.
    - Strips fragments
    - Unwraps Google tracking redirects
    - Strips marketing/tracking query parameters (utm_*, fbclid, gclid, etc.)
    - Normalizes scheme and host to lowercase
    - Normalizes trailing slashes for paths
    - Preserves job IDs for known ATS (Greenhouse, Lever, Ashby, Workable, etc.)
    """
    if not url or not isinstance(url, str):
        return ""
    try:
        from urllib.parse import urlparse, parse_qsl, urlencode
        url = unwrap_tracking_redirect_url(url)
        parsed = urlparse(url.strip())
        scheme = "https" if parsed.scheme.lower() in ("http", "https") else (parsed.scheme.lower() or "https")
        netloc = parsed.netloc.lower()
        if netloc.startswith("www."):
            netloc = netloc[4:]

        path = parsed.path
        if len(path) > 1 and path.endswith("/"):
            path = path.rstrip("/")

        TRACKING_PARAMS = {
            "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
            "fbclid", "gclid", "ref", "ref_id", "source", "trk", "trackingid",
            "gh_src", "lever-source", "ashby_jid", "utm", "from", "sp", "src"
        }
        query_parts = []
        if parsed.query:
            params = parse_qsl(parsed.query, keep_blank_values=False)
            filtered = [(k, v) for k, v in params if k.lower() not in TRACKING_PARAMS]
            if filtered:
                filtered.sort(key=lambda x: x[0])
                query_parts = urlencode(filtered)

        query = ("?" + query_parts) if query_parts else ""
        return f"{scheme}://{netloc}{path}{query}"
    except Exception:
        return url.strip().rstrip("/")


def is_ad_or_tracking_url(url: str) -> bool:
    """Detect search engine ad clicks, sponsored tracking redirects, and affiliate trackers."""
    if not url or not isinstance(url, str):
        return False
    u = url.lower()
    return any(p in u for p in (
        "/aclick?", "/aclick/", "bing.com/aclick", "googleadservices.com",
        "doubleclick.net", "adclick.", "/adclick?", "adservice.google",
        "ad.doubleclick", "syndicatedsearch.goog", "/pagead/", "clk.google"
    ))


def generate_job_fingerprint(company: str, title: str, location: str) -> str:
    """Generate a content identity fingerprint for cross-source job deduplication."""
    c = re.sub(r'[^a-z0-9]', '', (company or '').lower())
    t = re.sub(r'[^a-z0-9]', '', (title or '').lower())
    l = re.sub(r'[^a-z0-9]', '', (location or '').lower())
    return f"{c}::{t}::{l}"


def calculate_search_yield(total_hits: int, new_candidates: int) -> float:
    """
    Calculate the yield rate of a search iteration.
    Returns fraction of hits that became new candidates (0.0 to 1.0).
    """
    if total_hits <= 0:
        return 0.0
    return round(new_candidates / total_hits, 3)


SEARCH_TIER_PRIORITY = {
    1: "official_ats",      # Greenhouse, Lever, Ashby, Workable direct
    2: "regional_boards",   # Bayt, GulfTalent, NaukriGulf, GulfJobs
    3: "specialized",       # LinkedIn, Indeed (regional), Wellfound
    4: "general",           # DuckDuckGo/Google general search
    5: "secondary",         # Aggregators, other sources
}


def assign_source_tier(url: str) -> int:
    """Classify a URL into a search source tier (1=best, 5=worst)."""
    url_lower = url.lower()
    if any(d in url_lower for d in ["greenhouse.io", "lever.co", "ashbyhq.com", "workable.com", "smartrecruiters.com"]):
        return 1
    if any(d in url_lower for d in ["bayt.com", "gulftalent.com", "naukrigulf.com", "gulfjobs.com", "laimoon.com", "akhtaboot.com", "foundit.ae"]):
        return 2
    if any(d in url_lower for d in ["linkedin.com", "indeed.com", "glassdoor.com", "wellfound.com"]):
        return 3
    if any(d in url_lower for d in ["remoteok.com", "weworkremotely.com", "himalayas.app"]):
        return 3
    return 4


class SearchHit:
    def __init__(
        self,
        url: str,
        title: str,
        snippet: str,
        source_domain: str,
        retrieved_at: Optional[str] = None,
        provider_citation_id: Optional[str] = None
    ):
        self.url = url
        self.title = title
        self.snippet = snippet
        self.source_domain = source_domain
        self.retrieved_at = retrieved_at or datetime.now(timezone.utc).isoformat()
        self.provider_citation_id = provider_citation_id

    def to_dict(self) -> Dict[str, Any]:
        return {
            "url": self.url,
            "title": self.title,
            "snippet": self.snippet,
            "source_domain": self.source_domain,
            "retrieved_at": self.retrieved_at,
            "provider_citation_id": self.provider_citation_id,
        }

PRIMARY_ATS_DOMAINS = [
    "boards.greenhouse.io",
    "job-boards.greenhouse.io",
    "job-boards.eu.greenhouse.io",
    "jobs.lever.co",
    "jobs.ashbyhq.com",
    "apply.workable.com",
    "jobs.smartrecruiters.com",
    "careers.breezy.hr",
    "wd1.myworkdayjobs.com",
    "wd3.myworkdayjobs.com",
    "jobs.jobvite.com",
    "apply.bamboohr.com",
]

# Regional job boards organized by geography — used for broad internet sweep
REGIONAL_JOB_BOARDS: Dict[str, List[str]] = {
    # Gulf / MENA — primary focus
    "gulf": [
        "bayt.com", "naukrigulf.com", "gulftalent.com", "gulfjobs.com",
        "dubizzle.com/jobs", "laimoon.com", "akhtaboot.com",
        "foundit.ae", "monstergulf.com", "gulfnews.com/jobs",
        "khaleejtimes.com/jobs", "dubaicareers.ae", "qureos.com",
    ],
    # UAE-specific company career pages (top employers)
    "uae_careers": [
        "emiratesgroupcareers.com", "careers.etisalat.ae",
        "jobs.emaar.ae", "careers.adnoc.ae", "mubadala.com/careers",
        "dpworld.com/careers", "adib.ae/careers", "bankfab.com/careers",
        "mashreqbank.com/careers", "du.ae/careers",
    ],
    # Saudi Arabia
    "saudi": [
        "bayt.com", "naukrigulf.com", "gulftalent.com",
        "mihnati.com", "tanqeeb.com", "wadaef.sa",
        "aramco.com/careers", "sabic.com/careers", "neom.com/careers",
    ],
    # India
    "india": [
        "naukri.com", "foundit.in", "shine.com", "timesjobs.com",
        "instahyre.com", "cutshort.io", "wellfound.com",
    ],
    # Global / Remote
    "global": [
        "wellfound.com", "remoteok.com", "weworkremotely.com",
        "himalayas.app", "arc.dev", "justremote.co",
    ],
    # Europe
    "europe": [
        "eurojobs.com", "eurotechjobs.com", "berlinstartupjobs.com",
        "justjoin.it", "landing.jobs",
    ],
    # US & Canada
    "us": [
        "builtin.com", "dice.com", "simplyhired.com",
    ],
}

# Signals in job snippets that indicate it's a real job post (not a blog/guide)
JOB_CONTENT_SIGNALS = frozenset([
    "apply", "hiring", "career", "open role", "we are looking",
    "responsibilities", "requirements", "qualifications", "join us",
    "salary", "compensation", "full-time", "full time", "remote",
    "position", "vacancy", "vacancies", "opportunity", "engineer",
    "developer", "scientist", "manager", "analyst", "architect",
])

LOW_QUALITY_RESEARCH_DOMAINS = [
    "quora.com",
    "pinterest.com",
    "facebook.com",
    "instagram.com",
    "tiktok.com",
    "twitter.com",
    "x.com",
    "slideshare.net",
]


def is_valid_job_url(url: str) -> bool:
    """
    Validate that a URL points to an individual direct job opening or authoritative career page,
    strictly rejecting login portals, candidate accounts, expired/closed pages, and root search index forms.
    Supports ATS portals (Greenhouse, Lever, Ashby, Workable) and regional job boards (Bayt, Naukrigulf, LinkedIn, Indeed).
    """
    if not url or not isinstance(url, str):
        return False
    u = unwrap_tracking_redirect_url(url.strip())
    u_lower = u.lower()

    # Reject fast-drop signals for closed, 404, or expired job postings
    if any(sig in u_lower for sig in (
        "error=true", "expired_jd_redirect", "not_found=true",
        "job-closed", "job_closed", "position-closed", "job-expired", "status=expired"
    )):
        return False

    # 1. Reject authentication & login endpoints across all domains
    if any(auth_part in u_lower for auth_part in (
        "/users/sign_in", "/users/login", "/sign_in", "/signin", "/login",
        "/auth/", "/session", "/saml", "/sso", "login.", "signin.", "auth."
    )):
        return False

    # 1.5. Reject candidate profiles, applicant CVs, and post-apply confirmation pages
    if any(profile_part in u_lower for profile_part in (
        "/people/", "/candidates/", "/talent/", "/profiles/", "/profile/",
        "/resume/", "/resumes/", "/cv/", "/members/", "/confirmation",
        "/submitted", "/thank-you", "/thanks", "/applied"
    )):
        return False

    # 2. Reject internal recruiter / employee onboarding subdomains & candidate profile subdomains
    try:
        parsed = urlparse(u_lower)
    except Exception:
        return False

    hostname = (parsed.hostname or "").lower()
    if hostname.startswith("www."):
        hostname = hostname[4:]
    path = parsed.path or ""

    # Reject local/internal addresses in production discovery
    if "localhost" in hostname or hostname in ("127.0.0.1", "0.0.0.0"):
        return False

    # Reject social media, video, news, and non-job research domains
    if any(hostname == d or hostname.endswith("." + d) for d in DISALLOWED_JOB_DOMAINS):
        return False

    # Reject explicit 404 paths
    if "/404" in path:
        return False

    if hostname.startswith("people.") or hostname.startswith("talent.") or hostname.startswith("candidate.") or hostname.startswith("candidates."):
        return False

    if hostname in (
        "app2.greenhouse.io", "my.greenhouse.io", "onboarding.greenhouse.io",
        "app.greenhouse.io", "harvest.greenhouse.io"
    ):
        return False

    # Reject empty or root index paths
    if path in ("", "/"):
        return False

    # 3. Greenhouse ATS Validation:
    # Must have a job ID or specific job path (e.g. /jobs/<id> or ?gh_jid=<id> or /job?gh_jid=<id>)
    if "greenhouse.io" in hostname:
        has_job_id = bool(
            re.search(r"/jobs/\d+", path)
            or re.search(r"gh_jid=\d+", parsed.query)
            or re.search(r"/job\b", path)
            or "boards-api" in hostname
            or path.startswith("/v1/boards")
        )
        if not has_job_id:
            return False

    # 4. Lever ATS Validation:
    # Must have a job UUID or alphanumeric slug in path: /<company>/<job_id> (at least 2 segments)
    if "lever.co" in hostname:
        path_segments = [seg for seg in path.strip("/").split("/") if seg]
        if len(path_segments) < 2:
            return False
        if path_segments[0] in ("signin", "login"):
            return False

    # 5. Ashby ATS Validation:
    # Must have a job ID or posting slug after company: jobs.ashbyhq.com/<company>/<job_id>
    if "ashbyhq.com" in hostname:
        path_segments = [seg for seg in path.strip("/").split("/") if seg]
        if len(path_segments) < 2:
            return False

    # 6. Workable ATS Validation:
    # Must have /j/<job_id> and not be a not_found redirect
    if "workable.com" in hostname:
        if "/j/" not in path or "not_found=true" in u_lower:
            return False

    # 6.1 SmartRecruiters ATS Validation:
    # Must have at least 2 segments in path: /<company>/<job_id_or_slug>
    if "smartrecruiters.com" in hostname:
        path_segments = [seg for seg in path.strip("/").split("/") if seg]
        if len(path_segments) < 2:
            return False
        if path_segments[0] in ("signin", "login"):
            return False

    # 6.2 Workday ATS Validation:
    # Must have /job/ or /jobs/ in path
    if "myworkdayjobs.com" in hostname:
        if "/job/" not in path and "/jobs/" not in path:
            return False

    # 6.3 BambooHR ATS Validation:
    # Must have /jobs/view.php, /careers/, /jobs/<id>, or id query param
    if "bamboohr.com" in hostname:
        has_bamboo_job = bool(
            "/jobs/view.php" in path
            or "/careers/" in path
            or re.search(r"/jobs/\d+", path)
            or re.search(r"\bid=\d+", parsed.query)
        )
        if not has_bamboo_job:
            return False

    # 7. Check for specific job posting patterns on aggregators and company sites
    is_specific_posting = bool(
        re.search(r"/(?:view|jobs?|job-listing|careers?|position|role)/[a-zA-Z0-9_\-]+", path)
        or re.search(r"\b(?:vjk|jk|jid|jl|jobid|job_id|gh_jid|joblistingid)=[a-zA-Z0-9_\-]+", parsed.query)
        or re.search(r"[-_/](?:jid|jl|job)[-_/]?\d+", path, re.IGNORECASE)
        or re.search(r"-\d+/?$", path)
        or re.search(r"/\d+/?$", path)
        or "viewjob" in path
        or "job-listings" in path
        or "-jid-" in path
        or "job-detail" in path
    )

    # 8. Aggregators: allow specific job detail pages with IDs, but reject generic empty search forms
    aggregator_domains = (
        # Gulf / MENA
        "bayt.com", "naukrigulf.com", "gulftalent.com", "gulfjobs.com",
        "laimoon.com", "akhtaboot.com", "foundit.ae", "monstergulf.com",
        "dubizzle.com", "gulfnews.com", "dubaicareers.ae", "qureos.com",
        "mihnati.com", "tanqeeb.com",
        # India
        "naukri.com", "foundit.in", "shine.com", "timesjobs.com",
        "instahyre.com", "cutshort.io",
        # Global aggregators
        "indeed.com", "glassdoor.com", "ziprecruiter.com", "monster.com",
        "simplyhired.com", "jooble.org", "talent.com", "remotedxb.com",
        "expertini.com", "adzuna.com", "adzuna.ae", "grabjobs.co",
        "careerjet.com", "careerjet.ae", "drjobs.ae", "recruit.net",
        # UK
        "totaljobs.com", "reed.co.uk",
        # Startup / remote
        "wellfound.com", "builtin.com", "dice.com", "himalayas.app",
        "arc.dev", "weworkremotely.com", "remoteok.com",
        # APAC
        "jobsdb.com", "jobstreet.com",
        # Europe
        "eurojobs.com", "eurotechjobs.com", "justjoin.it", "landing.jobs",
    )
    if any(hostname == ad or hostname.endswith("." + ad) for ad in aggregator_domains):
        if any(p in path for p in ("/search", "/skill", "/skills", "/category", "/browse", "/tags", "/jobs/")):
            # If path ends with -jobs or contains a count / aggregator pattern, reject
            if re.search(r"-\d+-jobs|jobs-in-|\b\d+-", path) or "-jobs" in path:
                return False
        if any(p in path for p in ("/search", "/skill", "/skills", "/category", "/browse", "/tags")):
            return False
        if "jobs-in" in path and not is_specific_posting:
            return False
        if not is_specific_posting:
            return False

    # Reject pure search result URLs for other domains if no job ID is present
    if any(p in path for p in ("/search/", "/skill/", "/skills/")) and not is_specific_posting:
        return False

    # 9. LinkedIn Jobs & Hiring Posts:
    # Accept /jobs/view/<id>, job slugs, and public hiring posts (/posts/, /feed/update/, /pulse/)
    # Reject generic search result listings (/jobs/search, /jobs/...-jobs, /jobs/collections)
    if "linkedin.com" in hostname:
        if "/in/" in path or "/company/" in path:
            return False  # Profile or company pages, not job listings
        if any(p in path for p in ("/posts/", "/feed/update/", "/pulse/")):
            return True
        if "/jobs/search" in path or "/jobs/collections" in path or "-jobs" in path:
            return False  # Aggregator search result listing, not an individual job
        if "/jobs/view/" in path or re.search(r"/jobs/[a-zA-Z0-9_\-]+", path):
            return True
        return False

    return True

def generate_ats_queries(role_query: str) -> List[str]:
    """
    Generate high-yield ATS and direct search queries targeting verified career pages.
    Combines primary ATS endpoints with semantic hiring keywords for maximum recall.
    Preserves location/city/country in the cleaned query for geographic accuracy.
    """
    # Step 1: Strip experience, tenure, and salary phrases that pollute title search
    exp_pattern = r"\b(?:with\s+)?(?:\d+\s*-\s*\d+|\d+\+?)\s*(?:years?|yrs?)(?:\s*(?:of)?\s*experience)?\b"
    cleaned = re.sub(exp_pattern, "", role_query, flags=re.IGNORECASE)
    cleaned = re.sub(r"\b(?:0\s*-\s*\d+|\d+\s*-\s*\d+)\s*(?:years?|yrs?)\b", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\b(?:salary\s*(?:above|over|below|between|up to)?\s*[\$₹€£AED\d,\.\s]+(?:lpa|k|usd|aed)?)\b", "", cleaned, flags=re.IGNORECASE)

    # Strip only verb/action filler words
    filler_pattern = r"\b(find|search|get|me|job|jobs|hiring|openings?|roles?|positions?|latest|0sec|0-sec|fresh|recent|newest|greenhouse|lever|ashby|workable|ats)\b"
    cleaned = re.sub(filler_pattern, "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\b(at|with|by|near)\b", " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if not cleaned:
        cleaned = role_query.strip()

    # Step 2: Detect location phrase ("in Dubai", "in Saudi or UAE", etc.)
    loc_match = re.search(r"\bin\s+([A-Za-z\s,]+?)(?=\s+(?:with|salary|having|\d|\Z))", role_query, re.IGNORECASE)
    location_phrase = loc_match.group(1).strip() if loc_match else ""
    if not location_phrase:
        loc_fallback = re.search(r"\b(Saudi Arabia|Saudi|Riyadh|Jeddah|KSA|UAE|Dubai|Abu Dhabi|Qatar|Doha|Kuwait|Bahrain|Oman|India|Bangalore|Bengaluru|Mumbai|Pune|Hyderabad|Delhi|London|UK|United Kingdom|Singapore|Germany|Berlin|Canada|Toronto|USA|US)\b", role_query, re.IGNORECASE)
        if loc_fallback:
            location_phrase = loc_fallback.group(1).strip()

    # Role-only portion (without "in <City>") for site: queries that filter by job location field
    role_only = re.sub(r"\bin\s+[A-Za-z\s,]+$", "", cleaned, flags=re.IGNORECASE).strip()
    for loc_token in ("saudi arabia", "saudi", "riyadh", "jeddah", "ksa", "uae", "dubai", "abu dhabi", "qatar", "india", "london", "uk", "usa", "us"):
        role_only = re.sub(rf"\b{loc_token}\b", "", role_only, flags=re.IGNORECASE).strip()
    role_only = re.sub(r"\s+", " ", role_only).strip() or cleaned

    loc_lower = (location_phrase or cleaned).lower()
    is_gulf = any(k in loc_lower for k in ("dubai", "uae", "abu dhabi", "gulf", "qatar", "saudi", "riyadh", "jeddah", "ksa", "mena"))

    queries = []

    # If targeting Gulf / Middle East, place regional ATS and targeted location queries at Tier 1
    if is_gulf:
        queries.extend([
            f'site:gulftalent.com "{role_only}"',
            f'site:bayt.com "{role_only}"',
            f'site:naukrigulf.com "{role_only}"',
            f'site:boards.greenhouse.io Dubai "{role_only}"',
            f'site:boards.greenhouse.io Riyadh "{role_only}"',
            f'site:boards.greenhouse.io UAE "{role_only}"',
            f'site:boards.greenhouse.io Saudi "{role_only}"',
            f'site:jobs.lever.co Dubai "{role_only}"',
            f'site:jobs.lever.co Riyadh "{role_only}"',
            f'site:apply.workable.com Dubai "{role_only}"',
            f'site:job-boards.eu.greenhouse.io {cleaned}',
        ])
    elif any(k in loc_lower for k in ("india", "bangalore", "bengaluru", "mumbai", "pune", "hyderabad", "delhi")):
        queries.extend([
            f'site:naukri.com "{role_only}"',
            f'site:instahyre.com "{role_only}"',
            f'site:foundit.in "{role_only}"',
            f'site:boards.greenhouse.io ("India" OR "Bangalore" OR "Remote") "{role_only}"',
        ])
    elif any(k in loc_lower for k in ("london", "uk", "united kingdom", "manchester", "birmingham")):
        queries.extend([
            f'site:jobs.lever.co "{role_only}" United Kingdom',
            f'site:boards.greenhouse.io ("London" OR "UK") "{role_only}"',
        ])

    # Standard primary ATS portals
    queries.extend([
        f"site:boards.greenhouse.io {cleaned}",
        f"site:job-boards.greenhouse.io {cleaned}",
        f"site:jobs.lever.co {cleaned}",
        f"site:jobs.ashbyhq.com {cleaned}",
        f"site:apply.workable.com {cleaned}",
        f"site:jobs.smartrecruiters.com {cleaned}",
        f"{role_only} {location_phrase} (site:greenhouse.io OR site:lever.co OR site:ashbyhq.com OR site:workable.com)".strip(),
        f"{cleaned} jobs apply careers",
        f"{cleaned} open role apply now",
    ])

    return queries


def generate_broad_job_queries(role_query: str) -> List[str]:
    """
    Generate broad internet sweep queries that go beyond ATS boards:
    - Regional job portals (Bayt, NaukriGulf, GulfTalent, Dubizzle, Laimoon, Indeed, GlassDoor, etc.)
    - LinkedIn Jobs via site: search
    - Company career page discovery ("<role> <city> careers site:amazon.jobs" etc.)
    - Google Jobs structured format
    - Direct company hiring pages for known regional employers

    Auto-detects region from location phrase and selects the relevant board cluster.
    """
    exp_pattern = r"\b(?:with\s+)?(?:\d+\s*-\s*\d+|\d+\+?)\s*(?:years?|yrs?)(?:\s*(?:of)?\s*experience)?\b"
    cleaned = re.sub(exp_pattern, "", role_query, flags=re.IGNORECASE)
    cleaned = re.sub(r"\b(?:0\s*-\s*\d+|\d+\s*-\s*\d+)\s*(?:years?|yrs?)\b", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\b(?:salary\s*(?:above|over|below|between|up to)?\s*[\$₹€£AED\d,\.\s]+(?:lpa|k|usd|aed)?)\b", "", cleaned, flags=re.IGNORECASE)

    filler = r"\b(find|search|get|me|job|jobs|hiring|openings?|roles?|positions?|latest|fresh|newest)\b"
    cleaned = re.sub(filler, "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\b(at|with|by|near)\b", " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip() or role_query.strip()

    loc_match = re.search(r"\bin\s+([A-Za-z\s,]+?)(?=\s+(?:with|salary|having|\d|\Z))", role_query, re.IGNORECASE)
    location_phrase = loc_match.group(1).strip() if loc_match else ""
    if not location_phrase:
        loc_fallback = re.search(r"\b(Saudi Arabia|Saudi|Riyadh|Jeddah|KSA|UAE|Dubai|Abu Dhabi|Qatar|Doha|Kuwait|Bahrain|Oman|India|Bangalore|Bengaluru|Mumbai|Pune|Hyderabad|Delhi|London|UK|United Kingdom|Singapore|Germany|Berlin|Canada|Toronto|USA|US)\b", role_query, re.IGNORECASE)
        if loc_fallback:
            location_phrase = loc_fallback.group(1).strip()

    loc_lower = (location_phrase or cleaned).lower()

    role_only = re.sub(r"\bin\s+[A-Za-z\s,]+$", "", cleaned, flags=re.IGNORECASE).strip()
    for loc_token in ("saudi arabia", "saudi", "riyadh", "jeddah", "ksa", "uae", "dubai", "abu dhabi", "qatar", "india", "london", "uk", "usa", "us"):
        role_only = re.sub(rf"\b{loc_token}\b", "", role_only, flags=re.IGNORECASE).strip()
    role_only = re.sub(r"\s+", " ", role_only).strip() or cleaned

    queries: List[str] = []

    # ── Tier 1: Organic LinkedIn discovery ──
    queries += [
        f'"{role_only}" {location_phrase} linkedin.com jobs'.strip(),
        f'"{role_only}" {location_phrase} linkedin hiring apply'.strip(),
        f'site:linkedin.com/posts "hiring" "{role_only}" {location_phrase}'.strip(),
    ]

    # ── Tier 2: Region-specific boards ──
    is_gulf = any(k in loc_lower for k in ("dubai", "uae", "abu dhabi", "saudi", "riyadh", "jeddah", "ksa", "gulf", "qatar", "doha", "mena"))
    if is_gulf:
        queries += [
            f'site:gulftalent.com/uae/jobs "{role_only}"',
            f'site:gulftalent.com/saudi-arabia/jobs "{role_only}"',
            f'site:gulftalent.com "{role_only}"',
            f'"{role_only}" Dubai jobs',
            f'"{role_only}" Saudi Arabia jobs',
            f'site:naukrigulf.com "{role_only}"',
            f'site:bayt.com "{role_only}"',
            f'"{role_only}" Dubai UAE linkedin.com jobs',
            f'"{role_only}" Riyadh Saudi linkedin.com jobs',
            f'"{role_only}" Dubai UAE indeed.com',
            f'"{role_only}" Riyadh Saudi Arabia indeed.com',
            f'"{role_only}" Dubai UAE glassdoor.com',
            f'"{role_only}" Riyadh Saudi Arabia glassdoor.com',
            f'"{role_only}" Dubai Riyadh UAE Saudi apply careers 2026',
        ]
    elif any(k in loc_lower for k in ("india", "bangalore", "bengaluru", "mumbai", "hyderabad", "pune", "delhi", "chennai")):
        queries += [
            f"site:naukri.com {cleaned}",
            f"site:foundit.in {cleaned}",
            f"site:instahyre.com {role_only}",
            f"site:cutshort.io {role_only}",
            f"site:shine.com {cleaned}",
            f"{role_only} {location_phrase} hiring apply careers 2026",
        ]
    elif any(k in loc_lower for k in ("london", "uk", "manchester", "edinburgh", "bristol")):
        queries += [
            f"site:reed.co.uk {role_only}",
            f"site:totaljobs.com {role_only}",
            f"{role_only} UK London careers apply 2026",
        ]
    elif any(k in loc_lower for k in ("new york", "san francisco", "seattle", "austin", "boston", "us", "usa", "remote")):
        queries += [
            f"site:builtin.com {role_only}",
            f"site:dice.com {role_only}",
            f"site:wellfound.com {role_only}",
            f"{role_only} careers US remote apply 2026",
        ]
    elif any(k in loc_lower for k in ("singapore", "hong kong")):
        queries += [
            f"site:jobsdb.com {cleaned}",
            f"site:jobstreet.com {role_only}",
            f"{role_only} careers Singapore apply 2026",
        ]
    else:
        # Generic fallback — organic queries that DDG returns well
        queries += [
            f"{cleaned} careers apply now 2026",
            f"{cleaned} job opening apply glassdoor.com OR indeed.com",
        ]

    # ── Tier 3: Glassdoor organic (don't use site:, use organic to get salary data too) ──
    queries.append(f"{role_only} {location_phrase} glassdoor.com careers salary".strip())

    # ── Tier 4: Wellfound / AngelList (startups globally — does work with site:) ──
    queries.append(f"site:wellfound.com/jobs {role_only}")

    # ── Tier 5: Direct career page discovery sweep ──
    # Natural language company career page queries — avoids ATS boards to hit direct /careers pages
    queries.append(f'"{role_only}" {location_phrase} careers -site:greenhouse.io -site:lever.co -site:ashbyhq.com'.strip())
    queries.append(f"{role_only} {location_phrase} \"open positions\" OR \"job openings\" OR \"we are hiring\"".strip())
    queries.append(f"{role_only} {location_phrase} inurl:careers OR inurl:jobs apply".strip())


    # De-duplicate while preserving order
    seen: set = set()
    unique_queries: List[str] = []
    for q in queries:
        q = q.strip()
        if q and q not in seen:
            seen.add(q)
            unique_queries.append(q)

    return unique_queries


def generate_technical_research_queries(topic: str) -> List[str]:
    """
    Generate authoritative, multi-angle technical research queries targeting
    official documentation, GitHub repositories, and architectural specifications.
    Avoids restrictive exact quotes that cause search engines to return 0 hits.
    """
    clean = re.sub(r"\b(what is|how does|explain|research|analyze|give me|find|summary of|teardown)\b", "", topic, flags=re.IGNORECASE).strip()
    clean = re.sub(r"\s+", " ", clean).strip() or topic.strip()
    return [
        f"{clean} documentation architecture core concepts",
        f"{clean} official documentation github internals",
        f"{clean} design principles execution tutorial guide",
        f"{clean} benchmarks performance comparison tradeoffs",
        f"{clean} API reference specification implementation",
    ]

def generate_market_research_queries(topic: str) -> List[str]:
    """
    Generate authoritative market research queries.
    Distinguishes between:
    1) Financial / Equity / Stock Market Intelligence (e.g. NSE, BSE, Nifty, stocks, weekly performance)
    2) SaaS competitor pricing & feature comparison.
    """
    lower = topic.lower()
    is_equity_market = any(k in lower for k in ("stock", "stocks", "equity", "equities", "nse", "bse", "nifty", "sensex", "shares", "share price", "perform this week"))

    clean = re.sub(r"\b(find|search|analyze|compare|comparison of|pricing for|market analysis of|give|top|best)\b", "", topic, flags=re.IGNORECASE).strip()
    clean = re.sub(r"\s+", " ", clean).strip() or topic.strip()

    if is_equity_market:
        market_label = "NSE" if "nse" in lower or "nifty" in lower else ("BSE" if "bse" in lower else "stock market")
        return [
            f"{market_label} benchmark index weekly trend performance breadth",
            f"{market_label} top performing sectoral indices this week",
            f"{market_label} weekly gainers high volume breakout momentum stocks",
            f"{market_label} stocks RSI bullish breakout near 52 week high",
            f"{market_label} corporate announcements order wins earnings capex",
            f"{market_label} top stocks in news latest positive developments",
            f"{market_label} stocks technical setup moving average support",
            f"{market_label} stocks analyst targets and downside risks",
        ]

    return [
        f"{clean} pricing plans tiers per user month official",
        f"{clean} features comparison matrix alternatives vs",
        f"{clean} SaaS market share pricing breakdown",
        f"{clean} pros cons limitations enterprise reviews",
        f"{clean} g2.com OR capterra.com OR trustradius.com reviews",
    ]


class SearchProvider(Protocol):
    def search(
        self,
        query: str,
        limit: int = 10,
        page: int = 1,
        freshness_days: Optional[int] = None,
        allowed_domains: Optional[List[str]] = None,
        blocked_domains: Optional[List[str]] = None
    ) -> List[SearchHit]:
        ...

class DuckDuckGoSearchProvider:
    """
    Public web search provider using DuckDuckGo Lite endpoint (high-throughput,
    anti-bot resilient, zero JS) with fallback to DDGS and HTML endpoints.
    Includes circuit breaker cooldown when IP rate-limits or connection drops occur.
    """
    LITE_URL = "https://lite.duckduckgo.com/lite/"
    SEARCH_URL = "https://html.duckduckgo.com/html/"

    # Class-level circuit breaker shared across queries in the process
    _circuit_broken_until: float = 0.0
    _consecutive_errors: int = 0
    _ERROR_THRESHOLD: int = 2
    _COOLDOWN_SECONDS: float = 60.0

    @classmethod
    def trip_circuit_breaker(cls, reason: str = "") -> None:
        cls._consecutive_errors += 1
        if cls._consecutive_errors >= cls._ERROR_THRESHOLD:
            cls._circuit_broken_until = time.time() + cls._COOLDOWN_SECONDS
            logger.warning(
                f"DuckDuckGo rate-limiting/connection drops detected ({cls._consecutive_errors} consecutive failures; latest: {reason}). "
                f"Tripping circuit breaker for {cls._COOLDOWN_SECONDS}s to avoid pipeline freeze."
            )

    @classmethod
    def record_success(cls) -> None:
        cls._consecutive_errors = 0

    @classmethod
    def is_cooling_down(cls) -> bool:
        return time.time() < cls._circuit_broken_until

    def search(
        self,
        query: str,
        limit: int = 10,
        page: int = 1,
        freshness_days: Optional[int] = None,
        allowed_domains: Optional[List[str]] = None,
        blocked_domains: Optional[List[str]] = None
    ) -> List[SearchHit]:
        if self.is_cooling_down():
            logger.debug(f"DuckDuckGo circuit breaker active; skipping search for '{query}'")
            return []

        hits: List[SearchHit] = []
        timelimit = None
        if freshness_days is not None and "site:" not in query.lower() and " or " not in query.lower():
            if freshness_days <= 1:
                timelimit = "d"
            elif freshness_days <= 7:
                timelimit = "w"
            elif freshness_days <= 31:
                timelimit = "m"

        # 1. Primary: DuckDuckGo Lite endpoint (fastest, no JS bot challenge, reliable SERP)
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }
        for attempt in range(1):
            try:
                with httpx.Client(timeout=2.5, headers=headers, follow_redirects=True) as client:
                    post_data: Dict[str, str] = {"q": query}
                    if page > 1:
                        post_data["s"] = str((page - 1) * 30)
                    if timelimit:
                        post_data["df"] = timelimit
                    resp = client.post(self.LITE_URL, data=post_data)
                    if resp.status_code == 200:
                        soup = BeautifulSoup(resp.text, "html.parser")
                        link_elems = soup.find_all("a", class_="result-link")
                        snippet_elems = soup.find_all("td", class_="result-snippet")
                        for i, a_tag in enumerate(link_elems):
                            actual_url = a_tag.get("href", "").strip()
                            title = a_tag.get_text().strip()
                            snippet = snippet_elems[i].get_text().strip() if i < len(snippet_elems) else ""

                            if "uddg=" in actual_url:
                                m = re.search(r"uddg=([^&]+)", actual_url)
                                if m:
                                    actual_url = unquote(m.group(1))

                            if not actual_url.startswith("http") or "duckduckgo.com" in actual_url or is_ad_or_tracking_url(actual_url):
                                continue

                            domain = urlparse(actual_url).netloc.lower()
                            if any(bad in domain for bad in LOW_QUALITY_RESEARCH_DOMAINS):
                                continue

                            try:
                                check_domain_policy(domain, allowed_domains, blocked_domains)
                            except DataHuntError:
                                continue

                            hits.append(SearchHit(
                                url=actual_url,
                                title=title,
                                snippet=snippet,
                                source_domain=domain
                            ))
                            if len(hits) >= limit:
                                break
                        if hits:
                            self.record_success()
                            return hits
                        break  # Got clean 200 from Lite endpoint, don't retry same endpoint
                    elif resp.status_code in (403, 429):
                        self.trip_circuit_breaker(f"HTTP {resp.status_code}")
                        break
            except Exception as lite_err:
                err_str = str(lite_err).lower()
                is_drop = any(t in err_str for t in ("10054", "connection reset", "connection closed", "timed out", "timeout"))
                logger.debug(f"DuckDuckGo Lite attempt {attempt+1} error on '{query}': {lite_err}")
                if is_drop:
                    self.trip_circuit_breaker(str(lite_err))
                    break

        if self.is_cooling_down():
            return hits

        # 2. Secondary fallback: DDGS client
        try:
            from ddgs import DDGS
            ddgs_kwargs: Dict[str, Any] = {"max_results": limit}
            if timelimit:
                ddgs_kwargs["timelimit"] = timelimit

            with DDGS(timeout=3) as ddgs_client:
                raw_results = list(ddgs_client.text(query, **ddgs_kwargs))
            if raw_results:
                for item in raw_results:
                    url = item.get("href") or item.get("link") or ""
                    title = item.get("title") or ""
                    snippet = item.get("body") or item.get("snippet") or ""

                    if not url.startswith("http") or is_ad_or_tracking_url(url):
                        continue

                    domain = urlparse(url).netloc.lower()
                    if any(bad in domain for bad in LOW_QUALITY_RESEARCH_DOMAINS):
                        continue

                    try:
                        check_domain_policy(domain, allowed_domains, blocked_domains)
                    except DataHuntError:
                        continue

                    hits.append(SearchHit(
                        url=url,
                        title=title,
                        snippet=snippet,
                        source_domain=domain
                    ))
                    if len(hits) >= limit:
                        break
            if hits:
                self.record_success()
                return hits
        except Exception as ddg_err:
            err_str = str(ddg_err).lower()
            if any(t in err_str for t in ("10054", "connection reset", "timed out", "timeout", "ratelimit")):
                self.trip_circuit_breaker(str(ddg_err))
            logger.debug(f"DDGS search attempt on '{query}': {ddg_err}")

        if self.is_cooling_down():
            return hits

        # 3. Tertiary fallback: HTML endpoint
        try:
            with httpx.Client(timeout=2.5, headers=headers, follow_redirects=True) as client:
                post_data = {"q": query}
                if page > 1:
                    post_data["s"] = str((page - 1) * 30)
                if timelimit:
                    post_data["df"] = timelimit
                resp = client.post(self.SEARCH_URL, data=post_data)
                if resp.status_code == 200:
                    soup = BeautifulSoup(resp.text, "html.parser")
                    results = soup.find_all("div", class_="result")
                    for res in results:
                        title_elem = res.find("a", class_="result__a")
                        snippet_elem = res.find("a", class_="result__snippet")
                        if not title_elem:
                            continue
                        raw_href = title_elem.get("href", "")
                        title = title_elem.get_text().strip()
                        snippet = snippet_elem.get_text().strip() if snippet_elem else ""

                        actual_url = raw_href
                        if "uddg=" in raw_href:
                            match = re.search(r"uddg=([^&]+)", raw_href)
                            if match:
                                actual_url = unquote(match.group(1))

                        if not actual_url.startswith("http") or "duckduckgo.com/y.js" in actual_url or is_ad_or_tracking_url(actual_url):
                            continue

                        domain = urlparse(actual_url).netloc.lower()
                        if any(bad in domain for bad in LOW_QUALITY_RESEARCH_DOMAINS):
                            continue

                        try:
                            check_domain_policy(domain, allowed_domains, blocked_domains)
                        except DataHuntError:
                            continue

                        hits.append(SearchHit(
                            url=actual_url,
                            title=title,
                            snippet=snippet,
                            source_domain=domain
                        ))
                        if len(hits) >= limit:
                            break
                    if hits:
                        self.record_success()
                        return hits
                elif resp.status_code in (403, 429):
                    self.trip_circuit_breaker(f"HTTP {resp.status_code}")
        except Exception as e:
            err_str = str(e).lower()
            if any(t in err_str for t in ("10054", "connection reset", "timed out", "timeout")):
                self.trip_circuit_breaker(str(e))
            logger.warning(f"DuckDuckGo HTML search error: {e}")

        # If restrictive freshness filter returned 0 hits, retry without timelimit ONLY IF circuit is healthy
        if not hits and timelimit and not self.is_cooling_down():
            logger.info(f"Freshness filter '{timelimit}' returned 0 hits for '{query}', falling back to standard search.")
            return self.search(
                query=query,
                limit=limit,
                page=page,
                freshness_days=None,
                allowed_domains=allowed_domains,
                blocked_domains=blocked_domains
            )

        return hits


class BraveSearchProvider:
    """Brave Search API — free tier (2000 req/month), no JS bot blocks, real SERP results.
    Set BRAVE_API_KEY in .env to enable. Silently returns [] when key absent."""

    _API_URL = "https://api.search.brave.com/res/v1/web/search"

    def search(
        self,
        query: str,
        limit: int = 10,
        page: int = 1,
        freshness_days: Optional[int] = None,
        allowed_domains: Optional[List[str]] = None,
        blocked_domains: Optional[List[str]] = None,
    ) -> List[SearchHit]:
        from datahunt.config import settings
        api_key = getattr(settings, "BRAVE_API_KEY", "").strip()
        if not api_key:
            return []
        params: Dict[str, Any] = {
            "q": query,
            "count": min(limit, 20),
            "offset": max(0, (page - 1) * limit),
        }
        if freshness_days:
            params["freshness"] = "pd" if freshness_days <= 1 else ("pw" if freshness_days <= 7 else "pm")
        try:
            resp = httpx.get(
                self._API_URL,
                params=params,
                headers={
                    "Accept": "application/json",
                    "Accept-Encoding": "gzip",
                    "X-Subscription-Token": api_key,
                },
                timeout=10.0,
                follow_redirects=True,
            )
            resp.raise_for_status()
            hits: List[SearchHit] = []
            for item in resp.json().get("web", {}).get("results", []):
                url = canonicalize_url(item.get("url", ""))
                if not url or is_ad_or_tracking_url(url):
                    continue
                if allowed_domains and not any(d in url for d in allowed_domains):
                    continue
                if blocked_domains and any(d in url for d in blocked_domains):
                    continue
                hits.append(SearchHit(
                    url=url,
                    title=item.get("title", ""),
                    snippet=item.get("description", ""),
                    source_domain=urlparse(url).netloc,
                ))
            logger.info(f"Brave Search returned {len(hits)} hits for '{query}'")
            return hits
        except Exception as e:
            logger.debug(f"Brave search error for '{query}': {e}")
            return []


class SerperSearchProvider:
    """Serper.dev — Google Search API, 2500 free queries/month, no CC.
    Set SERPER_API_KEY in .env to enable. Silently returns [] when key absent."""

    _API_URL = "https://google.serper.dev/search"

    def search(
        self,
        query: str,
        limit: int = 10,
        page: int = 1,
        freshness_days: Optional[int] = None,
        allowed_domains: Optional[List[str]] = None,
        blocked_domains: Optional[List[str]] = None,
    ) -> List[SearchHit]:
        from datahunt.config import settings
        api_key = getattr(settings, "SERPER_API_KEY", "").strip()
        if not api_key:
            return []
        payload: Dict[str, Any] = {"q": query, "num": min(limit, 10), "page": page}
        if freshness_days:
            if freshness_days <= 1:
                payload["tbs"] = "qdr:d"
            elif freshness_days <= 7:
                payload["tbs"] = "qdr:w"
            elif freshness_days <= 45:
                payload["tbs"] = "qdr:m"
        try:
            resp = httpx.post(
                self._API_URL,
                json=payload,
                headers={"X-API-KEY": api_key, "Content-Type": "application/json"},
                timeout=10.0,
            )
            resp.raise_for_status()
            hits: List[SearchHit] = []
            for item in resp.json().get("organic", []):
                url = canonicalize_url(item.get("link", ""))
                if not url or is_ad_or_tracking_url(url):
                    continue
                if allowed_domains and not any(d in url for d in allowed_domains):
                    continue
                if blocked_domains and any(d in url for d in blocked_domains):
                    continue
                hits.append(SearchHit(
                    url=url,
                    title=item.get("title", ""),
                    snippet=item.get("snippet", ""),
                    source_domain=urlparse(url).netloc,
                ))
            logger.info(f"Serper returned {len(hits)} hits for '{query}'")
            return hits
        except Exception as e:
            logger.debug(f"Serper search error for '{query}': {e}")
            return []


class TavilySearchProvider:
    """Tavily Search — AI-optimized, 1000 free queries/month, no CC.
    Set TAVILY_API_KEY in .env to enable. Silently returns [] when key absent."""

    _API_URL = "https://api.tavily.com/search"

    def search(
        self,
        query: str,
        limit: int = 10,
        page: int = 1,
        freshness_days: Optional[int] = None,
        allowed_domains: Optional[List[str]] = None,
        blocked_domains: Optional[List[str]] = None,
    ) -> List[SearchHit]:
        from datahunt.config import settings
        api_key = getattr(settings, "TAVILY_API_KEY", "").strip()
        if not api_key:
            return []
        payload: Dict[str, Any] = {
            "api_key": api_key,
            "query": query,
            "max_results": min(limit, 10),
            "search_depth": "basic",
        }
        if allowed_domains:
            payload["include_domains"] = allowed_domains[:5]
        if blocked_domains:
            payload["exclude_domains"] = blocked_domains[:5]
        if freshness_days and freshness_days <= 30:
            payload["days"] = max(1, freshness_days)
        try:
            resp = httpx.post(self._API_URL, json=payload, timeout=12.0)
            resp.raise_for_status()
            hits: List[SearchHit] = []
            for item in resp.json().get("results", []):
                url = canonicalize_url(item.get("url", ""))
                if not url or is_ad_or_tracking_url(url):
                    continue
                hits.append(SearchHit(
                    url=url,
                    title=item.get("title", ""),
                    snippet=item.get("content", ""),
                    source_domain=urlparse(url).netloc,
                ))
            logger.info(f"Tavily returned {len(hits)} hits for '{query}'")
            return hits
        except Exception as e:
            logger.debug(f"Tavily search error for '{query}': {e}")
            return []


class MockSearchProvider:
    """Mock/offline search provider for testing and deterministic validation."""
    def __init__(self, predefined_hits: Optional[List[SearchHit]] = None):
        self.predefined_hits = predefined_hits or []

    def search(
        self,
        query: str,
        limit: int = 10,
        page: int = 1,
        freshness_days: Optional[int] = None,
        allowed_domains: Optional[List[str]] = None,
        blocked_domains: Optional[List[str]] = None
    ) -> List[SearchHit]:
        if self.predefined_hits:
            start_idx = (page - 1) * limit
            hits = self.predefined_hits[start_idx:start_idx + limit]
        else:
            q_low = query.lower()
            if any(k in q_low for k in ("job", "jobs", "hiring", "careers", "intern", "vacancy", "openings", "engineer", "developer")):
                if page == 1:
                    hits = [
                        SearchHit(
                            url="https://example.com/careers/ai-engineer-dubai",
                            title="Senior AI Engineer - Dubai, UAE",
                            snippet="Join our fast-growing AI team in Dubai. Requirements: Python, LLMs, Machine Learning. Posted 0 sec ago.",
                            source_domain="example.com"
                        ),
                        SearchHit(
                            url="https://example.com/careers/ml-ops-dubai",
                            title="Machine Learning Engineer - Dubai",
                            snippet="Example AI Ltd is hiring an ML engineer in Dubai. Apply online through our official portal.",
                            source_domain="example.com"
                        ),
                        SearchHit(
                            url="https://techjobs.ae/post/ai-researcher-101",
                            title="AI Researcher - Dubai Tech Hub",
                            snippet="Looking for an AI researcher with PhD or 3+ years experience. Competitive compensation in Dubai.",
                            source_domain="techjobs.ae"
                        )
                    ]
                else:
                    hits = [
                        SearchHit(
                            url=f"https://example.com/careers/lead-ai-engineer-page{page}",
                            title=f"Lead AI Engineer - Dubai (Page {page})",
                            snippet="Advanced LLM and Generative AI role in Dubai. Fast growing team.",
                            source_domain="example.com"
                        ),
                        SearchHit(
                            url=f"https://techjobs.ae/post/ai-specialist-{page}",
                            title=f"AI Systems Architect - Riyadh (Page {page})",
                            snippet="Riyadh office seeking AI systems architect with 2+ years experience.",
                            source_domain="techjobs.ae"
                        ),
                    ]
            else:
                hits = [
                    SearchHit(
                        url="https://docs.langchain.com/oss/python/learn",
                        title="LangChain Architecture & Core Primitives",
                        snippet="Comprehensive guide to LangChain architecture, LCEL runtime, Runnable protocol, and production agent orchestration.",
                        source_domain="docs.langchain.com"
                    ),
                    SearchHit(
                        url="https://github.com/langchain-ai/langchain",
                        title="LangChain: Building applications with LLMs through composability",
                        snippet="Official repository. LCEL expression language provides declarative runtime composition with streaming, batching, and async protocols.",
                        source_domain="github.com"
                    ),
                    SearchHit(
                        url="https://python.langchain.com/docs/concepts/lcel/",
                        title="LangChain Expression Language (LCEL) Concepts",
                        snippet="Detailed specification of LCEL design patterns, RunnablePassthrough, RunnableParallel, fallbacks, and execution lifecycle.",
                        source_domain="python.langchain.com"
                    )
                ]

        filtered = []
        for hit in hits:
            try:
                check_domain_policy(hit.source_domain, allowed_domains, blocked_domains)
                filtered.append(hit)
            except DataHuntError:
                continue
            if len(filtered) >= limit:
                break
        return filtered

class LiveJobBoardSearchProvider:
    """
    Direct high-throughput public job board provider querying public APIs (Remotive, RemoteOK)
    when standard SERP search engines are rate-limited, blocked, or unavailable.
    """
    def search(
        self,
        query: str,
        limit: int = 10,
        page: int = 1,
        freshness_days: Optional[int] = None,
        allowed_domains: Optional[List[str]] = None,
        blocked_domains: Optional[List[str]] = None
    ) -> List[SearchHit]:
        hits: List[SearchHit] = []
        q_clean = query.lower()
        search_terms = re.sub(r"site:[^\s]+", "", q_clean)
        search_terms = re.sub(r"[^a-zA-Z0-9\s]", " ", search_terms).strip()
        noise_words = {"job", "jobs", "hiring", "role", "roles", "position", "positions", "open", "senior", "lead", "apply", "urgent", "remote"}
        keywords = [w for w in search_terms.split() if w not in noise_words]
        primary_kw = keywords[0] if keywords else "software"

        # Extract search query tokens and detect domain constraints
        query_tokens = [w for w in search_terms.split() if len(w) >= 2 and w not in noise_words]
        ai_query_terms = {"genai", "ai", "llm", "rag", "gpt", "agent", "agents", "machine", "learning", "ml", "nlp"}
        has_ai_constraint = any(t in ai_query_terms for t in query_tokens)

        # 0. Check site: constraint: RemoteOK and Remotive only provide their own domains
        site_match = re.search(r"site:([^\s]+)", q_clean)
        if site_match:
            target_site = site_match.group(1).lower()
            if not any(b in target_site for b in ("remoteok", "remotive")):
                return []

        # 0b. Check regional location constraints: RemoteOK/Remotive only serve global remote tech roles
        regional_keywords = ("saudi", "uae", "dubai", "riyadh", "abu dhabi", "jeddah", "ksa", "gulf", "middle east")
        has_regional_constraint = any(rk in q_clean for rk in regional_keywords)
        if has_regional_constraint and not any(rem in q_clean for rem in ("remote", "anywhere", "worldwide")):
            return []

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Accept": "application/json, text/plain, */*"
        }

        # 1. RemoteOK Public API (highest reliability, rich markdown descriptions, non-blocking)
        try:
            rok_url = f"https://remoteok.com/api?tag={primary_kw}"
            with httpx.Client(timeout=4.0, headers=headers) as client:
                r = client.get(rok_url)
                if r.status_code == 200:
                    items = r.json()
                    for job in items:
                        if not isinstance(job, dict) or not job.get("position"):
                            continue
                        url = job.get("url", "")
                        title = job.get("position", "")
                        company = job.get("company", "")
                        desc = job.get("description", "")
                        domain = urlparse(url).netloc.lower()
                        try:
                            check_domain_policy(domain, allowed_domains, blocked_domains)
                        except DataHuntError:
                            continue
                        clean_desc = re.sub(r"<[^>]+>", " ", desc).strip()
                        combined_text = f"{title} {clean_desc}".lower()
                        title_lower = title.lower()

                        if has_ai_constraint:
                            has_ai_title = any(re.search(r'\b' + re.escape(t) + r'\b', title_lower) for t in ai_query_terms)
                            if not has_ai_title and not any(r in title_lower for r in ("data scientist", "data engineer", "machine learning", "deep learning", "nlp", "computer vision", "algorithm", "research scientist")):
                                continue
                        elif query_tokens:
                            meaningful = [t for t in query_tokens if t not in ("software", "engineer", "developer")]
                            if meaningful and not any(t in combined_text for t in meaningful):
                                continue

                        snippet_text = clean_desc[:250].strip()
                        hits.append(SearchHit(
                            url=url,
                            title=f"{title} at {company}",
                            snippet=snippet_text,
                            source_domain=domain
                        ))
                        try:
                            from datahunt.tools.fetch import store_prefetched_job_document
                            full_job_text = f"# {title} at {company}\n\nCompany: {company}\nPosition: {title}\nURL: {url}\n\n## Job Description\n{clean_desc}\n"
                            store_prefetched_job_document(url, f"{title} at {company}", full_job_text)
                        except Exception:
                            pass
                        if len(hits) >= limit:
                            break
        except Exception as e:
            logger.debug(f"RemoteOK search error on '{query}': {e}")

        # 2. Remotive Public API fallback
        if len(hits) < limit:
            try:
                remotive_url = f"https://remotive.com/api/remote-jobs?search={primary_kw}&limit={limit}"
                with httpx.Client(timeout=4.0, headers=headers) as client:
                    r = client.get(remotive_url)
                    if r.status_code == 200:
                        data = r.json()
                        for job in data.get("jobs", []):
                            url = job.get("url", "")
                            title = job.get("title", "")
                            company = job.get("company_name", "")
                            desc = job.get("description", "")
                            domain = urlparse(url).netloc.lower()
                            try:
                                check_domain_policy(domain, allowed_domains, blocked_domains)
                            except DataHuntError:
                                continue
                            clean_desc = re.sub(r"<[^>]+>", " ", desc).strip()
                            combined_text = f"{title} {clean_desc}".lower()
                            title_lower = title.lower()

                            if has_ai_constraint:
                                has_ai_title = any(re.search(r'\b' + re.escape(t) + r'\b', title_lower) for t in ai_query_terms)
                                if not has_ai_title and not any(r in title_lower for r in ("data scientist", "data engineer", "machine learning", "deep learning", "nlp", "computer vision", "algorithm", "research scientist")):
                                    continue
                            elif query_tokens:
                                meaningful = [t for t in query_tokens if t not in ("software", "engineer", "developer")]
                                if meaningful and not any(t in combined_text for t in meaningful):
                                    continue

                            snippet_text = clean_desc[:250].strip()
                            hits.append(SearchHit(
                                url=url,
                                title=f"{title} at {company}",
                                snippet=snippet_text,
                                source_domain=domain
                            ))
                            try:
                                from datahunt.tools.fetch import store_prefetched_job_document
                                full_job_text = f"# {title} at {company}\n\nCompany: {company}\nPosition: {title}\nURL: {url}\n\n## Job Description\n{clean_desc}\n"
                                store_prefetched_job_document(url, f"{title} at {company}", full_job_text)
                            except Exception:
                                pass
                            if len(hits) >= limit:
                                break
            except Exception as e:
                logger.debug(f"Remotive search error on '{query}': {e}")

        return hits


class FreeHireSearchProvider:
    """Read-only adapter for FreeHire's public, structured job-search API."""

    def __init__(
        self,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        enabled: Optional[bool] = None,
        max_requests: Optional[int] = None,
    ):
        from datahunt.config import settings

        self.base_url = (base_url or settings.FREEHIRE_API_URL).rstrip("/")
        self.api_key = api_key if api_key is not None else settings.FREEHIRE_API_KEY
        self.enabled = enabled if enabled is not None else settings.FREEHIRE_ENABLED
        self.max_requests = max_requests if max_requests is not None else settings.FREEHIRE_MAX_REQUESTS_PER_RUN
        self.requests_used = 0
        self._request_lock = Lock()

    @staticmethod
    def _clean_query(query: str) -> str:
        # FreeHire has its own q grammar; search-plan site: operators are for
        # DuckDuckGo and would otherwise become literal query text.
        cleaned = re.sub(r"\bsite:[^\s]+", " ", query or "", flags=re.IGNORECASE)
        cleaned = re.sub(
            r"\b(?:with\s+)?\d+\s*(?:-|–|to)\s*\d+\s*(?:years?|yrs?)(?:\s+(?:of\s+)?experience)?\b",
            " ",
            cleaned,
            flags=re.IGNORECASE,
        )
        cleaned = re.sub(r"\b(?:find|search|jobs?|openings?|roles?|positions?)\b", " ", cleaned, flags=re.IGNORECASE)
        return re.sub(r"\s+", " ", cleaned).strip()

    @staticmethod
    def _query_filters(query: str) -> Dict[str, Any]:
        lower = (query or "").casefold()
        aliases = {
            "saudi arabia": "sa", "saudi": "sa", "saudi arab": "sa", "ksa": "sa",
            "united arab emirates": "ae", "uae": "ae",
            "qatar": "qa", "kuwait": "kw", "bahrain": "bh", "oman": "om",
            "india": "in", "united kingdom": "gb", "uk": "gb",
            "singapore": "sg", "canada": "ca", "united states": "us", "usa": "us",
        }
        countries = []
        for alias, code in aliases.items():
            if re.search(r"(?<!\w)" + re.escape(alias) + r"(?!\w)", lower) and code not in countries:
                countries.append(code)

        filters: Dict[str, Any] = {}
        if countries:
            filters["countries"] = countries

        experience = re.search(
            r"\b(\d+)\s*(?:-|–|to)\s*(\d+)\s*(?:years?|yrs?)(?:\s+(?:of\s+)?experience)?\b",
            lower,
        )
        if experience:
            filters["experience_years_min"] = int(experience.group(1))
            filters["experience_years_max"] = int(experience.group(2))
        return filters

    def search(
        self,
        query: str,
        limit: int = 10,
        page: int = 1,
        freshness_days: Optional[int] = None,
        allowed_domains: Optional[List[str]] = None,
        blocked_domains: Optional[List[str]] = None,
    ) -> List[SearchHit]:
        if not self.enabled or self.requests_used >= max(0, self.max_requests):
            return []

        try:
            check_domain_policy("freehire.me", blocked_domains=blocked_domains)
        except DataHuntError:
            return []

        cleaned_query = self._clean_query(query)
        if not cleaned_query:
            return []

        try:
            from datahunt.config import settings

            request_limit = min(max(1, limit), 100)
            offset = max(0, (page - 1) * request_limit)
            params: Dict[str, Any] = {
                "q": cleaned_query,
                "q_fields": "title,company,description,location",
                "limit": request_limit,
                "offset": offset,
                "sort": "posted_at",
                "order": "desc",
            }
            params.update(self._query_filters(query))
            if freshness_days is not None and freshness_days > 0:
                params["posted_within_days"] = freshness_days
            headers = {
                "Accept": "application/json",
                "User-Agent": "datahunt-agent/0.2 (+https://github.com/strelov1/freehire)",
            }
            if self.api_key.strip():
                headers["Authorization"] = f"Bearer {self.api_key.strip()}"

            with self._request_lock:
                if self.requests_used >= max(0, self.max_requests):
                    return []
                self.requests_used += 1
            timeout = min(max(3.0, settings.READ_TIMEOUT_SECONDS), 10.0)
            with httpx.Client(timeout=timeout, follow_redirects=True, headers=headers) as client:
                response = client.get(f"{self.base_url}/jobs/search", params=params)

            if response.status_code == 429:
                logger.warning("FreeHire search rate limit reached; continuing with local providers")
                return []
            if response.status_code >= 400:
                logger.debug("FreeHire search returned HTTP %s", response.status_code)
                return []

            payload = response.json()
            items = payload.get("data", []) if isinstance(payload, dict) else []
            hits: List[SearchHit] = []
            for item in items:
                if not isinstance(item, dict):
                    continue
                slug = str(item.get("public_slug") or "").strip()
                target_url = str(item.get("url") or "").strip()
                if not target_url.startswith(("http://", "https://")):
                    if not slug:
                        continue
                    target_url = f"https://freehire.me/jobs/{slug}"
                try:
                    check_domain_policy(urlparse(target_url).netloc, allowed_domains, blocked_domains)
                except DataHuntError:
                    continue

                title = str(item.get("title") or "Job opening").strip()
                company = str(item.get("company") or "").strip()
                location = str(item.get("location") or "").strip()
                description = str(item.get("description") or "").strip()
                description = BeautifulSoup(description, "html.parser").get_text(" ", strip=True)
                context = " | ".join(value for value in (company, location) if value)
                snippet = "\n".join(value for value in (context, description[:1200]) if value)
                display_title = f"{title} at {company}" if company else title
                hits.append(SearchHit(
                    url=target_url,
                    title=display_title,
                    snippet=snippet,
                    source_domain="freehire.me",
                    provider_citation_id=f"freehire:{slug}" if slug else None,
                ))
                if len(hits) >= limit:
                    break
            return hits
        except Exception as exc:
            logger.debug("FreeHire search failed for %r: %s", query, exc)
            return []

class LLMSearchProvider:
    """
    Search provider using Gemini LLM to discover and retrieve high-probability
    active career board URLs, primary documentation links, or authoritative sources.
    """
    def __init__(self, gemini_client=None):
        self.client = gemini_client

    def search(
        self,
        query: str,
        limit: int = 10,
        page: int = 1,
        freshness_days: Optional[int] = None,
        allowed_domains: Optional[List[str]] = None,
        blocked_domains: Optional[List[str]] = None
    ) -> List[SearchHit]:
        from datahunt.config import settings
        if not (settings.is_nvidia_configured or settings.is_gemini_configured):
            return []
        try:
            if not self.client:
                # Gemini client initialization (commented out as default in favor of NvidiaClient):
                # from datahunt.llm.gemini_client import GeminiClient
                # self.client = GeminiClient()
                if settings.LLM_PROVIDER == "nvidia" and settings.is_nvidia_configured:
                    from datahunt.llm.nvidia_client import NvidiaClient
                    self.client = NvidiaClient()
                else:
                    from datahunt.llm.gemini_client import GeminiClient
                    self.client = GeminiClient()
            if not self.client.is_live:
                return []

            if hasattr(self.client, "candidate_pool"):
                pass  # NvidiaClient manages per-call fallback internally
            else:
                from datahunt.llm.gemini_client import is_model_cooling, ALL_FREE_TIER_MODELS
                if all(is_model_cooling(m) for m in ALL_FREE_TIER_MODELS):
                    logger.debug(f"LLM search skipped for '{query}': all Gemini models in cooldown")
                    return []

            prompt = (
                f"You are a web search index for research and job hunting.\n"
                f"For the search query: '{query}'\n"
                f"Identify up to {limit} real, public, authoritative web pages, job postings, or primary documentation links.\n"
                f"Focus on actual platforms like boards.greenhouse.io, jobs.lever.co, jobs.ashbyhq.com, apply.workable.com, "
                f"or primary official company career sites and documentation.\n"
                f"Do NOT return example.com or placeholder URLs.\n"
                f"Output strictly a JSON array of objects with keys:\n"
                f"- 'title': page or role title\n"
                f"- 'company': company name (optional)\n"
                f"- 'url': full HTTP/HTTPS url\n"
                f"- 'snippet': 1-2 sentence description\n"
                f"- 'source_domain': domain name\n"
            )
            raw = self.client._call_gemini_json(prompt, schema_description="list of search hits", stage="search_planner")
            items = raw if isinstance(raw, list) else (raw.get("hits") or raw.get("results") or [])
            hits: List[SearchHit] = []
            for item in items:
                if not isinstance(item, dict):
                    continue
                u = item.get("url", "").strip()
                if not u.startswith("http") or "example.com" in u:
                    continue
                domain = urlparse(u).netloc.lower()
                try:
                    check_domain_policy(domain, allowed_domains, blocked_domains)
                except DataHuntError:
                    continue
                title = item.get("title", "Search Result")
                company = item.get("company")
                if company and company.lower() not in title.lower():
                    title = f"{title} at {company}"
                hits.append(SearchHit(
                    url=u,
                    title=title,
                    snippet=item.get("snippet", ""),
                    source_domain=domain
                ))
                if len(hits) >= limit:
                    break
            return hits
        except Exception as e:
            logger.debug(f"LLM search provider error for '{query}': {e}")
            return []

class DirectATSSearchProvider:
    """
    Direct high-throughput public ATS provider querying structured APIs directly
    (Greenhouse, Lever, Ashby, SmartRecruiters) when queries target ATS domains or when
    search engines are throttled, rate-limited, or blocked.
    Includes thread-safe in-memory caching (TTL 10 min).
    """
    _cache: Dict[str, tuple[float, List[Dict[str, Any]]]] = {}
    _cache_lock = Lock()
    _CACHE_TTL = 600.0  # 10 minutes

    # Known high-volume technology & enterprise employers with public ATS endpoints
    KNOWN_GREENHOUSE_BOARDS = [
        "tamara", "careem", "lucidmotors", "cognite", "andurilindustries",
        "bybit", "opswat", "fireblocks", "decimainternational", "ebury",
        "singlestore-linkedin", "cloudflare", "netskope", "udacity",
        "monks", "bursonglobalcareers", "blackduck", "stackadapt", "aift",
        "binance", "okx", "canonical", "gitlab", "elastic", "mongodb",
        "datadog", "scaleapi", "anthropic", "databricks", "snowflake",
        "automattic", "instacart", "reddit", "stripe"
    ]
    KNOWN_LEVER_BOARDS = [
        "spotify", "palantir", "figma", "atlassian", "canva", "deliveroo",
        "anghami", "postman", "tabby", "swvl", "talabat", "getir"
    ]
    KNOWN_ASHBY_BOARDS = [
        "openai", "perplexity", "cohere", "modal", "cursor", "anyscale",
        "linear", "replicate", "characterai", "together"
    ]

    def _fetch_greenhouse_board(self, board_token: str) -> List[Dict[str, Any]]:
        now = time.time()
        with self._cache_lock:
            key = f"gh_{board_token}"
            if key in self._cache:
                ts, data = self._cache[key]
                if now - ts < self._CACHE_TTL:
                    return data

        url = f"https://boards-api.greenhouse.io/v1/boards/{board_token}/jobs"
        try:
            with httpx.Client(timeout=4.0) as client:
                resp = client.get(url)
                if resp.status_code == 200:
                    jobs = resp.json().get("jobs", [])
                    with self._cache_lock:
                        self._cache[f"gh_{board_token}"] = (now, jobs)
                    return jobs
        except Exception as e:
            logger.debug(f"Direct ATS Greenhouse fetch error for {board_token}: {e}")
        return []

    def _fetch_lever_board(self, board_token: str) -> List[Dict[str, Any]]:
        now = time.time()
        with self._cache_lock:
            key = f"lever_{board_token}"
            if key in self._cache:
                ts, data = self._cache[key]
                if now - ts < self._CACHE_TTL:
                    return data

        url = f"https://api.lever.co/v0/postings/{board_token}?mode=json"
        try:
            with httpx.Client(timeout=4.0) as client:
                resp = client.get(url)
                if resp.status_code == 200:
                    postings = resp.json()
                    if isinstance(postings, list):
                        with self._cache_lock:
                            self._cache[f"lever_{board_token}"] = (now, postings)
                        return postings
        except Exception as e:
            logger.debug(f"Direct ATS Lever fetch error for {board_token}: {e}")
        return []

    def _fetch_ashby_board(self, board_token: str) -> List[Dict[str, Any]]:
        now = time.time()
        with self._cache_lock:
            key = f"ashby_{board_token}"
            if key in self._cache:
                ts, data = self._cache[key]
                if now - ts < self._CACHE_TTL:
                    return data

        url = f"https://api.ashbyhq.com/posting-api/job-board/{board_token}"
        try:
            with httpx.Client(timeout=4.0) as client:
                resp = client.get(url)
                if resp.status_code == 200:
                    jobs = resp.json().get("jobs", [])
                    with self._cache_lock:
                        self._cache[f"ashby_{board_token}"] = (now, jobs)
                    return jobs
        except Exception as e:
            logger.debug(f"Direct ATS Ashby fetch error for {board_token}: {e}")
        return []

    def search(
        self,
        query: str,
        limit: int = 10,
        page: int = 1,
        freshness_days: Optional[int] = None,
        allowed_domains: Optional[List[str]] = None,
        blocked_domains: Optional[List[str]] = None
    ) -> List[SearchHit]:
        from datahunt.config import settings
        if not getattr(settings, "DIRECT_ATS_ENABLED", True):
            return []

        q_low = query.lower()
        # 1. Detect target platforms
        is_greenhouse = "greenhouse.io" in q_low
        is_lever = "lever.co" in q_low
        is_ashby = "ashbyhq" in q_low
        is_ats_query = is_greenhouse or is_lever or is_ashby or any(ats in q_low for ats in ("workable", "smartrecruiters"))

        if not is_ats_query:
            return []

        # 2. Extract company handle if present in URL
        gh_targets = []
        lever_targets = []
        ashby_targets = []

        gh_url_match = re.search(r"(?:boards|job-boards)?\.greenhouse\.io/([a-zA-Z0-9_\-]+)", query)
        if gh_url_match:
            gh_targets.append(gh_url_match.group(1).lower())
        lever_url_match = re.search(r"jobs\.lever\.co/([a-zA-Z0-9_\-]+)", query)
        if lever_url_match:
            lever_targets.append(lever_url_match.group(1).lower())
        ashby_url_match = re.search(r"jobs\.ashbyhq\.com/([a-zA-Z0-9_\-]+)", query)
        if ashby_url_match:
            ashby_targets.append(ashby_url_match.group(1).lower())

        # If not in URL, check if known board names appear in query text
        if not gh_targets and not lever_targets and not ashby_targets:
            for b in self.KNOWN_GREENHOUSE_BOARDS:
                if re.search(r"\b" + re.escape(b) + r"\b", q_low):
                    gh_targets.append(b)
            for b in self.KNOWN_LEVER_BOARDS:
                if re.search(r"\b" + re.escape(b) + r"\b", q_low):
                    lever_targets.append(b)
            for b in self.KNOWN_ASHBY_BOARDS:
                if re.search(r"\b" + re.escape(b) + r"\b", q_low):
                    ashby_targets.append(b)

        # If no specific company was found, default to the relevant known boards for the ATS
        if not gh_targets and not lever_targets and not ashby_targets:
            if is_greenhouse:
                gh_targets = list(self.KNOWN_GREENHOUSE_BOARDS)
            elif is_lever:
                lever_targets = list(self.KNOWN_LEVER_BOARDS)
            elif is_ashby:
                ashby_targets = list(self.KNOWN_ASHBY_BOARDS)
            else:
                gh_targets = list(self.KNOWN_GREENHOUSE_BOARDS)
                lever_targets = list(self.KNOWN_LEVER_BOARDS)
                ashby_targets = list(self.KNOWN_ASHBY_BOARDS)

        # 3. Extract keywords for filtering
        location_keywords = []
        for loc in (
            "saudi", "saudi arabia", "saudi arab", "ksa", "riyadh", "jeddah", "dammam", "khobar",
            "uae", "dubai", "abu dhabi", "sharjah", "middle east", "mena", "remote",
            "qatar", "kuwait", "egypt", "cairo"
        ):
            if loc in q_low:
                location_keywords.append(loc)

        is_ai_query = any(k in q_low for k in (
            "ai", "ml", "machine learning", "data scientist", "data science",
            "deep learning", "nlp", "llm", "generative", "agentic", "computer vision",
            "artificial intelligence", "algorithm"
        ))
        ai_role_regex = re.compile(
            r"\b(ai|ml|machine\s*learning|data\s*scientist|data\s*science|deep\s*learning|nlp|llm|generative|agentic|prompt|computer\s*vision|artificial\s*intelligence|algorithm)\b",
            re.IGNORECASE
        )
        non_ai_disqualifying_regex = re.compile(
            r"\b(customer\s*care|customer\s*service|collections|copywriter|copy\s*writer|compliance|accountant|sales\s*manager|cashier|clerk|fraud|call\s*center|advisor|receptionist|telecaller|administrative|people\s*partner|legal\s*counsel)\b",
            re.IGNORECASE
        )

        all_target_comps = gh_targets + lever_targets + ashby_targets
        clean_q = re.sub(r"site:[^\s]+", " ", q_low)
        clean_q = re.sub(r"[^a-zA-Z0-9\s]", " ", clean_q)
        tokens = [w for w in clean_q.split() if len(w) >= 2 and w not in ("jobs", "job", "hiring", "apply", "careers", "career", "openings", "positions")]
        role_tokens = [t for t in tokens if t not in location_keywords and t not in all_target_comps]

        # Gather job candidates across platforms
        job_candidates: List[tuple[str, str, str, str]] = []
        for comp in gh_targets:
            for j in self._fetch_greenhouse_board(comp):
                title = j.get("title", "")
                loc_obj = j.get("location", {})
                loc_name = loc_obj.get("name", "") if isinstance(loc_obj, dict) else str(loc_obj or "")
                url = j.get("absolute_url", "")
                job_candidates.append((title, loc_name, url, comp))

        for comp in lever_targets:
            for j in self._fetch_lever_board(comp):
                title = j.get("text", "")
                loc_cats = j.get("categories", {})
                loc_name = loc_cats.get("location", "") if isinstance(loc_cats, dict) else ""
                if isinstance(loc_cats, dict) and loc_cats.get("allLocations"):
                    loc_name += " " + " ".join(loc_cats["allLocations"])
                url = j.get("hostedUrl", "")
                job_candidates.append((title, loc_name, url, comp))

        for comp in ashby_targets:
            for j in self._fetch_ashby_board(comp):
                title = j.get("title", "")
                loc_name = j.get("location", "")
                sec = j.get("secondaryLocations", [])
                if sec:
                    loc_name += " " + " ".join([l.get("location", "") if isinstance(l, dict) else str(l) for l in sec])
                if j.get("isRemote"):
                    loc_name += " Remote"
                url = j.get("jobUrl", "")
                job_candidates.append((title, loc_name, url, comp))

        hits: List[SearchHit] = []
        for title, loc_name, raw_url, comp in job_candidates:
            full_text = f"{title} {loc_name}".lower()

            # Filter by location if specified
            if location_keywords and not any(lk in full_text for lk in location_keywords):
                continue

            # Strict role filtering: enforce AI title match for AI queries
            if is_ai_query:
                if not ai_role_regex.search(title):
                    continue
                if non_ai_disqualifying_regex.search(title) and not ai_role_regex.search(title):
                    continue
            elif role_tokens:
                if not any(re.search(r"\b" + re.escape(rt) + r"\b", title.lower()) for rt in role_tokens):
                    continue

            url = canonicalize_url(raw_url)
            if not url or is_ad_or_tracking_url(url):
                continue
            if allowed_domains and not any(d in url for d in allowed_domains):
                continue
            if blocked_domains and any(d in url for d in blocked_domains):
                continue

            snippet = f"Official career posting for {title} at {comp.capitalize()} ({loc_name.strip()}). Direct ATS application."
            hits.append(SearchHit(
                url=url,
                title=f"{title} - {comp.capitalize()}",
                snippet=snippet,
                source_domain=urlparse(url).netloc,
            ))

            if len(hits) >= limit:
                break

        if hits:
            logger.info(f"Direct ATS returned {len(hits)} hits for '{query}'")
        return hits


class SearXNGSearchProvider:
    """
    SearXNG metasearch provider (self-hosted, open-source, aggregates Google/Bing/DDG/Brave).
    Queries {SEARXNG_URL}/search?q={query}&format=json
    Zero API keys needed, no external rate-limiting.
    """
    def __init__(self, base_url: Optional[str] = None):
        from datahunt.config import settings
        self.base_url = (base_url or getattr(settings, "SEARXNG_URL", "")).rstrip("/")
        self._last_failure = 0.0
        self._COOLDOWN_SECONDS = 30.0

    def is_cooling_down(self) -> bool:
        return time.time() < self._last_failure

    def search(
        self,
        query: str,
        limit: int = 10,
        page: int = 1,
        freshness_days: Optional[int] = None,
        allowed_domains: Optional[List[str]] = None,
        blocked_domains: Optional[List[str]] = None,
    ) -> List[SearchHit]:
        if not self.base_url or self.is_cooling_down():
            return []

        params: Dict[str, Any] = {
            "q": query,
            "format": "json",
            "pageno": page,
        }
        if freshness_days:
            if freshness_days <= 1:
                params["time_range"] = "day"
            elif freshness_days <= 7:
                params["time_range"] = "week"
            elif freshness_days <= 31:
                params["time_range"] = "month"

        try:
            with httpx.Client(timeout=4.0, headers={"Accept": "application/json", "User-Agent": "DataHunt/1.0"}) as client:
                resp = client.get(f"{self.base_url}/search", params=params)
                if resp.status_code == 200:
                    hits: List[SearchHit] = []
                    for item in resp.json().get("results", []):
                        url = canonicalize_url(item.get("url", ""))
                        if not url or is_ad_or_tracking_url(url):
                            continue
                        if allowed_domains and not any(d in url for d in allowed_domains):
                            continue
                        if blocked_domains and any(d in url for d in blocked_domains):
                            continue
                        hits.append(SearchHit(
                            url=url,
                            title=item.get("title", ""),
                            snippet=item.get("content", ""),
                            source_domain=urlparse(url).netloc,
                        ))
                        if len(hits) >= limit:
                            break
                    if hits:
                        logger.info(f"SearXNG returned {len(hits)} hits for '{query}'")
                    return hits
                else:
                    self._last_failure = time.time() + self._COOLDOWN_SECONDS
                    return []
        except Exception as e:
            logger.debug(f"SearXNG not reachable at {self.base_url}: {e}")
            self._last_failure = time.time() + self._COOLDOWN_SECONDS
            return []


class HybridSearchProvider:
    """
    Multi-tier adaptive search provider with resilient fallback mechanics:
    1. Direct ATS Ingestion (Greenhouse, Lever, SmartRecruiters) + FreeHire
    2. Primary High-Precision SERP APIs (Serper / Google -> Tavily -> Brave)
    3. Self-Hosted Metasearch (SearXNG)
    4. Public Free SERP Fallback (DuckDuckGo with auto-recovering circuit breaker)
    5. Direct Remote Boards (RemoteOK, Remotive)
    6. LLM-Assisted Semantic Search
    7. Deterministic Mock (Test isolation only)
    """
    def __init__(self, gemini_client=None, fallback_to_mock: bool = False):
        self.ats_provider = DirectATSSearchProvider()
        self.freehire = FreeHireSearchProvider()
        self.serper = SerperSearchProvider()
        self.tavily = TavilySearchProvider()
        self.brave = BraveSearchProvider()
        self.searxng = SearXNGSearchProvider()
        self.ddg = DuckDuckGoSearchProvider()
        self.job_board = LiveJobBoardSearchProvider()
        self.llm_search = LLMSearchProvider(gemini_client=gemini_client)
        self.fallback_to_mock = fallback_to_mock
        self.mock = MockSearchProvider() if fallback_to_mock else None

    def search(
        self,
        query: str,
        limit: int = 10,
        page: int = 1,
        freshness_days: Optional[int] = None,
        allowed_domains: Optional[List[str]] = None,
        blocked_domains: Optional[List[str]] = None
    ) -> List[SearchHit]:
        from datahunt.config import settings

        q_low = query.lower()
        is_job = any(k in q_low for k in ("job", "jobs", "hiring", "career", "careers", "intern", "vacancy", "openings", "engineer", "developer", "remote", "architect", "lead"))

        accumulated_hits: List[SearchHit] = []

        # =========================================================================
        # TIER 1: STRUCTURED CATALOGUE & DIRECT ATS INGESTION (Option 3)
        # =========================================================================
        # 1a. FreeHire structured catalogue for job queries
        freehire_hits = self.freehire.search(
            query,
            limit=limit,
            page=page,
            freshness_days=freshness_days,
            allowed_domains=allowed_domains,
            blocked_domains=blocked_domains,
        ) if is_job else []
        if len(freehire_hits) >= limit:
            return freehire_hits[:limit]
        if freehire_hits:
            accumulated_hits.extend(freehire_hits)

        # 1b. Direct ATS Provider: when query targets specific ATS or known boards
        if is_job and getattr(settings, "DIRECT_ATS_ENABLED", True):
            ats_hits = self.ats_provider.search(
                query,
                limit=limit - len(accumulated_hits),
                page=page,
                freshness_days=freshness_days,
                allowed_domains=allowed_domains,
                blocked_domains=blocked_domains,
            )
            if ats_hits:
                accumulated_hits.extend(ats_hits)
                if len(accumulated_hits) >= limit:
                    return accumulated_hits[:limit]

        # =========================================================================
        # TIER 2: HIGH-RELIABILITY CONFIGURED SERP ENGINES (Option 1)
        # Prioritize Serper (Google) → Tavily → Brave before free DDG to prevent
        # timeouts and circuit breaker freezes.
        # =========================================================================
        active_serp_providers = []
        if getattr(settings, "SERPER_API_KEY", "").strip():
            active_serp_providers.append((self.serper, "Serper"))
        if getattr(settings, "TAVILY_API_KEY", "").strip():
            active_serp_providers.append((self.tavily, "Tavily"))
        if getattr(settings, "BRAVE_API_KEY", "").strip():
            active_serp_providers.append((self.brave, "Brave"))

        for provider, name in active_serp_providers:
            remaining = limit - len(accumulated_hits)
            serp_hits = provider.search(
                query,
                limit=remaining,
                page=page,
                freshness_days=freshness_days,
                allowed_domains=allowed_domains,
                blocked_domains=blocked_domains,
            )
            if serp_hits:
                accumulated_hits.extend(serp_hits)
                if len(accumulated_hits) >= limit:
                    return accumulated_hits[:limit]

        # =========================================================================
        # TIER 3: LOCAL / SELF-HOSTED METASEARCH (Option 2 - SearXNG)
        # Aggregates Google/Bing/DDG/Brave without API keys or rate-limits
        # =========================================================================
        if getattr(settings, "SEARXNG_URL", "").strip():
            remaining = limit - len(accumulated_hits)
            searx_hits = self.searxng.search(
                query,
                limit=remaining,
                page=page,
                freshness_days=freshness_days,
                allowed_domains=allowed_domains,
                blocked_domains=blocked_domains,
            )
            if searx_hits:
                accumulated_hits.extend(searx_hits)
                if len(accumulated_hits) >= limit:
                    return accumulated_hits[:limit]

        # Return accumulated hits if any tier already found results
        if accumulated_hits:
            return accumulated_hits[:limit]

        # =========================================================================
        # TIER 4: PUBLIC ZERO-CONFIG SERP (DuckDuckGo Lite/HTML)
        # Only reached when API keys are unset/exhausted or higher tiers returned 0 hits
        # =========================================================================
        ddg_hits = self.ddg.search(
            query,
            limit=limit,
            page=page,
            freshness_days=freshness_days,
            allowed_domains=allowed_domains,
            blocked_domains=blocked_domains
        )
        if ddg_hits:
            return ddg_hits[:limit]

        # =========================================================================
        # TIER 5: DIRECT REMOTE BOARDS (RemoteOK / Remotive)
        # =========================================================================
        if is_job:
            hits = self.job_board.search(
                query,
                limit=limit,
                page=page,
                freshness_days=freshness_days,
                allowed_domains=allowed_domains,
                blocked_domains=blocked_domains
            )
            if hits:
                logger.info(f"Retrieved {len(hits)} live hits via direct job board endpoints for '{query}'")
                return hits

        # =========================================================================
        # TIER 6: LLM-ASSISTED SEMANTIC SEARCH
        # =========================================================================
        try:
            hits = self.llm_search.search(
                query,
                limit=limit,
                page=page,
                freshness_days=freshness_days,
                allowed_domains=allowed_domains,
                blocked_domains=blocked_domains
            )
            if hits:
                logger.info(f"Retrieved {len(hits)} live candidate hits via LLM search provider for '{query}'")
                return hits
        except Exception as e:
            logger.debug(f"LLM search fallback failed for '{query}': {e}")

        # =========================================================================
        # TIER 7: TEST MOCK (Only for test isolation)
        # =========================================================================
        if self.fallback_to_mock and self.mock:
            logger.info("Live search returned 0 hits across all live providers, utilizing test mock fallback")
            return self.mock.search(
                query,
                limit=limit,
                page=page,
                freshness_days=freshness_days,
                allowed_domains=allowed_domains,
                blocked_domains=blocked_domains
            )

        logger.info(f"Live search returned 0 hits across all live providers for '{query}'")
        return []

class SearchTool:
    name = "search_web"
    description = "Search the public web for candidate documents matching query."

    def __init__(self, provider: Optional[SearchProvider] = None):
        self.provider = provider or HybridSearchProvider()
        self._query_cache: Dict[str, List[Dict[str, Any]]] = {}

    def execute(
        self,
        query: str,
        limit: int = 10,
        page: int = 1,
        freshness_days: Optional[int] = None,
        allowed_domains: Optional[List[str]] = None,
        blocked_domains: Optional[List[str]] = None,
        **kwargs: Any
    ) -> ToolResult:
        if "max_results" in kwargs:
            limit = kwargs.pop("max_results")
        now = time.time()
        allowed_str = ",".join(sorted(allowed_domains or []))
        blocked_str = ",".join(sorted(blocked_domains or []))
        cache_key = f"{query}:{limit}:{page}:{freshness_days}:{allowed_str}:{blocked_str}"
        if cache_key in self._query_cache:
            entry = self._query_cache[cache_key]
            if isinstance(entry, tuple) and len(entry) == 2:
                ts, cached_data = entry
                if now - ts < 300:  # 5 minutes TTL
                    logger.info(f"Returning cached search results for query: {query}")
                    return ToolResult(success=True, data=cached_data, metadata={"cached": True})
                else:
                    self._query_cache.pop(cache_key, None)
            else:
                return ToolResult(success=True, data=entry, metadata={"cached": True})

        try:
            hits = self.provider.search(
                query=query,
                limit=limit,
                page=page,
                freshness_days=freshness_days,
                allowed_domains=allowed_domains,
                blocked_domains=blocked_domains
            )
            hit_dicts = [h.to_dict() for h in hits]
            # Pre-cache LinkedIn hiring posts so FetchTool retrieves full snippet text without hitting authwalls
            for h in hit_dicts:
                h_url = h.get("url") or ""
                if "linkedin.com" in h_url and any(p in h_url for p in ("/posts/", "/feed/update/", "/pulse/")):
                    h_title = h.get("title") or ""
                    h_snippet = h.get("snippet") or ""
                    if h_snippet:
                        try:
                            from datahunt.tools.fetch import store_prefetched_job_document
                            post_content = (
                                f"# {h_title}\n\n"
                                f"URL: {h_url}\n\n"
                                f"Platform: LinkedIn Hiring Post\n\n"
                                f"## Post Snippet / Description\n"
                                f"{h_snippet}\n"
                            )
                            store_prefetched_job_document(h_url, h_title, post_content)
                        except Exception:
                            pass
            if len(self._query_cache) >= 256:
                old_keys = list(self._query_cache.keys())[:64]
                for k in old_keys:
                    self._query_cache.pop(k, None)
            self._query_cache[cache_key] = (now, hit_dicts)
            return ToolResult(success=True, data=hit_dicts, metadata={"count": len(hit_dicts)})
        except Exception as e:
            logger.error(f"Search failed for query '{query}': {e}")
            return ToolResult(
                success=False,
                error_code=ErrorCode.SEARCH_UNAVAILABLE.value,
                error_message=f"Search failed: {e}",
                data=[]
            )


class ParallelSearchEngine:
    """
    Executes multiple search tasks concurrently using a controlled thread pool,
    deduplicating discovered URLs across query batches.
    """
    def __init__(self, search_tool: Optional[SearchTool] = None, max_workers: int = 4):
        self.search_tool = search_tool or SearchTool()
        self.max_workers = max_workers

    def search_batch(
        self,
        queries: List[Dict[str, Any]],
        limit_per_query: int = 15,
        allowed_domains: Optional[List[str]] = None,
        blocked_domains: Optional[List[str]] = None
    ) -> List[Dict[str, Any]]:
        """
        Execute queries in parallel.
        queries: list of dicts with at least {"query": str, "freshness_days": Optional[int]}
        Returns: unified deduplicated list of search hit dicts.
        """
        all_hits: List[Dict[str, Any]] = []
        seen_urls = set()

        def _do_search(q_item: Dict[str, Any]):
            q_text = q_item.get("query") if isinstance(q_item, dict) else str(q_item)
            freshness = q_item.get("freshness_days") if isinstance(q_item, dict) else None
            page = q_item.get("page", 1) if isinstance(q_item, dict) else 1
            res = self.search_tool.execute(
                query=q_text,
                limit=limit_per_query,
                page=page,
                freshness_days=freshness,
                allowed_domains=allowed_domains,
                blocked_domains=blocked_domains
            )
            return res.data if res.success else []

        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            future_to_query = {executor.submit(_do_search, q): q for q in queries}
            for future in as_completed(future_to_query):
                try:
                    hits = future.result()
                    for h in hits:
                        u = h.get("url")
                        if u and u not in seen_urls:
                            seen_urls.add(u)
                            all_hits.append(h)
                except Exception as ex:
                    logger.warning(f"Parallel search worker error: {ex}")

        return all_hits


class SearchProviderRegistry:
    """
    Registry for pluggable search providers (DuckDuckGo, FreeHire, LiveJobBoard, LLM, Mock, Hybrid).
    Enables dynamic provider selection, fallback chains, and pagination.
    """
    def __init__(self):
        self._providers: Dict[str, SearchProvider] = {}
        ddg = DuckDuckGoSearchProvider()
        freehire = FreeHireSearchProvider()
        jb = LiveJobBoardSearchProvider()
        mock = MockSearchProvider()
        hybrid = HybridSearchProvider()
        self.register("duckduckgo", ddg)
        self.register("ddg", ddg)
        self.register("freehire", freehire)
        self.register("fh", freehire)
        self.register("job_boards", jb)
        self.register("mock", mock)
        self.register("hybrid", hybrid)

    def register(self, name: str, provider: SearchProvider):
        self._providers[name.lower()] = provider

    def get(self, name: str) -> Optional[SearchProvider]:
        return self._providers.get(name.lower())

    def get_default(self) -> SearchProvider:
        return self._providers.get("hybrid") or self._providers.get("duckduckgo") or DuckDuckGoSearchProvider()

    def list_providers(self) -> List[str]:
        return list(self._providers.keys())

