"""
Application-level evaluation policies and single authoritative QualificationPolicy.
These are deterministic rules; LLM never overrides them.
"""
import re
import json
from enum import Enum
from typing import Optional, Tuple, List, Dict, Any, Set
from pydantic import BaseModel, Field
from datahunt.logger import logger


class MatchStatus(str, Enum):
    MATCH = "MATCH"
    MISMATCH = "MISMATCH"
    UNKNOWN = "UNKNOWN"


class TitleCategory(str, Enum):
    DIRECT = "DIRECT"
    CLOSE = "CLOSE"
    RELATED = "RELATED"
    ADJACENT = "ADJACENT"
    MISMATCH = "MISMATCH"


GEO_ALIAS_GROUPS: Dict[str, List[str]] = {
    "saudi": [
        "saudi", "saudi arabia", "ksa", "riyadh", "jeddah", "dammam", "khobar", "dhahran",
        "neom", "mecca", "medina", "makkah", "jubail", "yanbu", "tabuk", "taif", "abha",
        "al khobar", "qassim", "hail", "jizan", "najran"
    ],
    "uae": [
        "uae", "united arab emirates", "emirates", "dubai", "abu dhabi", "abu-dhabi",
        "sharjah", "ajman", "ras al khaimah", "fujairah", "umm al quwain", "al ain"
    ],
    "egypt": ["egypt", "cairo", "alexandria", "giza"],
    "qatar": ["qatar", "doha"],
    "kuwait": ["kuwait", "kuwait city"],
    "bahrain": ["bahrain", "manama"],
    "oman": ["oman", "muscat"],
    "gcc": ["gcc", "gulf", "arabian gulf", "khaleej", "remote - gcc", "remote gcc"],
    "mena": [
        "mena", "middle east", "middle-east", "middle east and north africa", "near east",
        "remote - mena", "remote mena", "remote - middle east", "remote middle east"
    ],
    "india": [
        "india", "bangalore", "bengaluru", "mumbai", "delhi", "new delhi", "hyderabad",
        "pune", "chennai", "gurgaon", "gurugram", "noida", "kolkata", "ahmedabad"
    ],
    "uk": [
        "uk", "united kingdom", "britain", "england", "london", "manchester", "birmingham",
        "edinburgh", "glasgow", "scotland", "bristol", "cambridge", "oxford"
    ],
    "us": [
        "us", "usa", "united states", "america", "san francisco", "new york", "seattle",
        "austin", "boston", "denver", "chicago", "california", "texas", "colorado",
        "washington", "los angeles", "san jose", "sunnyvale", "mountain view"
    ],
    "eu": [
        "germany", "france", "netherlands", "ireland", "spain", "italy", "berlin",
        "paris", "amsterdam", "dublin", "europe"
    ],
    "remote": ["remote", "worldwide", "global", "anywhere", "work from home", "wfh"],
}

REGIONAL_MEMBERS: Dict[str, Set[str]] = {
    "gcc": {"saudi", "uae", "qatar", "kuwait", "bahrain", "oman", "gcc"},
    "mena": {"saudi", "uae", "qatar", "kuwait", "bahrain", "oman", "egypt", "gcc", "mena"},
}

COUNTRY_CLUSTER_FOR = {alias: cluster for cluster, aliases in GEO_ALIAS_GROUPS.items() for alias in aliases}


