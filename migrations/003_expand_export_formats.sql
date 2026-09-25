-- Migration: 003_expand_export_formats.sql
-- Expand format check constraint on exports table to support 'md' and 'docx'

PRAGMA foreign_keys=off;

CREATE TABLE IF NOT EXISTS exports_new (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES research_runs(id) ON DELETE CASCADE,
    format TEXT NOT NULL CHECK (format IN ('json','csv','xlsx','md','docx')),
    file_name TEXT NOT NULL,
    storage_key TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    row_count INTEGER NOT NULL,
    include_evidence BOOLEAN NOT NULL DEFAULT 1,
    expires_at TEXT,
    created_at TEXT NOT NULL
);

INSERT OR IGNORE INTO exports_new SELECT * FROM exports;
DROP TABLE IF EXISTS exports;
ALTER TABLE exports_new RENAME TO exports;
CREATE INDEX IF NOT EXISTS idx_exports_run_id ON exports(run_id);

PRAGMA foreign_keys=on;
