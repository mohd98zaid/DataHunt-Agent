-- Migration 005: Extended Application Tracking & Interview Prep
PRAGMA foreign_keys = ON;

-- Additional fields for job_application_status
ALTER TABLE job_application_status ADD COLUMN cover_letter_text TEXT;
ALTER TABLE job_application_status ADD COLUMN interview_notes TEXT;
ALTER TABLE job_application_status ADD COLUMN offer_details_json TEXT;
ALTER TABLE job_application_status ADD COLUMN outcome_at TEXT;

CREATE TABLE IF NOT EXISTS interview_prep_sessions (
    id TEXT PRIMARY KEY,
    record_id TEXT NOT NULL REFERENCES extracted_records(id) ON DELETE CASCADE,
    company_name TEXT NOT NULL,
    role_title TEXT NOT NULL,
    technical_questions_json TEXT NOT NULL DEFAULT '[]',
    behavioral_questions_json TEXT NOT NULL DEFAULT '[]',
    role_questions_json TEXT NOT NULL DEFAULT '[]',
    company_questions_json TEXT NOT NULL DEFAULT '[]',
    interviewer_questions_json TEXT NOT NULL DEFAULT '[]',
    user_answers_json TEXT NOT NULL DEFAULT '[]',
    feedback_notes TEXT DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_prep_record ON interview_prep_sessions(record_id);
