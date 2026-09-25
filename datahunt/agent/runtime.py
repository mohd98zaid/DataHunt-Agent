"""
AgentRuntime — The primary decision-loop execution engine.

Replaces the 8-phase linear pipeline in agent/orchestrator.py with
an explicit observe→decide→act loop.
"""
import re
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from datahunt.config import settings
from datahunt.errors import DataHuntError, ErrorCode
from datahunt.logger import logger
from datahunt.llm import GeminiClient
from datahunt.tools import SearchTool, FetchTool, ExtractTool, VerifyTool, DedupeTool, ExportTool
from datahunt.tools.search import is_valid_job_url, canonicalize_url, generate_job_fingerprint
from datahunt.models import VerificationStatus

from .state import AgentState, AgentStatus
from .decision import AgentAction, DecisionEngine
from .policies import (
    GoalEvaluator,
    MatchStatus,
    match_location,
    match_experience,
    match_title_relevance,
    qualify_job,
    QualificationResult,
)


class AgentRuntime:
    """
    Observation → Decision → Action loop with explicit state, budgets,
    and deterministic stop conditions.
    """

    def __init__(self, client: GeminiClient, search: SearchTool, fetch: FetchTool,
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

    def run(self, state: AgentState, emit: Callable[[str, Dict], None]) -> Dict[str, Any]:
        """
        Execute the agent loop until a stop condition is reached.
        Returns the final result dict.
        """
        emit("status", {"message": "Understanding search requirements", "phase": "PLANNING"})
        state.status = AgentStatus.PLANNING

        # Initialize canonical job request if in jobs mode
        if state.mode in ("jobs", "job"):
            emit("status", {"message": "Intent: JOB_SEARCH", "phase": "PLANNING"})
            if not state.canonical_job_request:
                try:
                    from datahunt.agents import QueryUnderstandingAgent, JobSearchRequest
                    qu_agent = QueryUnderstandingAgent(gemini_client=self.client)
                    job_req = qu_agent.understand(state.request)
                    if isinstance(job_req, JobSearchRequest):
                        state.canonical_job_request = job_req
                        state.explicit_titles = [job_req.job_title] if job_req.job_title else []
                        state.explicit_skills = job_req.explicit_skills
                        state.inferred_skills = job_req.inferred_skills
                        state.locations = job_req.locations
                        state.location_operator = job_req.location_operator
                        state.explicit_location = job_req.location
                        state.explicit_experience_min = job_req.experience_min
                        state.explicit_experience_max = job_req.experience_max
                        state.remote_allowed = job_req.remote_allowed
                except Exception as e:
                    logger.warning(f"Could not build canonical job request: {e}")

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
            state.discovery_budget = state.discovery_budget or DiscoveryBudget(
                max_search_requests=state.max_search_calls,
                max_fetches=state.max_fetch_calls,
            )
            if state.discovery_state is None:
                target_geos = state.locations or ([state.explicit_location] if state.explicit_location else ["general"])
                state.discovery_state = DiscoveryState(
                    coverage_matrix=SourceCoverageMatrix(target_regions=target_geos)
                )

            initial_tasks = self.discovery_engine.generate_initial_tasks(state.canonical_job_request, state)

            if state.search_plan:
                injected_tasks = []
                for idx, q_item in enumerate(state.search_plan):
                    q_str = q_item.get("query") if isinstance(q_item, dict) else str(q_item)
                    injected_tasks.append(SearchTask(
                        id=f"injected_{idx}",
                        task_type=SearchTaskType.GENERAL_SEARCH,
                        query=q_str,
                        source=q_item.get("source_id", "injected_plan") if isinstance(q_item, dict) else "injected_plan",
                        source_type=DiscoveredSourceType.MAJOR_BOARD,
                        priority=q_item.get("tier", 2) if isinstance(q_item, dict) else 2,
                        depth=0,
                        round=1,
                        reason="Pre-planned search query",
                        location=state.explicit_location,
                    ))
                seen_q = set()
                combined_tasks = []
                for t in initial_tasks + injected_tasks:
                    if t.query not in seen_q:
                        seen_q.add(t.query)
                        combined_tasks.append(t)
                state.discovery_state.task_queue.extend(combined_tasks)
            else:
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

        while True:
            state.iteration += 1
            action, reason = self.decision_engine.decide(state)

            logger.info(f"[Decision #{state.iteration}] {action} — {reason}")
            emit("decision", {"iteration": state.iteration, "action": action, "reason": reason})
            state.record_action(f"iter{state.iteration}:{action}:{reason}")

            if action == AgentAction.STOP:
                state.completion_reason = reason
                break

            elif action == AgentAction.SEARCH or action == AgentAction.EXPAND_SEARCH:
                self._act_search(state, emit)

            elif action == AgentAction.FETCH:
                self._act_fetch(state, emit)

            elif action == AgentAction.VERIFY:
                self._act_verify(state, emit)

            elif action == AgentAction.ANALYZE:
                self._act_analyze(state, emit)

        return self._finalize(state, emit)

    def _build_search_plan(self, state: AgentState, emit):
        """Build a focused tiered search plan from the request."""
        is_job = state.mode in ("jobs", "job")
        if is_job:
            from datahunt.agents import QueryUnderstandingAgent, QueryExpansionAgent, SearchPlannerAgent, JobSearchRequest
            qu_agent = QueryUnderstandingAgent(gemini_client=self.client)
            qe_agent = QueryExpansionAgent(gemini_client=self.client)
            sp_agent = SearchPlannerAgent()

            if not state.canonical_job_request:
                state.canonical_job_request = qu_agent.understand(state.request)

            job_req = state.canonical_job_request if isinstance(state.canonical_job_request, JobSearchRequest) else qu_agent.understand(state.request)

            # 1. Select registered sources matching job spec
            from datahunt.sources.models import SourceRunResult, SourceStatus
            from datahunt.sources.adapters import get_adapter_for_source
            sources = self.source_registry.get_sources_for_spec(job_req)

            for s in sources:
                if s.id not in state.source_run_results:
                    state.source_run_results[s.id] = SourceRunResult(
                        source_id=s.id,
                        source_name=s.name,
                        source_type=s.source_type.value,
                        status=SourceStatus.PENDING,
                    )

            emit("status", {
                "message": f"Sources selected: {len(sources)} across ATS, Regional, Major, Tech, and Remote boards",
                "phase": "PLANNING"
            })

            # 2. Build adapter-targeted queries for all selected sources
            source_queries = []
            for s in sources:
                adapter = get_adapter_for_source(s)
                for sq in adapter.build_search_queries(job_req):
                    source_queries.append({
                        "query": sq,
                        "tier": s.priority,
                        "purpose": f"{s.source_type.value}:{s.name}",
                        "source_id": s.id
                    })

            # 3. Augment with search planner queries
            expanded = qe_agent.expand(job_req)
            tasks = sp_agent.plan(job_req, expanded)

            seen_q = set()
            combined_plan = []
            for q_obj in source_queries:
                q_txt = q_obj["query"].strip()
                if q_txt not in seen_q:
                    seen_q.add(q_txt)
                    combined_plan.append(q_obj)

            for t in tasks:
                q_txt = t.query.strip()
                if q_txt not in seen_q:
                    seen_q.add(q_txt)
                    combined_plan.append({"query": t.query, "tier": t.priority, "purpose": t.purpose})

            plan = combined_plan if combined_plan else [{"query": state.request, "tier": 1, "purpose": "primary"}]
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

    def _is_promising_candidate(self, hit: Dict[str, Any], state: AgentState) -> bool:
        """Lightweight pre-fetch triage to reject obvious junk before network requests."""
        url = (hit.get("url") or "").lower()
        title = (hit.get("title") or "").lower()
        snippet = (hit.get("snippet") or "").lower()
        text = f"{url} {title} {snippet}"

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
                if any(dr in title for dr in DISQUALIFYING_ROLES) and not any(ai in title for ai in ("genai", "generative ai", "llm", "ai engineer")):
                    return False

            # If user requested specific regions, e.g. Saudi/UAE, reject explicit foreign anchors
            if state.locations:
                req_geos = [loc.lower() for loc in state.locations]
                is_gulf = any(g in ("saudi", "saudi arabia", "uae", "dubai", "riyadh") for g in req_geos)
                if is_gulf:
                    FOREIGN_ANCHORS = ["cairo, egypt", "alexandria, egypt", "london, uk", "london, united kingdom", "bangalore, india", "denver, co", "san francisco, ca"]
                    if any(fa in text for fa in FOREIGN_ANCHORS) and not any(rg in text for rg in ("saudi", "uae", "dubai", "riyadh", "worldwide", "global remote")):
                        return False

        return True

    def _act_search(self, state: AgentState, emit):
        """Execute the next batch of searches with canonical URL deduplication and dynamic discovery."""
        state.status = AgentStatus.SEARCHING

        # Branch 1: Dynamic Discovery Queue
        if state.discovery_state and state.discovery_state.task_queue:
            from .discovery_models import DiscoveryRoundTelemetry
            batch_tasks = []
            while state.discovery_state.task_queue and len(batch_tasks) < 4:
                batch_tasks.append(state.discovery_state.task_queue.pop(0))

            for task in batch_tasks:
                if state.search_calls >= state.max_search_calls:
                    break
                if state.deadline > 0 and time.time() >= state.deadline:
                    break

                t_start = time.time()
                query = task.query
                page = getattr(task, "page", 1)
                emit("status", {"message": f"Searching [R{task.round}]: {query[:60]} (page {page})", "phase": "SEARCHING"})

                try:
                    res = self.search.execute(query=query, limit=15, page=page)
                    state.search_calls += 1
                    new_candidates = 0
                    initial_companies = len(state.discovery_state.discovered_companies)
                    initial_ats = len(state.discovery_state.discovered_ats)

                    if res.success and res.data:
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

                            detected_source = self.source_registry.identify_source_for_url(url)
                            hit_source_id = detected_source.id if detected_source else task.source
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

                        # Dynamic discovery: analyze hits to discover new companies, ATS platforms, and boards
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

                    # LangSmith and telemetry logging
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

                    self.decision_engine.record_search_iteration(
                        state, query, len(res.data) if res.success else 0, new_candidates
                    )
                    state.add_observation(
                        f"Search [R{task.round}] '{query[:35]}': {new_candidates} new candidates, {new_sources_discovered} new sources discovered"
                    )

                except Exception as e:
                    logger.warning(f"Discovery search error for '{query}': {e}")
                    state.add_warning(f"Search failed: {query[:40]}")

            # If task queue is empty for current round, advance to next discovery round if budget permits
            if not state.discovery_state.task_queue and state.discovery_state.current_round < state.discovery_budget.max_expansion_rounds:
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
                    state.discovery_state.task_queue.extend(next_tasks)
                    logger.info(f"Advanced to Discovery Round {state.discovery_state.current_round} with {len(next_tasks)} new tasks")
                    emit("status", {
                        "message": f"Advancing to Discovery Round {state.discovery_state.current_round} ({len(next_tasks)} tasks)",
                        "phase": "SEARCHING"
                    })
            return

        # Branch 2: Standard Search Plan
        batch_size = min(4, len(state.search_plan) - state.search_plan_index)
        if batch_size <= 0:
            return

        queries_batch = state.search_plan[state.search_plan_index:state.search_plan_index + batch_size]
        state.search_plan_index += batch_size

        for q_obj in queries_batch:
            if state.search_calls >= state.max_search_calls:
                break
            if state.deadline > 0 and time.time() >= state.deadline:
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

        # Prioritize locations if stated
        if state.locations:
            loc_tokens = [loc.lower() for loc in state.locations]
            promising.sort(key=lambda h: (
                0 if any(t in f"{h.get('url','')} {h.get('title','')} {h.get('snippet','')}".lower() for t in loc_tokens) else 1
            ))

        batch = promising[:10]
        # Retain remaining candidates
        state.candidate_urls = promising[10:]

        if not batch:
            self._act_extract(state, emit)
            return

        from concurrent.futures import ThreadPoolExecutor, as_completed
        emit("status", {"message": f"Fetching {len(batch)} candidate pages", "phase": "FETCHING"})

        with ThreadPoolExecutor(max_workers=6) as executor:
            futures = {executor.submit(self.fetch.execute, url=h.get("url"), run_id=state.run_id): h for h in batch}
            for future in as_completed(futures):
                if state.deadline > 0 and time.time() >= state.deadline:
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
        unprocessed = [d for d in state.fetched_docs
                       if not any(getattr(r, 'source_doc_id', None) == d.id for r in state.raw_records)]
        if not unprocessed:
            return

        from datahunt.models.task import ResearchSpec
        spec = self._build_spec(state)

        for doc in unprocessed:
            if state.deadline > 0 and time.time() >= state.deadline:
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
                        state.llm_calls += 1
                        emit("record.extracted", {
                            "record_id": rec.id,
                            "fields": rec.fields,
                            "source_url": doc.requested_url,
                        })
            except Exception as e:
                logger.warning(f"Extract error: {e}")

    def _act_verify(self, state: AgentState, emit):
        """Verify unverified records and deterministically qualify job candidates."""
        state.status = AgentStatus.VERIFYING
        already_processed = (
            set(r.id for r in state.verified_records)
            | set(r.id for r in state.rejected_records)
            | set(r.id for r in state.qualified_records)
            | set(r.id for r in state.disqualified_records)
        )
        unverified = [r for r in state.raw_records if r.id not in already_processed]

        if not unverified:
            return

        emit("status", {"message": f"Verifying {len(unverified)} candidates", "phase": "VERIFYING"})
        geo_name = state.explicit_location

        # Ensure canonical job request exists for jobs mode
        job_req = state.canonical_job_request
        if state.mode in ("jobs", "job") and not job_req:
            try:
                from datahunt.agents.query_understanding import JobSearchRequest
                job_req = JobSearchRequest(
                    raw_query=state.request,
                    job_title=state.explicit_titles[0] if state.explicit_titles else "",
                    locations=state.locations or ([state.explicit_location] if state.explicit_location else []),
                    location_operator=state.location_operator or "OR",
                    experience_min=state.explicit_experience_min,
                    experience_max=state.explicit_experience_max,
                    skills=state.explicit_skills,
                    inferred_skills=state.inferred_skills,
                    remote_allowed=state.remote_allowed if state.remote_allowed is not None else True,
                )
                state.canonical_job_request = job_req
            except Exception as e:
                logger.warning(f"Error creating fallback JobSearchRequest: {e}")

        for rec in unverified:
            if state.deadline > 0 and time.time() >= state.deadline:
                state.add_warning("Verification stopped: deadline reached")
                break

            try:
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
                    else:
                        state.disqualified_records.append(rec)
                        state.rejected_records.append(rec)
                        disqualify_reason = "; ".join(q_res.reasons) if not q_res.qualified else "Verification status rejected"
                        state.add_observation(f"Disqualified '{rec.fields.get('title', '?')}': {disqualify_reason}")
                else:
                    if rec.verification_status in (VerificationStatus.VERIFIED, VerificationStatus.NEEDS_REVIEW):
                        state.verified_records.append(rec)
                    else:
                        state.rejected_records.append(rec)

            except Exception as e:
                logger.warning(f"Verify error: {e}")
                rec.warnings.append(f"Verification error: {e}")
                state.rejected_records.append(rec)

    def _act_analyze(self, state: AgentState, emit):
        """Run job analysis and ranking with deterministic qualification fallback."""
        state.status = AgentStatus.ANALYZING
        target_records = state.qualified_records if state.qualified_records else state.verified_records
        emit("status", {"message": f"Analyzing {len(target_records)} results", "phase": "ANALYZING"})

        job_req = state.canonical_job_request
        if not job_req and state.mode in ("jobs", "job"):
            try:
                from datahunt.agents.query_understanding import JobSearchRequest
                job_req = JobSearchRequest(
                    raw_query=state.request,
                    job_title=state.explicit_titles[0] if state.explicit_titles else "",
                    locations=state.locations or ([state.explicit_location] if state.explicit_location else []),
                    location_operator=state.location_operator or "OR",
                    experience_min=state.explicit_experience_min,
                    experience_max=state.explicit_experience_max,
                    skills=state.explicit_skills,
                    inferred_skills=state.inferred_skills,
                    remote_allowed=state.remote_allowed if state.remote_allowed is not None else True,
                )
                state.canonical_job_request = job_req
            except Exception as e:
                logger.warning(f"Error building JobSearchRequest for analysis: {e}")

        try:
            from datahunt.agents import DataNormalizer, HardFilter, JobAnalysisAgent
            normalizer = DataNormalizer()
            hard_filter = HardFilter()
            analyzer = JobAnalysisAgent(gemini_client=self.client)

            normalized = [normalizer.normalize(r.fields, record_id=r.id, canonical_url=r.canonical_url or "") for r in target_records]
            if job_req:
                passed, _ = hard_filter.apply(normalized, job_req)
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
            except Exception as e:
                logger.warning(f"Dedupe error: {e}")
                final_records = records_to_dedupe
        else:
            final_records = []

        if state.mode in ("jobs", "job") and final_records:
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

        return {
            "status": result_status,
            "records": final_records,
            "records_verified": len(state.verified_records),
            "records_qualified": len(final_records),
            "records_rejected": len(state.rejected_records),
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
