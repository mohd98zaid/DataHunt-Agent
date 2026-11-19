"""
AgentRuntime — The primary decision-loop execution engine.

Replaces the 8-phase linear pipeline in agent/orchestrator.py with
an explicit observe→decide→act loop.
"""
import re
import time
import threading
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from datahunt.config import settings
from datahunt.errors import DataHuntError, ErrorCode
from datahunt.logger import logger
# Gemini client import (commented out as sole client in favor of NvidiaClient / any client):
# from datahunt.llm import GeminiClient
from datahunt.llm import NvidiaClient, GeminiClient
from datahunt.tools import SearchTool, FetchTool, ExtractTool, VerifyTool, DedupeTool, ExportTool
from datahunt.tools.search import is_valid_job_url, canonicalize_url, generate_job_fingerprint
from datahunt.models import VerificationStatus

from .state import AgentState, AgentStatus
from .decision import AgentAction, DecisionEngine
from .policies import (
    GoalEvaluator,
    MatchStatus,
    GEO_ALIAS_GROUPS,
    match_location,
    match_experience,
    match_title_relevance,
    qualify_job,
    QualificationResult,
)


class _SearchRateLimiter:
    """Rolling-window rate limiter: at most `rpm` calls per 60-second window.

    Thread-safe. _act_search acquires one slot per parallel worker before
    dispatching a search call to stay within the NVIDIA_RPM quota.
    """
    def __init__(self, rpm: int = 40):
        self.rpm = rpm
        from collections import deque
        self._window: deque = deque()
        self._lock = threading.Lock()

    def reconfigure(self, rpm: int) -> None:
        with self._lock:
            self.rpm = rpm

    def acquire(self) -> None:
        """Block until a call slot is available in the current 60 s window."""
        while True:
            with self._lock:
                now = time.time()
                while self._window and now - self._window[0] >= 60.0:
                    self._window.popleft()
                if len(self._window) < self.rpm:
                    self._window.append(now)
                    return
                wait = 60.0 - (now - self._window[0])
            time.sleep(max(0.05, wait))


# Module-level limiter; reconfigured to NVIDIA_RPM when AgentRuntime is constructed.
_search_rate_limiter = _SearchRateLimiter(rpm=40)

# Task-type → track membership (for telemetry / interleaving labels)
_PORTAL_TASK_TYPES: frozenset = frozenset(("BOARD_SEARCH", "REGIONAL_BOARD_SEARCH", "GENERAL_SEARCH"))
_CAREER_TASK_TYPES: frozenset = frozenset(("ATS_SEARCH", "CAREER_PAGE_SEARCH", "COMPANY_SEARCH"))


def _classify_job_source_category(url: str, source_str: str = "") -> str:
    combined = f"{url} {source_str}".lower()
    if any(k in combined for k in (
        "greenhouse", "lever.co", "ashbyhq", "workable", "smartrecruiters",
        "myworkday", "bamboohr", "breezy.hr", "jobvite"
    )):
        return "direct_ats"
    if any(k in combined for k in (
        "naukrigulf", "bayt", "gulftalent", "gulfjobs", "dubizzle",
        "foundit", "akhtaboot", "laimoon", "tanqeeb", "mihnati"
    )):
        return "regional_board"
    if any(k in combined for k in (
        "linkedin", "indeed", "glassdoor", "wellfound", "ziprecruiter", "monster"
    )):
        return "major_board"
    return "company_careers"


