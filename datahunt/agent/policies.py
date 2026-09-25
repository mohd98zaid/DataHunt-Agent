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
    "saudi": ["saudi", "saudi arabia", "ksa", "riyadh", "jeddah", "dammam", "khobar", "neom", "mecca", "medina", "makkah"],
    "uae": ["uae", "united arab emirates", "emirates", "dubai", "abu dhabi", "abu-dhabi", "sharjah", "ajman", "ras al khaimah"],
    "qatar": ["qatar", "doha"],
    "kuwait": ["kuwait", "kuwait city"],
    "bahrain": ["bahrain", "manama"],
    "oman": ["oman", "muscat"],
    "india": ["india", "bangalore", "bengaluru", "mumbai", "delhi", "hyderabad", "pune", "chennai", "gurgaon", "noida", "kolkata"],
    "uk": ["uk", "united kingdom", "britain", "england", "london", "manchester", "birmingham", "edinburgh"],
    "us": ["us", "usa", "united states", "america", "san francisco", "new york", "seattle", "austin", "boston", "denver", "chicago"],
    "eu": ["germany", "france", "netherlands", "ireland", "spain", "italy", "berlin", "paris", "amsterdam", "dublin"],
    "remote": ["remote", "worldwide", "global", "anywhere", "work from home", "wfh"],
}

COUNTRY_CLUSTER_FOR = {alias: cluster for cluster, aliases in GEO_ALIAS_GROUPS.items() for alias in aliases}


def match_location(requested: Optional[str], actual: Optional[str], remote_ok: bool = True) -> Tuple[MatchStatus, str]:
    """
    Returns (MatchStatus, reason).
    Never rejects an UNKNOWN location — let the verifier decide.
    """
    if not requested:
        return MatchStatus.MATCH, "No location constraint"

    if not actual or actual.strip().lower() in ("", "not specified", "n/a", "null", "none"):
        return MatchStatus.UNKNOWN, "Location not disclosed in job listing"

    req_lower = requested.lower().strip()
    act_lower = actual.lower().strip()

    if req_lower in act_lower or act_lower in req_lower:
        return MatchStatus.MATCH, f"Direct match: '{actual}'"

    req_cluster = COUNTRY_CLUSTER_FOR.get(req_lower)
    if not req_cluster:
        for alias, cluster in COUNTRY_CLUSTER_FOR.items():
            if re.search(r'\b' + re.escape(alias) + r'\b', req_lower):
                req_cluster = cluster
                break

    act_cluster = COUNTRY_CLUSTER_FOR.get(act_lower)
    if not act_cluster:
        for alias, cluster in COUNTRY_CLUSTER_FOR.items():
            if re.search(r'\b' + re.escape(alias) + r'\b', act_lower):
                act_cluster = cluster
                break

    if req_cluster and act_cluster:
        if req_cluster == act_cluster:
            return MatchStatus.MATCH, f"Same region cluster: {req_cluster}"

        if act_cluster == "remote" and remote_ok:
            if "worldwide" in act_lower or "global" in act_lower or "anywhere" in act_lower:
                return MatchStatus.MATCH, "Globally remote job matches any location"
            return MatchStatus.UNKNOWN, "Remote job with unspecified geography"

        return MatchStatus.MISMATCH, f"Location cluster mismatch: requested={req_cluster}, found={act_cluster}"

    if req_cluster and not act_cluster:
        return MatchStatus.UNKNOWN, f"Could not classify job location: '{actual}'"

    return MatchStatus.UNKNOWN, f"Cannot determine if '{actual}' matches '{requested}'"


def match_experience(req_min: Optional[int], req_max: Optional[int], job_min: Optional[int], job_max: Optional[int]) -> Tuple[MatchStatus, str]:
    """
    Returns (MatchStatus, reason).
    Missing experience info = UNKNOWN (not MISMATCH).
    """
    if req_min is None and req_max is None:
        return MatchStatus.MATCH, "No experience constraint"

    if job_min is None and job_max is None:
        return MatchStatus.UNKNOWN, "Experience requirements not specified in job listing"

    if req_max is not None and job_min is not None and job_min > req_max + 1:
        return MatchStatus.MISMATCH, f"Job requires {job_min}+ years but user has up to {req_max}"

    if req_min is not None and job_max is not None and job_max < req_min - 2:
        return MatchStatus.MISMATCH, f"Job caps at {job_max} years but user has {req_min}+ years"

    return MatchStatus.MATCH, "Experience range compatible"


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


class GoalEvaluator:
    """Evaluates whether the agent objective has been satisfied."""

    def is_satisfied(self, state) -> Tuple[bool, str]:
        n = len(state.verified_records)
        target = state.target_results
        if n >= target:
            return True, f"Found {n} verified results (target: {target})"
        return False, f"Found {n}/{target} results"

    def quality_summary(self, state) -> dict:
        return {
            "verified": len(state.verified_records),
            "rejected": len(state.rejected_records),
            "target": state.target_results,
            "search_iterations": len(state.search_iterations),
            "total_candidates": len(state.candidate_urls) + len(state.raw_records),
            "low_yield_streak": state.consecutive_low_yield_iterations,
            "warnings": len(state.warnings),
        }
