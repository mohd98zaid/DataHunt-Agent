"""
Application-level evaluation policies.
These are deterministic rules; LLM never overrides them.
"""
import re
from enum import Enum
from typing import Optional, Tuple, List

class MatchStatus(str, Enum):
    MATCH = "MATCH"
    MISMATCH = "MISMATCH"
    UNKNOWN = "UNKNOWN"


GEO_ALIAS_GROUPS = {
    "saudi": ["saudi", "saudi arabia", "ksa", "riyadh", "jeddah", "dammam", "khobar", "dhahran", "neom", "mecca", "medina", "makkah", "jubail", "yanbu"],
    "uae": ["uae", "united arab emirates", "emirates", "dubai", "abu dhabi", "abu-dhabi", "sharjah", "ajman", "ras al khaimah", "fujairah", "umm al quwain", "al ain"],
    "egypt": ["egypt", "cairo", "alexandria", "giza"],
    "qatar": ["qatar", "doha"],
    "kuwait": ["kuwait", "kuwait city"],
    "bahrain": ["bahrain", "manama"],
    "oman": ["oman", "muscat"],
    "india": ["india", "bangalore", "bengaluru", "mumbai", "delhi", "hyderabad", "pune", "chennai", "gurgaon", "noida", "kolkata"],
    "uk": ["uk", "united kingdom", "britain", "england", "london", "manchester", "birmingham", "edinburgh", "scotland"],
    "us": ["us", "usa", "united states", "america", "san francisco", "new york", "seattle", "austin", "boston", "denver", "chicago", "california", "texas", "colorado"],
    "eu": ["germany", "france", "netherlands", "ireland", "spain", "italy", "berlin", "paris", "amsterdam", "dublin"],
    "remote": ["remote", "worldwide", "global", "anywhere", "work from home", "wfh"],
}

COUNTRY_CLUSTER_FOR = {alias: cluster for cluster, aliases in GEO_ALIAS_GROUPS.items() for alias in aliases}


def match_location(requested: Optional[Any], actual: Optional[str], remote_ok: bool = True) -> Tuple[MatchStatus, str]:
    """
    Returns (MatchStatus, reason).
    Supports requested being a single string or a list of locations (e.g. ["Saudi Arabia", "UAE"]).
    Never rejects an UNKNOWN location — let the verifier/evaluator decide.
    """
    if not requested:
        return MatchStatus.MATCH, "No location constraint"

    if not actual or actual.strip().lower() in ("", "not specified", "n/a", "null", "none"):
        return MatchStatus.UNKNOWN, "Location not disclosed in job listing"

    act_lower = actual.lower().strip()

    # Collect requested locations as list
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

    # Direct match check
    for r in req_list:
        if r in act_lower or act_lower in r:
            return MatchStatus.MATCH, f"Direct location match: '{actual}'"

    # Identify requested clusters
    req_clusters = set()
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

    # Check for global remote
    if any(k in act_lower for k in ("worldwide", "global", "anywhere", "remote worldwide")):
        # Only global if not constrained to a specific foreign country
        has_foreign_anchor = any(
            re.search(r'\b' + re.escape(alias) + r'\b', act_lower)
            for g in ("us", "uk", "india", "egypt")
            for alias in GEO_ALIAS_GROUPS.get(g, [])
        )
        if not has_foreign_anchor:
            return MatchStatus.MATCH, "Worldwide remote matches any location"

    # Check plain unspecified remote
    if act_lower in ("remote", "remote / unspecified", "work from home", "wfh") or act_cluster == "remote":
        if remote_ok:
            return MatchStatus.UNKNOWN, "Remote job with unspecified geography"
        return MatchStatus.MISMATCH, "On-site requested, but job is remote"

    if req_clusters and act_cluster:
        if act_cluster in req_clusters:
            return MatchStatus.MATCH, f"Same region cluster: {act_cluster.upper()}"
        return MatchStatus.MISMATCH, f"Location cluster mismatch: requested={list(req_clusters)}, found={act_cluster.upper()}"

    if req_clusters and not act_cluster:
        return MatchStatus.UNKNOWN, f"Could not classify job location: '{actual}'"

    return MatchStatus.UNKNOWN, f"Cannot determine if '{actual}' matches '{requested}'"


