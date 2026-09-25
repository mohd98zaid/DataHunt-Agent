-- Migration 006: Continuous Job Monitoring & Company Research Profiles
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS company_research_profiles (
    id TEXT PRIMARY KEY,
    company_name TEXT NOT NULL UNIQUE,
    normalized_name TEXT NOT NULL,
    profile_json TEXT NOT NULL DEFAULT '{}',
    sources_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS job_change_events (
    id TEXT PRIMARY KEY,
    record_id TEXT NOT NULL REFERENCES extracted_records(id) ON DELETE CASCADE,
    change_type TEXT NOT NULL CHECK (change_type IN ('closed','removed','updated','salary_changed','location_changed')),
    old_value TEXT,
    new_value TEXT,
    detected_at TEXT NOT NULL,
    notified INTEGER NOT NULL DEFAULT 0 CHECK (notified IN (0,1))
);

CREATE TABLE IF NOT EXISTS discovery_schedules (
    id TEXT PRIMARY KEY DEFAULT 'default',
    query_text TEXT NOT NULL,
    structured_spec_json TEXT NOT NULL DEFAULT '{}',
    interval_hours INTEGER NOT NULL DEFAULT 24,
    last_run_at TEXT,
    next_run_at TEXT,
    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0,1)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_company_name ON company_research_profiles(company_name);
CREATE INDEX IF NOT EXISTS idx_change_events_record ON job_change_events(record_id, change_type);
CREATE INDEX IF NOT EXISTS idx_schedules_enabled ON discovery_schedules(enabled);
