import hashlib
import re
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Protocol
from urllib.parse import urlparse, unquote
import httpx
from bs4 import BeautifulSoup
from concurrent.futures import ThreadPoolExecutor, as_completed

from datahunt.errors import DataHuntError, ErrorCode
from datahunt.logger import logger
from datahunt.policy import check_domain_policy
from datahunt.tools.base import ToolResult

def canonicalize_url(url: str) -> str:
    """
    Produce a canonical, normalized URL for deduplication.
    - Strips fragments
    - Strips marketing/tracking query parameters (utm_*, fbclid, gclid, etc.)
    - Normalizes scheme and host to lowercase
    - Normalizes trailing slashes for paths
    - Preserves job IDs for known ATS (Greenhouse, Lever, Ashby, Workable, etc.)
    """
    if not url or not isinstance(url, str):
        return ""
    try:
        from urllib.parse import urlparse, parse_qsl, urlencode
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
    strictly rejecting login portals, candidate accounts, and root search index forms.
    Supports ATS portals (Greenhouse, Lever, Ashby, Workable) and regional job boards (Bayt, Naukrigulf, LinkedIn, Indeed).
    """
    if not url or not isinstance(url, str):
        return False
    u = url.strip()
    u_lower = u.lower()

    # Reject Greenhouse ?error=true redirect pages (expired/private/EU boards)
    if "greenhouse.io" in u_lower and "error=true" in u_lower:
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

    hostname = parsed.hostname or ""
    path = parsed.path or ""

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
    # Must have /j/<job_id>
    if "workable.com" in hostname:
        if "/j/" not in path:
            return False

    # 7. Check for specific job posting patterns on aggregators and company sites
    is_specific_posting = bool(
        re.search(r"/(?:view|jobs?|job-listing|careers?|position|role)/[a-zA-Z0-9_\-]+", path)
        or re.search(r"\b(?:vjk|jk|jid|jobid|job_id|gh_jid)=[a-zA-Z0-9_\-]+", parsed.query)
        or re.search(r"-\d+/?$", path)
        or re.search(r"/\d+/?$", path)
        or "viewjob" in path
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
        if any(p in path for p in ("/search", "/skill", "/skills", "/category", "/browse", "/tags")):
            return False
        if "jobs-in" in path and not is_specific_posting:
            return False
        if not is_specific_posting:
            return False

    # Reject pure search result URLs for other domains if no job ID is present
    if any(p in path for p in ("/search/", "/skill/", "/skills/")) and not is_specific_posting:
        return False

    # 9. LinkedIn Jobs — accept /jobs/view/<id> and search result pages (they always list real jobs)
    if "linkedin.com" in hostname:
        if "/jobs/view/" in path or "/jobs/search" in path:
            return True
        if "/in/" in path or "/company/" in path:
            return False  # Profile or company pages, not job listings
        if "/jobs" in path:
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
            f'"{role_only}" Dubai Riyadh UAE Saudi apply careers 2025 2026',
        ]
    elif any(k in loc_lower for k in ("india", "bangalore", "bengaluru", "mumbai", "hyderabad", "pune", "delhi", "chennai")):
        queries += [
            f"site:naukri.com {cleaned}",
            f"site:foundit.in {cleaned}",
            f"site:instahyre.com {role_only}",
            f"site:cutshort.io {role_only}",
            f"site:shine.com {cleaned}",
            f"{role_only} {location_phrase} hiring apply careers 2025 2026",
        ]
    elif any(k in loc_lower for k in ("london", "uk", "manchester", "edinburgh", "bristol")):
        queries += [
            f"site:reed.co.uk {role_only}",
            f"site:totaljobs.com {role_only}",
            f"{role_only} UK London careers apply 2025 2026",
        ]
    elif any(k in loc_lower for k in ("new york", "san francisco", "seattle", "austin", "boston", "us", "usa", "remote")):
        queries += [
            f"site:builtin.com {role_only}",
            f"site:dice.com {role_only}",
            f"site:wellfound.com {role_only}",
            f"{role_only} careers US remote apply 2025 2026",
        ]
    elif any(k in loc_lower for k in ("singapore", "hong kong")):
        queries += [
            f"site:jobsdb.com {cleaned}",
            f"site:jobstreet.com {role_only}",
            f"{role_only} careers Singapore apply 2025 2026",
        ]
    else:
        # Generic fallback — organic queries that DDG returns well
        queries += [
            f"{cleaned} careers apply now 2025 2026",
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
    Generate authoritative market, SaaS pricing, and competitor comparison queries
    targeting official pricing pages, feature matrices, and independent software reviews.
    """
    clean = re.sub(r"\b(find|search|analyze|compare|comparison of|pricing for|market analysis of)\b", "", topic, flags=re.IGNORECASE).strip()
    clean = re.sub(r"\s+", " ", clean).strip() or topic.strip()
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
        freshness_days: Optional[int] = None,
        allowed_domains: Optional[List[str]] = None,
        blocked_domains: Optional[List[str]] = None
    ) -> List[SearchHit]:
        ...