def match_experience(req_min: Optional[int], req_max: Optional[int], job_min: Optional[int], job_max: Optional[int]) -> Tuple[MatchStatus, str]:
    """
    Returns (MatchStatus, reason).
    Missing experience info = UNKNOWN (not MISMATCH).
    Strict mismatch when job requires more than user's stated maximum.
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


def match_title_relevance(
    req_title: Optional[str],
    job_title: Optional[str],
    alternative_titles: Optional[List[str]] = None
) -> Tuple[MatchStatus, float, str]:
    """
    Evaluates whether job_title matches the user's requested role using semantic categories.
    Returns (MatchStatus, relevance_score: float, reason: str).
    """
    if not req_title or not req_title.strip():
        return MatchStatus.MATCH, 1.0, "No title constraint"

    if not job_title or not job_title.strip():
        return MatchStatus.UNKNOWN, 0.3, "Job title not disclosed"

    req_t = req_title.lower().strip()
    job_t = job_title.lower().strip()

    # Direct exact/synonym match
    if req_t == job_t or req_t in job_t:
        return MatchStatus.MATCH, 1.0, f"Direct title match: '{job_title}'"

    # Anti-role / Disqualifying tracks for tech/AI queries
    DISQUALIFYING_TRACKS = [
        ("cybersecurity", ["cybersecurity", "cyber security", "infosec", "soc analyst", "security analyst", "penetration tester"]),
        ("full_stack", ["full stack", "fullstack", "front end", "frontend", "ui developer", "react developer", "angular developer", "web developer"]),
        ("sales_marketing", ["sales", "account executive", "marketing", "business development", "growth", "seo specialist"]),
        ("human_resources", ["hr ", "recruiter", "talent acquisition", "human resources", "people operations"]),
        ("finance_accounting", ["accountant", "auditor", "finance manager", "bookkeeper"]),
        ("qa_test", ["qa engineer", "quality assurance", "test automation", "sdit", "manual tester"]),
        ("network_it", ["network engineer", "sysadmin", "system administrator", "desktop support", "it support"]),
    ]

    is_ai_search = any(k in req_t for k in ("genai", "generative ai", "ai engineer", "llm", "machine learning", "ml engineer", "data scientist"))

    if is_ai_search:
        # Check if job title is an anti-role and does not contain GenAI/AI/LLM explicit keywords
        for track_name, bad_terms in DISQUALIFYING_TRACKS:
            if any(re.search(r'\b' + re.escape(t) + r'\b', job_t) for t in bad_terms):
                if not any(k in job_t for k in ("genai", "generative ai", "llm", "ai engineer")):
                    return MatchStatus.MISMATCH, 0.0, f"Title '{job_title}' belongs to '{track_name}' track, not requested AI/GenAI role"

    # GenAI specific semantic matching
    if any(k in req_t for k in ("genai", "generative ai")):
        STRONG_GENAI_TERMS = ["genai", "gen ai", "generative ai", "generative-ai", "llm", "large language model", "foundation model"]
        if any(term in job_t for term in STRONG_GENAI_TERMS):
            return MatchStatus.MATCH, 1.0, f"Strong GenAI title match: '{job_title}'"

        AI_ENG_TERMS = ["applied ai", "ai engineer", "artificial intelligence engineer", "ai platform engineer"]
        if any(term in job_t for term in AI_ENG_TERMS):
            return MatchStatus.MATCH, 0.9, f"AI engineering role matches GenAI request: '{job_title}'"

        ML_TERMS = ["machine learning engineer", "ml engineer", "software engineer - ai", "deep learning engineer"]
        if any(term in job_t for term in ML_TERMS):
            return MatchStatus.MATCH, 0.85, f"Related ML/AI role aligns with GenAI request: '{job_title}'"

        if "data scientist" in job_t:
            return MatchStatus.UNKNOWN, 0.4, f"Data science title '{job_title}' may have partial GenAI overlap"

        return MatchStatus.MISMATCH, 0.0, f"Title '{job_title}' does not match requested GenAI role"

    # Check alternative titles
    all_targets = [req_t]
    if alternative_titles:
        all_targets.extend([t.lower().strip() for t in alternative_titles if t])

    for target in all_targets:
        if target and (target in job_t or job_t in target):
            return MatchStatus.MATCH, 0.85, f"Title matches related role '{target}'"

    # Word overlap without counting stop-words
    STOP_WORDS = {"engineer", "developer", "lead", "senior", "junior", "staff", "principal", "manager", "jobs", "specialist"}
    req_words = [w for w in req_t.split() if len(w) > 2 and w not in STOP_WORDS]
    if req_words:
        matched_words = [w for w in req_words if w in job_t]
        overlap_ratio = len(matched_words) / len(req_words)
        if overlap_ratio >= 0.6:
            return MatchStatus.MATCH, round(0.5 + 0.4 * overlap_ratio, 2), f"Title partially matches: {', '.join(matched_words)}"
        elif overlap_ratio > 0:
            return MatchStatus.UNKNOWN, 0.4, f"Weak token overlap in title: {', '.join(matched_words)}"

    return MatchStatus.MISMATCH, 0.0, f"Title '{job_title}' does not match requested '{req_title}'"


SKILL_ALIASES = {
    "langchain": ["langchain", "lang chain", "lang-chain"],
    "fastapi": ["fastapi", "fast api", "fast-api"],
    "llm": ["llm", "large language model", "large language models", "foundation model"],
    "rag": ["rag", "retrieval augmented generation", "retrieval-augmented generation"],
    "pytorch": ["pytorch", "torch", "py torch"],
    "tensorflow": ["tensorflow", "tf", "tensor flow"],
    "kubernetes": ["kubernetes", "k8s", "kube"],
    "python": ["python", "python3", "python 3"],
    "genai": ["genai", "gen ai", "generative ai", "generative-ai"],
    "openai": ["openai", "open ai", "gpt", "gpt-4", "gpt4", "chatgpt"],
}

SKILL_CANONICAL = {}
for canonical, aliases in SKILL_ALIASES.items():
    for alias in aliases:
        SKILL_CANONICAL[alias.lower()] = canonical


def normalize_skill(skill: str) -> str:
    s = skill.lower().strip()
    return SKILL_CANONICAL.get(s, s)


def match_skills(required: List[str], job_text: str) -> Tuple[MatchStatus, List[str], List[str]]:
    """
    Returns (MatchStatus, matched_skills, missing_skills).
    Uses normalized matching, not naïve substring.
    """
    if not required:
        return MatchStatus.MATCH, [], []

    job_lower = job_text.lower()
    matched = []
    missing = []

    for skill in required:
        norm = normalize_skill(skill)
        aliases = SKILL_ALIASES.get(norm, [norm, skill.lower()])
        found = any(alias in job_lower for alias in aliases)
        if found:
            matched.append(skill)
        else:
            missing.append(skill)

    if len(missing) == 0:
        return MatchStatus.MATCH, matched, []
    elif len(matched) > 0:
        return MatchStatus.UNKNOWN, matched, missing  # partial = UNKNOWN not MISMATCH
    else:
        return MatchStatus.MISMATCH, [], missing


from pydantic import BaseModel, Field
from typing import Dict, Any


class QualificationResult(BaseModel):
    """Structured evaluation of whether a job candidate satisfies user constraints."""
    qualified: bool
    title_match: MatchStatus
    location_match: MatchStatus
    experience_match: MatchStatus
    skill_match: MatchStatus = MatchStatus.UNKNOWN
    reasons: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    score: float = 0.0
    score_breakdown: Dict[str, Any] = Field(default_factory=dict)


def qualify_job(
    job_record_or_dict: Any,
    job_req: Any,
    expanded: Optional[Any] = None
) -> QualificationResult:
    """
    Deterministic qualification of a job against a canonical JobSearchRequest.
    A job qualifies if and only if:
    - title_match != MISMATCH
    - location_match != MISMATCH
    - experience_match != MISMATCH
    - job is active
    """
    fields = getattr(job_record_or_dict, "fields", None)
    if fields is None and isinstance(job_record_or_dict, dict):
        fields = job_record_or_dict
    elif fields is None and hasattr(job_record_or_dict, "dict"):
        fields = job_record_or_dict.dict()
    fields = fields or {}

    title = fields.get("title") or fields.get("job_title") or ""
    location = fields.get("location") or ""
    
    # Parse experience from fields
    exp_min = fields.get("experience_min") or fields.get("experience_min_years")
    exp_max = fields.get("experience_max") or fields.get("experience_max_years")
    if isinstance(exp_min, str):
        try:
            exp_match = re.search(r'\d+', exp_min)
            exp_min = int(exp_match.group(0)) if exp_match else None
        except Exception:
            exp_min = None
    if isinstance(exp_max, str):
        try:
            exp_match = re.search(r'\d+', exp_max)
            exp_max = int(exp_match.group(0)) if exp_match else None
        except Exception:
            exp_max = None

    req_title = getattr(job_req, "job_title", "")
    req_locations = getattr(job_req, "locations", None) or getattr(job_req, "location", "")
    req_exp_min = getattr(job_req, "experience_min", None)
    req_exp_max = getattr(job_req, "experience_max", None)
    req_skills = getattr(job_req, "explicit_skills", None) or getattr(job_req, "skills", [])

    reasons: List[str] = []
    warnings: List[str] = []

    # 1. Title match
    t_status, t_score, t_reason = match_title_relevance(
        req_title, title, getattr(job_req, "alternative_titles", [])
    )
    reasons.append(t_reason)

    # 2. Location match
    remote_ok = getattr(job_req, "remote_allowed", True)
    l_status, l_reason = match_location(req_locations, location, remote_ok=remote_ok)
    if l_status == MatchStatus.UNKNOWN:
        warnings.append(l_reason)
    else:
        reasons.append(l_reason)

    # 3. Experience match
    e_status, e_reason = match_experience(req_exp_min, req_exp_max, exp_min, exp_max)
    if e_status == MatchStatus.UNKNOWN:
        warnings.append(e_reason)
    else:
        reasons.append(e_reason)

    # 4. Explicit skills match (inferred skills are never hard gates)
    job_blob = f"{title} {location} {' '.join(str(v) for v in fields.values())}"
    s_status, matched_s, missing_s = match_skills(req_skills, job_blob)
    if matched_s:
        reasons.append(f"Explicit skills matched: {', '.join(matched_s)}")
    if missing_s:
        warnings.append(f"Explicit skills missing: {', '.join(missing_s)}")

    # Qualification criteria: hard gates must not be confirmed MISMATCH
    is_qualified = (
        t_status != MatchStatus.MISMATCH
        and l_status != MatchStatus.MISMATCH
        and e_status != MatchStatus.MISMATCH
    )

    # Transparent scoring breakdown (title 40%, location 30%, experience 15%, skills 15%)
    t_val = 1.0 if t_status == MatchStatus.MATCH else (0.4 if t_status == MatchStatus.UNKNOWN else 0.0)
    l_val = 1.0 if l_status == MatchStatus.MATCH else (0.5 if l_status == MatchStatus.UNKNOWN else 0.0)
    e_val = 1.0 if e_status == MatchStatus.MATCH else (0.5 if e_status == MatchStatus.UNKNOWN else 0.0)
    s_val = 1.0 if s_status == MatchStatus.MATCH else (0.5 if s_status == MatchStatus.UNKNOWN else 0.2)

    total_score = round(0.40 * t_val + 0.30 * l_val + 0.15 * e_val + 0.15 * s_val, 2)
    score_breakdown = {
        "title": {"status": t_status.value, "score": t_val},
        "location": {"status": l_status.value, "score": l_val},
        "experience": {"status": e_status.value, "score": e_val},
        "skills": {"status": s_status.value, "score": s_val},
    }

    return QualificationResult(
        qualified=is_qualified,
        title_match=t_status,
        location_match=l_status,
        experience_match=e_status,
        skill_match=s_status,
        reasons=reasons,
        warnings=warnings,
        score=total_score,
        score_breakdown=score_breakdown,
    )


class GoalEvaluator:
    """Evaluates whether the agent objective has been satisfied."""

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
