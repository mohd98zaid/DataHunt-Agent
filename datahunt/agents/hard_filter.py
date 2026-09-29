"""
HardFilter — Step 26-27 of the job search pipeline.

Applies strict constraints from the user's JobSearchRequest to eliminate
jobs that definitely do not qualify (e.g. incompatible geography, strict remote violations,
unambiguously sub-floor salaries).
"""
from typing import List, Tuple
from datahunt.agents.query_understanding import JobSearchRequest
from datahunt.agents.normalizer import NormalizedJob
from datahunt.logger import logger


# ─────────────────────────────────────────────────────────────────────────────
# Geographic helpers
# ─────────────────────────────────────────────────────────────────────────────

GEO_GROUPS = {
    "saudi": ["saudi", "ksa", "riyadh", "jeddah", "dammam", "mecca", "medina"],
    "uae": ["uae", "emirates", "dubai", "abu dhabi", "sharjah", "ajman"],
    "india": ["india", "bangalore", "bengaluru", "mumbai", "delhi", "hyderabad", "pune", "chennai"],
    "uk": ["uk", "united kingdom", "england", "london", "manchester", "birmingham"],
    "us": ["us", "usa", "united states", "new york", "san francisco", "seattle", "austin"],
}


def _location_status(req_location: str, job_location: str, job_remote: str) -> str:
    """Returns 'match', 'mismatch', or 'unknown'.

    Three-way classification replaces the old binary match/reject logic so that
    jobs with ambiguous or unclassifiable locations are preserved rather than
    silently dropped.

    Supports multi-region requested locations (e.g. "Saudi or UAE") by checking
    if the job belongs to ANY of the requested geo groups.
    """
    if not req_location or not job_location:
        return "unknown"

    req_l = req_location.lower().strip()
    job_l = job_location.lower().strip()

    # Direct substring match
    if req_l in job_l or job_l in req_l:
        return "match"

    # Geo alias expansion — collect ALL groups the request mentions (multi-region support)
    req_groups = {
        g for g, aliases in GEO_GROUPS.items() if any(a in req_l for a in aliases)
    }
    job_group = next(
        (g for g, aliases in GEO_GROUPS.items() if any(a in job_l for a in aliases)),
        None,
    )

    # Remote wildcard — only for truly agnostic global remote jobs.
    # A US-specific "Denver, CO (Remote)" is NOT a global remote — it targets US workers.
    # Only fire the wildcard when the job location has no identifiable country group.
    GLOBAL_REMOTE_INDICATORS = ["worldwide", "global", "anywhere"]
    is_purely_agnostic_remote = (
        job_remote == "remote"
        and job_group is None  # no country context resolved
        and (
            # Plain/agnostic location string
            job_l in ("remote", "remote / unspecified", "remote (worldwide)", "worldwide", "global", "anywhere")
            # OR explicitly global wording
            or any(t in job_l for t in GLOBAL_REMOTE_INDICATORS)
        )
    )
    if is_purely_agnostic_remote:
        return "match"

    if req_groups and job_group:
        return "match" if job_group in req_groups else "mismatch"

    if req_groups and not job_group:
        # Job location doesn't map to any known region → can't classify → keep it
        return "unknown"

    return "unknown"


from typing import Any, List, Optional, Tuple
from datahunt.agent.policies import qualify_job, QualificationPolicy, match_location, match_skills, MatchStatus


class HardFilter:
    """
    Step 26-27: Filter out jobs that violate explicit hard constraints.
    Hard filters eliminate results permanently — ranking scores cannot rescue them.
    Delegates directly to the authoritative QualificationPolicy (Invariant D).
    """

    def __init__(self, policy: Optional[QualificationPolicy] = None):
        self.policy = policy

    def apply(self, jobs: List[Any], req: JobSearchRequest) -> Tuple[List[Any], List[Tuple[Any, str]]]:
        """
        Filter job candidates using the single authoritative QualificationPolicy.
        Supports both NormalizedJob instances, ExtractedRecords, and dicts.
        Returns:
            (qualified_jobs, rejected_jobs_with_reason)
        """
        passed: List[Any] = []
        rejected: List[Tuple[Any, str]] = []

        for job in jobs:
            res = self.policy.qualify(job, req) if self.policy else qualify_job(job, req)

            if res.qualified:
                passed.append(job)
            else:
                reason = "; ".join(res.rejection_reasons) if res.rejection_reasons else ("; ".join(res.reasons) if res.reasons else "Disqualified by qualification policy")
                rejected.append((job, reason))

        logger.info(f"HardFilter: {len(passed)} passed, {len(rejected)} rejected from {len(jobs)} candidate jobs.")
        return passed, rejected


HardFilterAgent = HardFilter
