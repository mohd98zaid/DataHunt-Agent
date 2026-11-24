-- Migration 008: Blocked Domains for Site-Level Exclusion
CREATE TABLE IF NOT EXISTS blocked_domains (
    domain TEXT PRIMARY KEY,
    reason TEXT DEFAULT '',
    blocked_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_blocked_domains_date ON blocked_domains(blocked_at);
