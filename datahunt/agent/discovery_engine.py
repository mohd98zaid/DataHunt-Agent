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
    "view", "index", "feed", "linkedin", "indeed", "glassdoor", "bayt", "gulftalent",
    "naukrigulf", "dubai", "saudi", "riyadh", "remote", "general", "fulltime", "parttime",
    "internship", "login", "register", "candidate", "applicant"
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

        is_gulf = any(k in loc_lower for k in ("saudi", "uae", "dubai", "riyadh", "gulf", "middle east", "abu dhabi", "doha", "qatar"))
        region_label = "Gulf/MENA" if is_gulf else (loc_str or "Global")

        # 1. ATS Portals (Priority 1)
        ats_targets = [
            ("boards.greenhouse.io", f'site:boards.greenhouse.io "{role_clean}" {loc_str}'.strip()),
            ("jobs.lever.co", f'site:jobs.lever.co "{role_clean}" {loc_str}'.strip()),
            ("jobs.ashbyhq.com", f'site:jobs.ashbyhq.com "{role_clean}" {loc_str}'.strip()),
            ("apply.workable.com", f'site:apply.workable.com "{role_clean}" {loc_str}'.strip()),
        ]
        for src, q in ats_targets:
            tasks.append(SearchTask(
                id=f"r1_ats_{len(tasks)}",
                task_type=SearchTaskType.ATS_SEARCH,
                query=q,
                source=src,
                source_type=DiscoveredSourceType.ATS_PORTAL,
                priority=1,
                depth=0,
                round=1,
                reason=f"Direct ATS harvest on {src}",
                location=loc_str,
            ))

        # 2. Regional Job Boards (Priority 1 for region-specific searches)
        if is_gulf:
            regional_targets = [
                ("bayt.com", f'site:bayt.com "{role_clean}" {loc_str}'.strip()),
                ("gulftalent.com", f'site:gulftalent.com "{role_clean}" {loc_str}'.strip()),
                ("naukrigulf.com", f'site:naukrigulf.com "{role_clean}" {loc_str}'.strip()),
            ]
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

        # 3. Major Job Boards (Priority 2)
        major_targets = [
            ("linkedin.com", f'site:linkedin.com/jobs "{role_clean}" {loc_str}'.strip()),
            ("glassdoor.com", f'site:glassdoor.com "{role_clean}" {loc_str}'.strip()),
        ]
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

        # 4. Niche AI / Tech Boards (Priority 2)
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

        # 5. Remote / Global Portals (Priority 3)
        if state.remote_allowed:
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

        # 6. Natural Language Direct Career Page Sweep (Priority 2)
        tasks.append(SearchTask(
            id=f"r1_careers_{len(tasks)}",
            task_type=SearchTaskType.CAREER_PAGE_SEARCH,
            query=f'"{role_clean}" {loc_str} (careers OR "open positions" OR "we are hiring")'.strip(),
            source="company_career_page",
            source_type=DiscoveredSourceType.COMPANY_CAREER_PAGE,
            priority=2,
            depth=0,
            round=1,
            reason="Direct employer career portal discovery",
            location=loc_str,
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

        return new_tasks

    def _extract_company_name(self, title: str, snippet: str) -> Optional[str]:
        """Extract employer/company name using common job title delimiters."""
        patterns = [
            r"(?:at|@)\s+([A-Z0-9][A-Za-z0-9\s&.]{1,25}?)(?:\s+in|\s+[-|–—]|\s+\(|$)",
            r"([A-Z0-9][A-Za-z0-9\s&.]{1,25}?)\s+is hiring\b",
            r"([A-Z0-9][A-Za-z0-9\s&.]{1,25}?)\s+Careers\b",
            r"[-|–—]\s+([A-Z0-9][A-Za-z0-9\s&.]{1,25}?)(?:\s+[-|–—]|\s+Careers|\s+Jobs|$)",
        ]
        text = f"{title}"
        for pat in patterns:
            m = re.search(pat, text)
            if m:
                cand = m.group(1).strip()
                cand_clean = re.sub(r"[^\w\s&]", "", cand).strip()
                if cand_clean and cand_clean.lower() not in IGNORED_COMPANIES and len(cand_clean) > 2:
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
        tasks: List[SearchTask] = []
        loc_str = " ".join(state.locations) if state.locations else (state.explicit_location or "")
        role = state.explicit_titles[0] if state.explicit_titles else "GenAI Engineer"
        role_clean = re.sub(r"\b(with|requiring)?\s*\d+[-–\sto]+\d*\s*(?:years?|yrs?)(?:\s*exp(?:erience)?)?\b", "", role, flags=re.IGNORECASE).strip() or role

        # Round 2: Source Expansion & Pagination
        if round_num == 2:
            # Paginate high-yielding tasks from Round 1
            productive_tasks = [t for t in discovery_state.completed_tasks if t.round == 1 and t.priority <= 2]
            for pt in productive_tasks[:4]:
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
                cities.extend(["Riyadh", "Jeddah", "Dammam", "NEOM"])
            if any(k in loc_str.lower() for k in ("uae", "emirates", "dubai")):
                cities.extend(["Dubai", "Abu Dhabi"])
            if not cities and loc_str:
                cities.append(loc_str)

            for city in cities[:4]:
                q_city = f'"{role_clean}" "{city}" hiring apply careers'
                if q_city not in discovery_state.executed_queries:
                    tasks.append(SearchTask(
                        id=f"r4_geo_{city.lower()}",
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

        return tasks

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

        if state.search_calls >= self.budget.max_search_requests:
            return True, StopReason.BUDGET_EXHAUSTED, f"Max search requests budget ({self.budget.max_search_requests}) reached"

        if discovery_state.current_round > self.budget.max_expansion_rounds:
            return True, StopReason.BUDGET_EXHAUSTED, f"Max discovery rounds ({self.budget.max_expansion_rounds}) completed"

        effective_count = len(state.qualified_records) if (state.mode in ("jobs", "job") and state.qualified_records) else len(state.verified_records)

        # Target results reached
        if effective_count >= state.target_results:
            if discovery_state.coverage_matrix.is_balanced(min_categories=2):
                return True, StopReason.TARGET_QUALIFIED_REACHED, f"Target results ({effective_count}/{state.target_results}) reached with balanced coverage"
            elif discovery_state.current_round >= 3:
                return True, StopReason.TARGET_QUALIFIED_REACHED, f"Target results ({effective_count}/{state.target_results}) reached across {discovery_state.current_round} rounds"

        # Diminishing returns detection
        if discovery_state.consecutive_low_yield_rounds >= 2:
            if effective_count > 0 and discovery_state.coverage_matrix.is_balanced(min_categories=2):
                return True, StopReason.DIMINISHING_RETURNS, f"Diminishing returns across 2 consecutive rounds; {effective_count} qualified jobs found"

        # No more tasks in queue
        if not discovery_state.task_queue and discovery_state.current_round > 1:
            if effective_count > 0:
                return True, StopReason.COVERAGE_COMPLETE_WITH_SATISFACTION, f"Completed discovery universe with {effective_count} qualified jobs"
            else:
                return True, StopReason.SOURCE_UNIVERSE_EXHAUSTED, "Exhausted all discovered search tasks with no matching records"

        return False, StopReason.TARGET_QUALIFIED_REACHED, ""
