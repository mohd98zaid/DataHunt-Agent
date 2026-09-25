import re
import time

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from datahunt.config import settings
from datahunt.db import (
    TaskRepository, RunRepository, DocumentRepository,
    RecordRepository, ExportRepository, ToolEventRepository
)
from datahunt.errors import DataHuntError, ErrorCode
from datahunt.logger import logger
from datahunt.models import (
    ResearchTask, ResearchRun, ResearchSpec, RunStatus,
    RunBudget, RunCounters, TaskStatus, VerificationStatus,
    ExportRecord
)
from datahunt.models.intent import ResearchIntent, ResearchOutputType
from datahunt.llm import GeminiClient
from datahunt.tools import (
    SearchTool, FetchTool, ExtractTool,
    VerifyTool, DedupeTool, ExportTool,
    generate_ats_queries
)
from datahunt.tracing import traceable

class ResearchOrchestrator:
    """
    Deterministic state machine managing research task execution,
    tool authorization, budget enforcement, and provenance tracking.
    """
    def __init__(
        self,
        gemini_client: Optional[GeminiClient] = None,
        search_tool: Optional[SearchTool] = None,
        fetch_tool: Optional[FetchTool] = None,
        extract_tool: Optional[ExtractTool] = None,
        verify_tool: Optional[VerifyTool] = None,
        dedupe_tool: Optional[DedupeTool] = None,
        export_tool: Optional[ExportTool] = None,
        model: Optional[str] = None,
    ):
        self.client = gemini_client or GeminiClient(model=model)
        self.search_tool = search_tool or SearchTool()
        self.fetch_tool = fetch_tool or FetchTool()
        self.extract_tool = extract_tool or ExtractTool(self.client)
        self.verify_tool = verify_tool or VerifyTool(self.client)
        self.dedupe_tool = dedupe_tool or DedupeTool()
        self.export_tool = export_tool or ExportTool()

        self.task_repo = TaskRepository()
        self.run_repo = RunRepository()
        self.doc_repo = DocumentRepository()
        self.record_repo = RecordRepository()
        self.export_repo = ExportRepository()
        self.tool_repo = ToolEventRepository()

    def create_task_and_run(
        self,
        request_text: str,
        max_records: int = 50,
        freshness_days: Optional[int] = 7,
        output_format: str = "json",
        contact_policy: str = "business_public_only",
        allowed_domains: Optional[List[str]] = None,
        blocked_domains: Optional[List[str]] = None,
        budget: Optional[RunBudget] = None,
        model: Optional[str] = None,
        agent_mode: Optional[str] = "auto",
    ) -> tuple[ResearchTask, ResearchRun]:
        """Normalize request, enforce intake policies, and initialize database records."""
        if model:
            run_client = GeminiClient(model=model)
            if hasattr(self.extract_tool, "client"):
                self.extract_tool.client = run_client
            if hasattr(self.verify_tool, "client"):
                self.verify_tool.client = run_client
        else:
            run_client = self.client
        operator_defaults = {
            "max_records": max_records,
            "freshness_days": freshness_days,
            "output_format": output_format,
            "contact_policy": contact_policy,
            "allowed_domains": allowed_domains or [],
            "blocked_domains": blocked_domains or [],
            "agent_mode": agent_mode or "auto",
        }

        spec = run_client.normalize_request(request_text, operator_defaults)

        # For deep research agent, default to Markdown (.md) dossier unless specifically requested otherwise
        effective_format = output_format
        if (agent_mode == "research" or getattr(spec, "agent_mode", None) == "research") and output_format == "json":
            effective_format = "md"

        task = ResearchTask(
            request_text=request_text,
            normalized_spec=spec,
            agent_mode=agent_mode or getattr(spec, "agent_mode", "auto"),
            intent_spec=getattr(spec, "intent_spec", None),
            requested_output_format=effective_format,
            max_records=max_records,
            status=TaskStatus.ACCEPTED
        )
        self.task_repo.create_task(task)

        # Determine if this is a job search for tighter query planning
        _is_job_mode = (
            getattr(spec, "intent_spec", None) is not None
            and spec.intent_spec.intent == ResearchIntent.JOB_SEARCH
            and (agent_mode or "").lower() != "research"
        )
        # For job searches: exhaust all available search-query and page budget to maximise ATS coverage.
        # For research/market: use a generous but bounded formula.
        if _is_job_mode:
            q_budget = settings.MAX_SEARCH_QUERIES          # use every query slot
            p_budget = settings.MAX_PAGES_FETCHED           # fetch every page we can
        else:
            q_budget = min(max(spec.max_records // 4, 5), settings.MAX_SEARCH_QUERIES)
            p_budget = min(max(spec.max_records * 3, 8),  settings.MAX_PAGES_FETCHED)

        run_b = budget or RunBudget(
            deadline_seconds=settings.MAX_RUN_SECONDS,
            max_search_queries=q_budget,
            max_pages=p_budget,
            max_output_records=spec.max_records,
        )

        run = ResearchRun(
            task_id=task.id,
            status=RunStatus.PLANNED,
            budget=run_b,
        )
        self.run_repo.create_run(run)

        logger.info(
            f"Created ResearchTask {task.id} and ResearchRun {run.id} for query: '{request_text}' (mode: {agent_mode})",
            extra={"task_id": task.id, "run_id": run.id}
        )
        return task, run

    @traceable(run_type="chain", name="DataHunt.ResearchRun")
    def execute_run(self, run_id: str, event_callback: Optional[Any] = None) -> Dict[str, Any]:
        """Execute a research run through all state machine transitions with real-time event broadcasting."""
        def emit_event(event_type: str, data: Dict[str, Any]):
            if event_callback:
                try:
                    event_callback(event_type, data)
                except Exception as ex:
                    logger.warning(f"Event callback error: {ex}")

        run = self.run_repo.get_run(run_id)
        if not run:
            raise DataHuntError(ErrorCode.INVALID_REQUEST, f"Run {run_id} not found.")

        task = self.task_repo.get_task(run.task_id)
        if not task:
            raise DataHuntError(ErrorCode.INVALID_REQUEST, f"Task {run.task_id} not found.")

        start_time = time.time()
        deadline = start_time + run.budget.deadline_seconds
        step_count = 0
        warnings = []
        counters = run.counters

        run.started_at = datetime.now(timezone.utc).isoformat()
        self.run_repo.update_run_status(run.id, RunStatus.PLANNING if hasattr(RunStatus, "PLANNING") else RunStatus.PLANNED)

        emit_event("run.started", {
            "task_id": task.id,
            "run_id": run.id,
            "query": task.request_text,
            "max_records": task.max_records,
            "status": "PLANNING"
        })

        model_mode = getattr(self.client, "mode", "auto")
        model_name = getattr(self.client, "model", "gemini-3.8-flash")
        model_label = f"Gemini ({model_name})"
        emit_event("model.active", {
            "model": model_name,
            "mode": model_mode,
            "label": model_label
        })

        try:
            # ----------------------------------------------------
            # 1. Planning Phase
            # ----------------------------------------------------
            logger.info("Starting planning phase", extra={"run_id": run.id, "event": "phase.planning"})
            emit_event("phase.change", {
                "phase": "PLANNING",
                "message": "Analyzing query and planning search...",
                "thought": f"Normalizing research objective: '{task.request_text}'",
                "counters": counters.__dict__
            })
            
            # Determine canonical intent for this run
            task_mode = getattr(task, "agent_mode", None) or getattr(task.normalized_spec, "agent_mode", None) or "auto"
            intent_spec = getattr(task, "intent_spec", None) or getattr(task.normalized_spec, "intent_spec", None)
            if not intent_spec:
                from datahunt.agents.intent_router import IntentRouter
                intent_spec = IntentRouter(gemini_client=self.client).classify(task.request_text, task_mode)
                task.intent_spec = intent_spec
                task.normalized_spec.intent_spec = intent_spec

            is_job_search = (intent_spec.intent == ResearchIntent.JOB_SEARCH) and (task_mode != "research")
            is_zero_sec = is_job_search and (task_mode == "jobs" or any(k in task.request_text.lower() for k in ("0sec", "0-sec", "latest", "recent", "fresh", "today", "just now", "newest")))

            plan = self.client.plan_research(task.normalized_spec, run.budget)
            queries = plan.get("queries", [])

            # Augment with direct ATS harvesting targets when doing job research, or technical specs when doing research, or market intelligence
            if is_job_search:
                from datahunt.agents import QueryUnderstandingAgent, QueryExpansionAgent, SearchPlannerAgent, JobSearchRequest
                qu_agent = QueryUnderstandingAgent(gemini_client=self.client)
                qe_agent = QueryExpansionAgent(gemini_client=self.client)
                sp_agent = SearchPlannerAgent()

                job_req = qu_agent.understand(task.request_text)
                if isinstance(job_req, JobSearchRequest):
                    emit_event("query.understood", {
                        "job_title": job_req.job_title,
                        "location": job_req.location,
                        "remote_status": job_req.remote_status,
                        "experience_min": job_req.experience_min,
                        "salary_min": job_req.salary_min,
                        "skills": job_req.skills
                    })
                    expanded = qe_agent.expand(job_req)
                    emit_event("query.expanded", {
                        "all_titles": expanded.all_titles,
                        "must_have_skills": expanded.must_have_skills,
                        "search_keywords": expanded.search_keywords
                    })
                    planned_tasks = sp_agent.plan(job_req, expanded)
                    queries = [{"query": t.query, "purpose": t.purpose, "source_type": t.source_type, "freshness_days": t.freshness_days} for t in planned_tasks]
                else:
                    from datahunt.tools.search import generate_ats_queries, generate_broad_job_queries
                    ats_queries = [{"query": q, "purpose": "direct_ats_harvest"} for q in generate_ats_queries(task.request_text)]
                    broad_queries = [{"query": q, "purpose": "broad_internet_sweep"} for q in generate_broad_job_queries(task.request_text)]
                    queries = ats_queries[:4] + broad_queries[:6] + queries + ats_queries[4:] + broad_queries[6:]

            elif task_mode == "market" or any(k in (task.request_text or "").lower() for k in ("pricing", "competitor", "market", "saas", "vs ", "alternative")):
                from datahunt.tools.search import generate_market_research_queries
                mkt_queries = [{"query": q, "purpose": "market_intelligence"} for q in generate_market_research_queries(task.request_text)]
                queries = mkt_queries[:3] + queries + mkt_queries[3:]
            elif task_mode == "research" or "research" in (task.request_text or "").lower():
                from datahunt.tools.search import generate_technical_research_queries
                tech_queries = [{"query": q, "purpose": "technical_documentation"} for q in generate_technical_research_queries(task.request_text)]
                queries = tech_queries[:3] + queries + tech_queries[3:]

            logger.info(f"Planned {len(queries)} search queries (ATS augmented: {is_job_search})", extra={"run_id": run.id})
            emit_event("plan.ready", {
                "queries": queries,
                "count": len(queries),
                "is_job_search": is_job_search,
                "is_zero_sec": is_zero_sec,
                "thought": f"Formulated {len(queries)} search queries for ATS sources." if is_job_search else f"Generated {len(queries)} search queries.",
            })

            # ----------------------------------------------------
            # NEW: Job-mode runs use the AgentRuntime decision loop.
            # Research/market modes continue with the existing linear pipeline.
            # ----------------------------------------------------
            fetched_docs = []
            verified_records = []
            combined_source_texts = ""
            _use_runtime = is_job_search
            _result = {}

            if _use_runtime:
                from datahunt.agent import AgentRuntime, AgentState

                canonical_kwargs = {}
                if 'job_req' in locals() and isinstance(job_req, JobSearchRequest):
                    canonical_kwargs = {
                        "canonical_job_request": job_req,
                        "locations": job_req.locations or ([job_req.location] if job_req.location else []),
                        "location_operator": job_req.location_operator or "OR",
                        "remote_allowed": job_req.remote_allowed if job_req.remote_allowed is not None else True,
                        "explicit_location": job_req.location or "",
                        "explicit_titles": [job_req.job_title] if job_req.job_title else [],
                        "explicit_experience_min": job_req.experience_min,
                        "explicit_experience_max": job_req.experience_max,
                        "explicit_skills": job_req.explicit_skills or job_req.skills or [],
                        "inferred_skills": job_req.inferred_skills or [],
                    }

                from datahunt.agent.discovery_models import DiscoveryBudget
                discovery_budget = DiscoveryBudget(
                    max_runtime_seconds=settings.MAX_RUN_SECONDS,
                    max_search_requests=run.budget.max_search_queries,
                    max_fetches=run.budget.max_pages,
                    max_expansion_rounds=6,
                )

                agent_state = AgentState(
                    request=task.request_text,
                    run_id=run.id,
                    task_id=task.id,
                    mode="jobs",
                    target_results=task.max_records,
                    max_search_calls=run.budget.max_search_queries,
                    max_fetch_calls=run.budget.max_pages,
                    deadline=deadline,
                    max_iterations=20,
                    discovery_budget=discovery_budget,
                    **canonical_kwargs,
                )
                # Inject the pre-computed search plan so we don't re-plan
                agent_state.search_plan = [
                    {
                        "query": q.get("query") if isinstance(q, dict) else str(q),
                        "tier": q.get("priority", 1 if (isinstance(q, dict) and q.get("purpose") == "direct_ats_harvest") else 2) if isinstance(q, dict) else 2,
                        "purpose": q.get("purpose", "general") if isinstance(q, dict) else "general",
                    }
                    for q in queries
                ]
                # search_plan_index starts at 0 — the decision loop iterates through all queries

                def _runtime_emit(event_type: str, data: dict):
                    if event_type == "status":
                        emit_event("phase.change", {
                            "phase": data.get("phase", "RUNNING"),
                            "message": data.get("message", ""),
                            "thought": "",
                            "counters": counters.__dict__,
                        })
                    elif event_type == "decision":
                        emit_event("agent.decision", {
                            "iteration": data.get("iteration"),
                            "action": data.get("action"),
                            "reason": data.get("reason"),
                        })
                    elif event_type in ("record.extracted", "record.verified"):
                        data["counters"] = counters.__dict__
                        emit_event(event_type, data)
                    else:
                        emit_event(event_type, data)

                _runtime = AgentRuntime(
                    client=self.client,
                    search=self.search_tool,
                    fetch=self.fetch_tool,
                    extract=self.extract_tool,
                    verify=self.verify_tool,
                    dedupe=self.dedupe_tool,
                    export=self.export_tool,
                )
                _result = _runtime.run(agent_state, _runtime_emit)

                # Bridge runtime results into synthesis variables
                verified_records = _result.get("records", [])
                final_records = verified_records
                fetched_docs = agent_state.fetched_docs
                warnings.extend(_result.get("warnings", []))
                counters.pages_fetched = _result.get("pages_fetched", 0)
                counters.pages_failed = _result.get("pages_failed", 0)
                counters.records_verified = _result.get("records_verified", 0)
                counters.records_rejected = _result.get("records_rejected", 0)
                counters.search_queries = _result.get("search_iterations", 0)

                # Persist fetched documents to SQLite database
                for doc in fetched_docs:
                    try:
                        self.doc_repo.upsert_document(doc)
                    except Exception as de:
                        logger.warning(f"Error persisting source document {doc.id}: {de}")

                # Persist all extracted and qualified records to SQLite database
                persisted_record_ids = set()
                for rec in (agent_state.raw_records or []):
                    try:
                        self.record_repo.insert_record(rec)
                        persisted_record_ids.add(rec.id)
                    except Exception as re:
                        logger.warning(f"Error inserting raw record {rec.id}: {re}")

                for rec in final_records:
                    if rec.id not in persisted_record_ids:
                        try:
                            self.record_repo.insert_record(rec)
                            persisted_record_ids.add(rec.id)
                        except Exception as re:
                            logger.warning(f"Error inserting final record {rec.id}: {re}")
                    try:
                        self.record_repo.update_record_status(rec.id, rec.verification_status, rec.confidence)
                    except Exception as ue:
                        logger.warning(f"Error updating record status {rec.id}: {ue}")

                counters.records_extracted = len(persisted_record_ids)
                counters.records_verified = len(final_records)

                # Emit verified events so the web cockpit and Kanban receive records live
                for rec in final_records:
                    emit_event("record.verified", {
                        "record_id": rec.id,
                        "status": rec.verification_status.value,
                        "confidence": rec.confidence,
                        "fields": rec.fields,
                        "warnings": rec.warnings,
                        "counters": counters.__dict__,
                    })

                for doc in fetched_docs[:8]:
                    snippet = (doc.extracted_text or "").strip()
                    if snippet:
                        combined_source_texts += f"Source URL: {doc.requested_url}\n{snippet[:2000]}\n\n---\n\n"

                logger.info(
                    f"AgentRuntime: {len(final_records)} verified records "
                    f"(reason: {_result.get('completion_reason', 'unknown')})",
                    extra={"run_id": run.id},
                )

            # ----------------------------------------------------
            # 2. Searching Phase (skipped for job-mode — AgentRuntime handles it)
            # ----------------------------------------------------
            if not _use_runtime:
                self.run_repo.update_run_status(run.id, RunStatus.SEARCHING)
                emit_event("phase.change", {
                    "phase": "SEARCHING",
                    "message": "Running search queries",
                    "thought": "Running search queries",
                    "counters": counters.__dict__
                })
                candidate_urls = []
                seen_urls = set()

                spec_freshness = getattr(task.normalized_spec, "freshness_days", None)
                base_freshness = spec_freshness

                blocked_domains = list(task.normalized_spec.source_policy.blocked_domains or [])
                from datahunt.tools.search import LOW_QUALITY_RESEARCH_DOMAINS
                for bad_d in LOW_QUALITY_RESEARCH_DOMAINS:
                    if bad_d not in blocked_domains:
                        blocked_domains.append(bad_d)

                queries_to_run = queries[:run.budget.max_search_queries]

                def _execute_single_query(q_obj):
                    q_text = q_obj.get("query") if isinstance(q_obj, dict) else str(q_obj)
                    t_start = time.time()
                    res = self.search_tool.execute(
                        query=q_text,
                        limit=15,
                        freshness_days=spec_freshness,
                        allowed_domains=task.normalized_spec.source_policy.allowed_domains,
                        blocked_domains=blocked_domains,
                    )
                    t_dur = int((time.time() - t_start) * 1000)
                    return q_obj, q_text, res, t_dur

                from concurrent.futures import ThreadPoolExecutor, as_completed
                with ThreadPoolExecutor(max_workers=5) as search_executor:
                    future_to_q = {search_executor.submit(_execute_single_query, q): q for q in queries_to_run}
                    for future in as_completed(future_to_q):
                        if time.time() > deadline:
                            warnings.append("Search stopped early: deadline reached.")
                            for f in future_to_q:
                                f.cancel()
                            break
                        try:
                            q_obj, query_text, search_res, dur = future.result()
                        except Exception as se:
                            logger.warning(f"Concurrent search worker exception: {se}")
                            continue

                        step_count += 1
                        evt = self.tool_repo.record_start(run.id, step_count, "search_web", {"query": query_text})

                        if search_res.success:
                            self.tool_repo.record_finish(evt.id, evt.status.SUCCEEDED, {"count": len(search_res.data)}, dur)
                            counters.search_queries += 1
                            emit_event("search.result", {
                                "query": query_text,
                                "results_count": len(search_res.data),
                                "duration_ms": dur,
                                "hits": [{"title": h.get("title"), "url": h.get("url")} for h in search_res.data[:3]],
                                "counters": counters.__dict__
                            })
                            added_from_query = 0
                            for hit in search_res.data:
                                u = hit.get("url")
                                if u and u not in seen_urls:
                                    seen_urls.add(u)
                                    candidate_urls.append(hit)
                                    added_from_query += 1
                                    if added_from_query >= 10:
                                        break

                            if counters.search_queries >= min(len(queries_to_run), 10) and len(candidate_urls) >= max(run.budget.max_pages, 75):
                                logger.info(f"Target candidate harvest reached ({len(candidate_urls)} URLs from {counters.search_queries} queries), proceeding to fetch phase.")
                                break
                        else:
                            self.tool_repo.record_finish(evt.id, evt.status.FAILED, {}, dur, search_res.error_code)

                # ----------------------------------------------------
                # 3. Collecting (Fetching) Phase
                # ----------------------------------------------------
                self.run_repo.update_run_status(run.id, RunStatus.COLLECTING)

                geo_loc = ""
                if getattr(task, "normalized_spec", None) and getattr(task.normalized_spec, "geography", None):
                    geo_loc = task.normalized_spec.geography.name or task.normalized_spec.geography.country or ""
                if not geo_loc and getattr(task, "request_text", None):
                    geo_loc = task.request_text

                target_pages = min(run.budget.max_pages, 25)
                pages_to_fetch = candidate_urls[:target_pages]

                emit_event("phase.change", {
                    "phase": "COLLECTING",
                    "message": f"Fetching candidate pages ({len(pages_to_fetch)} targets)",
                    "thought": "Fetching candidate pages",
                    "counters": counters.__dict__
                })

                def _do_fetch(hit_item):
                    t_url = hit_item.get("url")
                    t_start = time.time()
                    res = self.fetch_tool.execute(
                        url=t_url,
                        run_id=run.id,
                        allowed_domains=task.normalized_spec.source_policy.allowed_domains,
                        blocked_domains=task.normalized_spec.source_policy.blocked_domains,
                    )
                    t_dur = int((time.time() - t_start) * 1000)
                    return t_url, res, t_dur

                with ThreadPoolExecutor(max_workers=8) as executor:
                    future_map = {executor.submit(_do_fetch, hit): hit for hit in pages_to_fetch}
                    for future in as_completed(future_map):
                        if time.time() > deadline:
                            warnings.append("Fetching stopped early: deadline reached.")
                            for f in future_map:
                                f.cancel()
                            break
                        try:
                            target_url, fetch_res, dur = future.result()
                        except Exception as fe:
                            logger.warning(f"Concurrent fetch error on target: {fe}")
                            continue

                        step_count += 1
                        evt = self.tool_repo.record_start(run.id, step_count, "fetch_page", {"url": target_url})

                        if fetch_res.success and fetch_res.data:
                            doc = fetch_res.data
                            self.doc_repo.upsert_document(doc)
                            fetched_docs.append(doc)
                            counters.pages_fetched += 1
                            self.tool_repo.record_finish(evt.id, evt.status.SUCCEEDED, {"doc_id": doc.id, "status_code": doc.http_status}, dur)
                            emit_event("page.fetched", {
                                "url": target_url,
                                "status_code": doc.http_status,
                                "size_bytes": len(doc.extracted_text or ""),
                                "duration_ms": dur,
                                "counters": counters.__dict__
                            })
                        else:
                            counters.pages_failed += 1
                            if fetch_res.error_code == ErrorCode.FETCH_BLOCKED.value:
                                counters.pages_blocked += 1
                            self.tool_repo.record_finish(evt.id, evt.status.BLOCKED if fetch_res.error_code == ErrorCode.FETCH_BLOCKED.value else evt.status.FAILED, {}, dur, fetch_res.error_code)
                            emit_event("page.failed", {
                                "url": target_url,
                                "error_code": fetch_res.error_code,
                                "counters": counters.__dict__
                            })

                # ----------------------------------------------------
                # 4. Extracting Phase
                # ----------------------------------------------------
                self.run_repo.update_run_status(run.id, RunStatus.EXTRACTING)
                emit_event("phase.change", {
                    "phase": "EXTRACTING",
                    "message": f"Extracting records from {len(fetched_docs)} documents...",
                    "thought": "Extracting structured fields from documents.",
                    "counters": counters.__dict__
                })
                raw_records = []
                target_harvest_count = max(run.budget.max_output_records * 2, 20)

                for doc in fetched_docs:
                    if time.time() > deadline:
                        warnings.append("Extraction stopped early: deadline reached.")
                        break
                    if len(raw_records) >= target_harvest_count:
                        logger.info(f"Target record harvest reached ({len(raw_records)} records), proceeding to verification.")
                        break

                    step_count += 1
                    evt = self.tool_repo.record_start(run.id, step_count, "extract_records", {"doc_id": doc.id})
                    t0 = time.time()
                    extract_res = self.extract_tool.execute(doc, task.normalized_spec, run.id)
                    dur = int((time.time() - t0) * 1000)

                    if extract_res.success:
                        self.tool_repo.record_finish(evt.id, evt.status.SUCCEEDED, {"extracted": len(extract_res.data)}, dur)
                        for rec in extract_res.data:
                            self.record_repo.insert_record(rec)
                            raw_records.append(rec)
                            counters.records_extracted += 1
                            emit_event("record.extracted", {
                                "record_id": rec.id,
                                "fields": rec.fields,
                                "source_url": doc.requested_url,
                                "counters": counters.__dict__
                            })
                    else:
                        self.tool_repo.record_finish(evt.id, evt.status.FAILED, {}, dur, extract_res.error_code)

                # ----------------------------------------------------
                # 5. Verifying Phase
                # ----------------------------------------------------
                self.run_repo.update_run_status(run.id, RunStatus.VERIFYING)
                emit_event("phase.change", {
                    "phase": "VERIFYING",
                    "message": f"Verifying {len(raw_records)} extracted records...",
                    "thought": "Verifying entity facts and schema compliance.",
                    "counters": counters.__dict__
                })

                for rec in raw_records:
                    if time.time() > deadline:
                        warnings.append("Verification stopped early: time budget exceeded.")
                        break
                    step_count += 1
                    evt = self.tool_repo.record_start(run.id, step_count, "verify_record", {"record_id": rec.id})
                    t0 = time.time()
                    spec_geo = getattr(task.normalized_spec, "geography", None)
                    geo_name = getattr(spec_geo, "name", None) if spec_geo else None

                    if rec.record_type == "market_intel":
                        verify_required = ["company_name", "product_name"]
                    elif rec.record_type == "job_listing":
                        verify_required = ["title", "company"]
                    elif rec.record_type == "research_finding":
                        verify_required = ["name", "category"] if "name" in task.normalized_spec.requested_fields else task.normalized_spec.requested_fields[:2]
                    else:
                        verify_required = task.normalized_spec.requested_fields[:2]

                    v_res = self.verify_tool.execute(
                        record=rec,
                        required_fields=verify_required,
                        contact_policy=task.normalized_spec.contact_policy,
                        geography_rule=geo_name
                    )
                    dur = int((time.time() - t0) * 1000)

                    self.record_repo.update_record_status(rec.id, rec.verification_status, rec.confidence)
                    self.tool_repo.record_finish(evt.id, evt.status.SUCCEEDED, {"status": rec.verification_status.value}, dur)

                    if rec.verification_status == VerificationStatus.VERIFIED:
                        counters.records_verified += 1
                        verified_records.append(rec)
                    elif rec.verification_status == VerificationStatus.NEEDS_REVIEW:
                        counters.records_review += 1
                        has_geo_conflict = any("does not match requested geography" in w for w in rec.warnings)
                        has_title_conflict = any("Job title" in w for w in rec.warnings)
                        if not has_geo_conflict and not has_title_conflict and rec.confidence >= 0.75:
                            counters.records_verified += 1
                            rec.verification_status = VerificationStatus.VERIFIED
                            verified_records.append(rec)
                    elif rec.verification_status == VerificationStatus.REJECTED:
                        counters.records_rejected += 1

                    emit_event("record.verified", {
                        "record_id": rec.id,
                        "status": rec.verification_status.value,
                        "confidence": rec.confidence,
                        "fields": rec.fields,
                        "warnings": rec.warnings,
                        "counters": counters.__dict__
                    })

                # ----------------------------------------------------
                # 6. Deduplicating Phase
                # ----------------------------------------------------
                self.run_repo.update_run_status(run.id, RunStatus.DEDUPLICATING)
                emit_event("phase.change", {
                    "phase": "DEDUPLICATING",
                    "message": "Removing duplicate records...",
                    "thought": "Deduplicating by canonical URL and content.",
                    "counters": counters.__dict__
                })
                step_count += 1
                evt = self.tool_repo.record_start(run.id, step_count, "deduplicate_records", {"count": len(verified_records)})
                t0 = time.time()
                dedupe_res = self.dedupe_tool.execute(verified_records)
                dur = int((time.time() - t0) * 1000)
                self.tool_repo.record_finish(evt.id, evt.status.SUCCEEDED, {"unique": len(dedupe_res.data.get("unique_records", []))}, dur)

                final_records = dedupe_res.data.get("unique_records", [])
                counters.records_duplicate = dedupe_res.data.get("duplicates_count", 0)

                # ----------------------------------------------------
                # 6.5 Job Analysis & Multidimensional Relevance Scoring (job search mode only)
                # ----------------------------------------------------
                if final_records and is_job_search:
                    emit_event("phase.change", {
                        "phase": "ANALYZING",
                        "message": f"Analyzing {len(final_records)} results...",
                        "thought": "Analyzing results against requirements",
                        "counters": counters.__dict__
                    })
                    try:
                        from datahunt.agents import DataNormalizer, HardFilter, JobAnalysisAgent, LearningEngine, QueryUnderstandingAgent
                        normalizer = DataNormalizer()
                        hard_filter = HardFilter()
                        analyzer = JobAnalysisAgent(gemini_client=self.client)
                        learning_engine = LearningEngine()

                        normalized_jobs = [normalizer.normalize(r.fields, record_id=r.id, canonical_url=r.canonical_url or "") for r in final_records]
                        active_job_req = job_req if 'job_req' in locals() and hasattr(job_req, "job_title") else QueryUnderstandingAgent(self.client).understand(task.request_text)
                        active_expanded = expanded if 'expanded' in locals() else None

                        if hasattr(active_job_req, "job_title"):
                            passed_jobs, rejected_jobs = hard_filter.apply(normalized_jobs, active_job_req)
                            eval_jobs = passed_jobs if passed_jobs else normalized_jobs
                            matched_results = analyzer.analyze_and_rank(eval_jobs, active_job_req, active_expanded)
                            scored_map = {}
                            for m_res in matched_results:
                                boost = learning_engine.calculate_preference_boost(m_res.job)
                                m_res.relevance_score = round(min(max(m_res.relevance_score + boost, 0.05), 1.0), 2)
                                scored_map[m_res.job.raw_id] = m_res

                            new_final_records = []
                            for r in final_records:
                                if r.id in scored_map:
                                    m_res = scored_map[r.id]
                                    r.fields["relevance_score"] = m_res.relevance_score
                                    r.fields["match_level"] = m_res.match_level
                                    r.fields["matching_requirements"] = m_res.matching_requirements
                                    r.fields["missing_requirements"] = m_res.missing_requirements
                                    r.fields["unknown_requirements"] = m_res.unknown_requirements
                                    r.fields["match_explanation"] = m_res.match_explanation
                                    r.confidence = m_res.relevance_score
                                    new_final_records.append(r)

                            if new_final_records:
                                final_records = new_final_records
                                final_records.sort(key=lambda r: -(r.confidence or 0.0))
                                emit_event("jobs.analyzed", {
                                    "total_scored": len(final_records),
                                    "top_match": final_records[0].fields.get("title"),
                                    "top_score": final_records[0].confidence,
                                    "top_explanation": final_records[0].fields.get("match_explanation")
                                })
                    except Exception as ana_err:
                        logger.warning(f"Job analysis & ranking phase encountered non-fatal error: {ana_err}")

                # Cap to requested max_records
                if len(final_records) > task.max_records:
                    final_records = final_records[:task.max_records]

            # End of if not _use_runtime block
            # (job-mode already has final_records from AgentRuntime above)

            # Cap runtime final_records to max_records as well
            if _use_runtime and len(final_records) > task.max_records:
                final_records = final_records[:task.max_records]

            # ----------------------------------------------------
            # 7. Synthesis & Intelligence Summary
            # ----------------------------------------------------
            emit_event("phase.change", {
                "phase": "SYNTHESIZING",
                "message": "Summarizing results...",
                "thought": f"Building result summary from {len(fetched_docs)} sources",
                "counters": counters.__dict__
            })

            # Build combined source excerpts (only if runtime didn't already build them)
            if not combined_source_texts:
                source_excerpts = []
                for doc in fetched_docs[:8]:
                    text_snippet = (doc.extracted_text or "").strip()
                    if text_snippet:
                        source_excerpts.append(f"Source URL: {doc.requested_url}\n{text_snippet[:2000]}")
                combined_source_texts = "\n\n---\n\n".join(source_excerpts)

            export_meta = {
                "task_id": task.id,
                "request_text": task.request_text,
                "topic": task.normalized_spec.topic,
                "pages_fetched": counters.pages_fetched,
                "records_verified": len(final_records),
                "warnings": warnings,
            }

            if is_job_search and _use_runtime and _result.get("job_matches_markdown"):
                summary_text = _result.get("job_matches_markdown")
            else:
                try:
                    summary_text = self.client.summarize_run(
                        run_metadata=export_meta,
                        verified_records=[r.fields for r in final_records],
                        source_texts=combined_source_texts,
                        query_text=task.request_text,
                        agent_mode=task_mode
                    )
                except Exception as se:
                    logger.warning(f"Summarize run failed with {se}, synthesizing local research dossier...")
                    if task_mode == "market":
                        from datahunt.llm.gemini_client import synthesize_local_market_dossier
                        summary_text = synthesize_local_market_dossier(
                            query=task.request_text,
                            run_metadata=export_meta,
                            verified_records=[r.fields for r in final_records],
                            source_texts=combined_source_texts
                        )
                    elif intent_spec and (
                        intent_spec.requested_output == ResearchOutputType.ANSWER
                        or intent_spec.intent in (
                            ResearchIntent.HOW_TO,
                            ResearchIntent.EXPLANATION,
                            ResearchIntent.FACTUAL_RESEARCH,
                            ResearchIntent.COMPARISON
                        )
                    ):
                        from datahunt.llm.gemini_client import synthesize_direct_answer
                        summary_text = synthesize_direct_answer(
                            query=task.request_text,
                            run_metadata=export_meta,
                            verified_records=[r.fields for r in final_records],
                            source_texts=combined_source_texts,
                            intent_spec=intent_spec
                        )
                    else:
                        from datahunt.llm.gemini_client import synthesize_local_research_dossier
                        summary_text = synthesize_local_research_dossier(
                            query=task.request_text,
                            run_metadata=export_meta,
                            verified_records=[r.fields for r in final_records],
                            source_texts=combined_source_texts
                        )
            export_meta["summary"] = summary_text

            # ----------------------------------------------------
            # 8. Exporting Phase
            # ----------------------------------------------------
            self.run_repo.update_run_status(run.id, RunStatus.EXPORTING)
            emit_event("phase.change", {
                "phase": "EXPORTING",
                "message": f"Compiling final payload in format '{task.requested_output_format.upper()}'...",
                "thought": "Exporting results",
                "counters": counters.__dict__
            })
            step_count += 1
            evt = self.tool_repo.record_start(run.id, step_count, "export_results", {"format": task.requested_output_format})
            t0 = time.time()
            exp_res = self.export_tool.execute(
                run_id=run.id,
                records=final_records,
                format=task.requested_output_format,
                run_metadata=export_meta
            )
            dur = int((time.time() - t0) * 1000)

            export_data = {}
            if exp_res.success:
                export_data = exp_res.data
                self.export_repo.insert_export(
                    ExportRecord(
                        id=export_data['export_id'],
                        run_id=run.id,
                        format=export_data['format'],
                        file_name=export_data['file_name'],
                        storage_key=export_data['file_path'],
                        sha256=export_data['sha256'],
                        row_count=export_data['row_count'],
                        include_evidence=1,
                        expires_at=None,
                        created_at=datetime.now(timezone.utc).isoformat(),
                    )
                )
                self.tool_repo.record_finish(evt.id, evt.status.SUCCEEDED, export_data, dur)

                # For research & dossiers, also generate companion format so both .md and .docx are instantly available
                companion_fmt = "docx" if task.requested_output_format == "md" else ("md" if task.requested_output_format == "docx" else None)
                if companion_fmt:
                    try:
                        c_res = self.export_tool.execute(
                            run_id=run.id,
                            records=final_records,
                            format=companion_fmt,
                            run_metadata=export_meta
                        )
                        if c_res.success:
                            c_data = c_res.data
                            self.export_repo.insert_export(
                                ExportRecord(
                                    id=c_data['export_id'],
                                    run_id=run.id,
                                    format=c_data['format'],
                                    file_name=c_data['file_name'],
                                    storage_key=c_data['file_path'],
                                    sha256=c_data['sha256'],
                                    row_count=c_data['row_count'],
                                    include_evidence=1,
                                    expires_at=None,
                                    created_at=datetime.now(timezone.utc).isoformat(),
                                )
                            )
                    except Exception as ce:
                        logger.warning(f"Companion {companion_fmt} generation failed: {ce}")
            else:
                self.tool_repo.record_finish(evt.id, evt.status.FAILED, {}, dur, exp_res.error_code)

            # ----------------------------------------------------
            # 9. Completion & Summary
            # ----------------------------------------------------
            counters.tool_steps = step_count
            self.run_repo.update_run_counters(run.id, counters)

            has_results = (len(final_records) > 0 or len(fetched_docs) > 0)
            has_verification_failures = counters.records_rejected > 0
            critical_errors = [w for w in warnings if "error" in w.lower()]
            final_status = RunStatus.COMPLETED if has_results and not (has_verification_failures or critical_errors) else (RunStatus.PARTIAL if has_results else RunStatus.FAILED)
            now_iso = datetime.now(timezone.utc).isoformat()
            self.run_repo.update_run_status(run.id, final_status, finished_at=now_iso)
            self.task_repo.update_task_status(task.id, TaskStatus.COMPLETED)

            # Calculate average confidence across final verified records or research synthesis
            conf_vals = [r.confidence for r in final_records if r.confidence is not None]
            avg_conf = round(sum(conf_vals) / len(conf_vals), 2) if conf_vals else (0.95 if (len(final_records) > 0 or len(fetched_docs) > 0) else 0.0)

            final_payload = {
                "task_id": task.id,
                "run_id": run.id,
                "status": final_status.value,
                "records": final_records,
                "records_verified": len(final_records),
                "records_rejected": counters.records_rejected,
                "records_duplicate": counters.records_duplicate,
                "pages_fetched": counters.pages_fetched,
                "pages_failed": counters.pages_failed,
                "confidence": avg_conf,
                "model": getattr(self.client, "model", "gemini-3.8-flash"),
                "model_mode": getattr(self.client, "mode", "auto"),
                "export_file": export_data.get("file_name"),
                "export_path": export_data.get("file_path"),
                "warnings": warnings,
                "summary": summary_text
            }

            event_payload = dict(final_payload)
            event_payload["records"] = [
                {
                    "id": r.id,
                    "record_type": r.record_type,
                    "fields": r.fields,
                    "canonical_url": r.canonical_url,
                    "confidence": r.confidence,
                    "verification_status": r.verification_status.value if hasattr(r.verification_status, "value") else str(r.verification_status),
                    "warnings": r.warnings,
                }
                for r in final_records
            ]

            emit_event("run.completed", event_payload)
            return final_payload

        except Exception as e:
            logger.error(f"Run {run.id} failed with exception: {e}", exc_info=True)
            self.run_repo.update_run_error(run.id, ErrorCode.INTERNAL_ERROR.value, str(e))
            self.task_repo.update_task_status(task.id, TaskStatus.FAILED)
            emit_event("run.failed", {
                "error": str(e),
                "run_id": run.id,
                "task_id": task.id
            })
            raise