def match_location(requested: Optional[Any], actual: Optional[str], remote_ok: bool = True) -> Tuple[MatchStatus, str]:
    """
    Evaluates whether actual job location satisfies requested location constraint(s).
    Supports single strings (e.g. 'Saudi Arabia or UAE') or lists (e.g. ['Saudi Arabia', 'UAE']).
    Strict OR semantics: MATCH if job belongs to ANY requested location cluster.
    """
    if not requested:
        return MatchStatus.MATCH, "No location constraint"

    if not actual or actual.strip().lower() in ("", "not specified", "n/a", "null", "none"):
        return MatchStatus.UNKNOWN, "Location not disclosed in job listing"

    act_lower = actual.lower().strip()

    # Collect requested locations as normalized list
    if isinstance(requested, list):
        req_list = [str(r).lower().strip() for r in requested if r]
    else:
        req_str = str(requested).lower().strip()
        if " or " in req_str:
            req_list = [p.strip() for p in re.split(r'\s+or\s+', req_str, flags=re.IGNORECASE) if p.strip()]
        elif " and " in req_str:
            req_list = [p.strip() for p in re.split(r'\s+and\s+', req_str, flags=re.IGNORECASE) if p.strip()]
        else:
            req_list = [req_str]

    # Direct substring match
    for r in req_list:
        if r in act_lower or act_lower in r:
            return MatchStatus.MATCH, f"Direct location match: '{actual}'"

    # Identify requested clusters
    req_clusters: Set[str] = set()
    for r in req_list:
        cluster = COUNTRY_CLUSTER_FOR.get(r)
        if cluster:
            req_clusters.add(cluster)
        else:
            for alias, c in COUNTRY_CLUSTER_FOR.items():
                if re.search(r'\b' + re.escape(alias) + r'\b', r):
                    req_clusters.add(c)

    # Identify actual cluster
    act_cluster = COUNTRY_CLUSTER_FOR.get(act_lower)
    if not act_cluster:
        for alias, c in COUNTRY_CLUSTER_FOR.items():
            if re.search(r'\b' + re.escape(alias) + r'\b', act_lower):
                act_cluster = c
                break

    # Foreign anchor check for remote jobs (e.g. "Denver, CO (Remote)" or "US Only Remote")
    has_foreign_anchor = any(
        re.search(r'\b' + re.escape(alias) + r'\b', act_lower)
        for g in ("us", "uk", "india", "egypt")
        if g not in req_clusters
        for alias in GEO_ALIAS_GROUPS.get(g, [])
    )

    # Check for global remote
    if any(k in act_lower for k in ("worldwide", "global", "anywhere", "remote worldwide")):
        if not has_foreign_anchor:
            return MatchStatus.MATCH, "Worldwide remote matches any location"
        return MatchStatus.MISMATCH, f"Remote restricted to foreign anchor: '{actual}'"

    # Check plain unspecified remote
    if act_lower in ("remote", "remote / unspecified", "work from home", "wfh") or act_cluster == "remote":
        if has_foreign_anchor:
            return MatchStatus.MISMATCH, f"Remote job restricted to foreign anchor: '{actual}'"
        if remote_ok:
            return MatchStatus.UNKNOWN, "Remote job with unspecified geography"
        return MatchStatus.MISMATCH, "On-site requested, but job is remote"

    if req_clusters and act_cluster:
        if act_cluster in req_clusters:
            return MatchStatus.MATCH, f"Same region cluster: {act_cluster.upper()}"

        # Regional cluster hierarchy matching (e.g. GCC/MENA)
        if act_cluster in REGIONAL_MEMBERS:
            if any(rc in REGIONAL_MEMBERS[act_cluster] for rc in req_clusters):
                return MatchStatus.MATCH, f"Job region '{act_cluster.upper()}' encompasses requested location"

        for rc in req_clusters:
            if rc in REGIONAL_MEMBERS and act_cluster in REGIONAL_MEMBERS[rc]:
                return MatchStatus.MATCH, f"Job in '{act_cluster.upper()}' is within requested region '{rc.upper()}'"

        return MatchStatus.MISMATCH, f"Location cluster mismatch: requested={list(req_clusters)}, found={act_cluster.upper()}"

    if req_clusters and not act_cluster:
        return MatchStatus.UNKNOWN, f"Could not classify job location: '{actual}'"

    return MatchStatus.UNKNOWN, f"Cannot determine if '{actual}' matches '{requested}'"


def match_experience(
    req_min: Optional[int],
    req_max: Optional[int],
    job_min: Optional[int],
    job_max: Optional[int]
) -> Tuple[MatchStatus, str]:
    """
    Evaluates whether job experience requirement aligns with user profile.
    Missing experience info = UNKNOWN (not MISMATCH).
    Strict mismatch when job requires more than user's stated maximum or caps below user's minimum.
    """
    if req_min is None and req_max is None:
        return MatchStatus.MATCH, "No experience constraint"

    if job_min is None and job_max is None:
        return MatchStatus.UNKNOWN, "Experience requirements not specified in job listing"

    if req_max is not None and job_min is not None and job_min > req_max:
        return MatchStatus.MISMATCH, f"Job requires {job_min}+ years but user has up to {req_max} years"

    if req_min is not None and job_max is not None and job_max < req_min - 1:
        return MatchStatus.MISMATCH, f"Job caps at {job_max} years but user profile minimum is {req_min} years"

    return MatchStatus.MATCH, "Experience range compatible"


# ─────────────────────────────────────────────────────────────────────────────
# Robust Token & Phrase Skill Matching (Section 2, Invariants A & B)
# ─────────────────────────────────────────────────────────────────────────────

