import tempfile
from pathlib import Path
import pytest
from datahunt.db import (
    get_connection, run_migrations,
    TaskRepository, RunRepository, DocumentRepository,
    RecordRepository, ExportRepository, JobTrackingRepository
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


def test_job_tracking_repository_list_jobs(temp_db):
    run_migrations(db_path=temp_db)
    conn_factory = lambda: get_connection(temp_db)
    task_repo = TaskRepository(conn_factory=conn_factory)
    run_repo = RunRepository(conn_factory=conn_factory)
    rec_repo = RecordRepository(conn_factory=conn_factory)
    job_repo = JobTrackingRepository(conn_factory=conn_factory)

    task = ResearchTask(request_text="Jobs", normalized_spec=ResearchSpec(topic="Jobs"))
    task_repo.create_task(task)
    run = ResearchRun(task_id=task.id)
    run_repo.create_run(run)

    # 1. Insert multiple distinct jobs at the same company
    r1 = ExtractedRecord(
        id="rec_sw_ai",
        run_id=run.id,
        record_type="job_listing",
        canonical_url="https://boards.greenhouse.io/devrev",
        fields={"title": "Software Engineer - Applied AI", "company": "Devrev", "application_url": "https://job-boards.greenhouse.io/devrev/jobs/5722574004"},
        verification_status=VerificationStatus.VERIFIED,
        confidence=0.9
    )
    r2 = ExtractedRecord(
        id="rec_fwd_ai",
        run_id=run.id,
        record_type="job_listing",
        canonical_url="https://boards.greenhouse.io/devrev",
        fields={"title": "Forward Deployed Engineer - Applied AI", "company": "Devrev", "application_url": "https://job-boards.greenhouse.io/devrev/jobs/5837052004"},
        verification_status=VerificationStatus.NEEDS_REVIEW,
        confidence=0.75
    )
    # A duplicate copy of r1
    r1_dup = ExtractedRecord(
        id="rec_sw_ai_dup",
        run_id=run.id,
        record_type="job_listing",
        canonical_url="https://boards.greenhouse.io/devrev",
        fields={"title": "Software Engineer - Applied AI", "company": "Devrev", "application_url": "https://job-boards.greenhouse.io/devrev/jobs/5722574004"},
        verification_status=VerificationStatus.DUPLICATE,
        confidence=0.9
    )

    rec_repo.insert_record(r1)
    rec_repo.insert_record(r2)
    rec_repo.insert_record(r1_dup)

    # list_jobs with run_id
    jobs_run = job_repo.list_jobs(run_id=run.id)
    assert len(jobs_run) == 2  # exactly 2 unique jobs, duplicate is deduplicated
    titles = {j["fields"]["title"] for j in jobs_run}
    assert "Software Engineer - Applied AI" in titles
    assert "Forward Deployed Engineer - Applied AI" in titles

    # Check that the verified row was chosen for Software Engineer - Applied AI
    sw_job = next(j for j in jobs_run if j["fields"]["title"] == "Software Engineer - Applied AI")
    assert sw_job["id"] == "rec_sw_ai"
    assert sw_job["verification_status"] == "verified"

