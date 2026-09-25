import json
import sqlite3
from datetime import datetime, timezone
from typing import Dict, List, Optional
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

    def _row_to_run(self, row: sqlite3.Row) -> ResearchRun:
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

    def get_run(self, run_id: str) -> Optional[ResearchRun]:
        conn = self.conn_factory()
        try:
            row = conn.execute("SELECT * FROM research_runs WHERE id = ?;", (run_id,)).fetchone()
            if not row:
                return None
            return self._row_to_run(row)
        finally:
            conn.close()

    def get_latest_run(self) -> Optional[ResearchRun]:
        conn = self.conn_factory()
        try:
            row = conn.execute("SELECT * FROM research_runs ORDER BY created_at DESC LIMIT 1;").fetchone()
            if not row:
                return None
            return self._row_to_run(row)
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
            conn.execute('BEGIN')
            row = conn.execute("SELECT warning_json FROM research_runs WHERE id = ?;", (run_id,)).fetchone()
            warnings = json.loads(row["warning_json"] or '[]') if row else []
            warnings.append(warning)
            now_str = datetime.now(timezone.utc).isoformat()
            conn.execute(
                "UPDATE research_runs SET warning_json = ?, updated_at = ? WHERE id = ?;",
                (json.dumps(warnings), now_str, run_id)
            )
            conn.execute('COMMIT')
        except Exception:
            conn.execute('ROLLBACK')
            raise
        finally:
            conn.close()

    def list_recent_runs(self, limit: int = 50) -> List[ResearchRun]:
        conn = self.conn_factory()
        try:
            rows = conn.execute(
                "SELECT * FROM research_runs ORDER BY created_at DESC LIMIT ?;", (limit,)
            ).fetchall()
            return [self._row_to_run(row) for row in rows]
        finally:
            conn.close()

