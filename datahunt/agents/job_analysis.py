"""
JobAnalysisAgent — Steps 28-39 of the job search pipeline.

Compares each normalized job against the user's JobSearchRequest and ExpandedQuery.
Calculates multidimensional relevance scores, identifies matching vs missing requirements,
generates explainable match rationale, and ranks candidates considering freshness and source authority.
"""
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field
from datahunt.agents.query_understanding import JobSearchRequest
from datahunt.agents.query_expansion import ExpandedQuery
from datahunt.agents.normalizer import NormalizedJob
from datahunt.logger import logger


class JobMatchResult(BaseModel):
    """Detailed evaluation and match scorecard for an individual job posting."""
    job: NormalizedJob
    relevance_score: float = 0.0         # Normalized 0.0 to 1.0
    match_level: str = "good"           # "exceptional" | "strong" | "good" | "moderate" | "marginal"

    # Explainability features
    matching_requirements: List[str] = Field(default_factory=list)
    missing_requirements: List[str] = Field(default_factory=list)
    unknown_requirements: List[str] = Field(default_factory=list)
    match_explanation: str = ""

    # Multipliers & Weights applied
    title_match_score: float = 0.0
    skills_match_score: float = 0.0
    location_match_score: float = 0.0
    freshness_weight: float = 1.0
    source_quality_weight: float = 1.0


