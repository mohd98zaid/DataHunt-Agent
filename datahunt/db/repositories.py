import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from datahunt.db.connection import get_connection, db_transaction
from datahunt.models import (
    ResearchTask, ResearchSpec, TaskStatus,
    ResearchRun, RunStatus, RunBudget, RunCounters,
    ToolEvent, ToolEventStatus,
    SourceDocument, RetrievalStatus,
    ExtractedRecord, RecordEvidence, VerificationStatus,
    ExportRecord
)

class TaskRepository:
    def __init__(self, conn_factory=get_connection):
        self.conn_factory = conn_factory

    def create_task(self, task: ResearchTask) -> ResearchTask:
        conn = self.conn_factory()
        try:
            conn.execute(
                """
                INSERT INTO research_tasks (
                    id, request_text, normalized_spec_json, policy_version,
                    prompt_version, requested_output_format, max_records,
                    status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    task.id,
                    task.request_text,
                    task.normalized_spec.model_dump_json(),
                    task.policy_version,
                    task.prompt_version,
                    task.requested_output_format,
                    task.max_records,
                    task.status.value,
                    task.created_at,
                    task.updated_at,
                )
            )
            return task
        finally:
            conn.close()

    def get_task(self, task_id: str) -> Optional[ResearchTask]:
        conn = self.conn_factory()
        try:
            row = conn.execute("SELECT * FROM research_tasks WHERE id = ?;", (task_id,)).fetchone()
            if not row:
                return None
            spec_data = json.loads(row["normalized_spec_json"])
            return ResearchTask(
                id=row["id"],
                request_text=row["request_text"],
                normalized_spec=ResearchSpec(**spec_data),
                policy_version=row["policy_version"],
                prompt_version=row["prompt_version"],
                requested_output_format=row["requested_output_format"],
                max_records=row["max_records"],
                status=TaskStatus(row["status"]),
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            )
        finally:
            conn.close()

    def update_task_status(self, task_id: str, status: TaskStatus) -> None:
        conn = self.conn_factory()
        try:
            now_str = datetime.now(timezone.utc).isoformat()
            conn.execute(
                "UPDATE research_tasks SET status = ?, updated_at = ? WHERE id = ?;",
                (status.value, now_str, task_id)
            )
        finally:
            conn.close()

    def list_tasks(self, limit: int = 50) -> List[ResearchTask]:
        conn = self.conn_factory()
        try:
            rows = conn.execute(
                "SELECT * FROM research_tasks ORDER BY created_at DESC LIMIT ?;", (limit,)
            ).fetchall()
            results = []
            for row in rows:
                spec_data = json.loads(row["normalized_spec_json"])
                results.append(
                    ResearchTask(
                        id=row["id"],
                        request_text=row["request_text"],
                        normalized_spec=ResearchSpec(**spec_data),
                        policy_version=row["policy_version"],
                        prompt_version=row["prompt_version"],
                        requested_output_format=row["requested_output_format"],
                        max_records=row["max_records"],
                        status=TaskStatus(row["status"]),
                        created_at=row["created_at"],
                        updated_at=row["updated_at"],
                    )
                )
            return results
        finally:
            conn.close()

class RunRepository:
    def __init__(self, conn_factory=get_connection):
        self.conn_factory = conn_factory

    def create_run(self, run: ResearchRun) -> ResearchRun:
        conn = self.conn_factory()
        try:
            conn.execute(
                """
                INSERT INTO research_runs (
                    id, task_id, status, attempt, started_at, finished_at, deadline_at,
                    budget_json, counters_json, warning_json, error_code, error_message,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    run.id,
                    run.task_id,
                    run.status.value,
                    run.attempt,
                    run.started_at,
                    run.finished_at,
                    run.deadline_at,
                    run.budget.model_dump_json(),
                    run.counters.model_dump_json(),
                    json.dumps(run.warnings),
                    run.error_code,
                    run.error_message,
                    run.created_at,
                    run.updated_at,
                )
            )
            return run
        finally:
            conn.close()

    def get_run(self, run_id: str) -> Optional[ResearchRun]:
        conn = self.conn_factory()
        try:
            row = conn.execute("SELECT * FROM research_runs WHERE id = ?;", (run_id,)).fetchone()
            if not row:
                return None
            budget_data = json.loads(row["budget_json"])
            counters_data = json.loads(row["counters_json"])
            warnings_data = json.loads(row["warning_json"])
            return ResearchRun(
                id=row["id"],
                task_id=row["task_id"],
                status=RunStatus(row["status"]),
                attempt=row["attempt"],
                started_at=row["started_at"],
                finished_at=row["finished_at"],
                deadline_at=row["deadline_at"],
                budget=RunBudget(**budget_data),
                counters=RunCounters(**counters_data),
                warnings=warnings_data,
                error_code=row["error_code"],
                error_message=row["error_message"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            )
        finally:
            conn.close()

    def update_run_status(self, run_id: str, status: RunStatus, finished_at: Optional[str] = None) -> None:
        conn = self.conn_factory()
        try:
            now_str = datetime.now(timezone.utc).isoformat()
            if finished_at:
                conn.execute(
                    "UPDATE research_runs SET status = ?, finished_at = ?, updated_at = ? WHERE id = ?;",
                    (status.value, finished_at, now_str, run_id)
                )
            else:
                conn.execute(
                    "UPDATE research_runs SET status = ?, updated_at = ? WHERE id = ?;",
                    (status.value, now_str, run_id)
                )
        finally:
            conn.close()

    def update_run_counters(self, run_id: str, counters: RunCounters) -> None:
        conn = self.conn_factory()
        try:
            now_str = datetime.now(timezone.utc).isoformat()
            conn.execute(
                "UPDATE research_runs SET counters_json = ?, updated_at = ? WHERE id = ?;",
                (counters.model_dump_json(), now_str, run_id)
            )
        finally:
            conn.close()

    def update_run_error(self, run_id: str, error_code: str, error_message: str, status: RunStatus = RunStatus.FAILED) -> None:
        conn = self.conn_factory()
        try:
            now_str = datetime.now(timezone.utc).isoformat()
            conn.execute(
                """
                UPDATE research_runs 
                SET status = ?, error_code = ?, error_message = ?, finished_at = ?, updated_at = ? 
                WHERE id = ?;
                """,
                (status.value, error_code, error_message, now_str, now_str, run_id)
            )
        finally:
            conn.close()

    def add_warning(self, run_id: str, warning: str) -> None:
        conn = self.conn_factory()
        try:
            row = conn.execute("SELECT warning_json FROM research_runs WHERE id = ?;", (run_id,)).fetchone()
            if row:
                warnings = json.loads(row["warning_json"])
                warnings.append(warning)
                now_str = datetime.now(timezone.utc).isoformat()
                conn.execute(
                    "UPDATE research_runs SET warning_json = ?, updated_at = ? WHERE id = ?;",
                    (json.dumps(warnings), now_str, run_id)
                )
        finally:
            conn.close()

    def list_recent_runs(self, limit: int = 50) -> List[ResearchRun]:
        conn = self.conn_factory()
        try:
            rows = conn.execute(
                "SELECT * FROM research_runs ORDER BY created_at DESC LIMIT ?;", (limit,)
            ).fetchall()
            results = []
            for row in rows:
                budget_data = json.loads(row["budget_json"])
                counters_data = json.loads(row["counters_json"])
                warnings_data = json.loads(row["warning_json"])
                results.append(
                    ResearchRun(
                        id=row["id"],
                        task_id=row["task_id"],
                        status=RunStatus(row["status"]),
                        attempt=row["attempt"],
                        started_at=row["started_at"],
                        finished_at=row["finished_at"],
                        deadline_at=row["deadline_at"],
                        budget=RunBudget(**budget_data),
                        counters=RunCounters(**counters_data),
                        warnings=warnings_data,
                        error_code=row["error_code"],
                        error_message=row["error_message"],
                        created_at=row["created_at"],
                        updated_at=row["updated_at"],
                    )
                )
            return results
        finally:
            conn.close()

class DocumentRepository:
    def __init__(self, conn_factory=get_connection):
        self.conn_factory = conn_factory

    def upsert_document(self, doc: SourceDocument) -> SourceDocument:
        conn = self.conn_factory()
        try:
            conn.execute(
                """
                INSERT INTO source_documents (
                    id, run_id, requested_url, final_url, canonical_url, domain,
                    source_type, http_status, content_type, content_hash, etag,
                    last_modified, title, extracted_text, text_truncated, policy_flags_json,
                    retrieval_status, retrieved_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id, canonical_url) DO UPDATE SET
                    final_url = excluded.final_url,
                    http_status = excluded.http_status,
                    content_type = excluded.content_type,
                    content_hash = excluded.content_hash,
                    title = excluded.title,
                    extracted_text = excluded.extracted_text,
                    text_truncated = excluded.text_truncated,
                    retrieval_status = excluded.retrieval_status,
                    retrieved_at = excluded.retrieved_at;
                """,
                (
                    doc.id,
                    doc.run_id,
                    doc.requested_url,
                    doc.final_url,
                    doc.canonical_url or doc.requested_url,
                    doc.domain,
                    doc.source_type,
                    doc.http_status,
                    doc.content_type,
                    doc.content_hash,
                    doc.etag,
                    doc.last_modified,
                    doc.title,
                    doc.extracted_text,
                    doc.text_truncated,
                    json.dumps(doc.policy_flags),
                    doc.retrieval_status.value,
                    doc.retrieved_at,
                    doc.created_at,
                )
            )
            return doc
        finally:
            conn.close()

    def get_document(self, doc_id: str) -> Optional[SourceDocument]:
        conn = self.conn_factory()
        try:
            row = conn.execute("SELECT * FROM source_documents WHERE id = ?;", (doc_id,)).fetchone()
            if not row:
                return None
            return SourceDocument(
                id=row["id"],
                run_id=row["run_id"],
                requested_url=row["requested_url"],
                final_url=row["final_url"],
                canonical_url=row["canonical_url"],
                domain=row["domain"],
                source_type=row["source_type"],
                http_status=row["http_status"],
                content_type=row["content_type"],
                content_hash=row["content_hash"],
                etag=row["etag"],
                last_modified=row["last_modified"],
                title=row["title"],
                extracted_text=row["extracted_text"],
                text_truncated=row["text_truncated"],
                policy_flags=json.loads(row["policy_flags_json"]),
                retrieval_status=RetrievalStatus(row["retrieval_status"]),
                retrieved_at=row["retrieved_at"],
                created_at=row["created_at"],
            )
        finally:
            conn.close()

    def list_documents_for_run(self, run_id: str) -> List[SourceDocument]:
        conn = self.conn_factory()
        try:
            rows = conn.execute("SELECT * FROM source_documents WHERE run_id = ?;", (run_id,)).fetchall()
            return [
                SourceDocument(
                    id=r["id"],
                    run_id=r["run_id"],
                    requested_url=r["requested_url"],
                    final_url=r["final_url"],
                    canonical_url=r["canonical_url"],
                    domain=r["domain"],
                    source_type=r["source_type"],
                    http_status=r["http_status"],
                    content_type=r["content_type"],
                    content_hash=r["content_hash"],
                    etag=r["etag"],
                    last_modified=r["last_modified"],
                    title=r["title"],
                    extracted_text=r["extracted_text"],
                    text_truncated=r["text_truncated"],
                    policy_flags=json.loads(r["policy_flags_json"]),
                    retrieval_status=RetrievalStatus(r["retrieval_status"]),
                    retrieved_at=r["retrieved_at"],
                    created_at=r["created_at"],
                )
                for r in rows
            ]
        finally:
            conn.close()

class RecordRepository:
    def __init__(self, conn_factory=get_connection):
        self.conn_factory = conn_factory

    def insert_record(self, record: ExtractedRecord) -> ExtractedRecord:
        conn = self.conn_factory()
        try:
            conn.execute(
                """
                INSERT INTO extracted_records (
                    id, run_id, source_document_id, record_type, identity_key,
                    canonical_url, fields_json, normalized_fields_json,
                    verification_status, confidence, warnings_json,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    record.id,
                    record.run_id,
                    record.source_document_id,
                    record.record_type,
                    record.identity_key,
                    record.canonical_url,
                    json.dumps(record.fields),
                    json.dumps(record.normalized_fields),
                    record.verification_status.value,
                    record.confidence,
                    json.dumps(record.warnings),
                    record.created_at,
                    record.updated_at,
                )
            )
            # Insert any evidence attached
            for ev in record.evidence:
                conn.execute(
                    """
                    INSERT INTO record_evidence (
                        id, record_id, source_document_id, field_name, evidence_text,
                        locator_json, evidence_type, supports_value, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
                    """,
                    (
                        ev.id,
                        record.id,
                        ev.source_document_id,
                        ev.field_name,
                        ev.evidence_text,
                        json.dumps(ev.locator),
                        ev.evidence_type,
                        ev.supports_value,
                        ev.created_at,
                    )
                )
            return record
        finally:
            conn.close()

    def list_records_for_run(self, run_id: str, status: Optional[VerificationStatus] = None) -> List[ExtractedRecord]:
        conn = self.conn_factory()
        try:
            if status:
                rows = conn.execute(
                    "SELECT * FROM extracted_records WHERE run_id = ? AND verification_status = ?;",
                    (run_id, status.value)
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM extracted_records WHERE run_id = ?;", (run_id,)
                ).fetchall()
                
            records = []
            for r in rows:
                ev_rows = conn.execute(
                    "SELECT * FROM record_evidence WHERE record_id = ?;", (r["id"],)
                ).fetchall()
                evidence_list = [
                    RecordEvidence(
                        id=er["id"],
                        record_id=er["record_id"],
                        source_document_id=er["source_document_id"],
                        field_name=er["field_name"],
                        evidence_text=er["evidence_text"],
                        locator=json.loads(er["locator_json"]),
                        evidence_type=er["evidence_type"],
                        supports_value=er["supports_value"],
                        created_at=er["created_at"],
                    )
                    for er in ev_rows
                ]
                records.append(
                    ExtractedRecord(
                        id=r["id"],
                        run_id=r["run_id"],
                        source_document_id=r["source_document_id"],
                        record_type=r["record_type"],
                        identity_key=r["identity_key"],
                        canonical_url=r["canonical_url"],
                        fields=json.loads(r["fields_json"]),
                        normalized_fields=json.loads(r["normalized_fields_json"]),
                        verification_status=VerificationStatus(r["verification_status"]),
                        confidence=r["confidence"],
                        warnings=json.loads(r["warnings_json"]),
                        evidence=evidence_list,
                        created_at=r["created_at"],
                        updated_at=r["updated_at"],
                    )
                )
            return records
        finally:
            conn.close()

    def update_record_status(self, record_id: str, status: VerificationStatus, confidence: Optional[float] = None) -> None:
        conn = self.conn_factory()
        try:
            now_str = datetime.now(timezone.utc).isoformat()
            conn.execute(
                "UPDATE extracted_records SET verification_status = ?, confidence = ?, updated_at = ? WHERE id = ?;",
                (status.value, confidence, now_str, record_id)
            )
        finally:
            conn.close()

    def add_evidence(self, ev: RecordEvidence) -> None:
        conn = self.conn_factory()
        try:
            conn.execute(
                """
                INSERT INTO record_evidence (
                    id, record_id, source_document_id, field_name, evidence_text,
                    locator_json, evidence_type, supports_value, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    ev.id,
                    ev.record_id,
                    ev.source_document_id,
                    ev.field_name,
                    ev.evidence_text,
                    json.dumps(ev.locator),
                    ev.evidence_type,
                    ev.supports_value,
                    ev.created_at,
                )
            )
        finally:
            conn.close()

class ExportRepository:
    def __init__(self, conn_factory=get_connection):
        self.conn_factory = conn_factory

    def insert_export(self, exp: ExportRecord) -> ExportRecord:
        conn = self.conn_factory()
        try:
            conn.execute(
                """
                INSERT INTO exports (
                    id, run_id, format, file_name, storage_key, sha256,
                    row_count, include_evidence, expires_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    exp.id,
                    exp.run_id,
                    exp.format,
                    exp.file_name,
                    exp.storage_key,
                    exp.sha256,
                    exp.row_count,
                    exp.include_evidence,
                    exp.expires_at,
                    exp.created_at,
                )
            )
            return exp
        finally:
            conn.close()

    def list_exports_for_run(self, run_id: str) -> List[ExportRecord]:
        conn = self.conn_factory()
        try:
            rows = conn.execute("SELECT * FROM exports WHERE run_id = ?;", (run_id,)).fetchall()
            return [
                ExportRecord(
                    id=r["id"],
                    run_id=r["run_id"],
                    format=r["format"],
                    file_name=r["file_name"],
                    storage_key=r["storage_key"],
                    sha256=r["sha256"],
                    row_count=r["row_count"],
                    include_evidence=r["include_evidence"],
                    expires_at=r["expires_at"],
                    created_at=r["created_at"],
                )
                for r in rows
            ]
        finally:
            conn.close()

class ToolEventRepository:
    def __init__(self, conn_factory=get_connection):
        self.conn_factory = conn_factory

    def record_start(self, run_id: str, step_number: int, tool_name: str, request_json: dict) -> ToolEvent:
        conn = self.conn_factory()
        try:
            evt = ToolEvent(
                run_id=run_id,
                step_number=step_number,
                tool_name=tool_name,
                request_json=request_json,
                status=ToolEventStatus.STARTED
            )
            conn.execute(
                """
                INSERT INTO tool_events (
                    id, run_id, step_number, tool_name, request_json,
                    response_summary_json, status, error_code, duration_ms,
                    request_hash, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    evt.id,
                    evt.run_id,
                    evt.step_number,
                    evt.tool_name,
                    json.dumps(evt.request_json),
                    json.dumps(evt.response_summary_json),
                    evt.status.value,
                    evt.error_code,
                    evt.duration_ms,
                    evt.request_hash,
                    evt.created_at,
                )
            )
            return evt
        finally:
            conn.close()

    def record_finish(
        self,
        event_id: str,
        status: ToolEventStatus,
        response_summary_json: dict,
        duration_ms: int,
        error_code: Optional[str] = None
    ) -> None:
        conn = self.conn_factory()
        try:
            conn.execute(
                """
                UPDATE tool_events
                SET status = ?, response_summary_json = ?, duration_ms = ?, error_code = ?
                WHERE id = ?;
                """,
                (status.value, json.dumps(response_summary_json), duration_ms, error_code, event_id)
            )
        finally:
            conn.close()