SKILL_ALIASES: Dict[str, List[str]] = {
    "langchain": ["langchain", "lang chain", "lang-chain"],
    "langgraph": ["langgraph", "lang graph", "lang-graph"],
    "llamaindex": ["llamaindex", "llama index", "llama-index"],
    "fastapi": ["fastapi", "fast api", "fast-api"],
    "llm": ["llm", "llms", "large language model", "large language models", "foundation model", "foundation models"],
    "rag": ["rag", "rags", "retrieval augmented generation", "retrieval-augmented generation"],
    "pytorch": ["pytorch", "torch", "py torch", "py-torch"],
    "tensorflow": ["tensorflow", "tf", "tensor flow", "tensor-flow"],
    "kubernetes": ["kubernetes", "k8s", "kube"],
    "docker": ["docker", "containerization"],
    "python": ["python", "python3", "python 3", "python programming", "python development", "python developer"],
    "genai": ["genai", "gen ai", "generative ai", "generative-ai"],
    "openai": ["openai", "open ai", "gpt", "gpt-4", "gpt4", "chatgpt"],
    "aws bedrock": ["aws bedrock", "amazon bedrock", "bedrock"],
    "azure openai": ["azure openai", "azure open ai"],
    "azure": ["azure", "microsoft azure"],
    "aws": ["aws", "amazon web services"],
    "gcp": ["gcp", "google cloud", "google cloud platform"],
    "rust": ["rust", "rustlang"],
    "golang": ["golang", "go programming", "go developer", "golang developer"],
    "sql": ["sql", "mysql", "postgresql", "postgres"],
    "postgresql": ["postgresql", "postgres"],
    "react": ["react", "reactjs", "react.js"],
    "typescript": ["typescript", "ts"],
    "prompt engineering": ["prompt engineer", "prompt engineers", "prompt engineering", "prompt design"],
}

SKILL_CANONICAL: Dict[str, str] = {}
for canonical, aliases in SKILL_ALIASES.items():
    for alias in aliases:
        SKILL_CANONICAL[alias.lower()] = canonical


def normalize_skill(skill: str) -> str:
    s = skill.lower().strip()
    return SKILL_CANONICAL.get(s, s)


def check_skill_present(skill: str, text: str) -> bool:
    """
    Checks if a skill is present in text using normalized token/phrase matching.
    Guards against false positives (e.g. 'Python-like syntax' does NOT match Python).
    Distinguishes specific services (e.g. AWS Bedrock vs Azure OpenAI vs Azure).
    """
    if not skill or not text:
        return False

    norm = normalize_skill(skill)
    aliases = SKILL_ALIASES.get(norm, [norm, skill.lower().strip()])
    text_lower = text.lower()

    for alias in aliases:
        # Avoid single-letter or tiny false positive substring matches
        if len(alias) <= 2:
            pattern = re.compile(r'(?<![a-zA-Z0-9])' + re.escape(alias) + r'(?![a-zA-Z0-9])', re.IGNORECASE)
        else:
            pattern = re.compile(r'(?<!non-)(?<![a-zA-Z0-9])' + re.escape(alias) + r'(?![a-zA-Z0-9])', re.IGNORECASE)

        matches = list(pattern.finditer(text_lower))
        if not matches:
            continue

        # Check negative lookahead context like "python-like" or "like syntax"
        for m in matches:
            end = m.end()
            after = text_lower[end:end + 12]
            if re.match(r'^\s*[-–]?\s*like\b', after):
                continue
            return True

    return False


def match_skills(required: List[str], job_text: str) -> Tuple[MatchStatus, List[str], List[str]]:
    """
    Evaluates required explicit skills against job text.
    Invariant A: All explicitly requested skills must be present.
    Missing any explicit skill yields MatchStatus.MISMATCH (never UNKNOWN).
    Returns (MatchStatus, matched_skills, missing_skills).
    """
    if not required:
        return MatchStatus.MATCH, [], []

    matched: List[str] = []
    missing: List[str] = []

    for skill in required:
        if check_skill_present(skill, job_text):
            matched.append(skill)
        else:
            missing.append(skill)

    if not missing:
        return MatchStatus.MATCH, matched, []
    else:
        # Invariant A: Partial explicit match is a hard MISMATCH
        return MatchStatus.MISMATCH, matched, missing


# ─────────────────────────────────────────────────────────────────────────────
# Title Relevance & Anti-Role Family Matching (Section 7)
# ─────────────────────────────────────────────────────────────────────────────