class JobAnalysisAgent:
    """
    Steps 28-39: Analyze, score, explain, and rank candidate jobs.
    """

    def __init__(self, gemini_client=None):
        self._client = gemini_client

    def analyze_and_rank(
        self,
        jobs: List[NormalizedJob],
        req: JobSearchRequest,
        expanded: Optional[ExpandedQuery] = None
    ) -> List[JobMatchResult]:
        """Score each job and return sorted by descending relevance."""
        results: List[JobMatchResult] = []

        for job in jobs:
            match_res = self._score_job(job, req, expanded)
            results.append(match_res)

        # Rank candidates by relevance score descending
        results.sort(key=lambda r: r.relevance_score, reverse=True)
        return results

    def _score_job(
        self,
        job: NormalizedJob,
        req: JobSearchRequest,
        expanded: Optional[ExpandedQuery] = None
    ) -> JobMatchResult:
        """Compute multidimensional relevance score and explainability attributes."""
        matches: List[str] = []
        missing: List[str] = []
        unknown: List[str] = []

        # 1. Job Title Match (Weight: 0.35)
        title_score = 0.0
        job_t_lower = job.title.lower()
        req_t_lower = req.job_title.lower()

        all_target_titles = [req_t_lower]
        if expanded and expanded.all_titles:
            all_target_titles.extend([t.lower() for t in expanded.all_titles])

        if req_t_lower in job_t_lower:
            title_score = 1.0
            matches.append(f"Title directly matches '{req.job_title}'")
        elif any(alt in job_t_lower for alt in all_target_titles):
            title_score = 0.85
            matched_alt = next(alt for alt in all_target_titles if alt in job_t_lower)
            matches.append(f"Title matches related role '{matched_alt.title()}'")
        else:
            # Partial token overlap
            req_words = [w for w in req_t_lower.split() if len(w) > 2]
            overlap = sum(1 for w in req_words if w in job_t_lower)
            if req_words:
                title_score = 0.5 * (overlap / len(req_words))
            if title_score > 0.3:
                matches.append(f"Role title partially overlaps with '{req.job_title}'")
            else:
                missing.append(f"Title differs from requested '{req.job_title}'")

        # 2. Location & Remote Match (Weight: 0.25)
        loc_score = 0.5
        req_remote = req.remote_status.lower()

        if req_remote == "remote":
            if job.remote_status == "remote":
                loc_score = 1.0
                matches.append("Fully remote position as requested")
            elif job.remote_status == "hybrid":
                loc_score = 0.65
                matches.append("Hybrid remote work option available")
            else:
                loc_score = 0.2
                missing.append("Remote requested, but job is listed as on-site")
        else:
            if req.location and job.location:
                req_loc = req.location.lower()
                loc_lower = job.location.lower()
                
                # Check geographic tokens and city alias expansion
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

                if matched_geo:
                    loc_score = 1.0
                    matches.append(f"Direct location match in target region: {job.location}")
                elif job.remote_status == "remote":
                    loc_score = 0.75
                    matches.append("Remote eligible position")
                elif job.remote_status == "hybrid":
                    loc_score = 0.55
                    matches.append("Hybrid position")
                else:
                    loc_score = 0.35
                    missing.append(f"Located in {job.location} instead of {req.location}")
            elif job.remote_status == "remote":
                loc_score = 0.9
                matches.append("Remote friendly")

        # 3. Skills Match (Weight: 0.25)
        skills_score = 0.5
        target_skills = list(req.skills)
        if expanded and expanded.must_have_skills:
            target_skills = list(set(target_skills + expanded.must_have_skills))

        if target_skills:
            job_blob = f"{job.title} {job.location} {' '.join(job.skills)} {' '.join(str(v) for v in job.raw_fields.values())}".lower()
            matched_skills = [s for s in target_skills if s.lower() in job_blob]
            unmatched_skills = [s for s in target_skills if s.lower() not in job_blob]

            skills_score = len(matched_skills) / len(target_skills) if target_skills else 0.5
            if matched_skills:
                matches.append(f"Matches skills: {', '.join(matched_skills[:4])}")
            if unmatched_skills:
                unknown.append(f"Skills not explicitly mentioned: {', '.join(unmatched_skills[:3])}")
        else:
            unknown.append("No specific skills were specified in the query")

        # 4. Salary & Experience Match (Weight: 0.15)
        sal_exp_score = 0.6
        if req.salary_min and job.salary_min_annual:
            if job.salary_min_annual >= req.salary_min:
                sal_exp_score += 0.2
                matches.append(f"Meets salary target ({job.salary_currency} {job.salary_min_annual:,.0f})")
            else:
                sal_exp_score -= 0.1
                missing.append(f"Below target salary floor")
        elif not job.salary_raw:
            unknown.append("Compensation undisclosed in job listing")

        if req.experience_min is not None:
            if job.experience_min_years is not None:
                if job.experience_min_years <= req.experience_min:
                    matches.append(f"Experience required ({job.experience_min_years} yrs) aligns with your profile")
                else:
                    missing.append(f"Requires {job.experience_min_years} yrs exp")
            else:
                unknown.append("Years of experience unstated")

        # 5. Freshness Weight
        freshness_weight = 1.0
        if job.posted_age_seconds is not None:
            age_days = job.posted_age_seconds / 86400
            if age_days <= 1:
                freshness_weight = 1.05  # Bonus for today / 0-sec
                matches.append(f"⚡ Live freshness: {job.freshness_label}")
            elif age_days <= 7:
                freshness_weight = 1.0
            elif age_days <= 30:
                freshness_weight = 0.90
            else:
                freshness_weight = 0.75
                unknown.append(f"Posted {int(age_days)} days ago")

        # 6. Source Authority Weight
        source_quality_weight = 1.0
        domain_lower = (job.canonical_url or job.source_domain).lower()
        if any(ats in domain_lower for ats in ("greenhouse.io", "lever.co", "ashbyhq.com", "workable.com")):
            source_quality_weight = 1.05  # Direct ATS
        elif any(b in domain_lower for b in ("linkedin.com", "indeed.com", "bayt.com", "naukri.com")):
            source_quality_weight = 0.98

        # Weighted Base
        raw_score = (
            (title_score * 0.35) +
            (loc_score * 0.25) +
            (skills_score * 0.25) +
            (sal_exp_score * 0.15)
        )
        final_score = round(min(max(raw_score * freshness_weight * source_quality_weight, 0.05), 1.0), 2)

        # Match level descriptor
        if final_score >= 0.88:
            match_level = "exceptional"
        elif final_score >= 0.75:
            match_level = "strong"
        elif final_score >= 0.60:
            match_level = "good"
        elif final_score >= 0.40:
            match_level = "moderate"
        else:
            match_level = "marginal"

        # Concise explainability sentence
        top_reasons = [m for m in matches if not m.startswith("⚡")][:2]
        explanation = f"{match_level.title()} match: {'; '.join(top_reasons) if top_reasons else 'Role aligns with search criteria'}."

        return JobMatchResult(
            job=job,
            relevance_score=final_score,
            match_level=match_level,
            matching_requirements=matches,
            missing_requirements=missing,
            unknown_requirements=unknown,
            match_explanation=explanation,
            title_match_score=round(title_score, 2),
            skills_match_score=round(skills_score, 2),
            location_match_score=round(loc_score, 2),
            freshness_weight=round(freshness_weight, 2),
            source_quality_weight=round(source_quality_weight, 2)
        )