class DuckDuckGoSearchProvider:
    """
    Public web search provider using DuckDuckGo Lite endpoint (high-throughput,
    anti-bot resilient, zero JS) with fallback to DDGS and HTML endpoints.
    """
    LITE_URL = "https://lite.duckduckgo.com/lite/"
    SEARCH_URL = "https://html.duckduckgo.com/html/"
    
    def search(
        self,
        query: str,
        limit: int = 10,
        freshness_days: Optional[int] = None,
        allowed_domains: Optional[List[str]] = None,
        blocked_domains: Optional[List[str]] = None
    ) -> List[SearchHit]:
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
                with httpx.Client(timeout=3.0, headers=headers, follow_redirects=True) as client:
                    post_data: Dict[str, str] = {"q": query}
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

                            if not actual_url.startswith("http") or "duckduckgo.com" in actual_url:
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
                            return hits
                        break  # Got 200 from Lite endpoint, don't retry same endpoint
                    else:
                        import time
                        time.sleep(0.75 * (attempt + 1))  # Backoff on non-200
            except Exception as lite_err:
                logger.debug(f"DuckDuckGo Lite attempt {attempt+1} error on '{query}': {lite_err}")
                if attempt == 0:
                    import time
                    time.sleep(0.75)

        # 2. Secondary fallback: DDGS client
        try:
            from ddgs import DDGS
            ddgs_kwargs: Dict[str, Any] = {"max_results": limit}
            if timelimit:
                ddgs_kwargs["timelimit"] = timelimit

            raw_results = list(DDGS().text(query, **ddgs_kwargs))
            if raw_results:
                for item in raw_results:
                    url = item.get("href") or item.get("link") or ""
                    title = item.get("title") or ""
                    snippet = item.get("body") or item.get("snippet") or ""

                    if not url.startswith("http"):
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
                return hits
        except Exception as ddg_err:
            logger.debug(f"DDGS search attempt on '{query}': {ddg_err}")

        # 3. Tertiary fallback: HTML endpoint
        try:
            with httpx.Client(timeout=3.0, headers=headers, follow_redirects=True) as client:
                post_data = {"q": query}
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

                        if not actual_url.startswith("http") or "duckduckgo.com/y.js" in actual_url:
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
        except Exception as e:
            logger.warning(f"DuckDuckGo HTML search error: {e}")

        # If restrictive freshness filter returned 0 hits, retry without timelimit
        if not hits and timelimit:
            logger.info(f"Freshness filter '{timelimit}' returned 0 hits for '{query}', falling back to standard search.")
            return self.search(
                query=query,
                limit=limit,
                freshness_days=None,
                allowed_domains=allowed_domains,
                blocked_domains=blocked_domains
            )

        return hits

class MockSearchProvider:
    """Mock/offline search provider for testing and deterministic validation."""
    def __init__(self, predefined_hits: Optional[List[SearchHit]] = None):
        self.predefined_hits = predefined_hits or []

    def search(
        self,
        query: str,
        limit: int = 10,
        freshness_days: Optional[int] = None,
        allowed_domains: Optional[List[str]] = None,
        blocked_domains: Optional[List[str]] = None
    ) -> List[SearchHit]:
        if self.predefined_hits:
            hits = self.predefined_hits
        else:
            q_low = query.lower()
            if any(k in q_low for k in ("job", "jobs", "hiring", "careers", "intern", "vacancy", "openings", "engineer", "developer")):
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
        freshness_days: Optional[int] = None,
        allowed_domains: Optional[List[str]] = None,
        blocked_domains: Optional[List[str]] = None
    ) -> List[SearchHit]:
        from datahunt.config import settings
        if not settings.is_gemini_configured:
            return []
        try:
            if not self.client:
                from datahunt.llm.gemini_client import GeminiClient
                self.client = GeminiClient()
            if not self.client.is_live:
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

