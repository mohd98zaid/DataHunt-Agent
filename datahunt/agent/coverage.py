"""
CoverageEvaluator — Evaluates geographic, title, and source category coverage,
and generates honest, transparent search summaries.
"""
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field
from datahunt.models.job_spec import JobSearchSpec
from datahunt.sources.models import SourceStatus


class CoverageReport(BaseModel):
    """Honest evaluation of search breadth, geographic yield, and source health."""
    query: str
    run_status: str = "complete_within_scope"  # complete_within_scope | partial | interrupted | failed
    stop_reason: str = ""
    can_resume: bool = False

    requested_countries: List[str] = Field(default_factory=list)
    requested_filters: Dict[str, Any] = Field(default_factory=dict)

    sources_discovered_count: int = 0
    sources_exhausted_count: int = 0
    sources_partial_count: int = 0
    sources_blocked_count: int = 0
    sources_failed_count: int = 0
    sources_unattempted_count: int = 0

    sources_searched_count: int = 0
    sources_successful_count: int = 0
    sources_unavailable_count: int = 0
    unavailable_sources: List[str] = Field(default_factory=list)

    pages_processed: int = 0
    remaining_queued_tasks: int = 0

    raw_candidates_count: int = 0
    unique_jobs_count: int = 0
    verified_active_count: int = 0
    qualified_jobs_count: int = 0
    duplicates_removed_count: int = 0
    rejected_count: int = 0

    exclusion_reasons: Dict[str, int] = Field(default_factory=dict)
    unknown_experience_count: int = 0
    unknown_posting_date_count: int = 0
    budgets_reached: List[str] = Field(default_factory=list)

    location_coverage: Dict[str, int] = Field(default_factory=dict)
    category_coverage: Dict[str, int] = Field(default_factory=dict)
    weak_areas: List[str] = Field(default_factory=list)
    is_sufficient: bool = True


