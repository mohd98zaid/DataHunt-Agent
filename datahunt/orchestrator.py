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
    RunBudget, RunCounters, TaskStatus, VerificationStatus
)
from datahunt.llm import GeminiClient
from datahunt.tools import (
    SearchTool, FetchTool, ExtractTool,
    VerifyTool, DedupeTool, ExportTool
)

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
    ):
        self.client = gemini_client or GeminiClient()
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
    ) -> tuple[ResearchTask, ResearchRun]:
        """Normalize request, enforce intake policies, and initialize database records."""
        operator_defaults = {
            "max_records": max_records,
            "freshness_days": freshness_days,
            "output_format": output_format,
            "contact_policy": contact_policy,
            "allowed_domains": allowed_domains or [],
            "blocked_domains": blocked_domains or [],
        }

        spec = self.client.normalize_request(request_text, operator_defaults)

        task = ResearchTask(
            request_text=request_text,
            normalized_spec=spec,
            requested_output_format=output_format,
            max_records=max_records,
            status=TaskStatus.ACCEPTED
        )
        self.task_repo.create_task(task)

        run_b = budget or RunBudget(
            deadline_seconds=settings.MAX_RUN_SECONDS,
            max_search_queries=min(max(spec.max_records // 2, 2), settings.MAX_SEARCH_QUERIES),
            max_pages=min(max(spec.max_records * 2, 4), settings.MAX_PAGES_FETCHED),
            max_output_records=spec.max_records,
        )

        run = ResearchRun(
            task_id=task.id,
            status=RunStatus.PLANNED,
            budget=run_b,
        )
        self.run_repo.create_run(run)

        logger.info(
            f"Created ResearchTask {task.id} and ResearchRun {run.id} for query: '{request_text}'",
            extra={"task_id": task.id, "run_id": run.id}
        )
        return task, run

    def execute_run(self, run_id: str) -> Dict[str, Any]:
        """Execute a research run through all state machine transitions."""
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

        try:
            # ----------------------------------------------------
            # 1. Planning Phase
            # ----------------------------------------------------
            logger.info("Starting planning phase", extra={"run_id": run.id, "event": "phase.planning"})
            plan = self.client.plan_research(task.normalized_spec, run.budget)
            queries = plan.get("queries", [])
            logger.info(f"Planned {len(queries)} search queries", extra={"run_id": run.id})

            # ----------------------------------------------------
            # 2. Searching Phase
            # ----------------------------------------------------
            self.run_repo.update_run_status(run.id, RunStatus.SEARCHING)
            candidate_urls = []
            seen_urls = set()

            for q_obj in queries[:run.budget.max_search_queries]:
                if time.time() > deadline:
                    warnings.append("Search stopped early: deadline reached.")
                    break

                query_text = q_obj.get("query") if isinstance(q_obj, dict) else str(q_obj)
                step_count += 1
                evt = self.tool_repo.record_start(run.id, step_count, "search_web", {"query": query_text})
                
                t0 = time.time()
                search_res = self.search_tool.execute(
                    query=query_text,
                    limit=10,
                    allowed_domains=task.normalized_spec.source_policy.allowed_domains,
                    blocked_domains=task.normalized_spec.source_policy.blocked_domains,
                )
                dur = int((time.time() - t0) * 1000)

                if search_res.success:
                    self.tool_repo.record_finish(evt.id, evt.status.SUCCEEDED, {"count": len(search_res.data)}, dur)
                    counters.search_queries += 1
                    for hit in search_res.data:
                        u = hit.get("url")
                        if u and u not in seen_urls:
                            seen_urls.add(u)
                            candidate_urls.append(hit)
                else:
                    self.tool_repo.record_finish(evt.id, evt.status.FAILED, {}, dur, search_res.error_code)

            # ----------------------------------------------------
            # 3. Collecting (Fetching) Phase
            # ----------------------------------------------------
            self.run_repo.update_run_status(run.id, RunStatus.COLLECTING)
            fetched_docs = []

            pages_to_fetch = candidate_urls[:run.budget.max_pages]
            for hit in pages_to_fetch:
                if time.time() > deadline:
                    warnings.append("Fetching stopped early: deadline reached.")
                    break

                target_url = hit.get("url")
                step_count += 1
                evt = self.tool_repo.record_start(run.id, step_count, "fetch_page", {"url": target_url})

                t0 = time.time()
                fetch_res = self.fetch_tool.execute(
                    url=target_url,
                    run_id=run.id,
                    allowed_domains=task.normalized_spec.source_policy.allowed_domains,
                    blocked_domains=task.normalized_spec.source_policy.blocked_domains,
                )
                dur = int((time.time() - t0) * 1000)

                if fetch_res.success and fetch_res.data:
                    doc = fetch_res.data
                    self.doc_repo.upsert_document(doc)
                    fetched_docs.append(doc)
                    counters.pages_fetched += 1
                    self.tool_repo.record_finish(evt.id, evt.status.SUCCEEDED, {"doc_id": doc.id, "status_code": doc.http_status}, dur)
                else:
                    counters.pages_failed += 1
                    if fetch_res.error_code == ErrorCode.FETCH_BLOCKED.value:
                        counters.pages_blocked += 1
                    self.tool_repo.record_finish(evt.id, evt.status.BLOCKED if fetch_res.error_code == ErrorCode.FETCH_BLOCKED.value else evt.status.FAILED, {}, dur, fetch_res.error_code)

            # ----------------------------------------------------
            # 4. Extracting Phase
            # ----------------------------------------------------
            self.run_repo.update_run_status(run.id, RunStatus.EXTRACTING)
            raw_records = []

            for doc in fetched_docs:
                if time.time() > deadline:
                    warnings.append("Extraction stopped early: deadline reached.")
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
                else:
                    self.tool_repo.record_finish(evt.id, evt.status.FAILED, {}, dur, extract_res.error_code)

            # ----------------------------------------------------
            # 5. Verifying Phase
            # ----------------------------------------------------
            self.run_repo.update_run_status(run.id, RunStatus.VERIFYING)
            verified_records = []

            for rec in raw_records:
                step_count += 1
                evt = self.tool_repo.record_start(run.id, step_count, "verify_record", {"record_id": rec.id})
                t0 = time.time()
                v_res = self.verify_tool.execute(
                    record=rec,
                    required_fields=task.normalized_spec.requested_fields[:2],
                    contact_policy=task.normalized_spec.contact_policy
                )
                dur = int((time.time() - t0) * 1000)

                self.record_repo.update_record_status(rec.id, rec.verification_status, rec.confidence)
                self.tool_repo.record_finish(evt.id, evt.status.SUCCEEDED, {"status": rec.verification_status.value}, dur)

                if rec.verification_status == VerificationStatus.VERIFIED:
                    counters.records_verified += 1
                    verified_records.append(rec)
                elif rec.verification_status == VerificationStatus.NEEDS_REVIEW:
                    counters.records_review += 1
                elif rec.verification_status == VerificationStatus.REJECTED:
                    counters.records_rejected += 1

            # ----------------------------------------------------
            # 6. Deduplicating Phase
            # ----------------------------------------------------
            self.run_repo.update_run_status(run.id, RunStatus.DEDUPLICATING)
            step_count += 1
            evt = self.tool_repo.record_start(run.id, step_count, "deduplicate_records", {"count": len(verified_records)})
            t0 = time.time()
            dedupe_res = self.dedupe_tool.execute(verified_records)
            dur = int((time.time() - t0) * 1000)
            self.tool_repo.record_finish(evt.id, evt.status.SUCCEEDED, {"unique": len(dedupe_res.data.get("unique_records", []))}, dur)

            final_records = dedupe_res.data.get("unique_records", [])
            counters.records_duplicate = dedupe_res.data.get("duplicates_count", 0)

            # Cap to requested max_records
            if len(final_records) > task.max_records:
                final_records = final_records[:task.max_records]

            # ----------------------------------------------------
            # 7. Exporting Phase
            # ----------------------------------------------------
            self.run_repo.update_run_status(run.id, RunStatus.EXPORTING)
            step_count += 1
            evt = self.tool_repo.record_start(run.id, step_count, "export_results", {"format": task.requested_output_format})
            t0 = time.time()
            export_meta = {
                "task_id": task.id,
                "request_text": task.request_text,
                "topic": task.normalized_spec.topic,
                "pages_fetched": counters.pages_fetched,
                "records_verified": len(final_records),
                "warnings": warnings,
            }
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
                    self.export_repo.list_exports_for_run(run.id)[-1] if self.export_repo.list_exports_for_run(run.id) else
                    type('Exp', (), {'id': export_data['export_id'], 'run_id': run.id, 'format': export_data['format'], 'file_name': export_data['file_name'], 'storage_key': export_data['file_path'], 'sha256': export_data['sha256'], 'row_count': export_data['row_count'], 'include_evidence': 1, 'expires_at': None, 'created_at': datetime.now(timezone.utc).isoformat()})()
                )
                self.tool_repo.record_finish(evt.id, evt.status.SUCCEEDED, export_data, dur)
            else:
                self.tool_repo.record_finish(evt.id, evt.status.FAILED, {}, dur, exp_res.error_code)

            # ----------------------------------------------------
            # 8. Completion & Summary
            # ----------------------------------------------------
            counters.tool_steps = step_count
            self.run_repo.update_run_counters(run.id, counters)

            final_status = RunStatus.COMPLETED if len(final_records) > 0 and not warnings else RunStatus.PARTIAL
            now_iso = datetime.now(timezone.utc).isoformat()
            self.run_repo.update_run_status(run.id, final_status, finished_at=now_iso)
            self.task_repo.update_task_status(task.id, TaskStatus.COMPLETED)

            summary_text = self.client.summarize_run(export_meta, [r.fields for r in final_records])

            return {
                "task_id": task.id,
                "run_id": run.id,
                "status": final_status.value,
                "records_verified": len(final_records),
                "records_rejected": counters.records_rejected,
                "records_duplicate": counters.records_duplicate,
                "pages_fetched": counters.pages_fetched,
                "pages_failed": counters.pages_failed,
                "export_file": export_data.get("file_name"),
                "export_path": export_data.get("file_path"),
                "warnings": warnings,
                "summary": summary_text
            }

        except Exception as e:
            logger.error(f"Run {run.id} failed with exception: {e}", exc_info=True)
            self.run_repo.update_run_error(run.id, ErrorCode.INTERNAL_ERROR.value, str(e))
            self.task_repo.update_task_status(task.id, TaskStatus.FAILED)
            raise
