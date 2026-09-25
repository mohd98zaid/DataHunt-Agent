"""
SearchPlannerAgent — Step 9 of the job search pipeline.

Takes a structured JobSearchRequest and an ExpandedQuery, and generates
a comprehensive, multi-source SearchTask plan covering ATS endpoints,
regional portals, major boards, startup ecosystems, and web discovery.
"""
from typing import List, Optional
from pydantic import BaseModel, Field
from datahunt.agents.query_understanding import JobSearchRequest
from datahunt.agents.query_expansion import ExpandedQuery
from datahunt.tools.search import generate_ats_queries, generate_broad_job_queries


class SearchTask(BaseModel):
    """A discrete search operation with source categorization and execution priority."""
    query: str
    source_type: str = "web"  # ats | regional_board | job_board | remote | startup | career_page | web
    purpose: str = "general_discovery"
    priority: int = 1         # 1 (highest) to 5 (lowest)
    freshness_days: Optional[int] = None


class SearchPlannerAgent:
    """
    Step 9: Creates prioritized search queries across all permitted sources.
    """

    def plan(self, req: JobSearchRequest, expanded: ExpandedQuery) -> List[SearchTask]:
        """Produce an orchestrated list of SearchTasks covering all relevant tiers."""
        tasks: List[SearchTask] = []
        seen_queries = set()

        def add_task(query: str, source_type: str, purpose: str, priority: int, freshness: Optional[int] = None):
            q_clean = query.strip()
            if q_clean and q_clean not in seen_queries:
                seen_queries.add(q_clean)
                tasks.append(SearchTask(
                    query=q_clean,
                    source_type=source_type,
                    purpose=purpose,
                    priority=priority,
                    freshness_days=freshness
                ))

        primary_title = expanded.primary_title
        loc = req.location or ""
        remote = req.remote_status
        has_specific_loc = bool(loc and loc.lower() not in ("remote", "any", "worldwide", "global"))

        loc_tokens = [
            t.lower() for t in loc.replace(",", " ").split()
            if len(t) > 2 and t.lower() not in ("with", "years", "experience", "and", "or", "for")
        ]
        regional_keywords = (
            "bayt", "naukri", "gulftalent", "reed", "totaljobs", "laimoon", "dubizzle",
            "dubai", "riyadh", "saudi", "uae", "abu dhabi", "jeddah", "doha", "qatar", "ksa",
            "bangalore", "bengaluru", "mumbai", "pune", "delhi", "hyderabad",
            "london", "uk", "singapore", "berlin", "germany", "toronto", "canada"
        )

        # 1. Tier 1: Direct ATS endpoints & Local Portals (Highest precision & 0-sec live accuracy)
        # Pick top 3-4 ATS and top 2-3 regional board queries for primary title
        ats_raw = generate_ats_queries(f"{primary_title} in {loc}" if loc else primary_title)
        tier1_ats = 0
        tier1_reg = 0
        for q in ats_raw:
            is_reg = any(b in q.lower() for b in ("bayt", "naukri", "gulftalent"))
            if is_reg and tier1_reg < 3:
                add_task(q, source_type="regional_board", purpose="direct_ats_harvest", priority=1, freshness=None)
                tier1_reg += 1
            elif not is_reg and tier1_ats < 3:
                add_task(q, source_type="ats", purpose="direct_ats_harvest", priority=1, freshness=None)
                tier1_ats += 1

        # 2. Tier 2: Regional & Major Boards (Broad sweep) - select top 2-3
        broad_raw = generate_broad_job_queries(f"{primary_title} in {loc}" if loc else primary_title)
        tier2_count = 0
        for q in broad_raw:
            if tier2_count >= 3:
                break
            q_low = q.lower()
            is_regional = any(b in q_low for b in regional_keywords) or any(lt in q_low for lt in loc_tokens)
            s_type = "regional_board" if is_regional else "job_board"
            add_task(q, source_type=s_type, purpose="broad_internet_sweep", priority=2, freshness=None)
            tier2_count += 1

        # 3. Tier 3: Top alternative title regional boost & ATS queries (e.g. "AI Engineer" if primary is "GenAI Engineer")
        if expanded.all_titles and len(expanded.all_titles) > 1 and has_specific_loc:
            alt_primary = expanded.all_titles[1]
            if any(k in loc.lower() for k in ("dubai", "uae", "saudi", "riyadh", "abu dhabi", "gulf", "ksa")):
                add_task(f'"{alt_primary}" (Dubai OR Riyadh OR Saudi OR UAE) jobs site:boards.greenhouse.io', "ats", "direct_ats_harvest", 2, None)
                add_task(f'site:gulftalent.com "{alt_primary}"', "regional_board", "direct_ats_harvest", 2, None)
            else:
                add_task(f'site:jobs.lever.co "{alt_primary}" {loc}', "ats", "ats_synonym_harvest", 2, None)

        # 4. Tier 4: Remote / Startup Specific Boards (if requested or applicable)
        if remote in ("remote", "any") and not has_specific_loc:
            add_task(f"site:wellfound.com/jobs {primary_title} remote", "startup", "startup_remote", 3, None)
            add_task(f"site:weworkremotely.com {primary_title}", "remote", "remote_niche", 3, None)

        # Sort tasks: ensure priority 1 (initial round) comes first, capped at 12 tasks total
        def task_sort_key(t: SearchTask):
            if has_specific_loc:
                q_low = t.query.lower()
                is_direct_portal = any(k in q_low for k in ("gulftalent", "bayt", "naukrigulf"))
                is_city_job = any(c in q_low for c in ("dubai jobs", "saudi arabia jobs", "riyadh jobs", "uae jobs", "saudi jobs", "dubai uae", "riyadh saudi"))
                if is_direct_portal or is_city_job:
                    return (0, t.priority)
                is_geo_target = any(lt in q_low for lt in loc_tokens) or any(k in q_low for k in ("dubai", "riyadh", "saudi", "uae", "abu dhabi"))
                if is_geo_target:
                    return (1, t.priority)
            return (t.priority + 1, 1)

        tasks.sort(key=task_sort_key)
        # Cap total plan between 6 and 12 high-value queries
        return tasks[:12]
