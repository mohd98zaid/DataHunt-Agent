import re
import time
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.parse import urlparse

from datahunt.logger import logger
from datahunt.agent.discovery_models import (
    SearchTaskType,
    StopReason,
    DiscoveredSourceType,
    SearchTask,
    DiscoveryBudget,
    DiscoveryRoundTelemetry,
    SourceCoverageMatrix,
    DiscoveryState,
    CrawlTask,
    CrawlTaskStatus,
)

# Known ATS platforms and their hostname patterns
ATS_PLATFORMS = {
    "greenhouse": {
        "domain": "boards.greenhouse.io",
        "patterns": [r"boards\.greenhouse\.io/([^/?#]+)", r"job-boards\.greenhouse\.io/([^/?#]+)"],
    },
    "lever": {
        "domain": "jobs.lever.co",
        "patterns": [r"jobs\.lever\.co/([^/?#]+)"],
    },
    "ashby": {
        "domain": "jobs.ashbyhq.com",
        "patterns": [r"jobs\.ashbyhq\.com/([^/?#]+)"],
    },
    "workable": {
        "domain": "apply.workable.com",
        "patterns": [r"apply\.workable\.com/([^/?#]+)", r"([a-zA-Z0-9-]+)\.workable\.com"],
    },
    "smartrecruiters": {
        "domain": "jobs.smartrecruiters.com",
        "patterns": [r"jobs\.smartrecruiters\.com/([^/?#]+)"],
    },
    "breezy": {
        "domain": "breezy.hr",
        "patterns": [r"([a-zA-Z0-9-]+)\.breezy\.hr"],
    },
    "workday": {
        "domain": "myworkdayjobs.com",
        "patterns": [r"([a-zA-Z0-9-]+)\.myworkdayjobs\.com"],
    },
    "bamboohr": {
        "domain": "bamboohr.com",
        "patterns": [r"([a-zA-Z0-9-]+)\.bamboohr\.com"],
    },
    "teamtailor": {
        "domain": "career.teamtailor.com",
        "patterns": [r"([a-zA-Z0-9-]+)\.teamtailor\.com", r"career\.teamtailor\.com/([^/?#]+)"],
    },
    "personio": {
        "domain": "jobs.personio.com",
        "patterns": [r"([a-zA-Z0-9-]+)\.jobs\.personio\.com", r"([a-zA-Z0-9-]+)\.personio\.de", r"jobs\.personio\.com/([^/?#]+)"],
    },
    "rippling": {
        "domain": "ats.rippling.com",
        "patterns": [r"ats\.rippling\.com/([^/?#]+)"],
    },
}

IGNORED_COMPANIES = {
    "job", "jobs", "career", "careers", "hiring", "apply", "search", "post", "positions",
    "view", "index", "feed", "linkedin", "indeed", "glassdoor", "bayt", "baytcom", "bayt.com",
    "gulftalent", "naukrigulf", "naukri", "monster", "tanqeeb", "laimoon", "dubizzle",
    "teamtailor", "personio", "rippling", "dubai", "saudi", "riyadh",
    "remote", "general", "fulltime", "parttime", "internship", "login", "register", "candidate",
    "applicant", "apply now", "click here", "read more", "view job", "work in", "vacancies",
    "vacancy", "openings", "opening", "requirements", "requirement", "salary", "salaries",
    "opportunity", "opportunities", "middle east", "middleeast", "united arab emirates", "saudi arabia"
}

# Keep the regional seed plan broad enough to cover each source lane, but small
# enough that verification/fetch work remains ahead of another search wave.
# shortcut: 96 seed tasks, covers more portals and ATS hubs while staying within budget.
REGIONAL_INITIAL_TASK_LIMIT = 72
# shortcut: 8 hit-driven tasks per search batch, raise only with queue backpressure.
REGIONAL_DYNAMIC_TASK_LIMIT = 8
CORE_PUBLIC_ATS = (
    ("boards.greenhouse.io", "Greenhouse"),
    ("jobs.lever.co", "Lever"),
    ("jobs.ashbyhq.com", "Ashby"),
    ("apply.workable.com", "Workable"),
    ("jobs.smartrecruiters.com", "SmartRecruiters"),
    ("career.teamtailor.com", "Teamtailor"),
    ("jobs.personio.com", "Personio"),
    ("myworkdayjobs.com", "Workday"),
    ("ats.rippling.com", "Rippling"),
)