DISQUALIFYING_JOB_FAMILIES = [
    ("product_management", [
        "product manager", "product owner", "technical product manager",
        "product lead", "head of product", "vp product", "director of product"
    ]),
    ("sales_marketing", [
        "sales manager", "sales executive", "sales representative", "account executive",
        "business development", "bdr", "sdr", "sales specialist", "marketing manager",
        "marketing specialist", "growth specialist", "seo specialist"
    ]),
    ("business_analysis", [
        "business analyst", "bi analyst", "operations analyst", "strategy analyst", "financial analyst"
    ]),
    ("content_writing", [
        "content writer", "copywriter", "technical writer", "content creator",
        "editor", "content strategist"
    ]),
    ("human_resources", [
        "recruiter", "talent acquisition", "human resources", "hr manager",
        "people operations", "people partner"
    ]),
    ("qa_testing", [
        "qa engineer", "quality assurance", "test automation", "software tester",
        "manual tester", "sdit"
    ]),
    ("support_it", [
        "service desk", "desktop support", "it support", "help desk",
        "system administrator", "sysadmin"
    ]),
    ("cybersecurity", [
        "cybersecurity", "cyber security", "infosec", "soc analyst",
        "penetration tester", "security analyst"
    ]),
    ("finance_accounting", [
        "accountant", "auditor", "finance manager", "bookkeeper"
    ]),
    ("full_stack", [
        "full stack", "fullstack", "frontend", "front end", "web developer"
    ]),
]


def match_title_relevance(
    req_title: Optional[str],
    job_title: Optional[str],
    alternative_titles: Optional[List[str]] = None
) -> Tuple[MatchStatus, float, str]:
    """
    Evaluates whether job_title matches the user's requested role using semantic categories.
    Guarantees generic AI terms alone cannot rescue unrelated job families (Section 7).
    Returns (MatchStatus, relevance_score: float, reason: str).
    """
    if not req_title or not req_title.strip():
        return MatchStatus.MATCH, 1.0, "No title constraint"

    if not job_title or not job_title.strip():
        return MatchStatus.UNKNOWN, 0.3, "Job title not disclosed"

    req_t = req_title.lower().strip()
    job_t = job_title.lower().strip()

    # Exact equality
    if req_t == job_t:
        return MatchStatus.MATCH, 1.0, f"Exact title match: '{job_title}'"

    # Check whether the query is for a technical / engineering role
    is_tech_role = any(
        k in req_t for k in (
            "engineer", "developer", "architect", "programmer", "scientist",
            "applied ai", "ml", "genai", "generative ai", "consultant"
        )
    )

    # 1. Anti-role / Disqualifying job family check
    # Unrelated job families (Product, Sales, HR, Content, etc.) MUST be rejected
    # even if they contain 'AI' or 'GenAI' (e.g. 'AI Product Manager' -> MISMATCH)
    if is_tech_role:
        for family_name, bad_terms in DISQUALIFYING_JOB_FAMILIES:
            # If the user did not explicitly ask for this family:
            if not any(bt in req_t for bt in bad_terms):
                for bt in bad_terms:
                    if re.search(r'\b' + re.escape(bt) + r'\b', job_t):
                        return (
                            MatchStatus.MISMATCH,
                            0.0,
                            f"Title '{job_title}' belongs to unrelated family '{family_name}', not requested engineering role"
                        )

    # 2. GenAI specific semantic matching
    if any(k in req_t for k in ("genai", "generative ai")):
        STRONG_GENAI_TERMS = [
            "genai", "gen ai", "generative ai", "generative-ai", "llm", "large language model",
            "large language models", "foundation model", "foundation models", "prompt engineer",
            "prompt engineering", "rag engineer", "agentic", "ai agent", "ai agents"
        ]
        if any(term in job_t for term in STRONG_GENAI_TERMS):
            return MatchStatus.MATCH, 1.0, f"Direct GenAI title match: '{job_title}'"

        AI_ENG_TERMS = [
            "applied ai", "ai engineer", "artificial intelligence engineer", "ai platform engineer",
            "ai developer", "ai software engineer", "ai solutions engineer", "ai solutions architect",
            "ai architect", "ai consultant", "artificial intelligence consultant", "generative ai consultant",
            "ai technical lead", "ai lead", "head of ai", "director of ai", "ai specialist",
            "ai application developer", "ai infrastructure engineer", "ai systems engineer", "ai research engineer"
        ]
        if any(term in job_t for term in AI_ENG_TERMS):
            return MatchStatus.MATCH, 0.95, f"AI engineering role matches GenAI request: '{job_title}'"

        ML_TERMS = [
            "machine learning engineer", "ml engineer", "software engineer - ai", "deep learning engineer",
            "nlp engineer", "natural language processing", "computer vision engineer", "ml platform engineer",
            "mlops engineer", "applied scientist", "research scientist - ai", "ai researcher"
        ]
        if any(term in job_t for term in ML_TERMS):
            return MatchStatus.MATCH, 0.85, f"Related ML/AI role aligns with GenAI request: '{job_title}'"

        # Legitimate whole-word AI role pattern (e.g. "Software Engineer, AI" or "Staff Software Engineer, AI")
        if re.search(r'\bai\b', job_t) or re.search(r'\bgen\s*ai\b', job_t):
            # Must still have a technical role marker
            if any(term in job_t for term in ("engineer", "developer", "architect", "programmer", "scientist", "consultant")):
                return MatchStatus.MATCH, 0.85, f"AI-focused technical role matches GenAI request: '{job_title}'"

        if "data scientist" in job_t:
            return MatchStatus.UNKNOWN, 0.40, f"Data science title '{job_title}' may have partial GenAI overlap"

        return MatchStatus.MISMATCH, 0.0, f"Title '{job_title}' does not match requested GenAI role"

    # 3. Check alternative titles
    all_targets = [req_t]
    if alternative_titles:
        all_targets.extend([t.lower().strip() for t in alternative_titles if t])

    for target in all_targets:
        if target and (target == job_t or target in job_t):
            return MatchStatus.MATCH, 0.85, f"Title matches related role '{target}'"

    # 4. Non-AI / general role token overlap
    STOP_WORDS = {"engineer", "developer", "lead", "senior", "junior", "staff", "principal", "manager", "jobs", "specialist"}
    req_words = [w for w in req_t.split() if len(w) > 2 and w not in STOP_WORDS]
    if req_words:
        matched_words = [w for w in req_words if w in job_t]
        overlap_ratio = len(matched_words) / len(req_words)
        if overlap_ratio >= 0.5:
            return MatchStatus.MATCH, round(0.5 + 0.4 * overlap_ratio, 2), f"Title partially matches: {', '.join(matched_words)}"
        elif overlap_ratio > 0:
            return MatchStatus.UNKNOWN, 0.40, f"Weak token overlap in title: {', '.join(matched_words)}"
    else:
        all_words = [w for w in req_t.split() if len(w) > 2]
        matched = [w for w in all_words if w in job_t]
        if matched and len(matched) / len(all_words) >= 0.5:
            return MatchStatus.MATCH, 0.70, f"Title matches role terms: {', '.join(matched)}"

    return MatchStatus.MISMATCH, 0.0, f"Title '{job_title}' does not match requested '{req_title}'"


