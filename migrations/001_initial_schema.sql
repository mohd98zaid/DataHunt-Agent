-- Migration 001: Initial DataHunt SQLite Schema
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
