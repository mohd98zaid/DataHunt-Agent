import tempfile
from pathlib import Path
import pytest
from datahunt.db import (
    get_connection, run_migrations,
    TaskRepository, RunRepository, DocumentRepository,
    RecordRepository, ExportRepository
)
from datahunt.models import (
    ResearchTask, ResearchSpec, TaskStatus,
    ResearchRun, RunStatus, SourceDocument,
    ExtractedRecord, RecordEvidence, VerificationStatus,
    ExportRecord
)

@pytest.fixture
def temp_db():
    temp_dir = tempfile.mkdtemp()
    db_path = Path(temp_dir) / "test_datahunt.sqlite3"
    yield db_path

def test_migration_idempotency_and_wal(temp_db):
    # Run migrations once
    applied1 = run_migrations(db_path=temp_db)
    assert len(applied1) > 0
    assert "001_initial_schema.sql" in applied1

    # Check WAL mode
    conn = get_connection(db_path=temp_db)
    row = conn.execute("PRAGMA journal_mode;").fetchone()
    assert row[0].lower() == "wal"
    conn.close()

    # Run migrations second time (must be idempotent)
    applied2 = run_migrations(db_path=temp_db)
    assert len(applied2) == 0

def test_repositories_crud(temp_db):
    run_migrations(db_path=temp_db)
    conn_factory = lambda: get_connection(temp_db)

    task_repo = TaskRepository(conn_factory=conn_factory)
    run_repo = RunRepository(conn_factory=conn_factory)
    doc_repo = DocumentRepository(conn_factory=conn_factory)
    rec_repo = RecordRepository(conn_factory=conn_factory)
    exp_repo = ExportRepository(conn_factory=conn_factory)

    # 1. Create Task
    spec = ResearchSpec(topic="AI Engineers")
    task = ResearchTask(request_text="Find AI Engineers", normalized_spec=spec)
    task_repo.create_task(task)
    retrieved_task = task_repo.get_task(task.id)
    assert retrieved_task is not None
    assert retrieved_task.normalized_spec.topic == "AI Engineers"

    # 2. Create Run
    run = ResearchRun(task_id=task.id)
    run_repo.create_run(run)
    retrieved_run = run_repo.get_run(run.id)
    assert retrieved_run is not None
    assert retrieved_run.status == RunStatus.PLANNED

    # 3. Create Document
    doc = SourceDocument(
        run_id=run.id,
        requested_url="https://example.com/jobs/1",
        domain="example.com",
        title="AI Engineer",
        extracted_text="Hiring AI Engineer"
    )
    doc_repo.upsert_document(doc)
    docs = doc_repo.list_documents_for_run(run.id)
    assert len(docs) == 1
    assert docs[0].title == "AI Engineer"

    # 4. Create Record and Evidence
    ev = RecordEvidence(
        record_id="rec_1",
        source_document_id=doc.id,
        field_name="title",
        evidence_text="AI Engineer"
    )
    rec = ExtractedRecord(
        id="rec_1",
        run_id=run.id,
        source_document_id=doc.id,
        fields={"title": "AI Engineer", "company": "Corp"},
        evidence=[ev]
    )
    rec_repo.insert_record(rec)
    records = rec_repo.list_records_for_run(run.id)
    assert len(records) == 1
    assert len(records[0].evidence) == 1
    assert records[0].evidence[0].evidence_text == "AI Engineer"

    # 5. Create Export
    exp = ExportRecord(
        run_id=run.id,
        format="json",
        file_name="export_test.json",
        storage_key="/path/export_test.json",
        sha256="abc123hash",
        row_count=1
    )
    exp_repo.insert_export(exp)
    exports = exp_repo.list_exports_for_run(run.id)
    assert len(exports) == 1
    assert exports[0].file_name == "export_test.json"
