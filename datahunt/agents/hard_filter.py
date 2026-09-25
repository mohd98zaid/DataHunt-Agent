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


class HardFilter:
    """
    Step 26-27: Filter out jobs that violate explicit hard constraints.
    """

    def apply(self, jobs: List[NormalizedJob], req: JobSearchRequest) -> Tuple[List[NormalizedJob], List[Tuple[NormalizedJob, str]]]:
        """
        Filter job candidates.
        Returns:
            (qualified_jobs, rejected_jobs_with_reason)
        """
        passed: List[NormalizedJob] = []
        rejected: List[Tuple[NormalizedJob, str]] = []

        req_remote = (req.remote_status or "any").lower()
        req_loc = (req.location or "").lower()
        req_sal_min = req.salary_min
        req_exp_max = req.experience_max  # upper bound the user declared

        for job in jobs:
            # 1. Active status
            if not job.is_active:
                rejected.append((job, "Job posting marked as inactive"))
                continue

            # 2. Strict Remote Filter
            if req_remote == "remote":
                # If user demanded fully remote, job must be remote or at least hybrid
                if job.remote_status == "onsite":
                    # Check if location matches user's home location; if so, maybe acceptable, else reject
                    if req_loc and req_loc not in job.location.lower():
                        rejected.append((job, "Strict remote requested, but job is onsite in another location"))
                        continue

            elif req_remote == "onsite":
                # If user demanded strictly onsite in a city
                if job.remote_status == "remote" and req_loc and req_loc not in job.location.lower():
                    # Usually remote jobs are fine, but keep lenient
                    pass

            # 3. Strict Geographic Mismatch
            # Three-way: 'match' → pass, 'unknown' → pass with warning, 'mismatch' → reject
            if req_loc and job.location:
                status = _location_status(req.location or "", job.location, job.remote_status)

                if status == "mismatch":
                    rejected.append((
                        job,
                        f"Location '{job.location}' is a confirmed geographic mismatch for requested '{req.location}'"
                    ))
                    continue
                elif status == "unknown":
                    # Can't classify — let the job through; flag it for downstream
                    logger.debug(
                        f"HardFilter: location status unknown for '{job.location}' vs '{req.location}'; passing through"
                    )
                    # fall through to passed

            # 4. Salary Floor Filter (only if both query and job have salary in same currency)
            if req_sal_min and job.salary_min_annual is not None:
                if (req.salary_currency or "USD").upper() == (job.salary_currency or "USD").upper():
                    # Give 10% buffer for negotiable salaries
                    if job.salary_max_annual and job.salary_max_annual < req_sal_min * 0.85:
                        rejected.append((job, f"Disclosed max salary ({job.salary_currency} {job.salary_max_annual}) below minimum floor ({req_sal_min})"))
                        continue
                    elif not job.salary_max_annual and job.salary_min_annual < req_sal_min * 0.80:
                        rejected.append((job, f"Disclosed min salary ({job.salary_currency} {job.salary_min_annual}) below minimum floor ({req_sal_min})"))
                        continue

            # 5. Experience Mismatch (only reject clear mismatches, not unknowns)
            # If job has no experience data → unknown → let it through.
            # Only reject if job explicitly requires more years than user's declared maximum
            # (with a 1-year grace allowance).
            if req_exp_max is not None and job.experience_min_years is not None:
                if job.experience_min_years > req_exp_max + 1:  # allow 1 year grace
                    rejected.append((job, f"Job requires {job.experience_min_years}+ years, user max is {req_exp_max}"))
                    continue
            # If job.experience_min_years is None → unknown → pass through silently

            # Passed all hard gates
            passed.append(job)

        logger.info(f"HardFilter: {len(passed)} passed, {len(rejected)} rejected from {len(jobs)} candidate jobs.")
        return passed, rejected
