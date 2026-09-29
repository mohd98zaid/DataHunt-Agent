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


from typing import Any, List, Optional, Tuple
from datahunt.agent.policies import qualify_job, QualificationPolicy, match_location, match_skills, MatchStatus


def _location_status(req_location: str, job_location: str, job_remote: str = "") -> str:
    """
    Compatibility wrapper delegating directly to authoritative match_location() (Invariant D).
    Returns 'match', 'mismatch', or 'unknown'.
    """
    if not req_location or not job_location:
        return "unknown"
    status, _ = match_location(req_location, job_location, job_remote)
    if status == MatchStatus.MATCH:
        return "match"
    elif status == MatchStatus.MISMATCH:
        return "mismatch"
    return "unknown"


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
