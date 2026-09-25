-- 002_job_tracking.sql
-- Adds job_application_status table to track application and interview progress

CREATE TABLE IF NOT EXISTS job_application_status (
    record_id TEXT PRIMARY KEY REFERENCES extracted_records(id) ON DELETE CASCADE,
    applied_status TEXT NOT NULL DEFAULT 'not_applied'
        CHECK (applied_status IN ('not_applied', 'applied', 'interviewing', 'offered', 'rejected')),
    applied_at TEXT,
    interview_status TEXT NOT NULL DEFAULT 'no_call'
        CHECK (interview_status IN ('no_call', 'screening', 'technical', 'final_round', 'offered', 'rejected')),
    notes TEXT DEFAULT '',
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_job_status_applied ON job_application_status(applied_status);
CREATE INDEX IF NOT EXISTS idx_job_status_interview ON job_application_status(interview_status);
