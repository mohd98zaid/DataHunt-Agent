"""
Central JobSourceRegistry — Maintains all registered job sources, capabilities,
regions, and source category mapping.
"""
from typing import Dict, List, Optional
from datahunt.sources.models import JobSource, JobSourceType
from datahunt.models.job_spec import JobSearchSpec


class JobSourceRegistry:
    """Central registry of all supported public job sources and categories."""

    def __init__(self):
        self._sources: Dict[str, JobSource] = {}
        self._register_default_sources()

    def register(self, source: JobSource) -> None:
        """Register or override a job source."""
        self._sources[source.id] = source

    def get(self, source_id: str) -> Optional[JobSource]:
        """Retrieve a registered source by ID."""
        return self._sources.get(source_id)

    def list_all(self, enabled_only: bool = True) -> List[JobSource]:
        """Return all registered sources."""
        sources = list(self._sources.values())
        if enabled_only:
            return [s for s in sources if s.enabled]
        return sources

    def get_by_type(self, source_type: JobSourceType, enabled_only: bool = True) -> List[JobSource]:
        """Return all sources of a given category."""
        return [s for s in self.list_all(enabled_only=enabled_only) if s.source_type == source_type]

    def get_by_region(self, region: str, enabled_only: bool = True) -> List[JobSource]:
        """Return sources operating in a specific region or country (e.g. 'SA', 'AE', 'GLOBAL')."""
        r_upper = region.strip().upper()
        return [
            s for s in self.list_all(enabled_only=enabled_only)
            if "GLOBAL" in s.regions or r_upper in s.regions
        ]

    def get_sources_for_spec(self, spec: JobSearchSpec) -> List[JobSource]:
        """
        Select all relevant sources for a canonical JobSearchSpec based on
        requested locations, remote requirements, and explicit source preferences.
        """
        if spec.requested_sources:
            chosen = []
            for src_id in spec.requested_sources:
                src = self.get(src_id)
                if src and src.enabled:
                    chosen.append(src)
            if chosen:
                return chosen

        selected: Dict[str, JobSource] = {}

        # 1. Always include ATS sources (direct high quality public endpoints)
        for s in self.get_by_type(JobSourceType.ATS):
            selected[s.id] = s

        # 2. Always include Major Job Boards
        for s in self.get_by_type(JobSourceType.MAJOR_JOB_BOARD):
            selected[s.id] = s

        # 3. Always include AI/Tech Boards for AI/tech queries
        query_text = f"{' '.join(spec.titles)} {spec.raw_query}".lower()
        if any(k in query_text for k in ("ai", "genai", "ml", "engineer", "developer", "data", "software")):
            for s in self.get_by_type(JobSourceType.AI_TECH_JOB_BOARD):
                selected[s.id] = s

        # 4. Regional Boards matching user's requested locations
        is_gulf = False
        if spec.locations:
            for loc in spec.locations:
                l_low = loc.lower()
                if any(k in l_low for k in ("saudi", "riyadh", "jeddah", "dammam", "ksa")):
                    is_gulf = True
                    for s in self.get_by_region("SA"):
                        selected[s.id] = s
                elif any(k in l_low for k in ("uae", "dubai", "abu dhabi", "sharjah", "emirates")):
                    is_gulf = True
                    for s in self.get_by_region("AE"):
                        selected[s.id] = s
                elif any(k in l_low for k in ("india", "bangalore", "mumbai", "delhi", "pune")):
                    for s in self.get_by_region("IN"):
                        selected[s.id] = s
                elif any(k in l_low for k in ("uk", "london", "england")):
                    for s in self.get_by_region("UK"):
                        selected[s.id] = s

        if is_gulf:
            for s in self.get_by_region("GULF"):
                selected[s.id] = s

        # 5. Remote Boards if remote is requested or allowed
        if spec.remote is True or spec.remote_status in ("remote", "any") or not spec.locations:
            for s in self.get_by_type(JobSourceType.REMOTE_JOB_BOARD):
                selected[s.id] = s

        # 6. Company Career & Search Engine Index
        for s in self.get_by_type(JobSourceType.COMPANY_CAREER):
            selected[s.id] = s
        for s in self.get_by_type(JobSourceType.SEARCH_ENGINE_INDEX):
            selected[s.id] = s

        # Return sorted by priority (1 is highest)
        return sorted(list(selected.values()), key=lambda x: x.priority)

    def identify_source_for_url(self, url: str) -> Optional[JobSource]:
        """Identify which registered JobSource a given URL belongs to."""
        if not url:
            return None
        from urllib.parse import urlparse
        try:
            domain = urlparse(url).netloc.lower()
            path = urlparse(url).path.lower()
        except Exception:
            return None

        # 1. Match against known source domains
        for src in self.list_all(enabled_only=False):
            for d in src.domains:
                if d and (domain == d or domain.endswith("." + d)):
                    return src

        # 2. Match company careers if path has career indicators
        if any(ci in path for ci in ("/careers", "/career", "/jobs/", "/job/")):
            return self.get("company_careers")

        return self.get("search_engine_index")

    def _register_default_sources(self) -> None:
        """Register the standard suite of ATS platforms, boards, and indices."""
        defaults = [
            # ──────── 1. ATS Platforms (Section 9) ────────
            JobSource(
                id="greenhouse",
                name="Greenhouse",
                source_type=JobSourceType.ATS,
                domains=["boards.greenhouse.io", "job-boards.eu.greenhouse.io"],
                regions=["GLOBAL", "SA", "AE", "US", "UK", "IN"],
                priority=1,
            ),
            JobSource(
                id="lever",
                name="Lever",
                source_type=JobSourceType.ATS,
                domains=["jobs.lever.co"],
                regions=["GLOBAL", "SA", "AE", "US", "UK", "IN"],
                priority=1,
            ),
            JobSource(
                id="ashby",
                name="Ashby",
                source_type=JobSourceType.ATS,
                domains=["jobs.ashbyhq.com"],
                regions=["GLOBAL", "SA", "AE", "US", "UK"],
                priority=1,
            ),
            JobSource(
                id="workable",
                name="Workable",
                source_type=JobSourceType.ATS,
                domains=["apply.workable.com"],
                regions=["GLOBAL", "SA", "AE", "UK", "EU"],
                priority=1,
            ),
            JobSource(
                id="smartrecruiters",
                name="SmartRecruiters",
                source_type=JobSourceType.ATS,
                domains=["jobs.smartrecruiters.com"],
                regions=["GLOBAL", "SA", "AE", "US", "EU"],
                priority=1,
            ),
            JobSource(
                id="teamtailor",
                name="Teamtailor",
                source_type=JobSourceType.ATS,
                domains=["career.teamtailor.com", "careers.teamtailor.com"],
                regions=["GLOBAL", "EU", "AE", "UK"],
                priority=1,
            ),
            JobSource(
                id="personio",
                name="Personio",
                source_type=JobSourceType.ATS,
                domains=["jobs.personio.de", "jobs.personio.com"],
                regions=["GLOBAL", "EU", "UK", "AE"],
                priority=1,
            ),
            JobSource(
                id="workday",
                name="Workday",
                source_type=JobSourceType.ATS,
                domains=["myworkdayjobs.com"],
                regions=["GLOBAL", "SA", "AE", "US", "UK"],
                priority=2,
            ),
            JobSource(
                id="icims",
                name="iCIMS",
                source_type=JobSourceType.ATS,
                domains=["icims.com", "jobs.icims.com"],
                regions=["GLOBAL", "US", "UK", "AE"],
                priority=2,
            ),
            JobSource(
                id="oracle_recruiting",
                name="Oracle Recruiting",
                source_type=JobSourceType.ATS,
                domains=["oraclecloud.com"],
                regions=["GLOBAL", "SA", "AE", "US"],
                priority=2,
            ),
            JobSource(
                id="sap_successfactors",
                name="SAP SuccessFactors",
                source_type=JobSourceType.ATS,
                domains=["successfactors.com"],
                regions=["GLOBAL", "SA", "AE", "EU"],
                priority=2,
            ),
            JobSource(
                id="rippling",
                name="Rippling ATS",
                source_type=JobSourceType.ATS,
                domains=["ats.rippling.com"],
                regions=["GLOBAL", "US", "UK", "AE"],
                priority=2,
            ),

            # ──────── 2. Regional Job Boards (Gulf / Middle East / Asia) ────────
            JobSource(
                id="bayt",
                name="Bayt",
                source_type=JobSourceType.REGIONAL_JOB_BOARD,
                domains=["bayt.com"],
                regions=["SA", "AE", "GULF", "MENA"],
                priority=1,
            ),
            JobSource(
                id="gulftalent",
                name="GulfTalent",
                source_type=JobSourceType.REGIONAL_JOB_BOARD,
                domains=["gulftalent.com"],
                regions=["SA", "AE", "GULF", "MENA"],
                priority=1,
            ),
            JobSource(
                id="naukrigulf",
                name="NaukriGulf",
                source_type=JobSourceType.REGIONAL_JOB_BOARD,
                domains=["naukrigulf.com"],
                regions=["SA", "AE", "GULF", "MENA"],
                priority=1,
            ),
            JobSource(
                id="laimoon",
                name="Laimoon",
                source_type=JobSourceType.REGIONAL_JOB_BOARD,
                domains=["laimoon.com"],
                regions=["AE", "SA", "GULF"],
                priority=2,
            ),
            JobSource(
                id="dubizzle",
                name="Dubizzle Careers",
                source_type=JobSourceType.REGIONAL_JOB_BOARD,
                domains=["dubizzle.com"],
                regions=["AE"],
                priority=2,
            ),
            JobSource(
                id="naukri",
                name="Naukri",
                source_type=JobSourceType.REGIONAL_JOB_BOARD,
                domains=["naukri.com"],
                regions=["IN"],
                priority=2,
            ),
            JobSource(
                id="reed",
                name="Reed UK",
                source_type=JobSourceType.REGIONAL_JOB_BOARD,
                domains=["reed.co.uk"],
                regions=["UK"],
                priority=2,
            ),

            # ──────── 3. Major Global Job Boards ────────
            JobSource(
                id="linkedin",
                name="LinkedIn Jobs",
                source_type=JobSourceType.MAJOR_JOB_BOARD,
                domains=["linkedin.com"],
                regions=["GLOBAL", "SA", "AE", "US", "UK", "IN"],
                priority=1,
            ),
            JobSource(
                id="indeed",
                name="Indeed",
                source_type=JobSourceType.MAJOR_JOB_BOARD,
                domains=["indeed.com"],
                regions=["GLOBAL", "SA", "AE", "US", "UK", "IN"],
                priority=2,
            ),
            JobSource(
                id="glassdoor",
                name="Glassdoor",
                source_type=JobSourceType.MAJOR_JOB_BOARD,
                domains=["glassdoor.com"],
                regions=["GLOBAL", "SA", "AE", "US", "UK"],
                priority=3,
            ),

            # ──────── 4. AI & Tech Niche Boards ────────
            JobSource(
                id="aijobs",
                name="AI Jobs Net",
                source_type=JobSourceType.AI_TECH_JOB_BOARD,
                domains=["aijobs.net"],
                regions=["GLOBAL"],
                priority=2,
            ),
            JobSource(
                id="wellfound",
                name="Wellfound (AngelList)",
                source_type=JobSourceType.AI_TECH_JOB_BOARD,
                domains=["wellfound.com"],
                regions=["GLOBAL", "US", "AE", "UK"],
                priority=2,
            ),
            JobSource(
                id="ycombinator",
                name="Y Combinator Jobs",
                source_type=JobSourceType.AI_TECH_JOB_BOARD,
                domains=["ycombinator.com", "workatastartup.com"],
                regions=["GLOBAL"],
                priority=2,
            ),

            # ──────── 5. Remote Job Boards ────────
            JobSource(
                id="weworkremotely",
                name="We Work Remotely",
                source_type=JobSourceType.REMOTE_JOB_BOARD,
                domains=["weworkremotely.com"],
                regions=["GLOBAL"],
                priority=2,
            ),
            JobSource(
                id="remoteok",
                name="Remote OK",
                source_type=JobSourceType.REMOTE_JOB_BOARD,
                domains=["remoteok.com"],
                regions=["GLOBAL"],
                priority=2,
            ),
            JobSource(
                id="himalayas",
                name="Himalayas",
                source_type=JobSourceType.REMOTE_JOB_BOARD,
                domains=["himalayas.app"],
                regions=["GLOBAL"],
                priority=2,
            ),

            # ──────── 6. Company Career Pages ────────
            JobSource(
                id="company_careers",
                name="Company Direct Career Pages",
                source_type=JobSourceType.COMPANY_CAREER,
                domains=[],
                regions=["GLOBAL"],
                priority=1,
            ),

            # ──────── 7. Search Engine Index ────────
            JobSource(
                id="search_engine_index",
                name="Public Search Engine Index",
                source_type=JobSourceType.SEARCH_ENGINE_INDEX,
                domains=[],
                regions=["GLOBAL"],
                priority=3,
            ),
        ]

        for s in defaults:
            self.register(s)
