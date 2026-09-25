-- Migration 004: User Preferences & Saved Jobs
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS saved_jobs (
    record_id TEXT PRIMARY KEY REFERENCES extracted_records(id) ON DELETE CASCADE,
    saved_at TEXT NOT NULL,
    notes TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS ignored_jobs (
    record_id TEXT PRIMARY KEY REFERENCES extracted_records(id) ON DELETE CASCADE,
    reason TEXT DEFAULT 'other',
    ignored_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS user_preferences (
    id TEXT PRIMARY KEY DEFAULT 'default',
    preferred_titles_json TEXT DEFAULT '[]',
    preferred_locations_json TEXT DEFAULT '[]',
    preferred_skills_json TEXT DEFAULT '[]',
    salary_floor REAL,
    experience_target_min INTEGER,
    experience_target_max INTEGER,
    remote_preference TEXT DEFAULT 'any',
    ignored_companies_json TEXT DEFAULT '[]',
    positive_signals_json TEXT DEFAULT '{}',
    negative_signals_json TEXT DEFAULT '{}',
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_saved_jobs_date ON saved_jobs(saved_at);
CREATE INDEX IF NOT EXISTS idx_ignored_jobs_date ON ignored_jobs(ignored_at);
