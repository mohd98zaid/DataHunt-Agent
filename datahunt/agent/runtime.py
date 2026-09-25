"""
AgentRuntime — The primary decision-loop execution engine.

Replaces the 8-phase linear pipeline in agent/orchestrator.py with
an explicit observe→decide→act loop.
"""
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from datahunt.config import settings
from datahunt.errors import DataHuntError, ErrorCode
from datahunt.logger import logger
from datahunt.llm import GeminiClient
from datahunt.tools import SearchTool, FetchTool, ExtractTool, VerifyTool, DedupeTool, ExportTool
from datahunt.tools.search import is_valid_job_url
from datahunt.models import VerificationStatus

from .state import AgentState, AgentStatus
from .decision import AgentAction, DecisionEngine
from .policies import GoalEvaluator, match_location, match_experience, MatchStatus


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

    def run(self, state: AgentState, emit: Callable[[str, Dict], None]) -> Dict[str, Any]:
        """
        Execute the agent loop until a stop condition is reached.
        Returns the final result dict.
        """
        emit("status", {"message": "Understanding request", "phase": "PLANNING"})
        state.status = AgentStatus.PLANNING

        if state.search_plan:
            # Caller pre-injected a plan (e.g. orchestrator after LLM planning)
            emit("status", {"message": f"Using pre-built plan: {len(state.search_plan)} queries", "phase": "PLANNED"})
            # Still extract constraints from the request for filtering
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

        emit("status", {"message": f"Search plan ready: {len(state.search_plan)} queries", "phase": "PLANNED"})
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
        """Build a tiered search plan from the request."""
        is_job = state.mode in ("jobs", "job")
        if is_job:
            from datahunt.tools.search import generate_ats_queries, generate_broad_job_queries
            tier1 = [{"query": q, "tier": 1, "purpose": "official_ats"} for q in generate_ats_queries(state.request)[:8]]
            tier2 = [{"query": q, "tier": 2, "purpose": "regional_boards"} for q in generate_broad_job_queries(state.request)[:6]]
            plan = tier1 + tier2
        else:
            from datahunt.models.run import RunBudget
            spec = self.client.normalize_request(state.request)
            plan_data = self.client.plan_research(spec, RunBudget())
            queries = plan_data.get("queries", [{"query": state.request}])
            plan = [{"query": q.get("query", str(q)), "tier": 2, "purpose": q.get("purpose", "general")} for q in queries[:20]]

        state.search_plan = plan
        state.explicit_location = self._extract_location(state.request)
        state.explicit_experience_min, state.explicit_experience_max = self._extract_experience(state.request)

    def _act_search(self, state: AgentState, emit):
        """Execute the next batch of searches."""
        state.status = AgentStatus.SEARCHING
        batch_size = min(5, len(state.search_plan) - state.search_plan_index)
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
            emit("status", {"message": f"Searching: {query[:60]}", "phase": "SEARCHING"})

            before_count = len(state.candidate_urls)
            try:
                res = self.search.execute(query=query, limit=20)
                state.search_calls += 1
                new_candidates = 0

                if res.success:
                    for hit in res.data:
                        url = hit.get("url")
                        if not url or url in state.seen_urls:
                            continue
                        if state.mode in ("jobs", "job") and not is_valid_job_url(url):
                            continue
                        state.seen_urls.add(url)
                        state.candidate_urls.append(hit)
                        new_candidates += 1

                self.decision_engine.record_search_iteration(
                    state, query, len(res.data) if res.success else 0, new_candidates
                )
                state.add_observation(f"Search '{query[:40]}': {new_candidates} new candidates from {len(res.data) if res.success else 0} hits")

            except Exception as e:
                logger.warning(f"Search error for '{query}': {e}")
                state.add_warning(f"Search failed: {query[:40]}")

    def _act_fetch(self, state: AgentState, emit):
        """Fetch top candidates from the queue."""
        state.status = AgentStatus.FETCHING

        if state.explicit_location:
            loc = state.explicit_location.lower()
            state.candidate_urls.sort(key=lambda h: (
                0 if any(t in f"{h.get('url','')} {h.get('title','')} {h.get('snippet','')}".lower()
                         for t in loc.split()) else 1
            ))

        batch = state.candidate_urls[:15]
        state.candidate_urls = state.candidate_urls[15:]

        from concurrent.futures import ThreadPoolExecutor, as_completed
        emit("status", {"message": f"Fetching {len(batch)} pages", "phase": "FETCHING"})

        with ThreadPoolExecutor(max_workers=8) as executor:
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
                    for rec in res.data:
                        state.raw_records.append(rec)
                        state.llm_calls += 1
            except Exception as e:
                logger.warning(f"Extract error: {e}")

    def _act_verify(self, state: AgentState, emit):
        """Verify unverified records."""
        state.status = AgentStatus.VERIFYING
        already_processed = set(r.id for r in state.verified_records) | set(r.id for r in state.rejected_records)
        unverified = [r for r in state.raw_records if r.id not in already_processed]

        if not unverified:
            return

        emit("status", {"message": f"Verifying {len(unverified)} candidates", "phase": "VERIFYING"})
        geo_name = state.explicit_location

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

                job_loc = rec.fields.get("location", "")
                loc_status, loc_reason = match_location(state.explicit_location, job_loc)

                if loc_status == MatchStatus.MISMATCH:
                    state.rejected_records.append(rec)
                    state.add_observation(f"Rejected '{rec.fields.get('title', '?')}': {loc_reason}")
                    continue

                if rec.verification_status == VerificationStatus.VERIFIED or \
                   rec.verification_status == VerificationStatus.NEEDS_REVIEW:
                    state.verified_records.append(rec)
                    if loc_status == MatchStatus.UNKNOWN:
                        rec.warnings.append(f"Location unverified: {loc_reason}")
                else:
                    state.rejected_records.append(rec)

            except Exception as e:
                logger.warning(f"Verify error: {e}")
                rec.warnings.append(f"Verification error: {e}")
                state.verified_records.append(rec)

    def _act_analyze(self, state: AgentState, emit):
        """Run job analysis and ranking."""
        state.status = AgentStatus.ANALYZING
        emit("status", {"message": f"Analyzing {len(state.verified_records)} results", "phase": "ANALYZING"})
        try:
            from datahunt.agents import DataNormalizer, HardFilter, JobAnalysisAgent
            from datahunt.agents.query_understanding import JobSearchRequest
            normalizer = DataNormalizer()
            hard_filter = HardFilter()
            analyzer = JobAnalysisAgent(gemini_client=self.client)

            normalized = [normalizer.normalize(r.fields, record_id=r.id, canonical_url=r.canonical_url or "") for r in state.verified_records]
            job_req = JobSearchRequest(
                job_title=state.explicit_titles[0] if state.explicit_titles else "",
                location=state.explicit_location or "",
                experience_min=state.explicit_experience_min,
                experience_max=state.explicit_experience_max,
                skills=state.explicit_skills,
            )
            passed, _ = hard_filter.apply(normalized, job_req)
            if passed:
                analyzed = analyzer.analyze_and_rank(passed, job_req, None)
                scored_map = {m.job.raw_id: m for m in analyzed}
                for r in state.verified_records:
                    if r.id in scored_map:
                        m = scored_map[r.id]
                        r.fields["relevance_score"] = m.relevance_score
                        r.fields["match_level"] = m.match_level
                        r.fields["match_explanation"] = m.match_explanation
                        r.confidence = m.relevance_score
                state.verified_records.sort(key=lambda r: -(r.confidence or 0.0))
        except Exception as e:
            logger.warning(f"Analysis error (non-fatal): {e}")

    def _finalize(self, state: AgentState, emit) -> Dict[str, Any]:
        """Deduplicate, export, emit completion."""
        emit("status", {"message": f"Removing duplicates from {len(state.verified_records)} results", "phase": "DEDUPLICATING"})

        if state.verified_records:
            try:
                dedup_res = self.dedupe.execute(state.verified_records)
                final_records = dedup_res.data.get("unique_records", state.verified_records)
            except Exception as e:
                logger.warning(f"Dedupe error: {e}")
                final_records = state.verified_records
        else:
            final_records = []

        if state.mode in ("jobs", "job") and final_records:
            self._act_analyze(state, emit)
            final_records = state.verified_records  # re-sort by score

        emit("status", {"message": f"Found {len(final_records)} verified results", "phase": "COMPLETED"})

        result_status = "completed" if final_records else ("partial" if state.warnings else "failed")
        if len(final_records) < state.target_results and state.warnings:
            result_status = "partial"

        return {
            "status": result_status,
            "records": final_records,
            "records_verified": len(final_records),
            "records_rejected": len(state.rejected_records),
            "search_iterations": len(state.search_iterations),
            "pages_fetched": state.fetch_calls,
            "pages_failed": state.pages_failed,
            "llm_calls": state.llm_calls,
            "completion_reason": state.completion_reason,
            "warnings": state.warnings,
            "observations": state.observations[-20:],  # last 20 for UI
            "actions_taken": state.actions_taken,
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
