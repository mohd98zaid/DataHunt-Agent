"""
UserRepositories — Persistence layer for saved jobs, user preferences,
extended application tracking, company research, and monitoring.
"""
import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from datahunt.db.connection import get_connection, db_transaction


class UserPreferencesRepository:
    """Manages saved jobs, ignored jobs, and learned user preferences."""
    def __init__(self, conn_factory=get_connection):
        self.conn_factory = conn_factory

    def save_job(self, record_id: str, notes: str = "") -> None:
        conn = self.conn_factory()
        try:
            now = datetime.now(timezone.utc).isoformat()
            conn.execute(
                "INSERT OR REPLACE INTO saved_jobs (record_id, saved_at, notes) VALUES (?, ?, ?);",
                (record_id, now, notes)
            )
        finally:
            conn.close()

    def ignore_job(self, record_id: str, reason: str = "other") -> None:
        conn = self.conn_factory()
        try:
            now = datetime.now(timezone.utc).isoformat()
            conn.execute(
                "INSERT OR REPLACE INTO ignored_jobs (record_id, reason, ignored_at) VALUES (?, ?, ?);",
                (record_id, reason, now)
            )
        finally:
            conn.close()

    def get_saved_jobs(self) -> List[Dict[str, Any]]:
        conn = self.conn_factory()
        try:
            rows = conn.execute("""
                SELECT s.record_id, s.saved_at, s.notes, r.fields_json, r.canonical_url
                FROM saved_jobs s
                JOIN extracted_records r ON s.record_id = r.id
                ORDER BY s.saved_at DESC;
            """).fetchall()
            out = []
            for r in rows:
                f = json.loads(r["fields_json"]) if r["fields_json"] else {}
                out.append({
                    "record_id": r["record_id"],
                    "saved_at": r["saved_at"],
                    "notes": r["notes"],
                    "fields": f,
                    "url": r["canonical_url"]
                })
            return out
        finally:
            conn.close()

    def get_ignored_job_ids(self) -> List[str]:
        conn = self.conn_factory()
        try:
            rows = conn.execute("SELECT record_id FROM ignored_jobs;").fetchall()
            return [r["record_id"] for r in rows]
        finally:
            conn.close()

    def get_preferences(self, user_id: str = "default") -> Dict[str, Any]:
        conn = self.conn_factory()
        try:
            row = conn.execute("SELECT * FROM user_preferences WHERE id = ?;", (user_id,)).fetchone()
            if not row:
                return {
                    "preferred_titles": [],
                    "preferred_locations": [],
                    "preferred_skills": [],
                    "positive_signals": {},
                    "negative_signals": {},
                    "remote_preference": "any"
                }
            return {
                "preferred_titles": json.loads(row["preferred_titles_json"]),
                "preferred_locations": json.loads(row["preferred_locations_json"]),
                "preferred_skills": json.loads(row["preferred_skills_json"]),
                "positive_signals": json.loads(row["positive_signals_json"]),
                "negative_signals": json.loads(row["negative_signals_json"]),
                "remote_preference": row["remote_preference"]
            }
        finally:
            conn.close()

    def update_preferences(self, user_id: str, prefs: Dict[str, Any]) -> None:
        conn = self.conn_factory()
        try:
            now = datetime.now(timezone.utc).isoformat()
            conn.execute("""
                INSERT OR REPLACE INTO user_preferences (
                    id, preferred_titles_json, preferred_locations_json, preferred_skills_json,
                    salary_floor, experience_target_min, experience_target_max,
                    remote_preference, ignored_companies_json, positive_signals_json,
                    negative_signals_json, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """, (
                user_id,
                json.dumps(prefs.get("preferred_titles", [])),
                json.dumps(prefs.get("preferred_locations", [])),
                json.dumps(prefs.get("preferred_skills", [])),
                prefs.get("salary_floor"),
                prefs.get("experience_target_min"),
                prefs.get("experience_target_max"),
                prefs.get("remote_preference", "any"),
                json.dumps(prefs.get("ignored_companies", [])),
                json.dumps(prefs.get("positive_signals", {})),
                json.dumps(prefs.get("negative_signals", {})),
                now
            ))
        finally:
            conn.close()


class ApplicationTrackingRepository:
    """Manages job application lifecycle stages and preparation assets."""
    def __init__(self, conn_factory=get_connection):
        self.conn_factory = conn_factory

    def update_stage(self, record_id: str, status: str, notes: str = "", cover_letter: str = "") -> None:
        conn = self.conn_factory()
        try:
            now = datetime.now(timezone.utc).isoformat()
            conn.execute("""
                INSERT OR REPLACE INTO job_application_status (
                    record_id, applied_status, applied_at, notes,
                    cover_letter_text, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?);
            """, (record_id, status, now, notes, cover_letter, now))
        finally:
            conn.close()

    def get_applications(self) -> List[Dict[str, Any]]:
        conn = self.conn_factory()
        try:
            rows = conn.execute("""
                SELECT a.*, r.fields_json, r.canonical_url
                FROM job_application_status a
                JOIN extracted_records r ON a.record_id = r.id
                ORDER BY a.updated_at DESC;
            """).fetchall()
            out = []
            for r in rows:
                out.append({
                    "record_id": r["record_id"],
                    "status": r["applied_status"],
                    "applied_at": r["applied_at"],
                    "interview_status": r["interview_status"],
                    "notes": r["notes"],
                    "cover_letter": r["cover_letter_text"],
                    "job": json.loads(r["fields_json"]) if r["fields_json"] else {},
                    "url": r["canonical_url"]
                })
            return out
        finally:
            conn.close()


class CompanyResearchRepository:
    """Stores cached company intelligence dossiers."""
    def __init__(self, conn_factory=get_connection):
        self.conn_factory = conn_factory

    def get_profile(self, company_name: str) -> Optional[Dict[str, Any]]:
        conn = self.conn_factory()
        try:
            norm = company_name.lower().strip()
            row = conn.execute("SELECT * FROM company_research_profiles WHERE normalized_name = ?;", (norm,)).fetchone()
            if row:
                return json.loads(row["profile_json"])
            return None
        finally:
            conn.close()

    def save_profile(self, company_name: str, profile: Dict[str, Any], sources: List[str] = None) -> None:
        conn = self.conn_factory()
        try:
            now = datetime.now(timezone.utc).isoformat()
            norm = company_name.lower().strip()
            cid = f"comp_{abs(hash(norm))}"
            conn.execute("""
                INSERT OR REPLACE INTO company_research_profiles (
                    id, company_name, normalized_name, profile_json, sources_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?);
            """, (cid, company_name, norm, json.dumps(profile), json.dumps(sources or []), now, now))
        finally:
            conn.close()
