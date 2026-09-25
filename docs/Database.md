# Research AI Agent — Database Design

SQLite is the default persistence layer for the limited-compute MVP. The schema keeps stable operational fields relational and puts task-specific fields in validated JSON. The same repository interfaces and mostly the same SQL can move to PostgreSQL later.

## 1. Storage rules

- Enable foreign keys and WAL mode in SQLite.
- Store times as UTC ISO-8601 text in SQLite; use `timestamptz` in PostgreSQL.
- Store UUIDs as text in SQLite; use `uuid` in PostgreSQL if desired.
- Never store the Gemini API key, cookies, passwords, or browser profiles in the database.
- Store raw HTML only when necessary for reproducibility, with size and retention limits. Prefer extracted text plus a content hash.
- Keep PII-like contact values out of logs. The database may hold an approved public-business contact only with source evidence and classification.
- Use JSON for flexible task schemas, but validate it at the application boundary before writing.

## 2. Core entities

```text
research_tasks 1--N research_runs
research_runs  1--N tool_events
research_runs  1--N source_documents
source_documents 1--N extracted_records
extracted_records 1--N record_evidence
research_runs 1--N exports
```

## 3. SQLite migration 001

The following is a starting migration. Add indexes as query patterns become known; do not create a large number of speculative indexes on SQLite.

```sql
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS research_tasks (
    id TEXT PRIMARY KEY,
    request_text TEXT NOT NULL,
    normalized_spec_json TEXT NOT NULL,
    policy_version TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    requested_output_format TEXT NOT NULL DEFAULT 'json',
    max_records INTEGER NOT NULL DEFAULT 50 CHECK (max_records BETWEEN 1 AND 10000),
    status TEXT NOT NULL DEFAULT 'accepted'
        CHECK (status IN ('accepted','cancelled','completed','failed')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS research_runs (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES research_tasks(id),
    status TEXT NOT NULL DEFAULT 'planned'
        CHECK (status IN ('planned','searching','collecting','extracting','verifying',
                          'deduplicating','exporting','completed','partial','failed','cancelled')),
    attempt INTEGER NOT NULL DEFAULT 1 CHECK (attempt > 0),
    started_at TEXT,
    finished_at TEXT,
    deadline_at TEXT,
    budget_json TEXT NOT NULL,
    counters_json TEXT NOT NULL DEFAULT '{}',
    warning_json TEXT NOT NULL DEFAULT '[]',
    error_code TEXT,
    error_message TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS source_documents (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES research_runs(id),
    requested_url TEXT NOT NULL,
    final_url TEXT,
    canonical_url TEXT,
    domain TEXT NOT NULL,
    source_type TEXT NOT NULL DEFAULT 'web'
        CHECK (source_type IN ('web','search_result','pdf','api','user_supplied')),
    http_status INTEGER,
    content_type TEXT,
    content_hash TEXT,
    etag TEXT,
    last_modified TEXT,
    title TEXT,
    extracted_text TEXT,
    text_truncated INTEGER NOT NULL DEFAULT 0 CHECK (text_truncated IN (0,1)),
    policy_flags_json TEXT NOT NULL DEFAULT '[]',
    retrieval_status TEXT NOT NULL DEFAULT 'fetched'
        CHECK (retrieval_status IN ('candidate','fetched','cached','blocked','failed','too_large')),
    retrieved_at TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(run_id, canonical_url)
);

CREATE TABLE IF NOT EXISTS extracted_records (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES research_runs(id),
    source_document_id TEXT REFERENCES source_documents(id),
    record_type TEXT NOT NULL,
    identity_key TEXT,
    canonical_url TEXT,
    fields_json TEXT NOT NULL,
    normalized_fields_json TEXT NOT NULL DEFAULT '{}',
    verification_status TEXT NOT NULL DEFAULT 'unverified'
        CHECK (verification_status IN ('unverified','verified','needs_review','rejected','duplicate')),
    confidence REAL CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1)),
    warnings_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS record_evidence (
    id TEXT PRIMARY KEY,
    record_id TEXT NOT NULL REFERENCES extracted_records(id) ON DELETE CASCADE,
    source_document_id TEXT NOT NULL REFERENCES source_documents(id),
    field_name TEXT NOT NULL,
    evidence_text TEXT,
    locator_json TEXT NOT NULL DEFAULT '{}',
    evidence_type TEXT NOT NULL DEFAULT 'page_text'
        CHECK (evidence_type IN ('page_text','search_snippet','structured_data','user_supplied')),
    supports_value INTEGER NOT NULL DEFAULT 1 CHECK (supports_value IN (0,1)),
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS exports (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES research_runs(id),
    format TEXT NOT NULL CHECK (format IN ('json','csv','xlsx')),
    file_name TEXT NOT NULL,
    storage_key TEXT NOT NULL,
    sha256 TEXT,
    row_count INTEGER NOT NULL DEFAULT 0,
    include_evidence INTEGER NOT NULL DEFAULT 1 CHECK (include_evidence IN (0,1)),
    expires_at TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tool_events (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES research_runs(id),
    step_number INTEGER NOT NULL,
    tool_name TEXT NOT NULL,
    request_json TEXT NOT NULL DEFAULT '{}',
    response_summary_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL CHECK (status IN ('started','succeeded','failed','blocked','timed_out')),
    error_code TEXT,
    duration_ms INTEGER,
    request_hash TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS suppression_entries (
    id TEXT PRIMARY KEY,
    value_hash TEXT NOT NULL,
    value_type TEXT NOT NULL CHECK (value_type IN ('email','phone','domain','url','company')),
    reason TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'operator',
    expires_at TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(value_hash, value_type)
);

CREATE TABLE IF NOT EXISTS schema_migrations (
    version TEXT PRIMARY KEY,
    applied_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_runs_task_status ON research_runs(task_id, status);
CREATE INDEX IF NOT EXISTS idx_documents_run_domain ON source_documents(run_id, domain);
CREATE INDEX IF NOT EXISTS idx_documents_hash ON source_documents(content_hash);
CREATE INDEX IF NOT EXISTS idx_records_run_status ON extracted_records(run_id, verification_status);
CREATE INDEX IF NOT EXISTS idx_records_identity ON extracted_records(record_type, identity_key);
CREATE INDEX IF NOT EXISTS idx_evidence_record_field ON record_evidence(record_id, field_name);
CREATE INDEX IF NOT EXISTS idx_tool_events_run_step ON tool_events(run_id, step_number);
CREATE INDEX IF NOT EXISTS idx_suppression_type_hash ON suppression_entries(value_type, value_hash);
```

