-- Migration 007: Durable Checkpoints for Resumable Discovery & Crawling
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS run_checkpoints (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES research_runs(id) ON DELETE CASCADE,
    checkpoint_seq INTEGER NOT NULL DEFAULT 1,
    state_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(run_id, checkpoint_seq)
);

CREATE INDEX IF NOT EXISTS idx_checkpoints_run ON run_checkpoints(run_id);
CREATE INDEX IF NOT EXISTS idx_checkpoints_run_seq ON run_checkpoints(run_id, checkpoint_seq);