class AgentRuntime:
    """
    Observation → Decision → Action loop with explicit state, budgets,
    and deterministic stop conditions.
    """

    def __init__(self, client: Any, search: SearchTool, fetch: FetchTool,
                 extract: ExtractTool, verify: VerifyTool, dedupe: DedupeTool, export: ExportTool):
        self.client = client
        self.search = search
        self.fetch = fetch
        self.extract = extract
        self.verify = verify
        self.dedupe = dedupe
        self.export = export
        self.decision_engine = DecisionEngine()
        self.goal_evaluator = GoalEvaluator()
        from datahunt.sources.registry import JobSourceRegistry
        self.source_registry = JobSourceRegistry()
        from .discovery_engine import DiscoveryEngine
        self.discovery_engine = DiscoveryEngine()
        from datahunt.db import CheckpointRepository
        self.checkpoint_repo = CheckpointRepository()
        # Sync rate limiter to current settings (allows live override via settings UI)
        _search_rate_limiter.reconfigure(rpm=getattr(settings, "NVIDIA_RPM", 40))

    def _init_state(self, state: AgentState, emit: Callable[[str, Dict], None]):
        """Initialize state, canonical job request, discovery state, and search plan."""
        emit("status", {"message": "Understanding search requirements", "phase": "PLANNING"})
        state.status = AgentStatus.PLANNING

        state.raw_record_generation = 0
        state.last_verified_generation = -1
        state.processed_record_ids = set()

        # Initialize canonical job request if in jobs mode
        if state.mode in ("jobs", "job"):
            q_low = (state.request or "").lower()
            if "--fast" in q_low or "mode:fast" in q_low or "fast_mode" in q_low or state.collection_mode == "fast":
                state.collection_mode = "fast"
            elif "--thorough" in q_low or "mode:thorough" in q_low or state.collection_mode == "thorough":
                state.collection_mode = "thorough"
            else:
                state.collection_mode = getattr(settings, "DEFAULT_COLLECTION_MODE", "thorough")
            emit("status", {"message": "Intent: JOB_SEARCH", "phase": "PLANNING"})
            if not state.canonical_job_request:
                if not state.request or not state.request.strip():
                    raise ValueError("Job search query (raw_query) cannot be empty")
                from datahunt.agents import QueryUnderstandingAgent, JobSearchRequest
                qu_agent = QueryUnderstandingAgent(gemini_client=self.client)
                job_req = qu_agent.understand(state.request)
                if not isinstance(job_req, JobSearchRequest):
                    raise ValueError(f"Failed to parse valid JobSearchRequest from query: {state.request}")
                state.canonical_job_request = job_req
            else:
                if not getattr(state.canonical_job_request, "raw_query", "").strip():
                    raise ValueError("canonical_job_request.raw_query cannot be empty")
                job_req = state.canonical_job_request

            state.explicit_titles = [job_req.job_title] if job_req.job_title else []
            state.explicit_skills = job_req.explicit_skills
            state.inferred_skills = job_req.inferred_skills
            state.locations = job_req.locations
            state.location_operator = job_req.location_operator
            state.explicit_location = job_req.location
            state.explicit_experience_min = job_req.experience_min
            state.explicit_experience_max = job_req.experience_max
            state.remote_allowed = job_req.remote_allowed

            from .discovery_models import (
                SearchTaskType,
                StopReason,
                DiscoveredSourceType,
                SearchTask,
                DiscoveryBudget,
                DiscoveryRoundTelemetry,
                SourceCoverageMatrix,
                DiscoveryState,
            )
            state.max_search_calls = max(state.max_search_calls, 120)
            state.discovery_budget = state.discovery_budget or DiscoveryBudget(
                max_search_requests=state.max_search_calls,
                max_fetches=state.max_fetch_calls,
            )
            if state.discovery_state is None:
                target_geos = state.locations or ([state.explicit_location] if state.explicit_location else ["general"])
                state.discovery_state = DiscoveryState(
                    coverage_matrix=SourceCoverageMatrix(target_regions=target_geos)
                )

            # Section 2 & Phase 1/2: DiscoveryEngine is the sole runtime search authority in Job Mode.
            # Do NOT inject legacy search_plan into DiscoveryState.task_queue.
            initial_tasks = self.discovery_engine.generate_initial_tasks(state.canonical_job_request, state)
            state.discovery_state.task_queue.extend(initial_tasks)

            state.search_plan = [
                {"query": t.query, "tier": t.priority, "purpose": t.reason, "source_id": t.source, "page": t.page}
                for t in state.discovery_state.task_queue
            ]
            if state.explicit_location is None:
                state.explicit_location = self._extract_location(state.request)
            if state.explicit_experience_min is None and state.explicit_experience_max is None:
                state.explicit_experience_min, state.explicit_experience_max = self._extract_experience(state.request)
        else:
            if state.search_plan:
                emit("status", {"message": f"Using search plan: {len(state.search_plan)} queries", "phase": "PLANNED"})
                if state.explicit_location is None:
                    state.explicit_location = self._extract_location(state.request)
                if state.explicit_experience_min is None and state.explicit_experience_max is None:
                    state.explicit_experience_min, state.explicit_experience_max = self._extract_experience(state.request)
            else:
                try:
                    self._build_search_plan(state, emit)
                except Exception as e:
                    logger.warning(f"Planning failed: {e}; using basic query plan")
                    state.search_plan = [{"query": state.request, "tier": 1, "purpose": "primary"}]

        emit("status", {"message": f"Formulated {len(state.search_plan)} search queries", "phase": "PLANNED"})
        state.record_action("planned")

    def save_checkpoint(self, state: AgentState) -> Optional[int]:
        """Save a durable state checkpoint to the database."""
        run_id = getattr(state, "run_id", None) or getattr(state, "task_id", None)
        if not run_id:
            return None
        try:
            data = state.to_checkpoint_dict()
            return self.checkpoint_repo.save_checkpoint(run_id, data, seq=state.iteration)
        except Exception as e:
            logger.warning(f"Failed to save checkpoint for run {run_id}: {e}")
            return None

    def run(self, state: AgentState, emit: Callable[[str, Dict], None]) -> Dict[str, Any]:
        """
        Execute the agent loop until a stop condition is reached.
        Returns the final result dict.
        """
        self._init_state(state, emit)
        self.save_checkpoint(state)

        while True:
            state.iteration += 1
            action, reason = self.decision_engine.decide(state)

            logger.info(f"[Decision #{state.iteration}] {action} — {reason}")
            emit("decision", {"iteration": state.iteration, "action": action, "reason": reason})
            state.record_action(f"iter{state.iteration}:{action}:{reason}")

            if action == AgentAction.STOP:
                state.completion_reason = reason
                self.save_checkpoint(state)
                break

            elif action == AgentAction.CRAWL_DIRECT:
                self._act_crawl_direct(state, emit)

            elif action == AgentAction.SEARCH or action == AgentAction.EXPAND_SEARCH:
                self._act_search(state, emit)

            elif action == AgentAction.FETCH:
                self._act_fetch(state, emit)

            elif action == AgentAction.VERIFY:
                self._act_verify(state, emit)

            elif action == AgentAction.ANALYZE:
                self._act_analyze(state, emit)

            self.save_checkpoint(state)

        return self._finalize(state, emit)

    def resume(self, run_id: str, emit: Callable[[str, Dict], None]) -> Dict[str, Any]:
        """
        Durable resumption:
        1. Fetch the latest checkpoint for run_id from CheckpointRepository.
        2. Reconstruct AgentState from checkpoint via AgentState.from_checkpoint_dict(data).
        3. Re-queue any partial_crawl_tasks from state.discovery_state back into pending_crawl_tasks
           so they can continue with their saved cursors.
        4. Continue the decision loop without re-running initialization or duplicating work.
        """
        row = self.checkpoint_repo.get_latest_checkpoint(run_id)
        if not row:
            raise DataHuntError(f"No checkpoint found for run {run_id}", ErrorCode.NOT_FOUND)

        state_data = row.get("state_data", row) if isinstance(row, dict) else row
        state = AgentState.from_checkpoint_dict(state_data)
        state.run_id = run_id
        emit("status", {
            "message": f"Resuming search run {run_id} from checkpoint (iteration {state.iteration})",
            "phase": "RESUMING",
        })

        # Re-queue partial crawl tasks so they continue from saved cursors
        if state.discovery_state and state.discovery_state.partial_crawl_tasks:
            from datahunt.agent.discovery_models import CrawlTaskStatus
            while state.discovery_state.partial_crawl_tasks:
                t = state.discovery_state.partial_crawl_tasks.pop(0)
                t.status = CrawlTaskStatus.PENDING
                state.discovery_state.pending_crawl_tasks.append(t)
            state.crawl_done = False

        while True:
            state.iteration += 1
            action, reason = self.decision_engine.decide(state)

            logger.info(f"[Resumed Decision #{state.iteration}] {action} — {reason}")
            emit("decision", {"iteration": state.iteration, "action": action, "reason": reason})
            state.record_action(f"iter{state.iteration}:{action}:{reason}")

            if action == AgentAction.STOP:
                state.completion_reason = reason
                self.save_checkpoint(state)
                break

            elif action == AgentAction.CRAWL_DIRECT:
                self._act_crawl_direct(state, emit)

            elif action == AgentAction.SEARCH or action == AgentAction.EXPAND_SEARCH:
                self._act_search(state, emit)

            elif action == AgentAction.FETCH:
                self._act_fetch(state, emit)

            elif action == AgentAction.VERIFY:
                self._act_verify(state, emit)

            elif action == AgentAction.ANALYZE:
                self._act_analyze(state, emit)

            self.save_checkpoint(state)

        return self._finalize(state, emit)

    def _build_search_plan(self, state: AgentState, emit):
        """Build a focused tiered search plan from the request."""
        is_job = state.mode in ("jobs", "job")
        if is_job:
            # Section 2 & Phase 1/2: DiscoveryEngine is the sole runtime search authority in Job Mode.
            # SearchPlannerAgent does NOT control Job Agent runtime execution.
            from datahunt.agents import QueryUnderstandingAgent, JobSearchRequest
            qu_agent = QueryUnderstandingAgent(gemini_client=self.client)

            if not state.canonical_job_request:
                state.canonical_job_request = qu_agent.understand(state.request)

            job_req = state.canonical_job_request if isinstance(state.canonical_job_request, JobSearchRequest) else qu_agent.understand(state.request)

            initial_tasks = self.discovery_engine.generate_initial_tasks(job_req, state)
            plan = [
                {"query": t.query, "tier": t.priority, "purpose": t.reason, "source_id": t.source, "page": t.page}
                for t in initial_tasks
            ]
        else:
            from datahunt.models.run import RunBudget
            spec = self.client.normalize_request(state.request)
            plan_data = self.client.plan_research(spec, RunBudget())
            queries = plan_data.get("queries", [{"query": state.request}])
            plan = [{"query": q.get("query", str(q)), "tier": 2, "purpose": q.get("purpose", "general")} for q in queries[:10]]

        state.search_plan = plan
        if state.explicit_location is None:
            state.explicit_location = self._extract_location(state.request)
        if state.explicit_experience_min is None and state.explicit_experience_max is None:
            state.explicit_experience_min, state.explicit_experience_max = self._extract_experience(state.request)

    def _act_crawl_direct(self, state: AgentState, emit):
        """
        Execute direct multi-source crawling across ATS public APIs,
        regional job boards, and discovered company career pages.
        Processes queued crawl tasks or performs targeted initial crawls.
        Discovered job URLs are appended to state.candidate_urls for subsequent FetchTool processing.
        Never raises — all source errors are recorded as SOURCE_BLOCKED and crawling continues.
        """
        state.status = AgentStatus.CRAWLING

        from datahunt.sources.crawlers import MultiSourceCrawler, CrawlBudget
        from datahunt.agent.discovery_models import CrawlTaskStatus
        from datahunt.tools.search import canonicalize_url, is_valid_job_url

        loc_lower = " ".join(state.locations).lower() if state.locations else (
            (state.explicit_location or "").lower()
        )
        is_gulf = any(k in loc_lower for k in (
            "uae", "dubai", "abu dhabi", "sharjah", "saudi", "riyadh",
            "jeddah", "dammam", "khobar", "gulf", "mena", "gcc", "qatar"
        ))

        role_keywords = list(dict.fromkeys(
            state.explicit_titles + state.expanded_titles
        )) or ["Engineer"]
        locations = state.locations or ([state.explicit_location] if state.explicit_location else [])

        # Derive crawl budget from discovery budget or state limits (no hardcoded 25/6/80 caps)
        db = state.discovery_budget
        max_sources = getattr(db, "max_sources", 500) if db else 500
        max_pages_per_source = getattr(db, "max_pages_per_source", 50) if db else 50
        max_total_pages = getattr(db, "max_fetches", state.max_fetch_calls) if db else state.max_fetch_calls
        max_total_jobs = getattr(db, "max_total_jobs", 20000) if db else 20000

        budget = CrawlBudget(
            max_sources=max_sources,
            max_pages_per_source=max_pages_per_source,
            max_total_pages=max_total_pages,
            max_total_jobs=max_total_jobs,
            request_timeout=10.0,
            per_domain_delay=0.25,
            max_workers=8,
        )

        crawler = MultiSourceCrawler(budget=budget)
        crawl_result = None

        # Check if we have queued CrawlTasks in DiscoveryState
        # Process in concurrency batches until queue is exhausted or deadline/budget reached
        batch_size = 8
        if state.discovery_state and state.discovery_state.pending_crawl_tasks:
            while state.discovery_state.pending_crawl_tasks:
                if state.effective_search_deadline > 0 and time.time() >= state.effective_search_deadline:
                    break

                batch = []
                while state.discovery_state.pending_crawl_tasks and len(batch) < batch_size:
                    t = state.discovery_state.pending_crawl_tasks.pop(0)
                    t.status = CrawlTaskStatus.ACTIVE
                    state.discovery_state.active_crawl_tasks.append(t)
                    batch.append(t)

                if not batch:
                    break

                emit("status", {
                    "message": f"Direct crawl: processing batch of {len(batch)} source tasks ({len(state.discovery_state.pending_crawl_tasks)} remaining)",
                    "phase": "CRAWLING",
                })
                try:
                    crawl_res = crawler.crawl_tasks(
                        tasks=batch,
                        role_keywords=role_keywords,
                        deadline=state.effective_search_deadline,
                        external_emit=emit,
                        discovery_state=state.discovery_state,
                    )
                    if crawl_result is None:
                        crawl_result = crawl_res
                    else:
                        crawl_result.sources_discovered += crawl_res.sources_discovered
                        crawl_result.sources_crawled += crawl_res.sources_crawled
                        crawl_result.sources_blocked += crawl_res.sources_blocked
                        crawl_result.pages_fetched += crawl_res.pages_fetched
                        crawl_result.job_links_discovered += crawl_res.job_links_discovered
                        crawl_result.jobs_extracted += crawl_res.jobs_extracted
                        crawl_result.crawled_jobs.extend(crawl_res.crawled_jobs)
                        crawl_result.candidate_urls.extend(crawl_res.candidate_urls)
                        crawl_result.events.extend(crawl_res.events)
                        crawl_result.task_outcomes.update(crawl_res.task_outcomes)
                        crawl_result.task_reasons.update(crawl_res.task_reasons)

                    for t in batch:
                        if t in state.discovery_state.active_crawl_tasks:
                            state.discovery_state.active_crawl_tasks.remove(t)
                        outcome = crawl_res.task_outcomes.get(t.id, CrawlTaskStatus.COMPLETED)
                        t.status = outcome
                        if outcome == CrawlTaskStatus.COMPLETED:
                            state.discovery_state.completed_crawl_tasks.append(t)
                        elif outcome == CrawlTaskStatus.BLOCKED:
                            state.discovery_state.blocked_crawl_tasks.append(t)
                        elif outcome == CrawlTaskStatus.PARTIAL:
                            state.discovery_state.partial_crawl_tasks.append(t)
                        else:
                            state.discovery_state.failed_crawl_tasks.append(t)
                        key = f"{t.source_type}:{t.source}:{t.company or ''}:{t.location or ''}:{t.page}".lower()
                        state.discovery_state.crawled_sources.add(key)
                except Exception as e:
                    logger.warning(f"Error crawling tasks batch: {e}")
                    for t in batch:
                        if t in state.discovery_state.active_crawl_tasks:
                            state.discovery_state.active_crawl_tasks.remove(t)
                        t.status = CrawlTaskStatus.FAILED
                        state.discovery_state.failed_crawl_tasks.append(t)

            if not state.discovery_state.pending_crawl_tasks:
                state.crawl_done = True
        else:
            state.crawl_done = True
            discovered_ats = dict(state.discovery_state.discovered_ats) if state.discovery_state else {}
            discovered_companies = dict(state.discovery_state.discovered_companies) if state.discovery_state else {}

            emit("status", {
                "message": f"Direct crawl: {len(discovered_ats)} ATS + regional boards + career pages",
                "phase": "CRAWLING",
            })

            try:
                crawl_result = crawler.crawl(
                    role_keywords=role_keywords,
                    locations=locations,
                    discovered_ats=discovered_ats,
                    discovered_companies=discovered_companies,
                    is_gulf=is_gulf,
                    deadline=state.effective_search_deadline,
                    external_emit=emit,
                )
            except Exception as e:
                logger.warning(f"_act_crawl_direct error (non-fatal): {e}")
                state.add_warning(f"Direct crawl error: {e}")

        if crawl_result:
            new_candidates = 0
            for hit in crawl_result.candidate_urls:
                url = hit.get("url")
                if not url:
                    continue
                canon = canonicalize_url(url)
                if not canon or canon in state.seen_canonical_urls:
                    continue
                if state.mode in ("jobs", "job") and not is_valid_job_url(url):
                    if not hit.get("crawled"):
                        continue
                if state.mode in ("jobs", "job") and not self._is_promising_candidate(hit, state):
                    continue

                state.seen_canonical_urls.add(canon)
                state.seen_urls.add(url)
                state.candidate_urls.append(hit)
                new_candidates += 1

                src_cat = _classify_job_source_category(url, hit.get("source", ""))
                state.source_distribution[src_cat] = state.source_distribution.get(src_cat, 0) + 1

            state.crawl_sources_discovered += crawl_result.sources_discovered
            state.crawl_sources_crawled += crawl_result.sources_crawled
            state.crawl_sources_blocked += crawl_result.sources_blocked
            state.crawl_pages_fetched += crawl_result.pages_fetched
            state.crawl_job_links_discovered += crawl_result.job_links_discovered

            emit("crawl_summary", {
                "sources_discovered": crawl_result.sources_discovered,
                "sources_crawled": crawl_result.sources_crawled,
                "sources_blocked": crawl_result.sources_blocked,
                "pages_fetched": crawl_result.pages_fetched,
                "job_links_discovered": crawl_result.job_links_discovered,
                "new_candidates_added": new_candidates,
            })
            state.add_observation(
                f"Direct crawl: {crawl_result.sources_crawled} sources, "
                f"{crawl_result.pages_fetched} pages, {new_candidates} new candidates"
            )
            logger.info(
                f"_act_crawl_direct: {new_candidates} new candidates from "
                f"{crawl_result.sources_crawled} sources ({crawl_result.sources_blocked} blocked)"
            )



    def _is_promising_candidate(self, hit: Dict[str, Any], state: AgentState) -> bool:
        """Lightweight pre-fetch triage to reject obvious junk before network requests."""
        url = (hit.get("url") or "").lower()
        title = (hit.get("title") or "").lower()
        snippet = (hit.get("snippet") or "").lower()
        candidate_location = hit.get("location")
        if isinstance(candidate_location, dict):
            candidate_location = ", ".join(
                str(value) for value in candidate_location.values() if value
            )
        cand_loc_str = str(candidate_location or "").lower()
        text = f"{url} {title} {snippet} {cand_loc_str}"

        # 1. Non-job URL patterns
        JUNK_URL_PATTERNS = ["/jobs/search", "/browse/", "/tag/", "/category/", "/login", "/signup", "/people/", "/candidate/"]
        if any(p in url for p in JUNK_URL_PATTERNS):
            return False

        # 2. Obvious directory/people titles
        JUNK_TITLES = ["people directory", "candidate profile", "sign in", "log in", "create account"]
        if any(jt in title for jt in JUNK_TITLES):
            return False

        # 3. If in jobs mode:
        if state.mode in ("jobs", "job"):
            req_t = (state.canonical_job_request.job_title if state.canonical_job_request else state.request).lower()
            if any(k in req_t for k in ("genai", "generative ai", "llm", "ai engineer")):
                DISQUALIFYING_ROLES = [
                    "cybersecurity", "cyber security", "infosec", "soc analyst",
                    "full stack", "fullstack", "front end", "frontend", "ui developer",
                    "accountant", "bookkeeper", "sales manager", "recruiter", "talent acquisition"
                ]
                if any(dr in title for dr in DISQUALIFYING_ROLES) and not any(ai in title for ai in ("genai", "generative ai", "llm", "ai engineer", "ai full", "ai-full", "ai/ml")):
                    return False

            # If user requested specific regions, e.g. GCC/Gulf/Saudi/UAE, reject explicit foreign anchors and foreign domain subdomains
            req_geos = [loc.lower() for loc in (state.locations or ([state.explicit_location] if state.explicit_location else []))]
            is_gulf = any(any(k in g for k in ("saudi", "uae", "dubai", "riyadh", "jeddah", "dammam", "gulf", "gcc", "qatar", "kuwait", "bahrain", "oman")) for g in req_geos)
            if is_gulf:
                if candidate_location:
                    location_status, _ = match_location(
                        state.locations or state.explicit_location,
                        str(candidate_location),
                        remote_ok=state.remote_allowed is not False,
                    )
                    if location_status == MatchStatus.MISMATCH:
                        return False

                FOREIGN_DOMAINS = [
                    "eg.linkedin.com", "wuzzuf.net", "forasna.com",  # Egypt
                    "in.linkedin.com", "naukri.com", "foundit.in",   # India
                    "pk.linkedin.com", "rozee.pk",                   # Pakistan
                    "bd.linkedin.com",                               # Bangladesh
                    "uk.linkedin.com", "reed.co.uk",                 # UK
                ]
                if any(fd in url for fd in FOREIGN_DOMAINS):
                    return False

                FOREIGN_ANCHORS = [
                    "cairo, egypt", "alexandria, egypt", "egypt only", "based in egypt", "cairo",
                    "london, uk", "london, united kingdom", "bangalore, india", "bengaluru, india",
                    "pune, india", "mumbai, india", "hyderabad, india", "delhi, india",
                    "karachi, pakistan", "lahore, pakistan", "islamabad, pakistan",
                    "denver, co", "san francisco, ca", "new york, ny",
                    "india", "united states", "usa", "germany", "france", "paris", "europe",
                    "united kingdom", "london", "sweden", "norway", "denmark", "canada",
                    "australia", "singapore", "japan", "tokyo", "south korea",
                ]
                if any(fa in text for fa in FOREIGN_ANCHORS) and not any(rg in text for rg in ("saudi", "uae", "dubai", "riyadh", "doha", "qatar", "kuwait", "bahrain", "oman", "worldwide", "global remote")):
                    return False

            # If candidate is a LinkedIn post, ensure it is a hiring announcement rather than a job seeker post
            if "linkedin.com" in url and any(p in url for p in ("/posts/", "/feed/update/", "/pulse/")):
                JOB_SEEKER_PHRASES = [
                    "open to work", "#opentowork", "looking for a job", "looking for new opportunities",
                    "seeking new opportunities", "seeking a role", "seeking a job",
                    "seeking opportunity", "open for new opportunities",
                    "happy to announce that i've joined", "happy to announce i joined",
                    "thrilled to announce that i've joined", "thrilled to announce i joined",
                    "started a new position", "started a new role", "celebrating 5 years",
                    "celebrating 10 years", "promoted to", "congratulations to"
                ]
                comb_text = f"{title} {text}".lower()
                if any(jsp in comb_text for jsp in JOB_SEEKER_PHRASES):
                    return False

            # Drop candidates whose title or snippet explicitly indicates they are stale,
            # UNLESS searching in "All time" mode or thorough collection where full evaluation is required.
            is_all_time = (
                getattr(state, "is_all_time", False)
                or getattr(state, "collection_mode", "") == "thorough"
                or getattr(state, "freshness_days", None) in (0, -1, 99999)
                or (state.canonical_job_request and (
                    getattr(state.canonical_job_request, "is_all_time", False)
                    or getattr(state.canonical_job_request, "freshness_days", None) in (0, -1, 99999)
                ))
                or any(w in (state.request or "").lower() for w in ("all time", "all-time", "any time", "anytime", "all open", "all active"))
            )
            if not is_all_time:
                STALE_SNIPPET_PATTERNS = [
                    r"\b(?:[1-9]|\d{2})\s+years?\s+ago\b",
                    r"\b(?:over\s+|more\s+than\s+)?1\s+year\s+ago\b",
                    r"\b(?:[3-9]|1[0-2])\s+months?\s+ago\b",
                    r"\b(?:1[0-9]{2}|[2-9][0-9]{2})\s+days?\s+ago\b",  # 100+ days ago
                    r"\bposted[:\s]+(?:\w+\s+\d{1,2},\s+)?(2020|2021|2022|2023|2024)\b",
                ]
                if any(re.search(pat, snippet) or re.search(pat, title) for pat in STALE_SNIPPET_PATTERNS):
                    return False

        return True

    def _recover_candidate_location(self, record: Any, state: AgentState) -> None:
        """Recover a target-region location lost by board/LinkedIn extraction."""
        fields = getattr(record, "fields", {}) or {}
        current = str(fields.get("location") or "").strip().casefold()
        unknown_location = not current or current in {
            "remote", "remote / unspecified", "remote/unspecified", "unspecified", "not specified", "n/a"
        }
        if not unknown_location:
            return

        doc_text = ""
        doc_id = getattr(record, "source_document_id", None)
        if doc_id:
            for doc in getattr(state, "fetched_docs", []) or []:
                if getattr(doc, "id", None) == doc_id:
                    doc_text = getattr(doc, "extracted_text", "") or ""
                    break

        haystack = " ".join(
            str(fields.get(key) or "")
            for key in ("title", "description", "snippet", "job_url", "apply_url", "source_url")
        ) + " " + doc_text
        haystack = haystack.casefold()

        locations = (
            (("abu dhabi", "uae"), "Abu Dhabi, UAE"),
            (("abu dhabi", "united arab emirates"), "Abu Dhabi, UAE"),
            (("dubai", "uae"), "Dubai, UAE"),
            (("dubai", "united arab emirates"), "Dubai, UAE"),
            (("sharjah", "uae"), "Sharjah, UAE"),
            (("riyadh", "saudi"), "Riyadh, Saudi Arabia"),
            (("jeddah", "saudi"), "Jeddah, Saudi Arabia"),
            (("dammam", "saudi"), "Dammam, Saudi Arabia"),
            (("khobar", "saudi"), "Khobar, Saudi Arabia"),
            (("united arab emirates",), "UAE"),
            (("uae",), "UAE"),
            (("emirates",), "UAE"),
            (("saudi arabia",), "Saudi Arabia"),
            (("ksa",), "Saudi Arabia"),
            (("dubai",), "Dubai, UAE"),
            (("abu dhabi",), "Abu Dhabi, UAE"),
            (("sharjah",), "Sharjah, UAE"),
            (("riyadh",), "Riyadh, Saudi Arabia"),
            (("jeddah",), "Jeddah, Saudi Arabia"),
            (("dammam",), "Dammam, Saudi Arabia"),
            (("khobar",), "Khobar, Saudi Arabia"),
            (("السعودية",), "Saudi Arabia"),
            (("الامارات",), "UAE"),
            (("الإمارات",), "UAE"),
            (("أبو ظبي",), "Abu Dhabi, UAE"),
            (("أبوظبي",), "Abu Dhabi, UAE"),
            (("دبي",), "Dubai, UAE"),
            (("الرياض",), "Riyadh, Saudi Arabia"),
            (("جدة",), "Jeddah, Saudi Arabia"),
            (("الدمام",), "Dammam, Saudi Arabia"),
            (("doha",), "Doha, Qatar"),
            (("qatar",), "Qatar"),
            (("kuwait",), "Kuwait"),
            (("bahrain",), "Bahrain"),
            (("manama",), "Manama, Bahrain"),
            (("muscat",), "Muscat, Oman"),
            (("oman",), "Oman"),
        )
        for terms, normalized in locations:
            if all(re.search(r"(?<!\w)" + re.escape(term) + r"(?!\w)", haystack) for term in terms):
                fields["location"] = normalized
                fields["location_recovered"] = True
                return

        app_url = str(fields.get("application_url") or fields.get("job_url") or "")
        cand_hit = None
        for c in getattr(state, "candidate_urls", []) or []:
            if c.get("url") == app_url:
                cand_hit = c
                break
        if cand_hit and cand_hit.get("location"):
            cand_loc = str(cand_hit["location"]).strip()
            if cand_loc.lower() not in ("remote", "remote / unspecified", "unspecified", "not specified"):
                fields["location"] = cand_loc
                fields["location_recovered"] = True
                return
        if state.explicit_location and (fields.get("source") == "linkedin" or "linkedin.com" in app_url):
            fields["location"] = state.explicit_location
            fields["location_recovered"] = True
            return

    def _act_search(self, state: AgentState, emit):
        """Execute the next batch of searches with canonical URL deduplication and dynamic discovery."""
        state.status = AgentStatus.SEARCHING

        # Branch 1: Dynamic Discovery Queue
        if state.discovery_state:
            # If task queue is empty at start of search, advance rounds to generate tasks
            if not state.discovery_state.task_queue and state.discovery_state.current_round < state.discovery_budget.max_expansion_rounds:
                while not state.discovery_state.task_queue and state.discovery_state.current_round < state.discovery_budget.max_expansion_rounds:
                    state.discovery_state.current_round += 1
                    next_tasks = self.discovery_engine.generate_next_round_tasks(
                        state.discovery_state, state.canonical_job_request, state
                    )
                    if next_tasks:
                        prioritized = self.discovery_engine.prioritize_tasks(next_tasks, state.discovery_state)
                        state.discovery_state.task_queue.extend(prioritized)
                        break

            if state.discovery_state.task_queue:
                from .discovery_models import DiscoveryRoundTelemetry, SearchTaskType
                from datahunt.sources.models import SourceStatus

                # Determine batch size from settings (default 20, capped by remaining budget)
                cfg_batch = getattr(settings, "SEARCH_BATCH_SIZE", 20)
                remaining_budget = state.max_search_calls - state.search_calls
                batch_size = max(1, min(cfg_batch, remaining_budget, len(state.discovery_state.task_queue)))

                batch_tasks = []
                while state.discovery_state.task_queue and len(batch_tasks) < batch_size:
                    batch_tasks.append(state.discovery_state.task_queue.pop(0))

                # Tag each task with its track (portal vs career-site) for telemetry
                for t in batch_tasks:
                    t.metadata["track"] = (
                        "career_site" if t.task_type.value in _CAREER_TASK_TYPES else "job_portal"
                    )

                portal_count = sum(1 for t in batch_tasks if t.metadata.get("track") == "job_portal")
                career_count = len(batch_tasks) - portal_count
                emit("status", {
                    "message": (
                        f"Parallel search: {len(batch_tasks)} tasks "
                        f"[{portal_count} portal + {career_count} career-site]"
                    ),
                    "phase": "SEARCHING",
                })

                # --- Parallel execution ------------------------------------------------
                def _execute_task(task) -> Dict:
                    """Worker: rate-limited search for one task; returns result dict."""
                    _search_rate_limiter.acquire()
                    t_start = time.time()
                    query = task.query
                    page = getattr(task, "page", 1)
                    try:
                        res = self.search.execute(query=query, limit=15, page=page)
                    except Exception as ex:
                        return {"task": task, "res": None, "error": str(ex), "t_start": t_start}
                    return {"task": task, "res": res, "error": None, "t_start": t_start}

                max_workers = min(len(batch_tasks), getattr(settings, "NVIDIA_RPM", 40))
                futures = []
                with ThreadPoolExecutor(max_workers=max_workers) as executor:
                    for task in batch_tasks:
                        if state.search_calls >= state.max_search_calls:
                            break
                        if state.effective_search_deadline > 0 and time.time() >= state.effective_search_deadline:
                            break
                        futures.append(executor.submit(_execute_task, task))

                # --- Merge results in the main thread (state mutations are not thread-safe) ---
                for future in as_completed(futures):
                    try:
                        result = future.result()
                    except Exception as ex:
                        logger.warning(f"Search worker exception: {ex}")
                        continue

                    task = result["task"]
                    res = result["res"]
                    t_start = result["t_start"]
                    query = task.query

                    state.search_calls += 1

                    if result["error"] or res is None:
                        logger.warning(f"Discovery search error for '{query}': {result['error']}")
                        state.add_warning(f"Search failed: {query[:40]}")
                        if hasattr(state.discovery_state, "record_task_productivity"):
                            state.discovery_state.record_task_productivity(source=task.source, failed=True)
                        continue

                    new_candidates = 0
                    dup_candidates = 0
                    initial_companies = len(state.discovery_state.discovered_companies)
                    initial_ats = len(state.discovery_state.discovered_ats)

                    if res.success and res.data:
                        for hit in res.data:
                            url = hit.get("url")
                            if not url:
                                continue
                            canon = canonicalize_url(url)
                            if not canon or canon in state.seen_canonical_urls:
                                dup_candidates += 1
                                continue
                            if state.mode in ("jobs", "job") and not is_valid_job_url(url):
                                continue
                            if state.mode in ("jobs", "job") and not self._is_promising_candidate(hit, state):
                                continue

                            detected_source = self.source_registry.identify_source_for_url(url)
                            hit_source_id = detected_source.id if detected_source else task.source
                            hit_source_name = detected_source.name if detected_source else "Public Web"
                            hit["source_id"] = hit_source_id
                            hit["source"] = hit_source_name

                            state.seen_canonical_urls.add(canon)
                            state.seen_urls.add(url)
                            state.candidate_urls.append(hit)
                            new_candidates += 1

                            if "linkedin.com" in url and any(p in url for p in ("/posts/", "/feed/update/", "/pulse/")):
                                snippet = hit.get("snippet") or ""
                                if snippet:
                                    try:
                                        from datahunt.tools.fetch import store_prefetched_job_document
                                        post_content = (
                                            f"# {hit.get('title') or ''}\n\n"
                                            f"URL: {url}\n\n"
                                            f"Platform: LinkedIn Hiring Post\n\n"
                                            f"## Post Snippet / Description\n"
                                            f"{snippet}\n"
                                        )
                                        store_prefetched_job_document(url, hit.get("title") or "", post_content)
                                    except Exception:
                                        pass

                            src_cat = _classify_job_source_category(url, hit_source_name)
                            state.source_distribution[src_cat] = state.source_distribution.get(src_cat, 0) + 1

                            if hit_source_id in state.source_run_results:
                                s_rec = state.source_run_results[hit_source_id]
                                s_rec.records_found += 1
                                s_rec.status = SourceStatus.SUCCESS

                        # Dynamic discovery: follow-up tasks from hits
                        new_followup_tasks = self.discovery_engine.process_search_hits(
                            res.data, task, state, state.discovery_state
                        )
                        if new_followup_tasks:
                            state.discovery_state.task_queue.extend(new_followup_tasks)
                            logger.info(f"Discovered {len(new_followup_tasks)} follow-up search tasks from search hits")

                    task.metadata["results_count"] = len(res.data) if res.success else 0
                    task.metadata["new_candidates"] = new_candidates
                    state.discovery_state.completed_tasks.append(task)
                    state.discovery_state.executed_queries.add(query)

                    cur_round = task.round
                    state.discovery_state.round_novel_candidates[cur_round] = (
                        state.discovery_state.round_novel_candidates.get(cur_round, 0) + new_candidates
                    )

                    duration_ms = (time.time() - t_start) * 1000
                    new_sources_discovered = (
                        (len(state.discovery_state.discovered_companies) - initial_companies)
                        + (len(state.discovery_state.discovered_ats) - initial_ats)
                    )

                    telemetry = DiscoveryRoundTelemetry(
                        run_id=state.run_id,
                        round=task.round,
                        task_type=task.task_type.value,
                        source=task.source,
                        query=query,
                        depth=task.depth,
                        results_count=len(res.data) if res.success else 0,
                        new_jobs_count=new_candidates,
                        new_qualified_count=len(state.qualified_records),
                        new_sources_count=new_sources_discovered,
                        duration_ms=round(duration_ms, 2),
                    )
                    state.discovery_state.telemetry_logs.append(telemetry)
                    emit("telemetry", telemetry.__dict__)

                    if hasattr(state.discovery_state, "record_task_productivity"):
                        state.discovery_state.record_task_productivity(
                            source=task.source,
                            raw_hits=len(res.data) if res.success else 0,
                            candidate_jobs=new_candidates,
                            unique_jobs=new_candidates,
                            duplicate_jobs=dup_candidates,
                        )

                    self.decision_engine.record_search_iteration(
                        state, query, len(res.data) if res.success else 0, new_candidates
                    )
                    state.add_observation(
                        f"Search [R{task.round}/{task.metadata.get('track','?')}] '{query[:35]}': "
                        f"{new_candidates} new candidates, {new_sources_discovered} new sources"
                    )

                # If task queue is empty for current round, advance to next discovery round if budget permits
                while not state.discovery_state.task_queue and state.discovery_state.current_round < state.discovery_budget.max_expansion_rounds:
                    cur_round = state.discovery_state.current_round
                    novel = state.discovery_state.round_novel_candidates.get(cur_round, 0)
                    if novel == 0:
                        state.discovery_state.consecutive_low_yield_rounds += 1
                    else:
                        state.discovery_state.consecutive_low_yield_rounds = 0

                    state.discovery_state.current_round += 1
                    next_tasks = self.discovery_engine.generate_next_round_tasks(
                        state.discovery_state, state.canonical_job_request, state
                    )
                    if next_tasks:
                        prioritized = self.discovery_engine.prioritize_tasks(next_tasks, state.discovery_state)
                        state.discovery_state.task_queue.extend(prioritized)
                        logger.info(f"Advanced to Discovery Round {state.discovery_state.current_round} with {len(prioritized)} new tasks")
                        emit("status", {
                            "message": f"Advancing to Discovery Round {state.discovery_state.current_round} ({len(prioritized)} tasks)",
                            "phase": "SEARCHING"
                        })
                        break
                return

        # Branch 2: Standard Search Plan
        cfg_batch = getattr(settings, "SEARCH_BATCH_SIZE", 20)
        batch_size = min(cfg_batch, len(state.search_plan) - state.search_plan_index)
        if batch_size <= 0:
            return

        queries_batch = state.search_plan[state.search_plan_index:state.search_plan_index + batch_size]
        state.search_plan_index += batch_size

        for q_obj in queries_batch:
            if state.search_calls >= state.max_search_calls:
                break
            if state.effective_search_deadline > 0 and time.time() >= state.effective_search_deadline:
                break

            query = q_obj["query"]
            source_id = q_obj.get("source_id")
            emit("status", {"message": f"Searching: {query[:60]}", "phase": "SEARCHING"})

            try:
                res = self.search.execute(query=query, limit=15)
                state.search_calls += 1
                new_candidates = 0

                if res.success:
                    from datahunt.sources.models import SourceStatus
                    for hit in res.data:
                        url = hit.get("url")
                        if not url:
                            continue
                        canon = canonicalize_url(url)
                        if not canon or canon in state.seen_canonical_urls:
                            continue
                        if state.mode in ("jobs", "job") and not is_valid_job_url(url):
                            continue

                        # Identify source
                        detected_source = self.source_registry.identify_source_for_url(url)
                        hit_source_id = detected_source.id if detected_source else (source_id or "search_engine_index")
                        hit_source_name = detected_source.name if detected_source else "Public Web"
                        hit["source_id"] = hit_source_id
                        hit["source"] = hit_source_name

                        state.seen_canonical_urls.add(canon)
                        state.seen_urls.add(url)
                        state.candidate_urls.append(hit)
                        new_candidates += 1

                        if hit_source_id in state.source_run_results:
                            s_rec = state.source_run_results[hit_source_id]
                            s_rec.records_found += 1
                            s_rec.status = SourceStatus.SUCCESS

                    if source_id and source_id in state.source_run_results:
                        if state.source_run_results[source_id].records_found == 0 and state.source_run_results[source_id].status == SourceStatus.PENDING:
                            state.source_run_results[source_id].status = SourceStatus.NO_RESULTS
                else:
                    if source_id and source_id in state.source_run_results:
                        from datahunt.sources.models import SourceStatus
                        state.source_run_results[source_id].status = SourceStatus.UNAVAILABLE

                self.decision_engine.record_search_iteration(
                    state, query, len(res.data) if res.success else 0, new_candidates
                )
                state.add_observation(f"Search '{query[:40]}': {new_candidates} new candidates from {len(res.data) if res.success else 0} hits")

            except Exception as e:
                logger.warning(f"Search error for '{query}': {e}")
                state.add_warning(f"Search failed: {query[:40]}")
                if source_id and source_id in state.source_run_results:
                    from datahunt.sources.models import SourceStatus
                    state.source_run_results[source_id].status = SourceStatus.ERROR

    def _act_fetch(self, state: AgentState, emit):
        """Fetch top candidates from the queue with candidate triage and deduplication."""
        state.status = AgentStatus.FETCHING

        # Filter and deduplicate candidates before network requests
        promising = []
        for h in state.candidate_urls:
            u = h.get("url")
            if not u:
                continue
            canon = canonicalize_url(u)
            if canon in state.seen_document_ids:
                continue
            if not self._is_promising_candidate(h, state):
                continue
            promising.append(h)

        # Prioritize locations if stated using expanded regional aliases
        target_geos = state.locations or ([state.explicit_location] if state.explicit_location else [])
        if target_geos:
            expanded_loc_tokens = set()
            for loc in target_geos:
                l_str = str(loc).lower().strip()
                expanded_loc_tokens.add(l_str)
                for cluster, aliases in GEO_ALIAS_GROUPS.items():
                    if l_str == cluster or l_str in aliases or any(a in l_str for a in aliases if len(a) > 2):
                        expanded_loc_tokens.update(aliases)

            def _candidate_loc_rank(h: Dict[str, Any]) -> int:
                text = f"{h.get('url','')} {h.get('title','')} {h.get('snippet','')} {h.get('location','')}".lower()
                if any(t in text for t in expanded_loc_tokens):
                    return 0
                return 1

            promising.sort(key=_candidate_loc_rank)

        remaining_fetches = max(0, state.max_fetch_calls - state.fetch_calls)
        is_thorough = getattr(state, "collection_mode", None) == "thorough"
        is_drain_phase = state.effective_search_deadline > 0 and time.time() >= state.effective_search_deadline
        hard_deadline_ok = state.effective_hard_deadline == 0 or time.time() < state.effective_hard_deadline

        if remaining_fetches == 0:
            # Complete existing searched items during drain phase or thorough mode if time permits
            if (is_thorough or is_drain_phase) and hard_deadline_ok and promising:
                remaining_fetches = min(25, len(promising))
            else:
                if promising:
                    state.add_warning(
                        f"Fetch budget exhausted; dropped {len(promising)} unprocessed candidates"
                    )
                state.candidate_urls = []
                return

        batch = promising[:min(25, remaining_fetches)]
        # Retain remaining candidates
        state.candidate_urls = promising[len(batch):]

        if not batch:
            self._act_extract(state, emit)
            return

        from concurrent.futures import ThreadPoolExecutor, as_completed
        emit("status", {"message": f"Fetching {len(batch)} candidate pages", "phase": "FETCHING"})

        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = {executor.submit(self.fetch.execute, url=h.get("url"), run_id=state.run_id): h for h in batch}
            for future in as_completed(futures):
                if state.effective_hard_deadline > 0 and time.time() >= state.effective_hard_deadline:
                    for f in futures:
                        f.cancel()
                    break
                try:
                    res = future.result()
                    state.fetch_calls += 1
                    if res.success and res.data:
                        canon = canonicalize_url(res.data.requested_url)
                        state.seen_document_ids.add(canon)
                        state.fetched_docs.append(res.data)
                        emit("status", {"message": f"Fetched: {res.data.requested_url[:50]}", "phase": "FETCHING"})
                    else:
                        state.pages_failed += 1
                except Exception as e:
                    state.pages_failed += 1
                    logger.warning(f"Fetch error: {e}")

        self._act_extract(state, emit)

    def _act_extract(self, state: AgentState, emit):
        """Extract records from fetched documents."""
        if not hasattr(state, "extracted_doc_ids"):
            state.extracted_doc_ids = set()

        unprocessed = [
            d for d in state.fetched_docs
            if d.id not in state.extracted_doc_ids
            and not any(getattr(r, 'source_document_id', None) == d.id for r in state.raw_records)
        ]
        if not unprocessed:
            return

        from datahunt.models.task import ResearchSpec
        spec = self._build_spec(state)
        records_added = 0

        for doc in unprocessed:
            state.extracted_doc_ids.add(doc.id)
            if state.effective_hard_deadline > 0 and time.time() >= state.effective_hard_deadline:
                break
            try:
                res = self.extract.execute(doc, spec, state.run_id)
                if res.success:
                    detected_source = self.source_registry.identify_source_for_url(doc.requested_url)
                    src_name = detected_source.name if detected_source else "Public Web"
                    for rec in res.data:
                        rec.fields.setdefault("source", src_name)
                        rec.fields.setdefault("sources", [src_name])
                        rec.fields.setdefault("job_url", doc.requested_url)
                        rec.fields.setdefault("apply_url", doc.requested_url)
                        rec.fields.setdefault("primary_application_url", doc.requested_url)
                        rec.fields.setdefault("all_source_urls", [doc.requested_url])
                        state.raw_records.append(rec)
                        records_added += 1
                        state.llm_calls += 1
                        emit("record.extracted", {
                            "record_id": rec.id,
                            "fields": rec.fields,
                            "source_url": doc.requested_url,
                        })
            except Exception as e:
                logger.warning(f"Extract error: {e}")

        if records_added > 0:
            state.raw_record_generation += 1

    def _act_verify(self, state: AgentState, emit):
        """Verify unverified records and deterministically qualify job candidates."""
        state.status = AgentStatus.VERIFYING
        from .state import get_unprocessed_records
        unverified = get_unprocessed_records(state)

        if not unverified:
            state.last_verified_generation = state.raw_record_generation
            return

        # Entity-level deduplication BEFORE expensive verification & qualification (Section 11)
        if state.mode in ("jobs", "job") and len(unverified) > 1:
            try:
                dedup_res = self.dedupe.execute(unverified)
                if dedup_res.success:
                    unique_candidates = dedup_res.data.get("unique_records", unverified)
                    unique_cand_ids = {r.id for r in unique_candidates}
                    for r in unverified:
                        if r.id not in unique_cand_ids:
                            r.verification_status = VerificationStatus.DUPLICATE
                            if hasattr(state, "duplicate_records"):
                                state.duplicate_records.append(r)
                            state.processed_record_ids.add(r.id)
                            r_src = r.fields.get("source") or "web"
                            if hasattr(state, "discovery_state") and hasattr(state.discovery_state, "record_task_productivity"):
                                state.discovery_state.record_task_productivity(source=r_src, duplicate_jobs=1)
                    unverified = [r for r in unverified if r.id in unique_cand_ids]
            except Exception as e:
                logger.warning(f"Early deduplication error before verification: {e}")

        emit("status", {"message": f"Verifying {len(unverified)} candidates", "phase": "VERIFYING"})
        geo_name = state.explicit_location
        job_req = state.canonical_job_request

        for rec in unverified:
            state.processed_record_ids.add(rec.id)
            if state.effective_hard_deadline > 0 and time.time() >= state.effective_hard_deadline:
                state.add_warning("Verification stopped: hard deadline reached")
                break

            rec_src = rec.fields.get("source") or "web"
            try:
                if state.mode in ("jobs", "job"):
                    self._recover_candidate_location(rec, state)
                v_res = self.verify.execute(
                    record=rec,
                    required_fields=["title", "company"] if state.mode in ("jobs", "job") else ["name"],
                    contact_policy="business_public_only",
                    geography_rule=geo_name,
                )

                if state.mode in ("jobs", "job") and job_req:
                    q_res = qualify_job(rec, job_req)
                    rec.confidence = q_res.score
                    rec.fields["relevance_score"] = q_res.score
                    rec.fields["match_level"] = "HIGH" if q_res.score >= 0.8 else ("MEDIUM" if q_res.score >= 0.5 else "LOW")
                    rec.fields["match_explanation"] = "; ".join(q_res.reasons)
                    rec.fields["score_breakdown"] = q_res.score_breakdown
                    if q_res.warnings:
                        rec.warnings.extend(q_res.warnings)

                    if q_res.qualified and (rec.verification_status in (VerificationStatus.VERIFIED, VerificationStatus.NEEDS_REVIEW)):
                        state.qualified_records.append(rec)
                        state.verified_records.append(rec)
                        state.add_observation(f"Qualified '{rec.fields.get('title', '?')}': score {q_res.score} - {'; '.join(q_res.reasons)}")
                        emit("record.verified", {
                            "record_id": rec.id,
                            "status": rec.verification_status.value,
                            "confidence": rec.confidence,
                            "fields": rec.fields,
                            "warnings": rec.warnings,
                        })
                        if hasattr(state, "discovery_state") and hasattr(state.discovery_state, "record_task_productivity"):
                            state.discovery_state.record_task_productivity(
                                source=rec_src, verified_jobs=1, qualified_jobs=1
                            )
                    else:
                        rec.verification_status = VerificationStatus.REJECTED
                        rec.confidence = min(q_res.score, 0.2)
                        state.disqualified_records.append(rec)
                        state.rejected_records.append(rec)
                        disqualify_reason = "; ".join(q_res.reasons) if not q_res.qualified else "Verification status rejected"
                        state.add_observation(f"Disqualified '{rec.fields.get('title', '?')}': {disqualify_reason}")

                        reasons_found = False
                        for rz in q_res.rejection_reasons:
                            reasons_found = True
                            if "location" in rz or "unverified_location" in rz:
                                state.rejection_reasons_tally["location_mismatch"] = state.rejection_reasons_tally.get("location_mismatch", 0) + 1
                            elif "title" in rz:
                                state.rejection_reasons_tally["title_mismatch"] = state.rejection_reasons_tally.get("title_mismatch", 0) + 1
                            elif "experience" in rz:
                                state.rejection_reasons_tally["experience_mismatch"] = state.rejection_reasons_tally.get("experience_mismatch", 0) + 1
                            elif "missing_explicit_skill" in rz:
                                state.rejection_reasons_tally["missing_explicit_skill"] = state.rejection_reasons_tally.get("missing_explicit_skill", 0) + 1
                            else:
                                state.rejection_reasons_tally["insufficient_evidence"] = state.rejection_reasons_tally.get("insufficient_evidence", 0) + 1
                        if not reasons_found:
                            state.rejection_reasons_tally["insufficient_evidence"] = state.rejection_reasons_tally.get("insufficient_evidence", 0) + 1

                        if hasattr(state, "discovery_state") and hasattr(state.discovery_state, "record_task_productivity"):
                            state.discovery_state.record_task_productivity(
                                source=rec_src, rejected_jobs=1
                            )
                else:
                    if rec.verification_status in (VerificationStatus.VERIFIED, VerificationStatus.NEEDS_REVIEW):
                        state.verified_records.append(rec)
                        if hasattr(state, "discovery_state") and hasattr(state.discovery_state, "record_task_productivity"):
                            state.discovery_state.record_task_productivity(source=rec_src, verified_jobs=1)
                    else:
                        rec.verification_status = VerificationStatus.REJECTED
                        state.rejected_records.append(rec)
                        state.rejection_reasons_tally["insufficient_evidence"] = state.rejection_reasons_tally.get("insufficient_evidence", 0) + 1
                        if hasattr(state, "discovery_state") and hasattr(state.discovery_state, "record_task_productivity"):
                            state.discovery_state.record_task_productivity(source=rec_src, rejected_jobs=1)

            except Exception as e:
                logger.warning(f"Verify error: {e}")
                rec.verification_status = VerificationStatus.REJECTED
                rec.warnings.append(f"Verification error: {e}")
                state.rejected_records.append(rec)
                state.rejection_reasons_tally["insufficient_evidence"] = state.rejection_reasons_tally.get("insufficient_evidence", 0) + 1
                if hasattr(state, "discovery_state") and hasattr(state.discovery_state, "record_task_productivity"):
                    state.discovery_state.record_task_productivity(
                        source=rec_src, verification_failures=1, rejected_jobs=1
                    )

        state.last_verified_generation = state.raw_record_generation


    def _act_analyze(self, state: AgentState, emit):
        """Run job analysis and ranking with deterministic qualification fallback."""
        state.status = AgentStatus.ANALYZING
        target_records = state.qualified_records if state.qualified_records else (
            [] if state.mode in ("jobs", "job") else state.verified_records
        )
        emit("status", {"message": f"Analyzing {len(target_records)} results", "phase": "ANALYZING"})
        job_req = state.canonical_job_request

        try:
            from datahunt.agents import DataNormalizer, HardFilter, JobAnalysisAgent
            normalizer = DataNormalizer()
            hard_filter = HardFilter()
            analyzer = JobAnalysisAgent(gemini_client=self.client)

            normalized = [normalizer.normalize(r.fields, record_id=r.id, canonical_url=r.canonical_url or "") for r in target_records]
            if job_req:
                passed, rejected = hard_filter.apply(normalized, job_req)
                passed_ids = {p.raw_id for p in passed}
                # Hard filter elimination: rejected jobs must NEVER return to the final list
                target_records[:] = [r for r in target_records if r.id in passed_ids]
                state.qualified_records[:] = [r for r in state.qualified_records if r.id in passed_ids]
                for rej_job, rej_reason in rejected:
                    state.add_observation(f"Hard filter eliminated '{rej_job.title}': {rej_reason}")

                if passed:
                    analyzed = analyzer.analyze_and_rank(passed, job_req, None)
                    scored_map = {m.job.raw_id: m for m in analyzed}
                    for r in target_records:
                        if r.id in scored_map:
                            m = scored_map[r.id]
                            r.fields["relevance_score"] = m.relevance_score
                            r.fields["match_level"] = m.match_level
                            r.fields["match_explanation"] = m.match_explanation
                            r.confidence = m.relevance_score
                    target_records.sort(key=lambda r: -(r.confidence or 0.0))
        except Exception as e:
            logger.warning(f"Analysis LLM error (falling back to deterministic qualification): {e}")
            self._deterministic_qualification(state, emit)

    def _deterministic_qualification(self, state: AgentState, emit):
        """Deterministic qualification fallback when Gemini/LLM analysis is unavailable."""
        job_req = state.canonical_job_request
        if not job_req:
            return

        target_records = state.qualified_records if state.qualified_records else state.verified_records
        for r in target_records:
            q_res = qualify_job(r, job_req)
            r.confidence = q_res.score
            r.fields["relevance_score"] = q_res.score
            r.fields["match_level"] = "HIGH" if q_res.score >= 0.8 else ("MEDIUM" if q_res.score >= 0.5 else "LOW")
            r.fields["match_explanation"] = "; ".join(q_res.reasons)
            r.fields["score_breakdown"] = q_res.score_breakdown
            if q_res.warnings:
                r.warnings.extend(q_res.warnings)

        target_records.sort(key=lambda r: -(r.confidence or 0.0))

    def _finalize(self, state: AgentState, emit) -> Dict[str, Any]:
        """Deduplicate, qualify, export, emit completion."""
        # For jobs mode, export qualified records; for other modes, verified records
        records_to_dedupe = state.qualified_records if state.mode in ("jobs", "job") else state.verified_records
        emit("status", {"message": f"Removing duplicates from {len(records_to_dedupe)} results", "phase": "DEDUPLICATING"})

        if records_to_dedupe:
            try:
                dedup_res = self.dedupe.execute(records_to_dedupe)
                final_records = dedup_res.data.get("unique_records", records_to_dedupe)
                unique_ids = {r.id for r in final_records}
                for r in records_to_dedupe:
                    if r.id not in unique_ids:
                        r.verification_status = VerificationStatus.DUPLICATE
                        if hasattr(state, "duplicate_records"):
                            state.duplicate_records.append(r)
            except Exception as e:
                logger.warning(f"Dedupe error: {e}")
                final_records = records_to_dedupe
        else:
            final_records = []

        # PHASE 10: Final qualification safety gate (Section 21)
        if state.mode in ("jobs", "job") and final_records:
            job_req = state.canonical_job_request
            safe_final = []
            for r in final_records:
                q_res = qualify_job(r, job_req) if job_req else None
                if q_res is None or (q_res.qualified and q_res.eligibility_status == "ELIGIBLE"):
                    safe_final.append(r)
                else:
                    logger.warning(
                        f"Final safety gate eliminated ineligible job '{r.fields.get('title')}': {q_res.rejection_reasons}"
                    )
                    if hasattr(state, "disqualified_records"):
                        state.disqualified_records.append(r)
                    state.add_warning(f"Final safety gate eliminated ineligible job '{r.fields.get('title')}'")
            final_records = safe_final
            if job_req:
                assert all(
                    qualify_job(r, job_req).eligibility_status == "ELIGIBLE"
                    for r in final_records
                ), "Final safety gate violated: ineligible job escaped to output"
            final_records.sort(key=lambda r: -(getattr(r, "confidence", 0.0) or 0.0))

        # Coverage evaluation and honest summary reporting
        coverage_report_dict = None
        coverage_summary = ""
        job_matches_markdown = ""
        if state.mode in ("jobs", "job"):
            try:
                from datahunt.agent.coverage import CoverageEvaluator, format_job_matches_markdown
                from datahunt.models.job_spec import JobSearchSpec
                evaluator = CoverageEvaluator()
                spec = state.canonical_job_request if state.canonical_job_request else JobSearchSpec(raw_query=state.request)
                cov_report = evaluator.evaluate(state, spec)
                coverage_report_dict = cov_report.model_dump()
                coverage_summary = evaluator.format_summary(cov_report, spec)
                job_matches_markdown = format_job_matches_markdown(final_records, spec, cov_report)
                emit("coverage_report", coverage_report_dict)
                emit("status", {
                    "message": f"Discovery complete: {cov_report.qualified_jobs_count} qualified jobs from {cov_report.sources_successful_count} sources",
                    "phase": "COMPLETED"
                })
            except Exception as ce:
                logger.warning(f"Error evaluating coverage report: {ce}")
        else:
            emit("status", {"message": f"Found {len(final_records)} qualified results", "phase": "COMPLETED"})

        result_status = "completed" if len(final_records) >= state.target_results else ("partial" if final_records else "failed")
        if coverage_report_dict and "run_status" in coverage_report_dict:
            result_status = coverage_report_dict["run_status"]

        # Transform to canonical JobRecord list if in jobs mode
        canonical_job_records = []
        if state.mode in ("jobs", "job"):
            from datahunt.models.job_record import JobRecord
            from datahunt.models.evidence import Evidence
            import uuid
            for r in final_records:
                f = getattr(r, "fields", {}) or {}
                ev_list = []
                for ev in getattr(r, "evidence", []) or []:
                    if isinstance(ev, Evidence):
                        ev_list.append(ev)
                    elif isinstance(ev, dict):
                        ev_list.append(Evidence(
                            claim=ev.get("claim") or ev.get("snippet", ""),
                            source_url=ev.get("source_url") or ev.get("url", ""),
                            source_title=ev.get("source_title", ""),
                            source_type=ev.get("source_type", "web_page"),
                            confidence=float(ev.get("confidence", 0.9)),
                        ))
                src_url = (
                    f.get("job_url")
                    or f.get("apply_url")
                    or getattr(r, "canonical_url", None)
                    or f"https://job.datahunt.internal/{getattr(r, 'id', 'listing')}"
                )
                canonical_u = getattr(r, "canonical_url", None) or src_url or ""

                if not ev_list and src_url:
                    ev_list.append(Evidence(
                        claim=f"{f.get('title', 'Job')} at {f.get('company', 'Company')} ({f.get('location', 'Location')})",
                        source_url=src_url,
                        source_title=f.get("source", "Job Posting") or "Job Posting",
                        source_type="job_board",
                        confidence=float(getattr(r, "confidence", 0.85) or 0.85),
                    ))

                conf = getattr(r, "confidence", 0.0) or 0.0
                m_score = round(conf * 100.0 if conf <= 1.0 else conf, 1)

                canonical_job_records.append(JobRecord(
                    id=getattr(r, "id", f"job_{uuid.uuid4().hex[:12]}"),
                    title=f.get("title") or f.get("job_title", "") or "",
                    company=f.get("company", "") or "",
                    location=f.get("location", "") or "",
                    url=src_url,
                    canonical_url=canonical_u,
                    source=f.get("source", "Web"),
                    source_type=f.get("source_type", "job_board"),
                    description=f.get("description"),
                    posted_at=f.get("posted_at"),
                    experience_min=f.get("experience_min"),
                    experience_max=f.get("experience_max"),
                    explicit_skills=f.get("explicit_skills") or [],
                    responsibilities=f.get("responsibilities") or [],
                    salary_min=f.get("salary_min"),
                    salary_max=f.get("salary_max"),
                    salary_currency=f.get("salary_currency"),
                    verification_status=getattr(r, "verification_status", "verified") if isinstance(getattr(r, "verification_status", None), str) else getattr(getattr(r, "verification_status", None), "value", "verified"),
                    qualification_status="QUALIFIED",
                    qualification_reasons=f.get("match_explanation", "").split("; ") if f.get("match_explanation") else [],
                    match_score=m_score,
                    score_breakdown=f.get("score_breakdown") or {},
                    evidence=ev_list,
                    raw_fields=f,
                ))

        diagnostics = {
            "counters": {
                "search_queries": state.search_calls,
                "pages_fetched": state.fetch_calls,
                "candidates": len(state.seen_canonical_urls) or len(state.candidate_urls),
                "jobs_extracted": len(state.raw_records),
                "duplicates": len(getattr(state, "duplicate_records", [])),
                "verified": len(state.verified_records),
                "qualified": len(final_records),
                "rejected": len(state.rejected_records),
            },
            "crawl_telemetry": {
                "sources_discovered": getattr(state, "crawl_sources_discovered", 0),
                "sources_crawled": getattr(state, "crawl_sources_crawled", 0),
                "sources_blocked": getattr(state, "crawl_sources_blocked", 0),
                "pages_fetched": getattr(state, "crawl_pages_fetched", 0),
                "job_links_discovered": getattr(state, "crawl_job_links_discovered", 0),
            },
            "rejection_reasons": {
                "location_mismatch": state.rejection_reasons_tally.get("location_mismatch", 0),
                "title_mismatch": state.rejection_reasons_tally.get("title_mismatch", 0),
                "experience_mismatch": state.rejection_reasons_tally.get("experience_mismatch", 0),
                "missing_explicit_skill": state.rejection_reasons_tally.get("missing_explicit_skill", 0),
                "insufficient_evidence": state.rejection_reasons_tally.get("insufficient_evidence", 0),
            },
            "source_distribution": {
                "direct_ats": state.source_distribution.get("direct_ats", 0),
                "regional_board": state.source_distribution.get("regional_board", 0),
                "company_careers": state.source_distribution.get("company_careers", 0),
                "major_board": state.source_distribution.get("major_board", 0),
            }
        }

        return {
            "status": result_status,
            "records": final_records,
            "job_records": canonical_job_records,
            "canonical_job_records": canonical_job_records,
            "records_verified": len(state.verified_records),
            "records_qualified": len(final_records),
            "records_rejected": len(state.rejected_records),
            "records_duplicate": len(getattr(state, "duplicate_records", [])),
            "search_iterations": len(state.search_iterations),
            "pages_fetched": state.fetch_calls,
            "pages_failed": state.pages_failed,
            "llm_calls": state.llm_calls,
            "completion_reason": state.completion_reason,
            "warnings": state.warnings,
            "observations": state.observations[-20:],
            "actions_taken": state.actions_taken,
            "coverage_report": coverage_report_dict,
            "coverage_summary": coverage_summary,
            "job_matches_markdown": job_matches_markdown,
            "discovery_state": state.discovery_state,
            "discovered_companies_count": len(state.discovery_state.discovered_companies) if state.discovery_state else 0,
            "discovered_ats_count": len(state.discovery_state.discovered_ats) if state.discovery_state else 0,
            "telemetry_logs": [t.__dict__ for t in state.discovery_state.telemetry_logs] if state.discovery_state else [],
            "diagnostics": diagnostics,
        }

    def _extract_location(self, request: str) -> Optional[str]:
        """Deterministic location extraction from request text."""
        import re
        loc_patterns = [
            r'\bin\s+(Saudi Arabia|UAE|United Arab Emirates|Riyadh|Dubai|Abu Dhabi|Qatar|Kuwait|Bahrain|Oman|India|UK|London|US|USA|Pakistan|Egypt)\b',
        ]
        for pattern in loc_patterns:
            m = re.search(pattern, request, re.IGNORECASE)
            if m:
                return m.group(1)

        for keyword in ["Saudi Arabia", "UAE", "Dubai", "Riyadh", "Qatar", "Kuwait", "India", "UK", "USA"]:
            if keyword.lower() in request.lower():
                return keyword
        return None

    def _extract_experience(self, request: str):
        """Deterministic experience extraction."""
        import re
        m = re.search(r'(\d+)\s*[-–to]+\s*(\d+)\s*years?', request, re.IGNORECASE)
        if m:
            return int(m.group(1)), int(m.group(2))
        m = re.search(r'(\d+)\+?\s*years?', request, re.IGNORECASE)
        if m:
            return int(m.group(1)), None
        return None, None

    def _build_spec(self, state: AgentState):
        """Build a minimal ResearchSpec for extraction."""
        from datahunt.models.task import ResearchSpec, SourcePolicy, Geography
        fields = ["title", "company", "location", "application_url", "salary", "experience", "description"] \
            if state.mode in ("jobs", "job") else ["name", "description", "source"]
        return ResearchSpec(
            topic=state.request,
            requested_fields=fields,
            max_records=state.target_results * 2,
            geography=Geography(name=state.explicit_location or ""),
            source_policy=SourcePolicy(allowed_domains=[], blocked_domains=[]),
        )