class HybridSearchProvider:
    """Multi-tier search provider: DDG -> Regional LLM Discovery / Job Boards -> Deterministic Mock."""
    def __init__(self, gemini_client=None):
        self.ddg = DuckDuckGoSearchProvider()
        self.job_board = LiveJobBoardSearchProvider()
        self.llm_search = LLMSearchProvider(gemini_client=gemini_client)
        self.mock = MockSearchProvider()

    def search(
        self,
        query: str,
        limit: int = 10,
        freshness_days: Optional[int] = None,
        allowed_domains: Optional[List[str]] = None,
        blocked_domains: Optional[List[str]] = None
    ) -> List[SearchHit]:
        # 1. Try DuckDuckGo
        hits = self.ddg.search(
            query,
            limit=limit,
            freshness_days=freshness_days,
            allowed_domains=allowed_domains,
            blocked_domains=blocked_domains
        )
        if hits:
            return hits

        q_low = query.lower()
        is_site_query = "site:" in q_low
        is_regional = any(k in q_low for k in (
            "saudi", "riyadh", "jeddah", "ksa", "uae", "dubai", "abu dhabi", "gulf", "qatar", "doha",
            "kuwait", "bahrain", "oman", "mena", "india", "bangalore", "bengaluru", "mumbai", "pune",
            "hyderabad", "delhi", "london", "uk", "singapore", "germany", "berlin", "canada", "toronto",
            "bayt", "gulftalent", "naukri", "foundit", "reed", "totaljobs"
        ))
        is_job = any(k in q_low for k in ("job", "jobs", "hiring", "career", "careers", "intern", "vacancy", "openings", "engineer", "developer", "remote", "architect", "lead"))

        # 2. For regional or site: queries or non-job research, try LLM-assisted search discovery first
        if is_regional or is_site_query or not is_job:
            try:
                hits = self.llm_search.search(
                    query,
                    limit=limit,
                    freshness_days=freshness_days,
                    allowed_domains=allowed_domains,
                    blocked_domains=blocked_domains
                )
                if hits:
                    logger.info(f"Retrieved {len(hits)} live candidate hits via LLM search provider for '{query}'")
                    return hits
            except Exception as e:
                logger.debug(f"LLM search fallback failed for '{query}': {e}")

        # 3. Direct job board APIs for generic/remote job queries
        if is_job and not is_site_query:
            hits = self.job_board.search(
                query,
                limit=limit,
                freshness_days=freshness_days,
                allowed_domains=allowed_domains,
                blocked_domains=blocked_domains
            )
            if hits:
                logger.info(f"Retrieved {len(hits)} live hits via direct job board endpoints for '{query}'")
                return hits

        # 4. If not tried yet, try LLM-assisted search discovery
        if not (is_regional or is_site_query or not is_job):
            try:
                hits = self.llm_search.search(
                    query,
                    limit=limit,
                    freshness_days=freshness_days,
                    allowed_domains=allowed_domains,
                    blocked_domains=blocked_domains
                )
                if hits:
                    logger.info(f"Retrieved {len(hits)} live candidate hits via LLM search provider for '{query}'")
                    return hits
            except Exception as e:
                logger.debug(f"LLM search fallback failed for '{query}': {e}")

        # 5. Final deterministic fallback (mock)
        logger.info("Live search returned 0 hits across all live providers, utilizing fallback")
        return self.mock.search(
            query,
            limit=limit,
            freshness_days=freshness_days,
            allowed_domains=allowed_domains,
            blocked_domains=blocked_domains
        )

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
        freshness_days: Optional[int] = None,
        allowed_domains: Optional[List[str]] = None,
        blocked_domains: Optional[List[str]] = None
    ) -> ToolResult:
        now = time.time()
        allowed_str = ",".join(sorted(allowed_domains or []))
        blocked_str = ",".join(sorted(blocked_domains or []))
        cache_key = f"{query}:{limit}:{freshness_days}:{allowed_str}:{blocked_str}"
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
                freshness_days=freshness_days,
                allowed_domains=allowed_domains,
                blocked_domains=blocked_domains
            )
            hit_dicts = [h.to_dict() for h in hits]
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
            res = self.search_tool.execute(
                query=q_text,
                limit=limit_per_query,
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