# ─────────────────────────────────────────────────────────────────────────────
# Authoritative QualificationPolicy Engine (Invariant D)
# ─────────────────────────────────────────────────────────────────────────────

class QualificationResult(BaseModel):
    """Structured evaluation of whether a job candidate satisfies user constraints."""
    qualified: bool
    eligibility_status: str = "ELIGIBLE"  # "ELIGIBLE" | "INELIGIBLE"
    title_category: TitleCategory = TitleCategory.MISMATCH
    title_match: MatchStatus
    location_match: MatchStatus
    experience_match: MatchStatus
    skill_match: MatchStatus = MatchStatus.UNKNOWN
    salary_match: MatchStatus = MatchStatus.MATCH
    required_skills: List[str] = Field(default_factory=list)
    matched_skills: List[str] = Field(default_factory=list)
    missing_skills: List[str] = Field(default_factory=list)
    rejection_reasons: List[str] = Field(default_factory=list)
    reasons: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    score: float = 0.0
    score_breakdown: Dict[str, Any] = Field(default_factory=dict)


class QualificationPolicy:
    """
    Single authoritative qualification policy for all job evaluations in DataHunt.
    Every hard constraint is evaluated deterministically.
    """

    def evaluate_title(
        self,
        req_title: Optional[str],
        job_title: Optional[str],
        alt_titles: Optional[List[str]] = None
    ) -> Tuple[TitleCategory, MatchStatus, float, str]:
        status, score, reason = match_title_relevance(req_title, job_title, alt_titles)
        if status == MatchStatus.MISMATCH:
            cat = TitleCategory.MISMATCH
        elif score >= 0.98:
            cat = TitleCategory.DIRECT
        elif score >= 0.90:
            cat = TitleCategory.CLOSE
        elif score >= 0.70:
            cat = TitleCategory.RELATED
        else:
            cat = TitleCategory.ADJACENT
        return cat, status, score, reason

    def evaluate_location(
        self,
        requested: Optional[Any],
        actual: Optional[str],
        remote_ok: bool = True
    ) -> Tuple[MatchStatus, str]:
        return match_location(requested, actual, remote_ok=remote_ok)

    def evaluate_skills(
        self,
        explicit_required: List[str],
        job_text: str,
        inferred: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        status, matched, missing = match_skills(explicit_required, job_text)
        inf_matched = []
        if inferred:
            _, inf_matched, _ = match_skills(inferred, job_text)

        return {
            "required": explicit_required,
            "matched": matched,
            "missing": missing,
            "status": status,
            "inferred_matched": inf_matched,
        }

    def evaluate_experience(
        self,
        req_min: Optional[int],
        req_max: Optional[int],
        job_min: Optional[int],
        job_max: Optional[int]
    ) -> Tuple[MatchStatus, str]:
        return match_experience(req_min, req_max, job_min, job_max)

    def evaluate_salary(
        self,
        req_min: Optional[float],
        req_curr: Optional[str],
        job_min: Optional[float],
        job_max: Optional[float],
        job_curr: Optional[str],
        is_multi_region: bool = False
    ) -> Tuple[MatchStatus, str]:
        if req_min is None:
            return MatchStatus.MATCH, "No salary requirement specified"

        # Multi-region without explicit single currency must not guess currency
        if is_multi_region and (not req_curr or req_curr == "USD"):
            return MatchStatus.MATCH, "Multi-region query; salary floor not strictly enforced without explicit currency"

        if job_min is None and job_max is None:
            return MatchStatus.UNKNOWN, "Salary not disclosed in job listing"

        # If both disclosed in same currency, enforce floor
        if req_curr and job_curr and req_curr.upper() == job_curr.upper():
            if job_max and job_max < req_min * 0.85:
                return MatchStatus.MISMATCH, f"Disclosed max salary ({job_curr} {job_max}) below minimum floor ({req_min})"
            if not job_max and job_min and job_min < req_min * 0.80:
                return MatchStatus.MISMATCH, f"Disclosed min salary ({job_curr} {job_min}) below minimum floor ({req_min})"

        return MatchStatus.MATCH, "Salary satisfies requirements or is unspecified"

    def qualify(
        self,
        job_record_or_dict: Any,
        job_req: Any,
        expanded: Optional[Any] = None
    ) -> QualificationResult:
        """
        Authoritative qualification of a job against a canonical JobSearchRequest / JobSearchSpec.
        A job qualifies if and only if ALL hard constraints pass.
        """
        fields = getattr(job_record_or_dict, "fields", None)
        if fields is None and isinstance(job_record_or_dict, dict):
            fields = job_record_or_dict
        elif fields is None and hasattr(job_record_or_dict, "model_dump"):
            fields = job_record_or_dict.model_dump()
        elif fields is None and hasattr(job_record_or_dict, "dict"):
            fields = job_record_or_dict.dict()
        fields = fields or {}

        title = fields.get("title") or fields.get("job_title") or getattr(job_record_or_dict, "title", "")
        company = fields.get("company") or fields.get("company_name") or getattr(job_record_or_dict, "company", "")
        location = fields.get("location") or getattr(job_record_or_dict, "location", "")
        remote_val = fields.get("remote") if "remote" in fields else getattr(job_record_or_dict, "remote", None)
        remote_status = fields.get("remote_status") or ("remote" if remote_val else getattr(job_record_or_dict, "remote_status", "onsite"))
        is_active = fields.get("is_active", True) if "is_active" in fields else getattr(job_record_or_dict, "is_active", True)

        # Parse experience from fields
        exp_min = fields.get("experience_min") or fields.get("experience_min_years")
        exp_max = fields.get("experience_max") or fields.get("experience_max_years")
        if isinstance(exp_min, str):
            try:
                m = re.search(r'\d+', exp_min)
                exp_min = int(m.group(0)) if m else None
            except Exception:
                exp_min = None
        if isinstance(exp_max, str):
            try:
                m = re.search(r'\d+', exp_max)
                exp_max = int(m.group(0)) if m else None
            except Exception:
                exp_max = None

        # Parse salary from fields
        sal_min = fields.get("salary_min") or fields.get("salary_min_annual")
        sal_max = fields.get("salary_max") or fields.get("salary_max_annual")
        sal_curr = fields.get("salary_currency") or "USD"

        # Canonical request attributes
        req_title = getattr(job_req, "job_title", "") or (job_req.titles[0] if getattr(job_req, "titles", None) else "")
        req_locations = getattr(job_req, "locations", None) or ([job_req.location] if getattr(job_req, "location", None) else [])
        req_exp_min = getattr(job_req, "experience_min", None)
        req_exp_max = getattr(job_req, "experience_max", None)
        req_explicit_skills = getattr(job_req, "explicit_skills", None)
        if req_explicit_skills is None:
            q_low = (getattr(job_req, "raw_query", "") or "").lower()
            if any(k in q_low for k in ("requiring", "requires", "must have", "skills:", "tech stack", "with skills")):
                req_explicit_skills = getattr(job_req, "skills", []) or []
            else:
                req_explicit_skills = []
        req_inferred_skills = getattr(job_req, "inferred_skills", []) or []

        req_sal_min = getattr(job_req, "salary_min", None)
        req_sal_curr = getattr(job_req, "salary_currency", None)
        req_remote_allowed = getattr(job_req, "remote_allowed", True)
        if req_remote_allowed is None:
            req_remote_allowed = True
        req_excluded_titles = getattr(job_req, "excluded_titles", []) or []
        req_excluded_companies = getattr(job_req, "excluded_companies", []) or []

        reasons: List[str] = []
        warnings: List[str] = []
        rejection_reasons: List[str] = []

        # 1. Title Policy
        t_cat, t_status, t_score, t_reason = self.evaluate_title(
            req_title, title, getattr(job_req, "alternative_titles", [])
        )
        reasons.append(t_reason)
        if t_status == MatchStatus.MISMATCH or t_cat == TitleCategory.MISMATCH:
            rejection_reasons.append(f"title_job_family_mismatch:{title}")

        # 2. Location & Remote Policy
        l_status, l_reason = self.evaluate_location(req_locations, location, remote_ok=req_remote_allowed)
        if l_status == MatchStatus.UNKNOWN:
            warnings.append(l_reason)
        else:
            reasons.append(l_reason)

        has_strict_location = bool(req_locations and any(loc.lower() not in ("any", "worldwide", "global") for loc in req_locations))
        if has_strict_location:
            if l_status == MatchStatus.MISMATCH:
                rejection_reasons.append(f"location_mismatch:{location}")
            elif l_status == MatchStatus.UNKNOWN:
                is_agnostic_remote = (
                    req_remote_allowed
                    and remote_status == "remote"
                    and location.lower().strip() in ("remote", "remote / unspecified", "worldwide", "global", "anywhere", "work from home", "wfh")
                )
                if not is_agnostic_remote:
                    rejection_reasons.append(f"unverified_location:{location or 'undisclosed'}")

        req_remote_status = (getattr(job_req, "remote_status", None) or "").lower()
        if req_remote_status == "remote" and remote_status == "onsite":
            if req_locations and not any(loc.lower() in location.lower() for loc in req_locations):
                rejection_reasons.append(f"remote_mismatch:Strict remote requested, but job is onsite in {location}")
        elif req_remote_status == "onsite" and remote_status == "remote" and not req_remote_allowed:
            rejection_reasons.append("remote_mismatch:Strict onsite requested, but job is remote")

        # 3. Experience Policy
        e_status, e_reason = self.evaluate_experience(req_exp_min, req_exp_max, exp_min, exp_max)
        if e_status == MatchStatus.UNKNOWN:
            warnings.append(e_reason)
        else:
            reasons.append(e_reason)
        if e_status == MatchStatus.MISMATCH:
            rejection_reasons.append(f"experience_mismatch:{e_reason}")

        # 4. Explicit Skills Policy (Invariant A: hard gates)
        job_skills = fields.get("skills") or getattr(job_record_or_dict, "skills", []) or []
        job_blob = f"{title} {company} {location} {' '.join(str(s) for s in job_skills)} {' '.join(str(v) for v in fields.values())}"
        skill_res = self.evaluate_skills(req_explicit_skills, job_blob, inferred=req_inferred_skills)
        matched_s = skill_res["matched"]
        missing_s = skill_res["missing"]
        s_status = skill_res["status"]

        if matched_s:
            reasons.append(f"Explicit skills matched: {', '.join(matched_s)}")
        if missing_s:
            reasons.append(f"Disqualified: Missing required explicit skills ({', '.join(missing_s)})")
            for m in missing_s:
                rejection_reasons.append(f"missing_explicit_skill:{m.lower()}")


        # 4b. Inferred skills policy (Invariant B: ranking only, never a hard gate)
        inf_matched = skill_res.get("inferred_matched", [])
        if inf_matched:
            reasons.append(f"Inferred domain skills matched: {', '.join(inf_matched)}")

        # 5. Salary Policy
        is_multi_reg = len(req_locations) > 1 or " or " in (getattr(job_req, "location", "") or "").lower()
        sal_status, sal_reason = self.evaluate_salary(
            req_sal_min, req_sal_curr, sal_min, sal_max, sal_curr, is_multi_region=is_multi_reg
        )
        if sal_status == MatchStatus.MISMATCH:
            rejection_reasons.append(f"salary_mismatch:{sal_reason}")

        # 6. Exclusions & Active Status
        if not is_active:
            rejection_reasons.append("inactive_posting")
        if req_excluded_titles and any(et.lower() in title.lower() for et in req_excluded_titles):
            rejection_reasons.append(f"excluded_title:{title}")
        if req_excluded_companies and any(ec.lower() in company.lower() for ec in req_excluded_companies):
            rejection_reasons.append(f"excluded_company:{company}")

        # Hard Qualification Determination
        is_qualified = (len(rejection_reasons) == 0)
        eligibility_status = "ELIGIBLE" if is_qualified else "INELIGIBLE"

        # Transparent Scoring Breakdown
        t_val = 1.0 if t_status == MatchStatus.MATCH else (0.4 if t_status == MatchStatus.UNKNOWN else 0.0)
        l_val = 1.0 if l_status == MatchStatus.MATCH else (0.5 if l_status == MatchStatus.UNKNOWN else 0.0)
        e_val = 1.0 if e_status == MatchStatus.MATCH else (0.5 if e_status == MatchStatus.UNKNOWN else 0.0)
        s_val = 1.0 if s_status == MatchStatus.MATCH else (0.5 if s_status == MatchStatus.UNKNOWN else 0.0)

        inf_modifier = 0.0
        if req_inferred_skills:
            inf_ratio = len(inf_matched) / len(req_inferred_skills)
            inf_modifier = round((inf_ratio - 0.5) * 0.10, 2)

        base_score = round(min(max(0.40 * t_val + 0.30 * l_val + 0.15 * e_val + 0.15 * s_val + inf_modifier, 0.05), 1.0), 2)
        total_score = base_score if is_qualified else min(base_score, 0.20)

        score_breakdown = {
            "title": {"status": t_status.value, "category": t_cat.value, "score": t_val},
            "location": {"status": l_status.value, "score": l_val},
            "experience": {"status": e_status.value, "score": e_val},
            "skills": {"status": s_status.value, "score": s_val, "matched": matched_s, "missing": missing_s},
            "inferred_skills": {"matched": inf_matched, "modifier": inf_modifier},
        }

        return QualificationResult(
            qualified=is_qualified,
            eligibility_status=eligibility_status,
            title_category=t_cat,
            title_match=t_status,
            location_match=l_status,
            experience_match=e_status,
            skill_match=s_status,
            salary_match=sal_status,
            required_skills=req_explicit_skills,
            matched_skills=matched_s,
            missing_skills=missing_s,
            rejection_reasons=rejection_reasons,
            reasons=reasons,
            warnings=warnings,
            score=total_score,
            score_breakdown=score_breakdown,
        )


_DEFAULT_POLICY = QualificationPolicy()


def qualify_job(
    job_record_or_dict: Any,
    job_req: Any,
    expanded: Optional[Any] = None
) -> QualificationResult:
    """Authoritative qualification delegating to QualificationPolicy."""
    return _DEFAULT_POLICY.qualify(job_record_or_dict, job_req, expanded)


class GoalEvaluator:
    """Evaluates whether the agent objective has been satisfied based on useful qualified jobs."""

    def is_satisfied(self, state) -> Tuple[bool, str]:
        if state.mode in ("jobs", "job"):
            n = len(state.qualified_records)
            target = state.target_results
            if n >= target:
                return True, f"Found {n} qualified results (target: {target})"
            return False, f"Found {n}/{target} qualified results"
        else:
            n = len(state.verified_records)
            target = state.target_results
            if n >= target:
                return True, f"Found {n} verified results (target: {target})"
            return False, f"Found {n}/{target} results"

    def quality_summary(self, state) -> dict:
        return {
            "verified": len(state.verified_records),
            "qualified": len(state.qualified_records),
            "rejected": len(state.rejected_records),
            "target": state.target_results,
            "search_iterations": len(state.search_iterations),
            "total_candidates": len(state.candidate_urls) + len(state.raw_records),
            "low_yield_streak": state.consecutive_low_yield_iterations,
            "warnings": len(state.warnings),
        }
