"""
Source Adapters — Category-specific query generation, execution, and error isolation.
"""
import time
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional
from datahunt.sources.models import JobSource, JobSourceType, SourceStatus, SourceRunResult
from datahunt.models.job_spec import JobSearchSpec
from datahunt.logger import logger


class BaseSourceAdapter(ABC):
    """Abstract base adapter for a job source category."""

    def __init__(self, source: JobSource):
        self.source = source

    @abstractmethod
    def generate_queries(self, spec: JobSearchSpec) -> List[str]:
        """Generate targeted search queries for this specific source."""
        pass

    def execute(self, spec: JobSearchSpec, search_tool: Any, limit_per_query: int = 15) -> tuple[List[Dict[str, Any]], SourceRunResult]:
        """
        Execute queries for this source with isolated error handling and health tracking.
        Returns (raw_hits, SourceRunResult).
        Never raises exceptions to caller.
        """
        queries = self.generate_queries(spec)
        t0 = time.time()
        result = SourceRunResult(
            source_id=self.source.id,
            source_name=self.source.name,
            queries_executed=0,
            raw_results=0,
            unique_results=0,
            qualified_results=0,
            errors=[],
            latency=0.0
        )
        hits: List[Dict[str, Any]] = []

        if not queries:
            result.status = SourceStatus.NO_RESULTS
            result.latency = time.time() - t0
            return hits, result

        for q in queries:
            result.queries_executed += 1
            try:
                res = search_tool.execute(query=q, limit=limit_per_query)
                if res.success and res.data:
                    for item in res.data:
                        # Stamp source metadata onto the hit
                        item["source_id"] = self.source.id
                        item["source_name"] = self.source.name
                        item["source_type"] = self.source.source_type.value
                        hits.append(item)
                    result.raw_results += len(res.data)
                elif not res.success:
                    err_msg = res.error or "Search returned unsuccessful status"
                    result.errors.append(f"Query '{q[:30]}': {err_msg}")
                    err_low = err_msg.lower()
                    if "timeout" in err_low:
                        result.status = SourceStatus.TIMEOUT
                    elif "429" in err_low or "rate limit" in err_low or "quota" in err_low:
                        result.status = SourceStatus.RATE_LIMITED
                    elif "403" in err_low or "blocked" in err_low or "bot" in err_low:
                        result.status = SourceStatus.BLOCKED
                    else:
                        result.status = SourceStatus.FAILED
            except Exception as e:
                err_str = str(e)
                result.errors.append(f"Query '{q[:30]}' exception: {err_str}")
                err_low = err_str.lower()
                if "timeout" in err_low:
                    result.status = SourceStatus.TIMEOUT
                elif "429" in err_low or "rate limit" in err_low:
                    result.status = SourceStatus.RATE_LIMITED
                elif "403" in err_low or "blocked" in err_low:
                    result.status = SourceStatus.BLOCKED
                else:
                    result.status = SourceStatus.FAILED
                logger.warning(f"Source '{self.source.id}' query '{q[:40]}' failed: {e}")

        result.latency = round(time.time() - t0, 3)
        if hits:
            result.status = SourceStatus.SUCCESS
        elif result.status == SourceStatus.SUCCESS:
            result.status = SourceStatus.NO_RESULTS

        return hits, result


class ATSAdapter(BaseSourceAdapter):
    """Adapter for ATS platforms (Greenhouse, Lever, Ashby, Workable, etc.)."""

    def generate_queries(self, spec: JobSearchSpec) -> List[str]:
        titles = spec.titles or ([spec.job_title] if spec.job_title else ["Engineer"])
        primary_title = titles[0]
        locations = spec.locations or ([spec.location] if spec.location else [])

        queries = []
        domains = self.source.domains or []

        loc_str = ""
        if len(locations) > 1:
            loc_str = f"({' OR '.join(locations)})"
        elif locations:
            loc_str = locations[0]

        for dom in domains:
            if loc_str:
                queries.append(f'site:{dom} {loc_str} "{primary_title}"')
            else:
                queries.append(f'site:{dom} "{primary_title}"')

        # If alternative titles exist and location is specified, add top synonym
        if len(titles) > 1 and domains and loc_str:
            queries.append(f'site:{domains[0]} {loc_str} "{titles[1]}"')

        return queries[:4]


class RegionalBoardAdapter(BaseSourceAdapter):
    """Adapter for regional job boards (Bayt, GulfTalent, NaukriGulf, etc.)."""

    def generate_queries(self, spec: JobSearchSpec) -> List[str]:
        titles = spec.titles or ([spec.job_title] if spec.job_title else ["Engineer"])
        primary_title = titles[0]
        locations = spec.locations or ([spec.location] if spec.location else [])
        domains = self.source.domains or []

        queries = []
        loc_str = ""
        if len(locations) > 1:
            loc_str = f"({' OR '.join(locations)})"
        elif locations:
            loc_str = locations[0]

        for dom in domains:
            if loc_str and not any(r in dom for r in ("gulftalent", "bayt", "naukrigulf")):
                queries.append(f'site:{dom} {loc_str} "{primary_title}"')
            else:
                queries.append(f'site:{dom} "{primary_title}"')

        return queries[:3]


