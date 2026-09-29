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
}

IGNORED_COMPANIES = {
    "job", "jobs", "career", "careers", "hiring", "apply", "search", "post", "positions",
    "view", "index", "feed", "linkedin", "indeed", "glassdoor", "bayt", "baytcom", "bayt.com",
    "gulftalent", "naukrigulf", "naukri", "monster", "tanqeeb", "dubai", "saudi", "riyadh",
    "remote", "general", "fulltime", "parttime", "internship", "login", "register", "candidate",
    "applicant", "apply now", "click here", "read more", "view job", "work in", "vacancies",
    "vacancy", "openings", "opening", "requirements", "requirement", "salary", "salaries",
    "opportunity", "opportunities", "middle east", "middleeast", "united arab emirates", "saudi arabia"
}


class DiscoveryEngine:
    """
    Drives dynamic, multi-round job source discovery.
    Observes search hits -> extracts companies, ATS endpoints, and new boards ->
    generates targeted next-round search tasks -> balances the coverage matrix ->
    evaluates stopping conditions based on coverage and diminishing returns.
    """

    def __init__(self, budget: Optional[DiscoveryBudget] = None):
        self.budget = budget or DiscoveryBudget()

    def generate_initial_tasks(self, spec: Any, state: Any) -> List[SearchTask]:
        """
        Formulate Round 1 broad discovery tasks across ATS platforms, regional boards,
        major boards, and niche tech/remote portals based on user requirements.
        """
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
        is_generic_gulf = any(k in loc_lower for k in ("gulf", "middle east", "mena", "gcc"))
        is_gulf = has_uae or has_saudi or has_qatar or is_generic_gulf
        region_label = "Gulf/MENA" if is_gulf else (loc_str or "Global")

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
            regional_targets = [
                ("naukrigulf.com", f'site:naukrigulf.com "{role_clean}" {primary_loc}'.strip()),
                ("bayt.com", f'site:bayt.com "{role_clean}" {primary_loc}'.strip()),
                ("gulftalent.com", f'site:gulftalent.com "{role_clean}" {primary_loc}'.strip()),
            ]
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

        # 2. Major Job Boards (Priority 2, or Priority 1 for LinkedIn when non-gulf)
        major_targets = [
            ("indeed.com", f'site:indeed.com/jobs "{role_clean}" {primary_loc}'.strip()),
            ("glassdoor.com", f'site:glassdoor.com "{role_clean}" {primary_loc}'.strip()),
        ]
        if not is_gulf:
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
        ats_hubs = target_locations[:2] if target_locations else ([primary_loc] if primary_loc else [""])
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
            primary_suffix = f" {primary_loc.strip()}" if primary_loc and primary_loc.strip() else ""
            tasks.append(SearchTask(
                id=f"r1_ats_alt_{len(tasks)}",
                task_type=SearchTaskType.ATS_SEARCH,
                query=f'site:boards.greenhouse.io "{alt_role}"{primary_suffix}',
                source="boards.greenhouse.io",
                source_type=DiscoveredSourceType.ATS_PORTAL,
                priority=2,
                depth=0,
                round=1,
                reason=f"Direct ATS harvest on Greenhouse for '{alt_role}' ({primary_loc or 'Global'})",
                location=primary_loc or loc_str,
            ))
            tasks.append(SearchTask(
                id=f"r1_ats_alt_{len(tasks)}",
                task_type=SearchTaskType.ATS_SEARCH,
                query=f'site:jobs.lever.co "{alt_role}"{primary_suffix}',
                source="jobs.lever.co",
                source_type=DiscoveredSourceType.ATS_PORTAL,
                priority=2,
                depth=0,
                round=1,
                reason=f"Direct ATS harvest on Lever for '{alt_role}' ({primary_loc or 'Global'})",
                location=primary_loc or loc_str,
            ))

        # 5. Direct In-Depth Web Search (Priority 2)
        sweep_locs = target_locations[:3] if target_locations else ([loc_str] if loc_str else [""])
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
        role_clean = re.sub(r"\b(with|requiring)?\s*\d+[-–\sto]+\d*\s*(?:years?|yrs?)(?:\s*exp(?:erience)?)?\b", "", role, flags=re.IGNORECASE).strip() or role

        for hit in hits:
            url = (hit.get("url") or "").strip()
            title = (hit.get("title") or "").strip()
            snippet = (hit.get("snippet") or "").strip()
            domain = hit.get("source_domain") or (urlparse(url).netloc.lower() if url else "")
            if not url:
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
                                if q not in discovery_state.executed_queries:
                                    new_tasks.append(SearchTask(
                                        id=f"ats_dyn_{company_slug}_{len(new_tasks)}",
                                        task_type=SearchTaskType.ATS_SEARCH,
                                        query=q,
                                        source=ats_domain,
                                        source_type=DiscoveredSourceType.ATS_PORTAL,
                                        priority=1,
                                        depth=task.depth + 1,
                                        round=discovery_state.current_round + 1,
                                        company=company_slug,
                                        ats_platform=ats_key,
                                        reason=f"Direct harvest on discovered {ats_key} portal for '{company_slug}'",
                                        location=task.location,
                                    ))

                                # Enqueue direct crawl task for this ATS company
                                discovery_state.enqueue_crawl_task(CrawlTask(
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
                if q_comp not in discovery_state.executed_queries:
                    new_tasks.append(SearchTask(
                        id=f"comp_dyn_{len(new_tasks)}",
                        task_type=SearchTaskType.COMPANY_SEARCH,
                        query=q_comp,
                        source="company_career_page",
                        source_type=DiscoveredSourceType.COMPANY_CAREER_PAGE,
                        priority=2,
                        depth=task.depth + 1,
                        round=discovery_state.current_round + 1,
                        company=company_extracted,
                        reason=f"Discovered employer '{company_extracted}' career page search",
                        location=task.location,
                    ))

                # Enqueue direct career page crawl task if URL is from employer domain or career path
                career_cand_url = url
                if not any(b in (urlparse(career_cand_url).netloc.lower()) for b in ("linkedin", "indeed", "glassdoor", "bayt", "naukrigulf", "gulftalent")):
                    discovery_state.enqueue_crawl_task(CrawlTask(
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
                is_job_path = any(k in url.lower() for k in ("/jobs", "/careers", "/career", "/vacancies", "/job-opening"))
                
                if (
                    not is_social_or_search
                    and is_job_path
                    and clean_netloc not in discovery_state.discovered_boards
                    and clean_netloc not in discovery_state.searched_sources
                    and len(discovery_state.discovered_boards) < self.budget.max_source_discoveries
                ):
                    discovery_state.discovered_boards.add(clean_netloc)
                    logger.info(f"Discovered potential job board domain: {clean_netloc}")
                    q_board = f'site:{clean_netloc} "{role_clean}"'
                    if q_board not in discovery_state.executed_queries:
                        new_tasks.append(SearchTask(
                            id=f"board_dyn_{clean_netloc}_{len(new_tasks)}",
                            task_type=SearchTaskType.BOARD_SEARCH,
                            query=q_board,
                            source=clean_netloc,
                            source_type=DiscoveredSourceType.MAJOR_BOARD,
                            priority=3,
                            depth=task.depth + 1,
                            round=discovery_state.current_round + 1,
                            reason=f"Discovered job board domain {clean_netloc}",
                            location=task.location,
                        ))

                    # Also enqueue direct board crawl task
                    discovery_state.enqueue_crawl_task(CrawlTask(
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
        role_clean = re.sub(r"\b(with|requiring)?\s*\d+[-–\sto]+\d*\s*(?:years?|yrs?)(?:\s*exp(?:erience)?)?\b", "", role, flags=re.IGNORECASE).strip() or role

        while round_num <= self.budget.max_expansion_rounds:
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
                for comp_slug, comp_info in list(discovery_state.discovered_ats.items())[:6]:
                    ats_domain = comp_info.get("domain", "boards.greenhouse.io")
                    q = f'site:{ats_domain}/{comp_slug} ("{role_clean}" OR engineer OR AI OR LLM)'
                    if q not in discovery_state.executed_queries:
                        tasks.append(SearchTask(
                            id=f"r3_ats_{comp_slug}",
                            task_type=SearchTaskType.ATS_SEARCH,
                            query=q,
                            source=ats_domain,
                            source_type=DiscoveredSourceType.ATS_PORTAL,
                            priority=1,
                            depth=2,
                            round=3,
                            company=comp_slug,
                            reason=f"Deep dive ATS search for company {comp_slug}",
                            location=loc_str,
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

                for city in cities[:4]:
                    q_city = f'"{role_clean}" "{city}" hiring apply careers'
                    if q_city not in discovery_state.executed_queries:
                        tasks.append(SearchTask(
                            id=f"r4_geo_{city.lower().replace(' ', '_')}",
                            task_type=SearchTaskType.GENERAL_SEARCH,
                            query=q_city,
                            source="regional_hub",
                            source_type=DiscoveredSourceType.REGIONAL_BOARD,
                            priority=2,
                            depth=2,
                            round=4,
                            reason=f"Targeted city-level search for {city}",
                            location=city,
                        ))

            # Round 5: Title & Skill Variations
            elif round_num == 5:
                synonyms = ["Generative AI Engineer", "LLM Engineer", "AI Engineer", "Foundation Model Engineer", "Machine Learning Engineer"]
                for syn in synonyms[:3]:
                    if syn.lower() != role_clean.lower():
                        q_syn = f'"{syn}" {loc_str} (careers OR apply)'
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
                                location=loc_str,
                            ))

            # Round 6: Freshness & Residual Depth
            elif round_num == 6:
                q_fresh = f'"{role_clean}" {loc_str} careers apply 2026'
                if q_fresh not in discovery_state.executed_queries:
                    tasks.append(SearchTask(
                        id="r6_freshness",
                        task_type=SearchTaskType.GENERAL_SEARCH,
                        query=q_fresh,
                        source="freshness_sweep",
                        source_type=DiscoveredSourceType.MAJOR_BOARD,
                        priority=2,
                        depth=3,
                        round=6,
                        metadata={"freshness_days": 30},
                        reason="Freshness residual search for recent openings",
                        location=loc_str,
                    ))

            if tasks:
                discovery_state.current_round = round_num
                return tasks

            round_num += 1

        discovery_state.current_round = min(round_num, self.budget.max_expansion_rounds)
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
        if state.deadline > 0 and now >= state.deadline:
            return True, StopReason.DEADLINE_EXCEEDED, "Time budget deadline reached"

        # Check if pending crawl tasks, candidate URLs, or unverified records remain in flight
        from datahunt.agent.state import get_unprocessed_records
        unproc = get_unprocessed_records(state)
        has_pending_work = bool(
            getattr(state, "candidate_urls", None)
            or (getattr(discovery_state, "pending_crawl_tasks", None))
            or (unproc and state.raw_record_generation > getattr(state, "last_verified_generation", -1))
        )

        if state.search_calls >= self.budget.max_search_requests:
            if has_pending_work:
                return False, StopReason.BUDGET_EXHAUSTED, ""
            return True, StopReason.BUDGET_EXHAUSTED, f"Max search requests budget ({self.budget.max_search_requests}) reached"

        if discovery_state.current_round > self.budget.max_expansion_rounds:
            if has_pending_work:
                return False, StopReason.BUDGET_EXHAUSTED, ""
            return True, StopReason.BUDGET_EXHAUSTED, f"Max discovery rounds ({self.budget.max_expansion_rounds}) completed"

        if state.mode in ("jobs", "job"):
            effective_count = len(state.qualified_records)
        else:
            effective_count = len(state.verified_records)

        # Target results reached
        if effective_count >= state.target_results:
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
            if discovery_state.current_round < self.budget.max_expansion_rounds:
                return False, StopReason.TARGET_QUALIFIED_REACHED, ""

            if effective_count > 0:
                return True, StopReason.COVERAGE_COMPLETE_WITH_SATISFACTION, f"Completed discovery universe with {effective_count} qualified jobs"
            else:
                return True, StopReason.SOURCE_UNIVERSE_EXHAUSTED, "Exhausted all discovered search tasks with no matching records"

        return False, StopReason.TARGET_QUALIFIED_REACHED, ""