class DocumentRepository:
    def __init__(self, conn_factory=get_connection):
        self.conn_factory = conn_factory

    def upsert_document(self, doc: SourceDocument) -> SourceDocument:
        conn = self.conn_factory()
        try:
            c_url = doc.canonical_url or doc.requested_url
            existing = conn.execute(
                "SELECT id FROM source_documents WHERE run_id = ? AND canonical_url = ?;",
                (doc.run_id, c_url)
            ).fetchone()
            if existing:
                doc.id = existing["id"]

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

    def _row_to_evidence(self, er: sqlite3.Row) -> RecordEvidence:
        return RecordEvidence(
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

    def _row_to_record(self, r: sqlite3.Row, evidence_list: List[RecordEvidence]) -> ExtractedRecord:
        return ExtractedRecord(
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

    def insert_record(self, record: ExtractedRecord) -> ExtractedRecord:
        conn = self.conn_factory()
        try:
            conn.execute('BEGIN')
            # Verify source_document_id exists to prevent FK violation
            doc_id = record.source_document_id
            if doc_id:
                check = conn.execute("SELECT 1 FROM source_documents WHERE id = ?;", (doc_id,)).fetchone()
                if not check:
                    fallback = conn.execute("SELECT id FROM source_documents WHERE run_id = ? LIMIT 1;", (record.run_id,)).fetchone()
                    doc_id = fallback["id"] if fallback else None
            record.source_document_id = doc_id

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
                    doc_id,
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
            # Insert any evidence attached atomically only if doc_id is valid
            if doc_id:
                for ev in record.evidence:
                    ev_doc_id = ev.source_document_id or doc_id
                    ev_check = conn.execute("SELECT 1 FROM source_documents WHERE id = ?;", (ev_doc_id,)).fetchone()
                    if not ev_check:
                        ev_doc_id = doc_id
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
                            ev_doc_id,
                            ev.field_name,
                            ev.evidence_text,
                            json.dumps(ev.locator),
                            ev.evidence_type,
                            ev.supports_value,
                            ev.created_at,
                        )
                    )
            conn.execute('COMMIT')
            return record
        except Exception:
            conn.execute('ROLLBACK')
            raise
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
                
            if not rows:
                return []

            record_ids = [r["id"] for r in rows]
            placeholders = ",".join("?" * len(record_ids))
            ev_rows = conn.execute(
                f"SELECT * FROM record_evidence WHERE record_id IN ({placeholders});",
                record_ids
            ).fetchall()

            evidence_by_record: Dict[str, List[RecordEvidence]] = {}
            for er in ev_rows:
                evidence_by_record.setdefault(er["record_id"], []).append(self._row_to_evidence(er))

            return [
                self._row_to_record(r, evidence_by_record.get(r["id"], []))
                for r in rows
            ]
        finally:
            conn.close()

    def update_record_status(self, record_id: str, status: VerificationStatus, confidence: Optional[float] = None) -> None:
        conn = self.conn_factory()
        try:
            now_str = datetime.now(timezone.utc).isoformat()
            if confidence is not None:
                conn.execute(
                    "UPDATE extracted_records SET verification_status = ?, confidence = ?, updated_at = ? WHERE id = ?;",
                    (status.value, confidence, now_str, record_id)
                )
            else:
                conn.execute(
                    "UPDATE extracted_records SET verification_status = ?, updated_at = ? WHERE id = ?;",
                    (status.value, now_str, record_id)
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

    def get_record(self, record_id: str) -> Optional[ExtractedRecord]:
        conn = self.conn_factory()
        try:
            r = conn.execute("SELECT * FROM extracted_records WHERE id = ?;", (record_id,)).fetchone()
            if not r:
                return None
            ev_rows = conn.execute(
                "SELECT * FROM record_evidence WHERE record_id = ?;", (r["id"],)
            ).fetchall()
            evidence_list = [self._row_to_evidence(er) for er in ev_rows]
            return self._row_to_record(r, evidence_list)
        finally:
            conn.close()

class JobTrackingRepository:
    def __init__(self, conn_factory=get_connection):
        self.conn_factory = conn_factory

    def get_job_detail(self, record_id: str) -> Optional[Dict[str, Any]]:
        conn = self.conn_factory()
        try:
            query = """
                SELECT 
                    r.id, r.run_id, r.source_document_id, r.canonical_url,
                    r.fields_json, r.confidence, r.verification_status, r.created_at as scraped_at,
                    s.applied_status, s.applied_at, s.interview_status, s.notes,
                    d.title as doc_title, d.extracted_text as job_description, d.domain
                FROM extracted_records r
                LEFT JOIN job_application_status s ON r.id = s.record_id
                LEFT JOIN source_documents d ON r.source_document_id = d.id
                WHERE r.id = ?;
            """
            row = conn.execute(query, (record_id,)).fetchone()
            if not row:
                return None

            ev_rows = conn.execute(
                "SELECT field_name, evidence_text FROM record_evidence WHERE record_id = ?;", (record_id,)
            ).fetchall()

            fields = json.loads(row["fields_json"]) if row["fields_json"] else {}
            return {
                "id": row["id"],
                "run_id": row["run_id"],
                "canonical_url": row["canonical_url"] or fields.get("application_url"),
                "fields": fields,
                "confidence": row["confidence"],
                "verification_status": row["verification_status"],
                "scraped_at": row["scraped_at"],
                "applied_status": row["applied_status"] or "not_applied",
                "applied_at": row["applied_at"],
                "interview_status": row["interview_status"] or "no_call",
                "notes": row["notes"] or "",
                "doc_title": row["doc_title"],
                "domain": row["domain"],
                "job_description": row["job_description"] or fields.get("description") or "",
                "evidence": [{"field": er["field_name"], "text": er["evidence_text"]} for er in ev_rows]
            }
        finally:
            conn.close()

    def list_jobs(self, run_id: Optional[str] = None, limit: int = 200) -> List[Dict[str, Any]]:
        conn = self.conn_factory()
        try:
            sql = """
                SELECT 
                    r.id, r.run_id, r.source_document_id, r.canonical_url,
                    r.fields_json, r.confidence, r.verification_status, r.created_at as scraped_at,
                    s.applied_status, s.applied_at, s.interview_status, s.notes,
                    d.title as doc_title, d.domain
                FROM extracted_records r
                LEFT JOIN job_application_status s ON r.id = s.record_id
                LEFT JOIN source_documents d ON r.source_document_id = d.id
                WHERE (
                    r.record_type = 'job_listing' 
                    OR r.canonical_url LIKE '%greenhouse.io%'
                    OR r.canonical_url LIKE '%lever.co%'
                    OR r.canonical_url LIKE '%ashbyhq.com%'
                    OR r.canonical_url LIKE '%workable.com%'
                    OR r.fields_json LIKE '%"salary"%'
                    OR r.fields_json LIKE '%"employment_type"%'
                    OR r.fields_json LIKE '%"company"%'
                )
            """
            params = []
            if run_id:
                sql += " AND r.run_id = ?"
                params.append(run_id)

            sql += " ORDER BY r.created_at DESC LIMIT ?;"
            params.append(limit)

            rows = conn.execute(sql, params).fetchall()
            jobs = []
            for row in rows:
                fields = json.loads(row["fields_json"]) if row["fields_json"] else {}
                jobs.append({
                    "id": row["id"],
                    "run_id": row["run_id"],
                    "canonical_url": row["canonical_url"] or fields.get("application_url"),
                    "fields": fields,
                    "confidence": row["confidence"],
                    "verification_status": row["verification_status"],
                    "scraped_at": row["scraped_at"],
                    "applied_status": row["applied_status"] or "not_applied",
                    "applied_at": row["applied_at"],
                    "interview_status": row["interview_status"] or "no_call",
                    "notes": row["notes"] or "",
                    "doc_title": row["doc_title"],
                    "domain": row["domain"]
                })
            return jobs
        finally:
            conn.close()

    def update_status(
        self,
        record_id: str,
        applied_status: Optional[str] = None,
        applied_at: Optional[str] = None,
        interview_status: Optional[str] = None,
        notes: Optional[str] = None
    ) -> Dict[str, Any]:
        conn = self.conn_factory()
        try:
            now_str = datetime.now(timezone.utc).isoformat()
            # Ensure row exists
            existing = conn.execute("SELECT * FROM job_application_status WHERE record_id = ?;", (record_id,)).fetchone()
            if not existing:
                conn.execute(
                    """
                    INSERT INTO job_application_status (
                        record_id, applied_status, applied_at, interview_status, notes, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?);
                    """,
                    (
                        record_id,
                        applied_status or "not_applied",
                        applied_at or (now_str if applied_status == "applied" else None),
                        interview_status or "no_call",
                        notes or "",
                        now_str
                    )
                )
            else:
                new_applied = applied_status if applied_status is not None else existing["applied_status"]
                new_applied_at = applied_at if applied_at is not None else (
                    now_str if (new_applied == "applied" and not existing["applied_at"]) else existing["applied_at"]
                )
                new_interview = interview_status if interview_status is not None else existing["interview_status"]
                new_notes = notes if notes is not None else existing["notes"]

                conn.execute(
                    """
                    UPDATE job_application_status 
                    SET applied_status = ?, applied_at = ?, interview_status = ?, notes = ?, updated_at = ?
                    WHERE record_id = ?;
                    """,
                    (new_applied, new_applied_at, new_interview, new_notes, now_str, record_id)
                )

            return self.get_job_detail(record_id)
        finally:
            conn.close()

    def delete_job(self, record_id: str) -> bool:
        """Permanently delete a job record, its tracking status, and associated evidence."""
        conn = self.conn_factory()
        try:
            with conn:
                conn.execute("DELETE FROM job_application_status WHERE record_id = ?;", (record_id,))
                conn.execute("DELETE FROM record_evidence WHERE record_id = ?;", (record_id,))
                cur = conn.execute("DELETE FROM extracted_records WHERE id = ?;", (record_id,))
                return cur.rowcount > 0
        finally:
            conn.close()

    def delete_jobs(self, record_ids: List[str]) -> int:
        """Permanently bulk delete multiple job records within a transaction."""
        if not record_ids:
            return 0
        conn = self.conn_factory()
        try:
            placeholders = ",".join(["?"] * len(record_ids))
            with conn:
                conn.execute(f"DELETE FROM job_application_status WHERE record_id IN ({placeholders});", record_ids)
                conn.execute(f"DELETE FROM record_evidence WHERE record_id IN ({placeholders});", record_ids)
                cur = conn.execute(f"DELETE FROM extracted_records WHERE id IN ({placeholders});", record_ids)
                return cur.rowcount
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