class DiscoveryEngine:
    """
    Drives dynamic, multi-round job source discovery.
    Observes search hits -> extracts companies, ATS endpoints, and new boards ->
    generates targeted next-round search tasks -> balances the coverage matrix ->
    evaluates stopping conditions based on coverage and diminishing returns.
    """

    def __init__(self, budget: Optional[DiscoveryBudget] = None):
        self.budget = budget or DiscoveryBudget()

    @staticmethod
    def _clean_role(role: str) -> str:
        return re.sub(
            r"\b(with|requiring)?\s*\d+[-–\sto]+\d*\s*(?:years?|yrs?)(?:\s*exp(?:erience)?)?\b",
            "",
            role,
            flags=re.IGNORECASE,
        ).strip() or role

    @staticmethod
    def _requested_regions(state: Any) -> List[str]:
        """Return the user's regions verbatim, without adding city/global targets."""
        raw_regions = list(getattr(state, "locations", None) or [])
        if not raw_regions:
            explicit = getattr(state, "explicit_location", None)
            if explicit:
                raw_regions = [explicit]

        regions: List[str] = []
        for raw in raw_regions:
            for part in re.split(r"\s+(?:or|and)\s+|[,/;]", str(raw), flags=re.IGNORECASE):
                region = part.strip()
                if not region or region.lower() in {"remote", "anywhere", "worldwide", "global", "any"}:
                    continue
                if region.casefold() not in {r.casefold() for r in regions}:
                    regions.append(region)
        return regions

    @staticmethod
    def _explicit_remote_requested(state: Any) -> bool:
        request = (getattr(state, "request", "") or "").lower()
        if re.search(r"\b(?:remote|anywhere|worldwide|wfh|telecommute)\b", request):
            return True
        canonical = getattr(state, "canonical_job_request", None)
        return getattr(canonical, "remote_status", "") in ("remote", "hybrid")

    @staticmethod
    def _region_hubs(region: str) -> List[str]:
        """Use known hubs only as query terms; the requested region stays primary."""
        low = region.casefold()
        if any(k in low for k in ("uae", "united arab emirates", "emirates")):
            return ["Dubai", "Abu Dhabi", "Sharjah"]
        if any(k in low for k in ("gulf", "gcc", "mena", "middle east")):
            return ["Dubai", "Riyadh", "Doha"]
        if any(k in low for k in ("saudi", "ksa")):
            return ["Riyadh", "Jeddah", "Dammam", "Khobar", "NEOM"]
        if "qatar" in low:
            return ["Doha"]
        if "india" in low:
            return ["Bengaluru", "Hyderabad", "Pune", "Gurgaon", "Noida", "Mumbai"]
        if any(k in low for k in ("dubai", "abu dhabi", "sharjah", "ajman")):
            return [region]
        if any(k in low for k in ("riyadh", "jeddah", "dammam", "khobar", "neom")):
            return [region]
        return [region]

    @staticmethod
    def _regional_portals(region: str) -> List[Tuple[str, str]]:
        low = region.casefold()
        if any(k in low for k in (
            "uae", "united arab emirates", "emirates", "dubai", "abu dhabi", "sharjah",
            "saudi", "ksa", "riyadh", "jeddah", "dammam", "khobar", "neom", "qatar", "doha",
            "gulf", "gcc", "mena", "middle east",
        )):
            return [
                ("naukrigulf.com", "NaukriGulf"),
                ("bayt.com", "Bayt"),
                ("gulftalent.com", "GulfTalent"),
                ("laimoon.com", "Laimoon"),
                ("dubizzle.com", "Dubizzle"),
                ("tanqeeb.com", "Tanqeeb"),
            ]
        if any(k in low for k in ("india", "bengaluru", "bangalore", "hyderabad", "pune", "gurgaon", "gurugram", "noida", "mumbai", "delhi", "chennai")):
            return [("naukri.com", "Naukri"), ("instahyre.com", "Instahyre"), ("foundit.in", "Foundit")]
        if any(k in low for k in ("uk", "united kingdom", "london")):
            return [("reed.co.uk", "Reed"), ("totaljobs.com", "Totaljobs")]
        # A generic regional index query keeps unknown regions in the regional lane.
        return [("regional_portals", "Regional portals")]

    @staticmethod
    def _query_scope(role: str, region: str, hub: Optional[str] = None) -> str:
        terms = [f'"{role}"', f'"{region}"']
        if hub and hub.casefold() != region.casefold():
            terms.append(f'"{hub}"')
        return " ".join(terms)

    @staticmethod
    def _query_regions(role: str, regions: List[str]) -> str:
        if len(regions) <= 1:
            return DiscoveryEngine._query_scope(role, regions[0]) if regions else f'"{role}"'
        region_terms = " OR ".join(f'"{region}"' for region in regions)
        return f'"{role}" ({region_terms})'

    @staticmethod
    def _task_location(region: str, hub: Optional[str] = None) -> str:
        # Keep the exact requested region visible while retaining city-level
        # compatibility for the existing geographic drilling consumers.
        return region if not hub or hub.casefold() == region.casefold() else f"{region} — {hub}"

    def _generate_regional_initial_tasks(self, state: Any, role: str, alt_role: Optional[str]) -> List[SearchTask]:
        regions = self._requested_regions(state)
        tasks: List[SearchTask] = []
        lane_region_tasks: Dict[str, Dict[str, List[SearchTask]]] = {
            lane: {region: [] for region in regions}
            for lane in (
                "regional_portals", "major_boards", "public_ats_careers",
                "linkedin_hiring_posts", "general_web",
            )
        }
        total_created = 0

        def add(
            lane: str,
            prefix: str,
            task_type: SearchTaskType,
            query: str,
            source: str,
            source_type: DiscoveredSourceType,
            region: str,
            priority: int,
            reason: str,
            hub: Optional[str] = None,
            **extra: Any,
        ) -> None:
            nonlocal total_created
            metadata = {"lane": lane, "requested_region": region}
            if hub:
                metadata["city"] = hub
            metadata.update(extra.pop("metadata", {}))
            if lane not in lane_region_tasks:
                lane_region_tasks[lane] = {}
            if region not in lane_region_tasks[lane]:
                lane_region_tasks[lane][region] = []
            lane_region_tasks[lane][region].append(SearchTask(
                id=f"r1_{prefix}_{total_created}",
                task_type=task_type,
                query=query.strip(),
                source=source,
                source_type=source_type,
                priority=priority,
                depth=0,
                round=1,
                reason=reason,
                location=self._task_location(region, hub),
                metadata=metadata,
                **extra,
            ))
            total_created += 1

        for region in regions:
            hubs = self._region_hubs(region)
            portal_hubs = hubs[:3]
            ats_hubs = hubs[:2] + [region]
            scope = self._query_scope(role, region)

            # 1. Regional portals
            for portal, portal_name in self._regional_portals(region):
                for hub in portal_hubs:
                    scoped = self._query_scope(role, region, hub)
                    query = (
                        f"site:{portal} {scoped} jobs"
                        if portal != "regional_portals"
                        else f"{scoped} jobs \"regional job board\""
                    )
                    add(
                        "regional_portals", "reg", SearchTaskType.REGIONAL_BOARD_SEARCH,
                        query, portal, DiscoveredSourceType.REGIONAL_BOARD, region, 1,
                        f"Regional portal sweep on {portal_name}", hub=hub,
                    )

            # 2. Major job boards (LinkedIn jobs is distinct from public posts below)
            for source in ("indeed.com", "glassdoor.com", "linkedin.com/jobs"):
                add(
                    "major_boards", "major", SearchTaskType.BOARD_SEARCH,
                    f"site:{source} {scope} jobs",
                    source, DiscoveredSourceType.MAJOR_BOARD, region, 2,
                    f"Major job board sweep on {source}",
                )

            # 3. Public ATS and employer career pages
            for domain, platform in CORE_PUBLIC_ATS:
                for hub in ats_hubs:
                    add(
                        "public_ats_careers", "ats", SearchTaskType.ATS_SEARCH,
                        f"site:{domain} {self._query_scope(role, region, hub)}",
                        domain, DiscoveredSourceType.ATS_PORTAL, region, 2,
                        f"Public ATS search on {platform}", hub=hub, ats_platform=platform.lower(),
                    )
            add(
                "public_ats_careers", "careers", SearchTaskType.CAREER_PAGE_SEARCH,
                f"{scope} (careers OR \"open positions\" OR \"we are hiring\")",
                "company_career_page", DiscoveredSourceType.COMPANY_CAREER_PAGE, region, 2,
                "Public employer career-page discovery",
            )

            # 4. Public LinkedIn hiring posts; no profiles or authenticated feeds.
            for hub in [region] + hubs[:2]:
                add(
                    "linkedin_hiring_posts", "lipost", SearchTaskType.BOARD_SEARCH,
                    f'site:linkedin.com/posts "hiring" {self._query_scope(role, region, hub)}',
                    "linkedin.com", DiscoveredSourceType.COMMUNITY_FORUM, region, 2,
                    "Public LinkedIn recruiter/founder hiring-post discovery", hub=hub,
                )
            if hubs:
                add(
                    "linkedin_hiring_posts", "lipost_team", SearchTaskType.BOARD_SEARCH,
                    f'site:linkedin.com/posts "we are hiring" {self._query_scope(role, region, hubs[0])}',
                    "linkedin.com", DiscoveredSourceType.COMMUNITY_FORUM, region, 2,
                    "Public LinkedIn team hiring announcement discovery", hub=hubs[0],
                )

            # 5. General web-indexed job postings.  The alternate title is kept
            # in this lane rather than creating another source lane.
            for search_role in [role] + ([alt_role] if alt_role else []):
                add(
                    "general_web", "direct", SearchTaskType.BOARD_SEARCH,
                    f'{self._query_scope(search_role, region, hubs[0] if hubs else None)} jobs (careers OR apply OR hiring)',
                    "direct_search", DiscoveredSourceType.MAJOR_BOARD, region, 2,
                    f"General web-indexed job search for {search_role}",
                )

        # Interleave lanes and regions so a bounded plan cannot spend the whole budget on
        # portal pagination before ATS, LinkedIn, or web coverage is attempted.
        lane_order = (
            "regional_portals", "major_boards", "public_ats_careers",
            "linkedin_hiring_posts", "general_web",
        )
        lane_tasks: Dict[str, List[SearchTask]] = {}
        for lane in lane_order:
            merged: List[SearchTask] = []
            max_r = max((len(lane_region_tasks[lane].get(r, [])) for r in regions), default=0)
            for idx in range(max_r):
                for r in regions:
                    r_list = lane_region_tasks[lane].get(r, [])
                    if idx < len(r_list):
                        merged.append(r_list[idx])
            lane_tasks[lane] = merged

        positions = {lane: 0 for lane in lane_order}
        while len(tasks) < REGIONAL_INITIAL_TASK_LIMIT:
            added = False
            for lane in lane_order:
                pos = positions[lane]
                bucket = lane_tasks[lane]
                if pos < len(bucket):
                    tasks.append(bucket[pos])
                    positions[lane] = pos + 1
                    added = True
                    if len(tasks) >= REGIONAL_INITIAL_TASK_LIMIT:
                        break
            if not added:
                break
        return tasks

    @staticmethod
    def _hit_matches_role(url: str, title: str, snippet: str, role: str) -> bool:
        text = f"{url} {title} {snippet}".casefold()
        role_tokens = [t for t in re.findall(r"[a-z0-9]+", role.casefold()) if len(t) > 2]
        ai_role = any(term in role.casefold() for term in ("genai", "generative ai", "llm", "ai engineer", "machine learning"))
        if ai_role:
            ai_terms = (
                "genai", "generative ai", "llm", "large language model", "machine learning",
                "deep learning", "ai engineer", "artificial intelligence", "rag", "agentic",
                "ai agent", "prompt engineer", "ai architect", "ai solution architect",
                "copilot", "bedrock", "vertex ai", "vector database", "intelligent automation"
            )
            if any(term in text for term in ai_terms):
                return True
            return bool(
                (re.search(r"\bai\b", text) or re.search(r"\bgen\s*ai\b", text))
                and re.search(r"\b(engineer|developer|researcher|scientist|architect|consultant|specialist|lead|manager)\b", text)
            )
        from datahunt.agent.policies import ROLE_TAXONOMY
        for family_key, family_spec in ROLE_TAXONOMY.items():
            if any(re.search(r'\b' + re.escape(k) + r'\b', role.casefold()) for k in family_spec["keywords"]):
                if any(dt in text for dt in family_spec["direct_titles"]):
                    return True
                if any(re.search(r'\b' + re.escape(k) + r'\b', text) for k in family_spec["keywords"]):
                    return True

        return any(token in text for token in role_tokens)

    @staticmethod
    def _hit_matches_requested_region(url: str, title: str, snippet: str, regions: List[str]) -> bool:
        """Reject explicit foreign-region hits before they spawn more work."""
        if not regions:
            return True

        text = f"{url} {title} {snippet}".casefold()
        target_terms = {
            "saudi arabia": ("saudi", "saudi arabia", "ksa", "riyadh", "jeddah", "dammam", "khobar", "neom"),
            "uae": ("uae", "united arab emirates", "dubai", "abu dhabi", "sharjah", "ajman"),
            "qatar": ("qatar", "doha"),
            "kuwait": ("kuwait",),
            "bahrain": ("bahrain", "manama"),
            "oman": ("oman", "muscat"),
        }
        requested_terms = []
        for region in regions:
            low = region.casefold()
            requested_terms.extend(target_terms.get(low, (low,)))

        foreign_terms = (
            "india", "bangalore", "bengaluru", "hyderabad", "pune", "mumbai", "delhi",
            "united states", "usa", "new york", "denver", "chicago", "california",
            "united kingdom", "london", "europe", "germany", "france", "paris",
            "sweden", "norway", "denmark", "finland", "canada", "toronto", "australia",
            "singapore", "japan", "tokyo", "south korea", "egypt", "cairo",
        )
        has_target = any(re.search(r"(?<!\w)" + re.escape(term) + r"(?!\w)", text) for term in requested_terms)
        has_foreign = any(re.search(r"(?<!\w)" + re.escape(term) + r"(?!\w)", text) for term in foreign_terms)
        return not has_foreign or has_target

    @staticmethod
    def _task_scope_location(task: SearchTask, regions: List[str]) -> str:
        if task.location:
            return task.location
        if len(regions) == 1:
            return regions[0]
        return " OR ".join(regions) if regions else "general"

    @staticmethod
    def _task_requested_region(task: SearchTask, regions: List[str]) -> Optional[str]:
        requested = (task.metadata or {}).get("requested_region") if getattr(task, "metadata", None) else None
        if requested:
            return requested
        if (task.metadata or {}).get("requested_regions"):
            return None
        location = (task.location or "").casefold()
        for region in regions:
            if region.casefold() in location:
                return region
        return None

    def generate_initial_tasks(self, spec: Any, state: Any) -> List[SearchTask]:
        """
        Formulate Round 1 broad discovery tasks across ATS platforms, regional boards,
        major boards, and niche tech/remote portals based on user requirements.
        """
        requested_regions = self._requested_regions(state)
        if requested_regions:
            role = getattr(state, "explicit_titles", None) or []
            role_clean = self._clean_role(role[0] if role else "GenAI Engineer")
            alt_role = (
                "Generative AI Engineer"
                if "genai" in role_clean.lower()
                else ("AI Engineer" if "generative" in role_clean.lower() else None)
            )
            tasks = self._generate_regional_initial_tasks(state, role_clean, alt_role)
            remote_tasks: List[SearchTask] = []
            if self._explicit_remote_requested(state):
                # Remote is an opt-in lane. Keep the regional scope on the task
                # so a regional + remote request cannot silently become global.
                for region in requested_regions:
                    remote_tasks.append(SearchTask(
                        id=f"r1_remote_{len(tasks) + len(remote_tasks)}",
                        task_type=SearchTaskType.BOARD_SEARCH,
                        query=f'site:remoteok.com "{role_clean}" "{region}" remote',
                        source="remoteok.com",
                        source_type=DiscoveredSourceType.REMOTE_BOARD,
                        priority=3,
                        depth=0,
                        round=1,
                        reason=f"Opt-in remote job search scoped to {region}",
                        location=f"{region} — Remote",
                        metadata={"lane": "remote_opt_in", "requested_region": region},
                    ))
            if remote_tasks:
                # Reserve space for explicitly requested remote work instead of
                # silently slicing that lane out of the bounded regional plan.
                tasks = tasks[:max(0, REGIONAL_INITIAL_TASK_LIMIT - len(remote_tasks))]
                tasks.extend(remote_tasks)
            return tasks[:REGIONAL_INITIAL_TASK_LIMIT]

        tasks: List[SearchTask] = []
        loc_str = " ".join(state.locations) if state.locations else (state.explicit_location or "")
        loc_lower = loc_str.lower()
        role = state.explicit_titles[0] if state.explicit_titles else "GenAI Engineer"
        
        # Clean role of experience noise
        role_clean = re.sub(r"\b(with|requiring)?\s*\d+[-–\sto]+\d*\s*(?:years?|yrs?)(?:\s*exp(?:erience)?)?\b", "", role, flags=re.IGNORECASE).strip()
        role_clean = role_clean or role

        has_uae = any(k in loc_lower for k in ("uae", "dubai", "abu dhabi", "sharjah", "emirates"))
        has_saudi = any(k in loc_lower for k in ("saudi", "riyadh", "jeddah", "dammam", "khobar", "ksa"))
        has_qatar = any(k in loc_lower for k in ("qatar", "doha"))
        has_india = any(k in loc_lower for k in ("india", "bangalore", "bengaluru", "mumbai", "delhi", "hyderabad", "pune", "chennai", "gurgaon", "gurugram", "noida"))
        is_generic_gulf = any(k in loc_lower for k in ("gulf", "middle east", "mena", "gcc"))
        is_gulf = has_uae or has_saudi or has_qatar or is_generic_gulf
        region_label = "Gulf/MENA" if is_gulf else ("India" if has_india else (loc_str or "Global"))

        # Determine discrete target focal locations
        target_locations: List[str] = []
        if is_gulf:
            if has_uae and not has_saudi:
                target_locations = ["Dubai", "Abu Dhabi", "Sharjah", "UAE"]
            elif has_saudi and not has_uae:
                target_locations = ["Riyadh", "Jeddah", "Dammam", "Khobar", "NEOM", "Saudi Arabia"]
            elif has_qatar and not has_uae and not has_saudi:
                target_locations = ["Doha", "Qatar"]
            else:
                target_locations = ["Dubai", "Abu Dhabi", "Sharjah", "Riyadh", "Jeddah", "UAE", "Saudi Arabia"]
        elif has_india:
            target_locations = ["Bengaluru", "Hyderabad", "Pune", "Gurgaon", "Noida", "Mumbai", "India"]
        elif state.locations:
            for loc in state.locations:
                parts = re.split(r"\s+or\s+|\s*,\s*", str(loc), flags=re.IGNORECASE)
                for p in parts:
                    clean_p = p.strip()
                    if clean_p and clean_p not in target_locations:
                        target_locations.append(clean_p)
        elif loc_str:
            parts = re.split(r"\s+or\s+|\s*,\s*", str(loc_str), flags=re.IGNORECASE)
            for p in parts:
                clean_p = p.strip()
                if clean_p and clean_p not in target_locations:
                    target_locations.append(clean_p)

        primary_loc = target_locations[0] if target_locations else (loc_str or "")

        # Alternative high-yield title for expanded discovery
        alt_role = "Generative AI Engineer" if "genai" in role_clean.lower() else ("AI Engineer" if "generative" in role_clean.lower() else None)

        # 1. Regional Job Boards (Priority 1 for Gulf/Regional sweeps)
        if is_gulf:
            regional_hubs = []
            if has_uae or is_generic_gulf:
                regional_hubs.append("Dubai")
            if has_saudi:
                regional_hubs.append("Riyadh")
            if not regional_hubs:
                regional_hubs = [primary_loc]

            regional_targets = []
            for rhub in regional_hubs:
                regional_targets.extend([
                    ("naukrigulf.com", f'site:naukrigulf.com "{role_clean}" {rhub}'.strip()),
                    ("bayt.com", f'site:bayt.com "{role_clean}" {rhub}'.strip()),
                    ("gulftalent.com", f'site:gulftalent.com "{role_clean}" {rhub}'.strip()),
                ])
            if has_uae or is_generic_gulf:
                regional_targets.append(("linkedin.com", f'site:linkedin.com/jobs "{role_clean}" Dubai'))
                if "Abu Dhabi" in target_locations:
                    regional_targets.append(("linkedin.com", f'site:linkedin.com/jobs "{role_clean}" "Abu Dhabi"'))
                if "Sharjah" in target_locations:
                    regional_targets.append(("linkedin.com", f'site:linkedin.com/jobs "{role_clean}" Sharjah'))
                if alt_role:
                    regional_targets.append(("linkedin.com", f'site:linkedin.com/jobs "{alt_role}" Dubai'))
            if has_saudi or is_generic_gulf:
                regional_targets.append(("linkedin.com", f'site:linkedin.com/jobs "{role_clean}" Riyadh'))
                if "Jeddah" in target_locations:
                    regional_targets.append(("linkedin.com", f'site:linkedin.com/jobs "{role_clean}" Jeddah'))
                if "Dammam" in target_locations:
                    regional_targets.append(("linkedin.com", f'site:linkedin.com/jobs "{role_clean}" Dammam'))
                if alt_role:
                    regional_targets.append(("linkedin.com", f'site:linkedin.com/jobs "{alt_role}" Riyadh'))
            if has_qatar and not (has_uae or has_saudi):
                regional_targets.append(("linkedin.com", f'site:linkedin.com/jobs "{role_clean}" Doha'))

            for src, q in regional_targets:
                tasks.append(SearchTask(
                    id=f"r1_reg_{len(tasks)}",
                    task_type=SearchTaskType.REGIONAL_BOARD_SEARCH,
                    query=q,
                    source=src,
                    source_type=DiscoveredSourceType.REGIONAL_BOARD,
                    priority=1,
                    depth=0,
                    round=1,
                    reason=f"Regional job board sweep on {src}",
                    location=loc_str,
                ))
        elif has_india:
            india_targets = [
                ("naukri.com", f'site:naukri.com "{role_clean}" India'.strip()),
                ("instahyre.com", f'site:instahyre.com "{role_clean}"'.strip()),
                ("foundit.in", f'site:foundit.in "{role_clean}"'.strip()),
                ("linkedin.com", f'site:linkedin.com/jobs "{role_clean}" India'),
                ("linkedin.com", f'site:linkedin.com/jobs "{role_clean}" Bengaluru'),
            ]
            if alt_role:
                india_targets.append(("linkedin.com", f'site:linkedin.com/jobs "{alt_role}" India'))

            for src, q in india_targets:
                tasks.append(SearchTask(
                    id=f"r1_india_{len(tasks)}",
                    task_type=SearchTaskType.REGIONAL_BOARD_SEARCH,
                    query=q,
                    source=src,
                    source_type=DiscoveredSourceType.REGIONAL_BOARD,
                    priority=1,
                    depth=0,
                    round=1,
                    reason=f"Indian regional job board sweep on {src}",
                    location="India",
                ))

        # 2. Major Job Boards (Priority 2, or Priority 1 for LinkedIn when non-gulf)
        major_targets = [
            ("indeed.com", f'site:indeed.com/jobs "{role_clean}" {primary_loc}'.strip()),
            ("glassdoor.com", f'site:glassdoor.com "{role_clean}" {primary_loc}'.strip()),
        ]
        if not is_gulf and not has_india:
            major_targets.insert(0, ("linkedin.com", f'site:linkedin.com/jobs "{role_clean}" {primary_loc}'.strip()))

        for src, q in major_targets:
            tasks.append(SearchTask(
                id=f"r1_maj_{len(tasks)}",
                task_type=SearchTaskType.BOARD_SEARCH,
                query=q,
                source=src,
                source_type=DiscoveredSourceType.MAJOR_BOARD,
                priority=2,
                depth=0,
                round=1,
                reason=f"Major job board sweep on {src}",
                location=loc_str,
            ))

        # 2.1 LinkedIn Hiring Posts ("Hidden Market" recruiter & founder hiring posts)
        li_post_tasks = []
        if is_gulf:
            if has_uae or is_generic_gulf:
                li_post_tasks.append(f'site:linkedin.com/posts "hiring" "{role_clean}" Dubai')
                li_post_tasks.append(f'site:linkedin.com/posts "hiring" "{role_clean}" "Abu Dhabi"')
            if has_saudi or is_generic_gulf:
                li_post_tasks.append(f'site:linkedin.com/posts "hiring" "{role_clean}" Riyadh')
                li_post_tasks.append(f'site:linkedin.com/posts "hiring" "{role_clean}" Jeddah')
        elif has_india:
            li_post_tasks.append(f'site:linkedin.com/posts "hiring" "{role_clean}" Bengaluru')
            li_post_tasks.append(f'site:linkedin.com/posts "hiring" "{role_clean}" India')
        elif primary_loc:
            li_post_tasks.append(f'site:linkedin.com/posts "hiring" "{role_clean}" {primary_loc}')
        else:
            li_post_tasks.append(f'site:linkedin.com/posts "hiring" "{role_clean}"')

        for q in li_post_tasks:
            tasks.append(SearchTask(
                id=f"r1_lipost_{len(tasks)}",
                task_type=SearchTaskType.BOARD_SEARCH,
                query=q,
                source="linkedin.com",
                source_type=DiscoveredSourceType.COMMUNITY_FORUM,
                priority=2,
                depth=0,
                round=1,
                reason="LinkedIn recruiter and founder hiring post discovery",
                location=primary_loc or loc_str,
            ))

        # 3. Direct Natural Language Career Page Sweep (Priority 2)
        tasks.append(SearchTask(
            id=f"r1_careers_{len(tasks)}",
            task_type=SearchTaskType.CAREER_PAGE_SEARCH,
            query=f'"{role_clean}" {primary_loc} (careers OR "open positions" OR "we are hiring")'.strip(),
            source="company_career_page",
            source_type=DiscoveredSourceType.COMPANY_CAREER_PAGE,
            priority=2,
            depth=0,
            round=1,
            reason="Direct employer career portal discovery",
            location=primary_loc or loc_str,
        ))

        # 4. ATS Portals (Priority 2)
        # Query primary hubs individually so search engines match discrete ATS postings
        if has_uae and has_saudi:
            ats_hubs = ["Dubai", "Abu Dhabi", "Riyadh", "Jeddah"]
        elif has_india:
            ats_hubs = ["India", "Bengaluru", "Hyderabad", "Pune"]
        elif target_locations:
            ats_hubs = target_locations[:4]
        else:
            ats_hubs = [primary_loc] if primary_loc else [""]

        for hub in ats_hubs:
            hub_suffix = f" {hub.strip()}" if hub and hub.strip() else ""
            tasks.append(SearchTask(
                id=f"r1_ats_{len(tasks)}",
                task_type=SearchTaskType.ATS_SEARCH,
                query=f'site:boards.greenhouse.io "{role_clean}"{hub_suffix}',
                source="boards.greenhouse.io",
                source_type=DiscoveredSourceType.ATS_PORTAL,
                priority=2,
                depth=0,
                round=1,
                reason=f"Direct ATS harvest on Greenhouse ({hub or 'Global'})",
                location=hub or loc_str,
            ))
            tasks.append(SearchTask(
                id=f"r1_ats_{len(tasks)}",
                task_type=SearchTaskType.ATS_SEARCH,
                query=f'site:jobs.lever.co "{role_clean}"{hub_suffix}',
                source="jobs.lever.co",
                source_type=DiscoveredSourceType.ATS_PORTAL,
                priority=2,
                depth=0,
                round=1,
                reason=f"Direct ATS harvest on Lever ({hub or 'Global'})",
                location=hub or loc_str,
            ))
            tasks.append(SearchTask(
                id=f"r1_ats_{len(tasks)}",
                task_type=SearchTaskType.ATS_SEARCH,
                query=f'site:apply.workable.com "{role_clean}"{hub_suffix}',
                source="apply.workable.com",
                source_type=DiscoveredSourceType.ATS_PORTAL,
                priority=2,
                depth=0,
                round=1,
                reason=f"Direct ATS harvest on Workable ({hub or 'Global'})",
                location=hub or loc_str,
            ))
            tasks.append(SearchTask(
                id=f"r1_ats_{len(tasks)}",
                task_type=SearchTaskType.ATS_SEARCH,
                query=f'site:jobs.ashbyhq.com "{role_clean}"{hub_suffix}',
                source="jobs.ashbyhq.com",
                source_type=DiscoveredSourceType.ATS_PORTAL,
                priority=2,
                depth=0,
                round=1,
                reason=f"Direct ATS harvest on Ashby ({hub or 'Global'})",
                location=hub or loc_str,
            ))

        # Secondary ATS title sweep for high-demand AI roles
        if alt_role:
            alt_hubs = ["Dubai", "Riyadh"] if (has_uae and has_saudi) else (["India", "Bengaluru"] if has_india else ([primary_loc] if primary_loc else [""]))
            for ahub in alt_hubs:
                ahub_suffix = f" {ahub.strip()}" if ahub and ahub.strip() else ""
                tasks.append(SearchTask(
                    id=f"r1_ats_alt_{len(tasks)}",
                    task_type=SearchTaskType.ATS_SEARCH,
                    query=f'site:boards.greenhouse.io "{alt_role}"{ahub_suffix}',
                    source="boards.greenhouse.io",
                    source_type=DiscoveredSourceType.ATS_PORTAL,
                    priority=2,
                    depth=0,
                    round=1,
                    reason=f"Direct ATS harvest on Greenhouse for '{alt_role}' ({ahub or 'Global'})",
                    location=ahub or loc_str,
                ))
                tasks.append(SearchTask(
                    id=f"r1_ats_alt_{len(tasks)}",
                    task_type=SearchTaskType.ATS_SEARCH,
                    query=f'site:jobs.lever.co "{alt_role}"{ahub_suffix}',
                    source="jobs.lever.co",
                    source_type=DiscoveredSourceType.ATS_PORTAL,
                    priority=2,
                    depth=0,
                    round=1,
                    reason=f"Direct ATS harvest on Lever for '{alt_role}' ({ahub or 'Global'})",
                    location=ahub or loc_str,
                ))

        # 5. Direct In-Depth Web Search (Priority 2)
        if has_uae and has_saudi:
            sweep_locs = ["Dubai", "Abu Dhabi", "Riyadh", "Jeddah", "UAE", "Saudi Arabia"]
        elif has_india:
            sweep_locs = ["Bengaluru", "Hyderabad", "Pune", "Gurgaon", "Mumbai", "India"]
        elif target_locations:
            sweep_locs = target_locations[:6]
        else:
            sweep_locs = [loc_str] if loc_str else [""]

        for sloc in sweep_locs:
            s_suffix = f" {sloc.strip()}" if sloc and sloc.strip() else ""
            tasks.append(SearchTask(
                id=f"r1_direct_{len(tasks)}",
                task_type=SearchTaskType.BOARD_SEARCH,
                query=f'"{role_clean}"{s_suffix} jobs',
                source="direct_search",
                source_type=DiscoveredSourceType.MAJOR_BOARD,
                priority=2,
                depth=0,
                round=1,
                reason=f"High-recall direct web search for {role_clean} in {sloc or 'Global'}",
                location=sloc or loc_str,
            ))

        # 6. Niche AI / Tech Boards (Priority 2)
        tasks.append(SearchTask(
            id=f"r1_niche_{len(tasks)}",
            task_type=SearchTaskType.BOARD_SEARCH,
            query=f'site:wellfound.com/jobs "{role_clean}"',
            source="wellfound.com",
            source_type=DiscoveredSourceType.NICHE_TECH_BOARD,
            priority=2,
            depth=0,
            round=1,
            reason="Tech startup & AI job board sweep on wellfound.com",
            location="Remote/Global",
        ))

        # 7. Remote / Global Portals (Priority 3 - Only when remote is explicitly requested or no location given)
        is_explicit_remote = any(r in (getattr(state, "request", "") or "").lower() for r in ("remote", "anywhere", "worldwide", "wfh", "telecommute"))
        if state.remote_allowed and (is_explicit_remote or not target_locations):
            remote_targets = [
                ("remoteok.com", f'site:remoteok.com "{role_clean}"'),
                ("himalayas.app", f'site:himalayas.app "{role_clean}"'),
            ]
            for src, q in remote_targets:
                tasks.append(SearchTask(
                    id=f"r1_rem_{len(tasks)}",
                    task_type=SearchTaskType.BOARD_SEARCH,
                    query=q,
                    source=src,
                    source_type=DiscoveredSourceType.REMOTE_BOARD,
                    priority=3,
                    depth=0,
                    round=1,
                    reason=f"Remote job portal sweep on {src}",
                    location="Remote",
                ))

        return tasks

    def process_search_hits(
        self,
        hits: List[Dict[str, Any]],
        task: SearchTask,
        state: Any,
        discovery_state: DiscoveryState,
    ) -> List[SearchTask]:
        """
        Analyze search results to discover new hiring companies, ATS portals,
        and new job boards, returning follow-up SearchTasks.
        """
        new_tasks: List[SearchTask] = []
        loc_str = " ".join(state.locations) if state.locations else (state.explicit_location or "")
        role = state.explicit_titles[0] if state.explicit_titles else "GenAI Engineer"
        role_clean = self._clean_role(role)
        requested_regions = self._requested_regions(state)
        regional_scope = bool(requested_regions)
        scheduled_queries = set(discovery_state.executed_queries)
        task_region = self._task_requested_region(task, requested_regions)
        task_scope_query = self._query_regions(
            role_clean,
            [task_region] if task_region else requested_regions,
        )
        dynamic_crawls = 0

        def add_followup(task: SearchTask) -> bool:
            """Bound and deduplicate hit-driven work so verification gets time."""
            if len(new_tasks) >= REGIONAL_DYNAMIC_TASK_LIMIT:
                return False
            if task.query in scheduled_queries:
                return False
            scheduled_queries.add(task.query)
            new_tasks.append(task)
            return True

        def enqueue_crawl(task: CrawlTask) -> bool:
            nonlocal dynamic_crawls
            if dynamic_crawls >= REGIONAL_DYNAMIC_TASK_LIMIT:
                return False
            enqueued = discovery_state.enqueue_crawl_task(task)
            if enqueued:
                dynamic_crawls += 1
            return enqueued

        for hit in hits:
            url = (hit.get("url") or "").strip()
            title = (hit.get("title") or "").strip()
            snippet = (hit.get("snippet") or "").strip()
            domain = hit.get("source_domain") or (urlparse(url).netloc.lower() if url else "")
            if not url:
                continue

            # Search indexes routinely return adjacent roles and foreign jobs.
            # Only relevant public hits may expand the regional search graph.
            if not self._hit_matches_role(url, title, snippet, role_clean):
                continue
            if regional_scope and not self._hit_matches_requested_region(url, title, snippet, requested_regions):
                continue

            # ----------------------------------------------------
            # 1. ATS Endpoint & Company Discovery from URL
            # ----------------------------------------------------
            ats_found = False
            for ats_key, ats_info in ATS_PLATFORMS.items():
                for pat in ats_info["patterns"]:
                    m = re.search(pat, url, re.IGNORECASE)
                    if m:
                        ats_found = True
                        company_slug = m.group(1).lower().strip()
                        if company_slug and company_slug not in IGNORED_COMPANIES and len(company_slug) > 1:
                            if company_slug not in discovery_state.discovered_ats and len(discovery_state.discovered_ats) < self.budget.max_ats_discoveries:
                                discovery_state.discovered_ats[company_slug] = {
                                    "platform": ats_key,
                                    "domain": ats_info["domain"],
                                    "company": company_slug,
                                    "sample_url": url,
                                }
                                discovery_state.coverage_matrix.record_hit("ats", task.location or "general")
                                logger.info(f"Discovered ATS [{ats_key}]: company '{company_slug}' at {url}")

                                # Register company
                                if company_slug not in discovery_state.discovered_companies:
                                    discovery_state.discovered_companies[company_slug] = {
                                        "name": company_slug,
                                        "source": f"ats:{ats_key}",
                                        "url": url,
                                    }

                                # Queue targeted ATS search for this company
                                ats_domain = ats_info["domain"]
                                q = f'site:{ats_domain}/{company_slug} ("{role_clean}" OR engineer OR AI)'
                                add_followup(SearchTask(
                                        id=f"ats_dyn_{company_slug}_{len(new_tasks)}",
                                        task_type=SearchTaskType.ATS_SEARCH,
                                        query=(
                                            f"{q} {task_scope_query}"
                                            if regional_scope else q
                                        ),
                                        source=ats_domain,
                                        source_type=DiscoveredSourceType.ATS_PORTAL,
                                        priority=1,
                                        depth=task.depth + 1,
                                        round=discovery_state.current_round + 1,
                                        company=company_slug,
                                        ats_platform=ats_key,
                                        reason=f"Direct harvest on discovered {ats_key} portal for '{company_slug}'",
                                        location=self._task_scope_location(task, requested_regions),
                                        metadata={
                                            "lane": "public_ats_careers",
                                            **({"requested_region": task_region} if task_region else {}),
                                            **({"requested_regions": requested_regions} if regional_scope and not task_region else {}),
                                        },
                                    ))

                                # Enqueue direct crawl task for this ATS company
                                enqueue_crawl(CrawlTask(
                                    id=f"crawl_ats_{ats_key}_{company_slug}_{len(discovery_state.pending_crawl_tasks)}",
                                    source=ats_domain,
                                    source_type="ats",
                                    url=url,
                                    company=company_slug,
                                    ats_platform=ats_key,
                                    location=task.location,
                                    query=role_clean,
                                    priority=1,
                                    depth=task.depth + 1,
                                    reason=f"Direct ATS crawl for '{company_slug}' on {ats_key}",
                                ))
                        break
                if ats_found:
                    break

            # ----------------------------------------------------
            # 2. Extract Company Names from Titles and Snippets
            # ----------------------------------------------------
            company_extracted = self._extract_company_name(title, snippet)
            if (
                company_extracted
                and company_extracted.lower() not in IGNORED_COMPANIES
                and len(company_extracted) > 2
                and company_extracted.lower() not in discovery_state.discovered_companies
                and len(discovery_state.discovered_companies) < self.budget.max_company_discoveries
            ):
                discovery_state.discovered_companies[company_extracted.lower()] = {
                    "name": company_extracted,
                    "source": "text_extraction",
                    "found_in_url": url,
                }
                discovery_state.coverage_matrix.record_hit("company_career_page", task.location or "general")
                logger.info(f"Discovered hiring employer: '{company_extracted}' from '{title}'")

                # Formulate company career portal search
                q_comp = f'"{company_extracted}" (careers OR jobs) ("{role_clean}" OR AI OR engineer)'
                add_followup(SearchTask(
                        id=f"comp_dyn_{len(new_tasks)}",
                        task_type=SearchTaskType.COMPANY_SEARCH,
                        query=(
                            f"{q_comp} {task_scope_query}"
                            if regional_scope else q_comp
                        ),
                        source="company_career_page",
                        source_type=DiscoveredSourceType.COMPANY_CAREER_PAGE,
                        priority=2,
                        depth=task.depth + 1,
                        round=discovery_state.current_round + 1,
                        company=company_extracted,
                        reason=f"Discovered employer '{company_extracted}' career page search",
                        location=self._task_scope_location(task, requested_regions),
                        metadata={
                            "lane": "public_ats_careers",
                            **({"requested_region": task_region} if task_region else {}),
                            **({"requested_regions": requested_regions} if regional_scope and not task_region else {}),
                        },
                    ))

                # Enqueue direct career page crawl task if URL is from employer domain or career path
                career_cand_url = url
                if not any(b in (urlparse(career_cand_url).netloc.lower()) for b in ("linkedin", "indeed", "glassdoor", "bayt", "naukrigulf", "gulftalent")):
                    enqueue_crawl(CrawlTask(
                        id=f"crawl_comp_{company_extracted.lower().replace(' ', '_')}_{len(discovery_state.pending_crawl_tasks)}",
                        source=urlparse(career_cand_url).netloc.lower() or company_extracted,
                        source_type="company_career_page",
                        url=career_cand_url,
                        company=company_extracted,
                        location=task.location,
                        query=role_clean,
                        priority=2,
                        depth=task.depth + 1,
                        reason=f"Direct career page crawl for employer '{company_extracted}'",
                    ))

            # ----------------------------------------------------
            # 3. New Job Board Discovery
            # ----------------------------------------------------
            if not ats_found and domain:
                clean_netloc = domain.replace("www.", "")
                is_social_or_search = any(k in clean_netloc for k in ("google", "duckduckgo", "bing", "yahoo", "facebook", "twitter", "x.com", "instagram", "wikipedia", "youtube", "medium.com"))
                is_known_major_aggregator = any(k in clean_netloc for k in ("linkedin.com", "indeed.com", "glassdoor.com", "monster.com", "ziprecruiter.com", "bayt.com", "gulftalent.com", "naukrigulf.com"))
                is_job_path = any(k in url.lower() for k in ("/jobs", "/careers", "/career", "/vacancies", "/job-opening"))
                
                # Filter out foreign country subdomains and ccTLDs during regional search
                is_gulf = any(g in (loc_str or "").lower() for g in ("saudi", "uae", "dubai", "riyadh", "qatar", "kuwait", "bahrain", "oman", "gulf", "gcc", "middle east"))
                FOREIGN_COUNTRY_SUBDOMAINS = (
                    "ru.", "au.", "sg.", "in.", "pe.", "de.", "fr.", "uk.", "ca.",
                    "br.", "jp.", "mx.", "es.", "it.", "nl.", "pl.", "ch.", "se.",
                    "no.", "fi.", "ie.", "nz.", "za.", "ph.", "pk.", "bd.", "vn.",
                    "th.", "my.", "id.", "eg.", "tr.", "cn.", "hk.", "tw.", "kr."
                )
                FOREIGN_COUNTRY_TLDS = (
                    ".fr", ".de", ".it", ".es", ".ru", ".in", ".co.uk", ".ca",
                    ".com.br", ".pl", ".nl", ".jp", ".cn", ".au", ".uk", ".br",
                    ".ch", ".se", ".no", ".fi", ".ie", ".nz", ".za", ".ph", ".pk",
                    ".bd", ".vn", ".th", ".my", ".id", ".tr", ".kr"
                )
                is_foreign_country = any(clean_netloc.startswith(p) for p in FOREIGN_COUNTRY_SUBDOMAINS) or any(clean_netloc.endswith(tld) for tld in FOREIGN_COUNTRY_TLDS)
                
                if (
                    not is_social_or_search
                    and not is_known_major_aggregator
                    and not (is_gulf and is_foreign_country)
                    and is_job_path
                    and clean_netloc not in discovery_state.discovered_boards
                    and clean_netloc not in discovery_state.searched_sources
                    and len(discovery_state.discovered_boards) < self.budget.max_source_discoveries
                ):
                    discovery_state.discovered_boards.add(clean_netloc)
                    logger.info(f"Discovered potential job board domain: {clean_netloc}")
                    q_board = f'site:{clean_netloc} "{role_clean}"'
                    add_followup(SearchTask(
                            id=f"board_dyn_{clean_netloc}_{len(new_tasks)}",
                            task_type=SearchTaskType.BOARD_SEARCH,
                            query=(
                                f"{q_board} {task_scope_query}"
                                if regional_scope else q_board
                            ),
                            source=clean_netloc,
                            source_type=DiscoveredSourceType.MAJOR_BOARD,
                            priority=3,
                            depth=task.depth + 1,
                            round=discovery_state.current_round + 1,
                            reason=f"Discovered job board domain {clean_netloc}",
                            location=self._task_scope_location(task, requested_regions),
                            metadata={
                                "lane": "regional_portals" if regional_scope else "major_boards",
                                **({"requested_region": task_region} if task_region else {}),
                                **({"requested_regions": requested_regions} if regional_scope and not task_region else {}),
                            },
                        ))

                    # Also enqueue direct board crawl task
                    enqueue_crawl(CrawlTask(
                        id=f"crawl_board_{clean_netloc}_{len(discovery_state.pending_crawl_tasks)}",
                        source=clean_netloc,
                        source_type="regional_board" if any(k in clean_netloc for k in ("gulf", "bayt", "naukri", "middleeast", "dubai", "saudi")) else "major_board",
                        url=url,
                        location=task.location,
                        query=role_clean,
                        priority=3,
                        depth=task.depth + 1,
                        reason=f"Direct board crawl on discovered domain {clean_netloc}",
                    ))

        return new_tasks

    def _extract_company_name(self, title: str, snippet: str) -> Optional[str]:
        """Extract employer/company name using common job title delimiters."""
        patterns = [
            r"(?:at|@)\s+([A-Z0-9][A-Za-z0-9\s&.]{1,30}?)(?:\s+in|\s+[-|–—]|\s+\(|$)",
            r"([A-Z0-9][A-Za-z0-9\s&.]{1,30}?)\s+is hiring\b",
            r"([A-Z0-9][A-Za-z0-9\s&.]{1,30}?)\s+Careers\b",
            r"[-|–—]\s+([A-Z0-9][A-Za-z0-9\s&.]{1,30}?)(?:\s+[-|–—]|\s+Careers|\s+Jobs|$)",
        ]
        text = f"{title}"
        for pat in patterns:
            m = re.search(pat, text)
            if m:
                cand = m.group(1).strip()
                cand_clean = re.sub(r"[^\w\s&]", "", cand).strip()
                if not cand_clean or len(cand_clean) <= 2:
                    continue
                cand_lower = cand_clean.lower()
                # 1. Skip if starts with digit (e.g. "62 Vacancies Sep 2026")
                if re.match(r"^\d", cand_clean):
                    continue
                # 2. Skip if candidate contains vacancy/opening/month/action words
                bad_keywords = (
                    "vacanc", "opening", "hiring", "apply now", "click here", "view job",
                    "read more", "job opening", "find job", "salary", "salaries",
                    "bayt", "naukri", "gulftalent", "linkedin", "indeed", "glassdoor",
                    "january", "february", "march", "april", "may", "june", "july",
                    "august", "september", "october", "november", "december",
                    "jan 20", "feb 20", "mar 20", "apr 20", "may 20", "jun 20",
                    "jul 20", "aug 20", "sep 20", "oct 20", "nov 20", "dec 20",
                )
                if any(bk in cand_lower for bk in bad_keywords):
                    continue
                # 3. Skip if purely location/region
                if cand_lower in ("saudi", "saudi arabia", "uae", "dubai", "riyadh", "abu dhabi", "remote", "middle east"):
                    continue
                if cand_lower not in IGNORED_COMPANIES:
                    return cand_clean
        return None

    def generate_next_round_tasks(
        self,
        discovery_state: DiscoveryState,
        spec: Any,
        state: Any,
    ) -> List[SearchTask]:
        """
        Formulate round-specific expansion tasks (Rounds 2 to 6).
        Round 2: Source Expansion (page 2 of productive boards, discovered boards)
        Round 3: Company & ATS Deep Dive (discovered companies & ATS)
        Round 4: Geographic Expansion (drilling into specific cities/regions)
        Round 5: Title & Skill Variations (synonyms & subdisciplines)
        Round 6: Residual Depth & Freshness (past 7-30 days)
        """
        round_num = discovery_state.current_round
        loc_str = " ".join(state.locations) if state.locations else (state.explicit_location or "")
        role = state.explicit_titles[0] if state.explicit_titles else "GenAI Engineer"
        role_clean = self._clean_role(role)
        requested_regions = self._requested_regions(state)
        regional_scope = bool(requested_regions)

        budget = getattr(state, "discovery_budget", None) or self.budget
        max_expansion_rounds = getattr(budget, "max_expansion_rounds", 12) if budget else 12

        while round_num <= max_expansion_rounds:
            tasks: List[SearchTask] = []

            # Round 2: Source Expansion & Pagination
            if round_num == 2:
                # Paginate high-yielding tasks from Round 1, favoring productive sources (Section 14 & 15)
                completed = list(discovery_state.completed_tasks)
                completed.sort(key=lambda pt: (
                    pt.priority,
                    -discovery_state.source_productivity.get(pt.source.lower(), {}).get("productivity_score", 1.0)
                ))
                for pt in completed[:4]:
                    src_stat = discovery_state.source_productivity.get(pt.source.lower(), {})
                    # Skip page 2 if source produced only duplicates and zero qualified jobs
                    if src_stat.get("duplicate_jobs", 0) > 0 and src_stat.get("qualified_jobs", 0) == 0:
                        continue
                    p2_task = SearchTask(
                        id=f"r2_page2_{pt.id}",
                        task_type=pt.task_type,
                        query=pt.query,
                        source=pt.source,
                        source_type=pt.source_type,
                        priority=pt.priority,
                        depth=pt.depth + 1,
                        round=2,
                        page=2,
                        reason=f"Page 2 expansion on productive source {pt.source}",
                        company=pt.company,
                        location=pt.location,
                    )
                    tasks.append(p2_task)

            # Round 3: Company & ATS Deep Dive
            elif round_num == 3:
                regions = requested_regions or [loc_str or "general"]
                for comp_slug, comp_info in list(discovery_state.discovered_ats.items())[:6]:
                    ats_domain = comp_info.get("domain", "boards.greenhouse.io")
                    for region in regions:
                        q = f'site:{ats_domain}/{comp_slug} ("{role_clean}" OR engineer OR AI OR LLM)'
                        if regional_scope:
                            q = f"{q} {self._query_scope(role_clean, region)}"
                        if q not in discovery_state.executed_queries:
                            tasks.append(SearchTask(
                                id=f"r3_ats_{comp_slug}_{len(tasks)}",
                                task_type=SearchTaskType.ATS_SEARCH,
                                query=q,
                                source=ats_domain,
                                source_type=DiscoveredSourceType.ATS_PORTAL,
                                priority=1,
                                depth=2,
                                round=3,
                                company=comp_slug,
                                reason=f"Deep dive ATS search for company {comp_slug}",
                                location=region,
                                metadata={"lane": "public_ats_careers", "requested_region": region},
                            ))

            # Round 4: Geographic Expansion / Specific City Drilling
            elif round_num == 4:
                cities: List[str] = []
                if any(k in loc_str.lower() for k in ("saudi", "ksa")):
                    cities.extend(["Riyadh", "Jeddah", "Dammam", "Khobar", "NEOM"])
                if any(k in loc_str.lower() for k in ("uae", "emirates", "dubai", "sharjah", "abu dhabi")):
                    cities.extend(["Dubai", "Abu Dhabi", "Sharjah"])
                if not cities and loc_str:
                    cities.append(loc_str)

                if regional_scope:
                    city_groups = [(region, self._region_hubs(region)) for region in requested_regions]
                else:
                    city_groups = [(loc_str, cities)]
                for region, region_cities in city_groups:
                    for city in region_cities[:5]:
                        q_city = (
                            f'{self._query_scope(role_clean, region, city)} hiring apply careers'
                            if regional_scope else f'"{role_clean}" "{city}" hiring apply careers'
                        )
                        if q_city not in discovery_state.executed_queries:
                            tasks.append(SearchTask(
                                id=f"r4_geo_{city.lower().replace(' ', '_')}_{len(tasks)}",
                                task_type=SearchTaskType.GENERAL_SEARCH,
                                query=q_city,
                                source="regional_hub",
                                source_type=DiscoveredSourceType.REGIONAL_BOARD,
                                priority=2,
                                depth=2,
                                round=4,
                                reason=f"Targeted city-level search for {city}",
                                location=city,
                                metadata=(
                                    {"lane": "general_web", "requested_region": region}
                                    if regional_scope else {}
                                ),
                            ))

            # Round 5: Title & Skill Variations
            elif round_num == 5:
                synonyms = [
                    "Generative AI Engineer", "LLM Engineer", "AI Engineer",
                    "Generative AI Solution Architect", "AI Solution Architect", "Enterprise AI Architect",
                    "Agentic AI Engineer", "RAG Engineer", "Foundation Model Engineer", "Machine Learning Engineer"
                ]
                from datahunt.agent.policies import ROLE_TAXONOMY
                role_low = role_clean.lower()
                for family_key, family_spec in ROLE_TAXONOMY.items():
                    if any(re.search(r'\b' + re.escape(k) + r'\b', role_low) for k in family_spec["keywords"]):
                        fam_titles = [t.title() for t in family_spec["direct_titles"] if t.lower() != role_low]
                        if fam_titles:
                            synonyms = fam_titles
                        break
                for syn in synonyms[:4]:
                    if syn.lower() != role_clean.lower():
                        regions = requested_regions or [loc_str]
                        for region in regions:
                            q_syn = (
                                f'"{syn}" "{region}" (careers OR apply)'
                                if regional_scope else f'"{syn}" {loc_str} (careers OR apply)'
                            )
                            if q_syn not in discovery_state.executed_queries:
                                tasks.append(SearchTask(
                                    id=f"r5_syn_{len(tasks)}",
                                    task_type=SearchTaskType.GENERAL_SEARCH,
                                    query=q_syn,
                                    source="title_synonym",
                                    source_type=DiscoveredSourceType.MAJOR_BOARD,
                                    priority=2,
                                    depth=3,
                                    round=5,
                                    reason=f"Role synonym expansion: '{syn}'",
                                    location=region,
                                    metadata=(
                                        {"lane": "general_web", "requested_region": region}
                                        if regional_scope else {}
                                    ),
                                ))

            # Round 6: Freshness & Residual Depth
            elif round_num == 6:
                regions = requested_regions or [loc_str]
                for region in regions:
                    q_fresh = f'"{role_clean}" "{region}" careers apply 2026'
                    if q_fresh not in discovery_state.executed_queries:
                        tasks.append(SearchTask(
                            id=f"r6_freshness_{len(tasks)}",
                            task_type=SearchTaskType.GENERAL_SEARCH,
                            query=q_fresh,
                            source="freshness_sweep",
                            source_type=DiscoveredSourceType.MAJOR_BOARD,
                            priority=2,
                            depth=3,
                            round=6,
                            metadata={
                                "freshness_days": 30,
                                **({"lane": "general_web", "requested_region": region} if regional_scope else {}),
                            },
                            reason="Freshness residual search for recent openings",
                            location=region,
                        ))

            # Round 7+: Continuous Frontier Expansion across taxonomy roles, ATS portals, and direct companies
            elif round_num >= 7:
                from datahunt.agent.policies import ROLE_TAXONOMY
                role_low = role_clean.lower()
                all_tax_titles = []
                for fam_spec in ROLE_TAXONOMY.values():
                    all_tax_titles.extend([t.title() for t in fam_spec.get("direct_titles", [])])
                    all_tax_titles.extend([t.title() for t in fam_spec.get("related_roles", [])])
                # Filter out exact current role
                candidates = [t for t in all_tax_titles if t.lower() != role_low]
                # Slice by round offset so each round investigates different roles
                offset = (round_num - 7) * 4
                slice_titles = candidates[offset : offset + 4]
                if not slice_titles and candidates:
                    # Loop back around if exhausted
                    slice_titles = candidates[:4]

                regions = requested_regions or [loc_str]
                ats_platforms = ["boards.greenhouse.io", "jobs.lever.co", "jobs.ashbyhq.com", "apply.workable.com"]
                chosen_ats = ats_platforms[(round_num - 7) % len(ats_platforms)]

                for title in slice_titles:
                    for reg in regions:
                        q_tax = f'site:{chosen_ats} "{title}" "{reg}"'
                        if q_tax not in discovery_state.executed_queries:
                            tasks.append(SearchTask(
                                id=f"r{round_num}_cont_{len(tasks)}",
                                task_type=SearchTaskType.ATS_SEARCH,
                                query=q_tax,
                                source=chosen_ats,
                                source_type=DiscoveredSourceType.ATS_PORTAL,
                                priority=2,
                                depth=4,
                                round=round_num,
                                metadata={
                                    "lane": "ats_sweep",
                                    "ats_platform": chosen_ats,
                                    "role_title": title,
                                    "requested_region": reg,
                                },
                                reason=f"Continuous frontier round {round_num}: {chosen_ats} search for '{title}' in {reg}",
                                location=reg,
                            ))

            if tasks:
                discovery_state.current_round = round_num
                return tasks

            round_num += 1

        discovery_state.current_round = min(round_num, max_expansion_rounds)
        return []

    def prioritize_tasks(self, tasks: List[SearchTask], discovery_state: DiscoveryState) -> List[SearchTask]:
        """
        Sort and adjust task priority based on observed source productivity (Sections 14 & 15).
        Sources producing high qualified yields are prioritized, while sources producing
        only duplicates or zero qualified jobs are deprioritized.
        """
        def task_sort_key(t: SearchTask) -> Tuple[int, float]:
            src_stats = discovery_state.source_productivity.get(t.source.lower(), {})
            score = src_stats.get("productivity_score", 1.0)
            # Primary: task priority. Secondary: higher productivity score first
            return (t.priority, -score)

        return sorted(tasks, key=task_sort_key)

    def should_stop(self, discovery_state: DiscoveryState, state: Any) -> Tuple[bool, StopReason, str]:
        """
        Evaluate multi-factor stopping criteria:
        1. Safety budgets: deadline exceeded, max search requests, max rounds
        2. Target qualified results reached with balanced coverage
        3. Diminishing returns detected after coverage is complete
        4. Discovery queue exhausted
        """
        now = time.time()
        search_deadline = getattr(state, "effective_search_deadline", 0.0) or getattr(state, "search_deadline", 0.0) or getattr(state, "deadline", 0.0)
        hard_deadline = getattr(state, "effective_hard_deadline", 0.0) or getattr(state, "hard_deadline", 0.0) or (
            (search_deadline + getattr(state, "drain_budget_seconds", 600.0)) if search_deadline > 0 else 0.0
        )
        if hard_deadline > 0 and now >= hard_deadline:
            return True, StopReason.DEADLINE_EXCEEDED, "Hard time budget deadline reached"

        # Check if pending crawl tasks, candidate URLs, or unverified records remain in flight
        from datahunt.agent.state import get_unprocessed_records
        unproc = get_unprocessed_records(state)
        has_pending_work = bool(
            getattr(state, "candidate_urls", None)
            or (getattr(discovery_state, "pending_crawl_tasks", None))
            or (unproc and state.raw_record_generation > getattr(state, "last_verified_generation", -1))
        )

        if search_deadline > 0 and now >= search_deadline:
            if has_pending_work:
                return False, StopReason.DEADLINE_EXCEEDED, ""
            return True, StopReason.DEADLINE_EXCEEDED, "Search deadline reached; all discovered candidates processed"

        budget = getattr(state, "discovery_budget", None) or self.budget
        max_search_requests = getattr(budget, "max_search_requests", self.budget.max_search_requests)
        max_expansion_rounds = getattr(budget, "max_expansion_rounds", self.budget.max_expansion_rounds)

        if state.search_calls >= max_search_requests:
            if has_pending_work:
                return False, StopReason.BUDGET_EXHAUSTED, ""
            return True, StopReason.BUDGET_EXHAUSTED, f"Max search requests budget ({max_search_requests}) reached"

        if discovery_state.current_round > max_expansion_rounds:
            if has_pending_work:
                return False, StopReason.BUDGET_EXHAUSTED, ""
            return True, StopReason.BUDGET_EXHAUSTED, f"Max discovery rounds ({max_expansion_rounds}) completed"

        is_job_mode = state.mode in ("jobs", "job")
        if is_job_mode:
            effective_count = len(state.qualified_records)
        else:
            effective_count = len(state.verified_records)

        # When continuous_time_governed is enabled, searches continue until the search deadline without stopping for target counts or diminishing returns.
        if not getattr(budget, "continuous_time_governed", False):
            # Target results reached
            is_fast = getattr(state, "collection_mode", None) != "thorough"
            if effective_count >= state.target_results:
                has_pending = bool(
                    discovery_state.task_queue
                    or getattr(discovery_state, "pending_crawl_tasks", None)
                )
                if is_fast or not has_pending:
                    has_geo_coverage = True
                    if state.locations and len(state.locations) > 1:
                        target_locs = [l.lower() for l in state.locations]
                        found_locs = set()
                        for rec in state.qualified_records:
                            rec_loc = (rec.fields.get("location") or "").lower()
                            for tl in target_locs:
                                if tl in rec_loc or any(alias in rec_loc for alias in ("riyadh", "jeddah", "dammam") if tl == "saudi arabia") or any(alias in rec_loc for alias in ("dubai", "abu dhabi", "sharjah") if tl == "uae"):
                                    found_locs.add(tl)
                        if len(found_locs) < len(target_locs) and discovery_state.current_round < 3:
                            has_geo_coverage = False

                    if has_geo_coverage and discovery_state.coverage_matrix.is_balanced(min_categories=2):
                        return True, StopReason.TARGET_QUALIFIED_REACHED, f"Target results ({effective_count}/{state.target_results}) reached with balanced coverage"
                    elif discovery_state.current_round >= 3:
                        return True, StopReason.TARGET_QUALIFIED_REACHED, f"Target results ({effective_count}/{state.target_results}) reached across {discovery_state.current_round} rounds"

            # Diminishing returns detection
            if discovery_state.consecutive_low_yield_rounds >= 2:
                has_pending = bool(
                    discovery_state.task_queue
                    or getattr(discovery_state, "pending_crawl_tasks", None)
                )
                if is_fast or not has_pending:
                    if effective_count > 0 and discovery_state.coverage_matrix.is_balanced(min_categories=2):
                        return True, StopReason.DIMINISHING_RETURNS, f"Diminishing returns across 2 consecutive rounds; {effective_count} qualified jobs found"

        # No more tasks in queue
        if not discovery_state.task_queue and discovery_state.current_round > 1:
            # Do not stop if candidates are pending fetch or records are awaiting verification
            if getattr(state, "candidate_urls", None):
                return False, StopReason.TARGET_QUALIFIED_REACHED, ""
            # Do not stop if crawl tasks are pending in queue
            if getattr(discovery_state, "pending_crawl_tasks", None):
                return False, StopReason.TARGET_QUALIFIED_REACHED, ""
            from datahunt.agent.state import get_unprocessed_records
            if get_unprocessed_records(state):
                return False, StopReason.TARGET_QUALIFIED_REACHED, ""

            # Do not stop early if more rounds can be expanded
            if discovery_state.current_round < max_expansion_rounds:
                return False, StopReason.TARGET_QUALIFIED_REACHED, ""

            if effective_count > 0:
                return True, StopReason.COVERAGE_COMPLETE_WITH_SATISFACTION, f"Completed discovery universe with {effective_count} qualified jobs"
            else:
                return True, StopReason.SOURCE_UNIVERSE_EXHAUSTED, "Exhausted all discovered search tasks with no matching records"

        return False, StopReason.TARGET_QUALIFIED_REACHED, ""
