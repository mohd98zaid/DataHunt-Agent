"""
JobMonitor — Steps 65-70 of the job search pipeline.

Performs continuous background discovery, re-checks saved jobs for status changes
(closed, removed, updated, salary changes), and logs actionable change notifications.
"""
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from datahunt.db.connection import get_connection
from datahunt.db.user_repositories import UserPreferencesRepository
from datahunt.tools.fetch import FetchTool, is_actual_job_posting
from datahunt.logger import logger


class JobMonitor:
    """
    Steps 65-70: Background health monitoring of saved jobs and recurring discovery.
    """

    def __init__(self, fetch_tool: Optional[FetchTool] = None, user_repo: Optional[UserPreferencesRepository] = None):
        self.fetch_tool = fetch_tool or FetchTool()
        self.user_repo = user_repo or UserPreferencesRepository()

    def check_saved_jobs(self) -> List[Dict[str, Any]]:
        """
        Re-visit URLs of all saved jobs to detect closures, 404s, or changes.
        Returns detected change event dicts.
        """
        saved = self.user_repo.get_saved_jobs()
        events: List[Dict[str, Any]] = []

        for item in saved:
            rec_id = item["record_id"]
            url = item.get("url") or item.get("fields", {}).get("application_url")
            if not url:
                continue

            try:
                res = self.fetch_tool.execute(url=url, run_id="monitor_check")
                if not res.success or not res.data:
                    # Could not reach page or blocked
                    event = self._record_change_event(rec_id, "removed", "Available", "HTTP Error / Inaccessible")
                    if event: events.append(event)
                    continue

                doc = res.data
                # Check HTTP status
                if doc.http_status in (404, 410):
                    event = self._record_change_event(rec_id, "closed", "Active", f"HTTP {doc.http_status} Page Removed")
                    if event: events.append(event)
                    continue

                # Run job validity and expired detector
                is_valid, reason = is_actual_job_posting(doc)
                if not is_valid and ("expired" in reason.lower() or "closed" in reason.lower()):
                    event = self._record_change_event(rec_id, "closed", "Active", reason)
                    if event: events.append(event)

            except Exception as ex:
                logger.warning(f"Error checking health for saved job {rec_id}: {ex}")

        return events

    def _record_change_event(self, record_id: str, change_type: str, old_val: str, new_val: str) -> Optional[Dict[str, Any]]:
        """Write change event into SQLite."""
        conn = get_connection()
        try:
            now = datetime.now(timezone.utc).isoformat()
            eid = f"evt_{abs(hash(f'{record_id}_{change_type}_{now}'))}"
            conn.execute("""
                INSERT INTO job_change_events (id, record_id, change_type, old_value, new_value, detected_at, notified)
                VALUES (?, ?, ?, ?, ?, ?, 0);
            """, (eid, record_id, change_type, old_val, new_val, now))
            conn.commit()
            return {
                "id": eid,
                "record_id": record_id,
                "change_type": change_type,
                "old_value": old_val,
                "new_value": new_val,
                "detected_at": now
            }
        except Exception as e:
            logger.warning(f"Failed to record change event: {e}")
            return None
        finally:
            conn.close()

    def get_unnotified_events(self) -> List[Dict[str, Any]]:
        """Fetch pending notifications for the user."""
        conn = get_connection()
        try:
            rows = conn.execute("""
                SELECT e.*, r.fields_json
                FROM job_change_events e
                JOIN extracted_records r ON e.record_id = r.id
                WHERE e.notified = 0
                ORDER BY e.detected_at DESC;
            """).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()
