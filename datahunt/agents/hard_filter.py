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
        req_exp_min = req.experience_min

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

            # 3. Strict Geographic Mismatch & Foreign Remote Exclusion
            if req_loc and job.location:
                loc_lower = job.location.lower()
                # Expand geographic aliases (e.g. Dubai matches UAE, Riyadh matches Saudi)
                geo_tokens = set(t.strip() for t in req_loc.replace(",", " ").split() if len(t.strip()) > 2 and t.strip() not in ("and", "the", "for", "with", "jobs", "roles"))
                GEO_CITY_MAP = {
                    "saudi": ["saudi", "ksa", "riyadh", "jeddah", "dammam", "khobar", "neom", "mecca", "medina"],
                    "uae": ["uae", "dubai", "abu dhabi", "sharjah", "ajman", "emirates"],
                    "india": ["india", "bangalore", "bengaluru", "mumbai", "delhi", "hyderabad", "pune", "chennai", "gurgaon", "noida"],
                    "uk": ["uk", "united kingdom", "london", "manchester", "birmingham", "england", "scotland"],
                    "us": ["us", "usa", "united states", "san francisco", "new york", "seattle", "austin", "boston", "chicago", "denver"],
                }
                for group_key, aliases in GEO_CITY_MAP.items():
                    if any(a in req_loc for a in aliases):
                        geo_tokens.update(aliases)

                matched_geo = any(t in loc_lower for t in geo_tokens)

                # Collect conflicting tokens from other country clusters
                conflicting_tokens = set()
                for group_key, aliases in GEO_CITY_MAP.items():
                    if not any(a in req_loc for a in aliases):
                        conflicting_tokens.update(aliases)

                has_conflict = any(ct in loc_lower for ct in conflicting_tokens)
                is_pure_global_remote = job.remote_status == "remote" and (
                    loc_lower in ("remote", "worldwide", "global", "anywhere", "remote / unspecified", "remote (worldwide)")
                    or "worldwide" in loc_lower
                    or "global" in loc_lower
                )

                if matched_geo:
                    pass  # Direct location match within requested geography
                elif is_pure_global_remote and req_remote != "onsite":
                    pass  # Truly agnostic global remote job
                elif job.remote_status == "remote" and has_conflict:
                    rejected.append((job, f"Remote location '{job.location}' is restricted to another country, does not match requested '{req.location}'"))
                    continue
                elif not matched_geo:
                    rejected.append((job, f"Location '{job.location}' does not match requested '{req.location}'"))
                    continue

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

            # 5. Experience Filter (hard cutoff only if job requires substantially more experience)
            if req_exp_min is not None and job.experience_min_years is not None:
                # If user has 2 years exp, and job requires 7+ years (Staff/Director), reject
                if job.experience_min_years > (req_exp_min + 3):
                    rejected.append((job, f"Job requires {job.experience_min_years}+ years exp, user specified {req_exp_min}"))
                    continue

            # Passed all hard gates
            passed.append(job)

        logger.info(f"HardFilter: {len(passed)} passed, {len(rejected)} rejected from {len(jobs)} candidate jobs.")
        return passed, rejected