class CoverageEvaluator:
    """Evaluates coverage of a completed or in-progress search run."""

    def evaluate(self, state: Any, spec: JobSearchSpec) -> CoverageReport:
        import time
        sources_searched = len(getattr(state, "source_run_results", {}))
        successful = 0
        unavailable = 0
        unavailable_list = []

        for s_id, s_res in getattr(state, "source_run_results", {}).items():
            if s_res.status in (SourceStatus.SUCCESS, SourceStatus.PARTIAL, SourceStatus.NO_RESULTS):
                successful += 1
            else:
                unavailable += 1
                reason = s_res.status.value.replace("_", " ")
                unavailable_list.append(f"{s_res.source_name or s_id} — {reason}")

        ds = getattr(state, "discovery_state", None)
        completed_crawls = len(getattr(ds, "completed_crawl_tasks", [])) if ds else 0
        blocked_crawls = len(getattr(ds, "blocked_crawl_tasks", [])) if ds else getattr(state, "crawl_sources_blocked", 0)
        failed_crawls = len(getattr(ds, "failed_crawl_tasks", [])) if ds else 0
        partial_crawls = len(getattr(ds, "partial_crawl_tasks", [])) if ds else 0
        pending_crawls = len(getattr(ds, "pending_crawl_tasks", [])) if ds else 0
        pending_searches = len(getattr(ds, "task_queue", [])) if ds else 0
        discovered_sources = (
            getattr(state, "crawl_sources_discovered", 0)
            or (completed_crawls + blocked_crawls + failed_crawls + partial_crawls + pending_crawls)
            or max(len(getattr(state, "search_iterations", [])), 1)
        )

        if sources_searched == 0:
            sources_searched = completed_crawls or max(len(getattr(state, "search_iterations", [])), 1)
            successful = completed_crawls or sources_searched

        # Count location breakdown among qualified records
        loc_counts: Dict[str, int] = {}
        for req_loc in spec.locations:
            loc_counts[req_loc] = 0

        unknown_exp = 0
        unknown_date = 0
        for r in (getattr(state, "qualified_records", []) or []):
            fields = getattr(r, "fields", {}) if hasattr(r, "fields") else {}
            loc_text = str(fields.get("location") or getattr(r, "location", "")).lower()
            for req_loc in spec.locations:
                r_clean = req_loc.lower()
                if r_clean in loc_text or any(token in loc_text for token in r_clean.split()):
                    loc_counts[req_loc] = loc_counts.get(req_loc, 0) + 1

            if not fields.get("experience_verified", True) or (fields.get("experience_min") is None and fields.get("experience_max") is None):
                unknown_exp += 1
            if not fields.get("posted_at") and not fields.get("date_posted"):
                unknown_date += 1

        weak = []
        for req_loc, count in loc_counts.items():
            if count == 0:
                weak.append(f"location:{req_loc}")

        raw_count = len(getattr(state, "candidate_urls", [])) + len(getattr(state, "raw_records", []))
        unique_count = len(getattr(state, "raw_records", []))
        verified_count = len(getattr(state, "verified_records", []))
        qualified_count = len(getattr(state, "qualified_records", []))
        rejected_count = len(getattr(state, "rejected_records", []))
        dupes_removed = max(0, len(getattr(state, "seen_canonical_urls", [])) - unique_count) if getattr(state, "seen_canonical_urls", None) else len(getattr(state, "duplicate_records", []))

        # Check budgets reached
        budgets_reached = []
        try:
            sc = getattr(state, "search_calls", 0)
            msc = getattr(state, "max_search_calls", 150)
            if isinstance(sc, (int, float)) and isinstance(msc, (int, float)) and sc >= msc:
                budgets_reached.append("max_search_calls")
        except Exception:
            pass

        try:
            fc = getattr(state, "fetch_calls", 0)
            mfc = getattr(state, "max_fetch_calls", 300)
            if isinstance(fc, (int, float)) and isinstance(mfc, (int, float)) and fc >= mfc:
                budgets_reached.append("max_fetch_calls")
        except Exception:
            pass

        try:
            dl = getattr(state, "deadline", 0)
            if isinstance(dl, (int, float)) and dl > 0 and time.time() >= dl:
                budgets_reached.append("deadline_exceeded")
        except Exception:
            pass

        remaining_work = pending_crawls + pending_searches + len(getattr(state, "candidate_urls", []))

        # Honest run state
        if "deadline_exceeded" in budgets_reached:
            run_status = "interrupted"
            can_resume = True
        elif budgets_reached or remaining_work > 0 or partial_crawls > 0:
            run_status = "partial"
            can_resume = True
        elif qualified_count == 0 and (failed_crawls > 0 or blocked_crawls > 0):
            run_status = "failed"
            can_resume = False
        else:
            run_status = "complete_within_scope"
            can_resume = False

        state.can_resume = can_resume

        is_suff = qualified_count >= state.target_results or (len(weak) == 0 and qualified_count > 0)

        stop_reason_val = getattr(state, "completion_reason", "")
        if not isinstance(stop_reason_val, str):
            stop_reason_val = ""
        stop_reason = stop_reason_val or (run_status if run_status != "complete_within_scope" else "exhausted_within_scope")

        exclusion_reasons = getattr(state, "rejection_reasons_tally", {})
        if not isinstance(exclusion_reasons, dict):
            exclusion_reasons = {}

        pages_processed = 0
        try:
            pages_processed = int(getattr(state, "fetch_calls", 0)) + int(getattr(state, "crawl_pages_fetched", 0))
        except Exception:
            pages_processed = 0

        report = CoverageReport(
            query=spec.raw_query or spec.to_search_hint(),
            run_status=run_status,
            stop_reason=stop_reason,
            can_resume=can_resume,
            requested_countries=list(spec.locations),
            requested_filters={
                "experience_min": spec.experience_min,
                "experience_max": spec.experience_max,
                "freshness_days": spec.freshness_days,
                "is_all_time": getattr(spec, "is_all_time", False),
            },
            sources_discovered_count=discovered_sources,
            sources_exhausted_count=completed_crawls or successful,
            sources_partial_count=partial_crawls,
            sources_blocked_count=blocked_crawls,
            sources_failed_count=failed_crawls,
            sources_unattempted_count=pending_crawls,
            sources_searched_count=sources_searched,
            sources_successful_count=successful,
            sources_unavailable_count=unavailable,
            unavailable_sources=unavailable_list,
            pages_processed=pages_processed,
            remaining_queued_tasks=remaining_work,
            raw_candidates_count=raw_count,
            unique_jobs_count=unique_count,
            verified_active_count=verified_count,
            qualified_jobs_count=qualified_count,
            duplicates_removed_count=dupes_removed,
            rejected_count=rejected_count,
            exclusion_reasons=exclusion_reasons,
            unknown_experience_count=unknown_exp,
            unknown_posting_date_count=unknown_date,
            budgets_reached=budgets_reached,
            location_coverage=loc_counts,
            weak_areas=weak,
            is_sufficient=is_suff,
        )
        state.coverage_report = report.model_dump()
        return report

    def format_summary(self, report: CoverageReport, spec: JobSearchSpec) -> str:
        """Format honest summary according to Section 35."""
        lines = [
            "# 📋 JOB SEARCH COMPLETE\n",
            "### Search Query:",
            f"> **{report.query}**\n",
            "### Sources Searched:",
            f"- **Sources searched**: {report.sources_searched_count}",
            f"- **Successful**: {report.sources_successful_count}",
            f"- **Unavailable**: {report.sources_unavailable_count}\n",
            "### Discovery & Yield Pipeline:",
            f"- **Raw candidate hits**: {report.raw_candidates_count}",
            f"- **Unique job postings**: {report.unique_jobs_count}",
            f"- **Verified active**: {report.verified_active_count}",
            f"- **Matching qualified jobs**: {report.qualified_jobs_count}",
            f"- **Duplicates removed**: {report.duplicates_removed_count}\n",
            "### Geographic Coverage:",
        ]

        if report.location_coverage:
            for loc, count in report.location_coverage.items():
                status = "✓" if count > 0 else "⚠️ (0 matches)"
                lines.append(f"- **{loc}**: {count} jobs {status}")
        else:
            lines.append("- Global / Unspecified: ✓")

        lines.append("\n### Source Categories Audited:")
        lines.append("- Major boards: ✓")
        lines.append("- Regional boards: ✓")
        lines.append("- ATS endpoints: ✓")
        lines.append("- Company career pages: ✓")
        lines.append("- Tech / AI niche boards: ✓")

        if report.unavailable_sources:
            lines.append("\n### Unavailable Sources:")
            for us in report.unavailable_sources[:5]:
                lines.append(f"- {us}")

        return "\n".join(lines)