class MajorBoardAdapter(BaseSourceAdapter):
    """Adapter for major global job boards (LinkedIn, Indeed, Glassdoor)."""

    def generate_queries(self, spec: JobSearchSpec) -> List[str]:
        titles = spec.titles or ([spec.job_title] if spec.job_title else ["Engineer"])
        primary_title = titles[0]
        locations = spec.locations or ([spec.location] if spec.location else [])

        queries = []
        loc_str = ""
        if len(locations) > 1:
            loc_str = f"({' OR '.join(locations)})"
        elif locations:
            loc_str = locations[0]

        if "linkedin.com" in self.source.domains:
            if loc_str:
                queries.append(f'site:linkedin.com/jobs {loc_str} "{primary_title}"')
            else:
                queries.append(f'site:linkedin.com/jobs "{primary_title}"')
        elif "indeed.com" in self.source.domains:
            if loc_str:
                queries.append(f'site:indeed.com {loc_str} "{primary_title}"')
            else:
                queries.append(f'site:indeed.com "{primary_title}"')
        elif "glassdoor.com" in self.source.domains:
            if loc_str:
                queries.append(f'site:glassdoor.com/Job {loc_str} "{primary_title}"')
            else:
                queries.append(f'site:glassdoor.com/Job "{primary_title}"')

        return queries[:2]


class TechBoardAdapter(BaseSourceAdapter):
    """Adapter for niche AI and technology job boards."""

    def generate_queries(self, spec: JobSearchSpec) -> List[str]:
        titles = spec.titles or ([spec.job_title] if spec.job_title else ["Engineer"])
        primary_title = titles[0]
        domains = self.source.domains or []
        queries = []

        for dom in domains:
            queries.append(f'site:{dom} "{primary_title}"')

        return queries[:2]


class RemoteBoardAdapter(BaseSourceAdapter):
    """Adapter for dedicated remote job boards."""

    def generate_queries(self, spec: JobSearchSpec) -> List[str]:
        titles = spec.titles or ([spec.job_title] if spec.job_title else ["Engineer"])
        primary_title = titles[0]
        domains = self.source.domains or []
        queries = []

        for dom in domains:
            queries.append(f'site:{dom} "{primary_title}"')

        return queries[:2]


class CompanyCareerAdapter(BaseSourceAdapter):
    """Adapter for discovering direct company career pages."""

    def generate_queries(self, spec: JobSearchSpec) -> List[str]:
        titles = spec.titles or ([spec.job_title] if spec.job_title else ["Engineer"])
        primary_title = titles[0]
        locations = spec.locations or ([spec.location] if spec.location else [])

        loc_str = ""
        if len(locations) > 1:
            loc_str = f"({' OR '.join(locations)})"
        elif locations:
            loc_str = locations[0]

        if loc_str:
            return [
                f'"{primary_title}" careers {loc_str} apply',
                f'"{primary_title}" hiring {loc_str} "job description"',
            ]
        return [
            f'"{primary_title}" careers apply',
            f'"{primary_title}" hiring "job description"',
        ]


class SearchIndexAdapter(BaseSourceAdapter):
    """General search engine indexed job page adapter."""

    def generate_queries(self, spec: JobSearchSpec) -> List[str]:
        titles = spec.titles or ([spec.job_title] if spec.job_title else ["Engineer"])
        primary_title = titles[0]
        locations = spec.locations or ([spec.location] if spec.location else [])

        loc_str = ""
        if len(locations) > 1:
            loc_str = f"in {' or '.join(locations)}"
        elif locations:
            loc_str = f"in {locations[0]}"

        exp_str = ""
        if spec.experience_min is not None and spec.experience_max is not None:
            exp_str = f"{spec.experience_min}-{spec.experience_max} years"

        parts = [f'"{primary_title}" jobs', loc_str, exp_str]
        q = " ".join(p for p in parts if p).strip()
        return [q]


def get_adapter_for_source(source: JobSource) -> BaseSourceAdapter:
    """Factory creating the appropriate adapter for a JobSource."""
    st = source.source_type
    if st == JobSourceType.ATS:
        return ATSAdapter(source)
    elif st == JobSourceType.REGIONAL_JOB_BOARD:
        return RegionalBoardAdapter(source)
    elif st == JobSourceType.MAJOR_JOB_BOARD:
        return MajorBoardAdapter(source)
    elif st == JobSourceType.AI_TECH_JOB_BOARD:
        return TechBoardAdapter(source)
    elif st == JobSourceType.REMOTE_JOB_BOARD:
        return RemoteBoardAdapter(source)
    elif st == JobSourceType.COMPANY_CAREER:
        return CompanyCareerAdapter(source)
    else:
        return SearchIndexAdapter(source)
