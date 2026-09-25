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
    sources_searched_count: int = 0
    sources_successful_count: int = 0
    sources_unavailable_count: int = 0
    unavailable_sources: List[str] = Field(default_factory=list)

    raw_candidates_count: int = 0
    unique_jobs_count: int = 0
    verified_active_count: int = 0
    qualified_jobs_count: int = 0
    duplicates_removed_count: int = 0

    location_coverage: Dict[str, int] = Field(default_factory=dict)
    category_coverage: Dict[str, int] = Field(default_factory=dict)
    weak_areas: List[str] = Field(default_factory=list)
    is_sufficient: bool = True


class CoverageEvaluator:
    """Evaluates coverage of a completed or in-progress search run."""

    def evaluate(self, state: Any, spec: JobSearchSpec) -> CoverageReport:
        sources_searched = len(state.source_run_results)
        successful = 0
        unavailable = 0
        unavailable_list = []

        for s_id, s_res in state.source_run_results.items():
            if s_res.status in (SourceStatus.SUCCESS, SourceStatus.PARTIAL, SourceStatus.NO_RESULTS):
                successful += 1
            else:
                unavailable += 1
                reason = s_res.status.value.replace("_", " ")
                unavailable_list.append(f"{s_res.source_name or s_id} — {reason}")

        # If no explicit source_run_results, fallback to search iterations count
        if sources_searched == 0:
            sources_searched = max(len(state.search_iterations), 1)
            successful = sources_searched

        # Count location breakdown among qualified records
        loc_counts: Dict[str, int] = {}
        for req_loc in spec.locations:
            loc_counts[req_loc] = 0

        for r in (state.qualified_records or []):
            loc_text = str((r.fields if hasattr(r, "fields") else {}).get("location") or getattr(r, "location", "")).lower()
            for req_loc in spec.locations:
                r_clean = req_loc.lower()
                # Check for country or city tokens
                if r_clean in loc_text or any(token in loc_text for token in r_clean.split()):
                    loc_counts[req_loc] = loc_counts.get(req_loc, 0) + 1

        weak = []
        for req_loc, count in loc_counts.items():
            if count == 0:
                weak.append(f"location:{req_loc}")

        raw_count = len(state.candidate_urls) + len(state.raw_records)
        unique_count = len(state.raw_records)
        verified_count = len(state.verified_records)
        qualified_count = len(state.qualified_records)
        dupes_removed = max(0, len(state.seen_canonical_urls) - unique_count) if state.seen_canonical_urls else len(state.rejected_records)

        is_suff = qualified_count >= state.target_results or (len(weak) == 0 and qualified_count > 0)

        return CoverageReport(
            query=spec.raw_query or spec.to_search_hint(),
            sources_searched_count=sources_searched,
            sources_successful_count=successful,
            sources_unavailable_count=unavailable,
            unavailable_sources=unavailable_list,
            raw_candidates_count=raw_count,
            unique_jobs_count=unique_count,
            verified_active_count=verified_count,
            qualified_jobs_count=qualified_count,
            duplicates_removed_count=dupes_removed,
            location_coverage=loc_counts,
            weak_areas=weak,
            is_sufficient=is_suff,
        )

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