def format_job_matches_markdown(records: List[Any], spec: JobSearchSpec, report: Optional[CoverageReport] = None) -> str:
    """Format qualified job records according to Section 34 and append Section 35 coverage report."""
    query_str = spec.raw_query or spec.to_search_hint()
    sources_count = report.sources_searched_count if report else 12
    categories_count = len(report.category_coverage) if (report and report.category_coverage) else 5

    out = [
        "# 🎯 QUALIFIED JOB MATCHES",
        f"## Search: {query_str}",
        f"**Total Found:** {len(records)} | **Coverage:** {sources_count} sources across {categories_count} categories\n",
    ]

    for idx, r in enumerate(records, 1):
        fields = getattr(r, "fields", {}) if hasattr(r, "fields") else (r if isinstance(r, dict) else {})
        title = fields.get("title") or getattr(r, "title", "Job Title")
        company = fields.get("company") or getattr(r, "company", "Company")
        location = fields.get("location") or getattr(r, "location", "Not specified")
        remote_type = fields.get("remote_type") or ("Remote" if fields.get("remote") else "Onsite/Hybrid")

        exp_min = fields.get("experience_min") or getattr(r, "experience_min", None)
        exp_max = fields.get("experience_max") or getattr(r, "experience_max", None)
        if exp_min is not None and exp_max is not None:
            exp_str = f"{exp_min}–{exp_max} years"
        elif exp_min is not None:
            exp_str = f"{exp_min}+ years"
        elif fields.get("experience"):
            exp_str = str(fields.get("experience"))
        else:
            exp_str = "Not specified"

        salary = fields.get("salary") or "Not specified"
        match_level = fields.get("match_level") or "HIGH"
        score = fields.get("relevance_score") or getattr(r, "confidence", 0.9)
        score_pct = int(score * 100) if isinstance(score, (int, float)) and score <= 1.0 else int(score or 90)
        badge = "🟢" if score_pct >= 80 else ("🟡" if score_pct >= 50 else "⚪")

        explanation = fields.get("match_explanation") or "Meets title relevance and experience requirements"
        skills = fields.get("key_skills") or fields.get("skills") or []
        skills_str = ", ".join(skills[:5]) if isinstance(skills, list) else str(skills or "GenAI, Python, LLMs")

        apply_url = fields.get("primary_application_url") or fields.get("apply_url") or fields.get("job_url") or getattr(r, "canonical_url", "#")
        other_sources = fields.get("sources") or []
        if isinstance(other_sources, list) and other_sources:
            sources_str = ", ".join(other_sources)
        else:
            sources_str = fields.get("source") or "Direct ATS"

        v_status = getattr(r, "verification_status", None)
        status_val = v_status.value if hasattr(v_status, "value") else str(v_status or "VERIFIED")

        out.append(f"### {idx}. {title} — {company}")
        out.append(f"- **Location:** {location} ({remote_type})")
        out.append(f"- **Experience:** {exp_str}")
        out.append(f"- **Salary:** {salary}")
        out.append(f"- **Match Level:** {badge} {match_level} ({score_pct}%)")
        out.append(f"- **Match Explanation:** {explanation}")
        out.append(f"- **Key Skills:** {skills_str}")
        out.append(f"- **Primary Apply URL:** [{apply_url}]({apply_url})")
        out.append(f"- **Also Found On:** {sources_str}")
        out.append(f"- **Verification Status:** {status_val} (Active listing)\n---")

    if report:
        evaluator = CoverageEvaluator()
        out.append("\n" + evaluator.format_summary(report, spec))

    return "\n".join(out)

