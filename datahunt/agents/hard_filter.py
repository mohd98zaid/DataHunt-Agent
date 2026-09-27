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


from datahunt.agent.policies import match_location, match_skills, MatchStatus


class HardFilter:
    """
    Step 26-27: Filter out jobs that violate explicit hard constraints.
    Hard filters eliminate results permanently — ranking scores cannot rescue them.
    """

    def apply(self, jobs: List[Any], req: JobSearchRequest) -> Tuple[List[Any], List[Tuple[Any, str]]]:
        """
        Filter job candidates.
        Supports both NormalizedJob instances and dicts.
        Returns:
            (qualified_jobs, rejected_jobs_with_reason)
        """
        passed: List[Any] = []
        rejected: List[Tuple[Any, str]] = []

        req_remote = (req.remote_status or "any").lower()
        req_locations = getattr(req, "locations", None) or ([req.location] if req.location else [])
        req_sal_min = req.salary_min
        req_exp_max = req.experience_max  # upper bound the user declared
        req_explicit_skills = getattr(req, "explicit_skills", None) or []
        req_excluded_titles = getattr(req, "excluded_titles", None) or []
        req_excluded_companies = getattr(req, "excluded_companies", None) or []

        for job in jobs:
            is_dict = isinstance(job, dict)
            is_active = job.get("is_active", True) if is_dict else getattr(job, "is_active", True)
            title = (job.get("title") or job.get("job_title") or "") if is_dict else getattr(job, "title", "")
            company = (job.get("company") or "") if is_dict else getattr(job, "company", "")
            location = (job.get("location") or "") if is_dict else getattr(job, "location", "")
            remote_status = (job.get("remote_status") or ("remote" if job.get("remote") else "onsite")) if is_dict else getattr(job, "remote_status", "onsite")
            skills = (job.get("skills") or []) if is_dict else getattr(job, "skills", [])

            # 1. Active status
            if not is_active:
                rejected.append((job, "Job posting marked as inactive"))
                continue

            # 2. Excluded Titles & Excluded Companies
            if req_excluded_titles:
                j_title_lower = title.lower()
                if any(et.lower() in j_title_lower for et in req_excluded_titles):
                    rejected.append((job, f"Job title '{title}' matches excluded title"))
                    continue

            if req_excluded_companies:
                j_comp_lower = company.lower()
                if any(ec.lower() in j_comp_lower for ec in req_excluded_companies):
                    rejected.append((job, f"Company '{company}' matches excluded company"))
                    continue

            # 3. Strict Remote Filter
            if req_remote == "remote":
                if remote_status == "onsite":
                    if req.location and req.location.lower() not in location.lower():
                        rejected.append((job, "Strict remote requested, but job is onsite in another location"))
                        continue

            elif req_remote == "onsite":
                if remote_status == "remote" and req.location and req.location.lower() not in location.lower():
                    pass

            # 4. Strict Geographic Mismatch (via canonical match_location policy)
            if req_locations and location:
                remote_ok = req_remote in ("any", "remote", "hybrid")
                l_status, l_reason = match_location(req_locations, location, remote_ok=remote_ok)
                if l_status == MatchStatus.MISMATCH:
                    rejected.append((
                        job,
                        f"Location '{location}' is a confirmed geographic mismatch: {l_reason}"
                    ))
                    continue
                elif l_status == MatchStatus.UNKNOWN:
                    logger.debug(
                        f"HardFilter: location status unknown for '{location}' vs '{req_locations}'; passing through"
                    )

            # 5. Salary Floor Filter (only if both query and job have salary in same currency)
            sal_min_annual = job.get("salary_min_annual") if is_dict else getattr(job, "salary_min_annual", None)
            sal_max_annual = job.get("salary_max_annual") if is_dict else getattr(job, "salary_max_annual", None)
            sal_currency = (job.get("salary_currency") if is_dict else getattr(job, "salary_currency", None)) or "USD"

            if req_sal_min and sal_min_annual is not None:
                if (req.salary_currency or "USD").upper() == sal_currency.upper():
                    if sal_max_annual and sal_max_annual < req_sal_min * 0.85:
                        rejected.append((job, f"Disclosed max salary ({sal_currency} {sal_max_annual}) below minimum floor ({req_sal_min})"))
                        continue
                    elif not sal_max_annual and sal_min_annual < req_sal_min * 0.80:
                        rejected.append((job, f"Disclosed min salary ({sal_currency} {sal_min_annual}) below minimum floor ({req_sal_min})"))
                        continue

            # 6. Experience Mismatch (only reject clear mismatches, not unknowns)
            exp_min_years = job.get("experience_min_years") if is_dict else getattr(job, "experience_min_years", None)
            if req_exp_max is not None and exp_min_years is not None:
                if exp_min_years > req_exp_max + 1:
                    rejected.append((job, f"Job requires {exp_min_years}+ years, user max is {req_exp_max}"))
                    continue

            # 7. Explicit Skills Gate (inferred skills are never hard gates)
            if req_explicit_skills:
                raw_f = job.get("raw_fields", {}) if is_dict else getattr(job, "raw_fields", {})
                job_blob = f"{title} {location} {' '.join(skills)} {' '.join(str(v) for v in (raw_f or {}).values())}"
                s_status, matched_s, missing_s = match_skills(req_explicit_skills, job_blob)
                if s_status == MatchStatus.MISMATCH:
                    rejected.append((job, f"Missing required explicit skills: {', '.join(missing_s)}"))
                    continue

            # Passed all hard gates
            passed.append(job)

        logger.info(f"HardFilter: {len(passed)} passed, {len(rejected)} rejected from {len(jobs)} candidate jobs.")
        return passed, rejected


HardFilterAgent = HardFilter