## 4. JSON contracts

### `research_tasks.normalized_spec_json`

```json
{
  "topic": "AI/ML jobs",
  "geography": {"name": "Dubai", "country": "AE"},
  "date_filter": {"kind": "posted_at", "after": "2026-09-01", "before": "2026-09-08"},
  "requested_fields": ["title", "company", "location", "posted_at", "application_url"],
  "max_records": 50,
  "source_policy": {"allowed_domains": [], "blocked_domains": []},
  "contact_policy": "business_public_only",
  "quality_bar": "every required field needs evidence or null"
}
```

### `research_runs.budget_json`

```json
{
  "deadline_seconds": 300,
  "max_tool_steps": 30,
  "max_search_queries": 12,
  "max_pages": 40,
  "max_browser_pages": 8,
  "max_response_bytes": 2000000,
  "max_output_records": 250
}
```

### `extracted_records.fields_json`

Keep the task-specific result in a JSON object, but reserve a few normalized fields for deduplication:

```json
{
  "title": "Machine Learning Engineer",
  "company": "Example AI Ltd",
  "location": "Dubai, UAE",
  "posted_at": "2026-09-05",
  "application_url": "https://example.com/jobs/123",
  "public_business_email": null,
  "public_business_phone": null
}
```

`normalized_fields_json` should contain normalized comparison values, not display text:

```json
{
  "company": "example ai ltd",
  "title": "machine learning engineer",
  "location": "dubai ae",
  "posted_at": "2026-09-05",
  "application_url": "https://example.com/jobs/123"
}
```

## 5. Transaction boundaries and idempotency

- Create task and initial run in one transaction.
- Start/end each tool event in its own short transaction so a crash leaves a visible unfinished event.
- Upsert a source document by `(run_id, canonical_url)`.
- Insert records with a generated ID; calculate `identity_key` before insert and use it for dedupe queries.
- Insert evidence in the same transaction as the record.
- Write the export to a temporary file, fsync/close it, compute SHA-256, then atomically rename and insert the `exports` row.
- Mark a run `completed` only after the export row is durable.
- On restart, mark stale `started` tool events as `timed_out` and resume from the last durable run phase.

## 6. SQLite configuration

Apply on every connection:

```sql
PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;
PRAGMA busy_timeout = 5000;
```

Use one write connection per process and short transactions. Avoid holding a transaction while making a network or Gemini call.

## 7. Migration path to PostgreSQL

1. Keep SQL behind repositories (`TaskRepository`, `RunRepository`, `DocumentRepository`, `RecordRepository`, `ExportRepository`).
2. Replace SQLite `TEXT` timestamps with `TIMESTAMPTZ` and UUID text with `UUID` only when the deployment needs it.
3. Replace JSON text with `JSONB`; add GIN indexes only for proven query patterns.
4. Change `INTEGER` booleans to `BOOLEAN`.
5. Use PostgreSQL advisory locks or a queue for run claiming.
6. Keep the same status values and event semantics.
7. Run a dual-read or offline migration rehearsal before switching writes.
8. Verify row counts, hashes, foreign keys, and sample export equality.

### PostgreSQL concurrency notes

Use `SELECT ... FOR UPDATE SKIP LOCKED` for queued runs. Keep a unique constraint on `(run_id, canonical_url)` and on `(value_hash, value_type)`. Store large source snapshots in object storage, retaining only a key and checksum in the database.

## 8. Retention and deletion

Suggested defaults, subject to the operator’s legal and privacy review:

| Data | Default retention |
|---|---:|
| Task/run metadata | 90 days |
| Source extracted text | 14 days |
| Export files | 7 days |
| Tool event summaries | 90 days |
| Suppression entries | until expiry or explicit removal |
| Raw browser artifacts | disabled by default |

Deletion must be auditable. Prefer a scheduled cleanup job that deletes expired exports and source text while retaining aggregate metrics and a deletion event.

