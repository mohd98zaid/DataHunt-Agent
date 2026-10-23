"""
SettingsRepository — Persistent storage for user-customized agent and system settings.
Ensures that all configuration modifications made via the UI persist across
server shutdown, reboots, and browser session closures.
"""
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from datahunt.db.connection import get_connection
from datahunt.logger import logger


class SettingsRepository:
    """Manages persistent key-value configuration overrides in SQLite."""

    def __init__(self, conn_factory=get_connection):
        self.conn_factory = conn_factory
        self._ensure_table()

    def _ensure_table(self) -> None:
        """Create the system_settings table if it doesn't already exist."""
        conn = self.conn_factory()
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS system_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
            """)
            conn.commit()
        except Exception as e:
            logger.warning(f"Error initializing system_settings table: {e}")
        finally:
            conn.close()

    def get_all(self) -> Dict[str, str]:
        """Fetch all persistent settings overrides from SQLite."""
        conn = self.conn_factory()
        try:
            rows = conn.execute("SELECT key, value FROM system_settings;").fetchall()
            return {row["key"]: row["value"] for row in rows}
        except Exception as e:
            logger.warning(f"Error reading system_settings: {e}")
            return {}
        finally:
            conn.close()

    def get(self, key: str, default: Optional[str] = None) -> Optional[str]:
        """Fetch a single setting by key."""
        conn = self.conn_factory()
        try:
            row = conn.execute("SELECT value FROM system_settings WHERE key = ?;", (key,)).fetchone()
            return row["value"] if row else default
        except Exception as e:
            logger.warning(f"Error reading system_settings key '{key}': {e}")
            return default
        finally:
            conn.close()

    def set_many(self, settings_map: Dict[str, Any]) -> None:
        """Atomically persist multiple key-value settings overrides."""
        conn = self.conn_factory()
        try:
            conn.execute("BEGIN")
            now = datetime.now(timezone.utc).isoformat()
            for k, v in settings_map.items():
                conn.execute(
                    "INSERT OR REPLACE INTO system_settings (key, value, updated_at) VALUES (?, ?, ?);",
                    (str(k), str(v), now)
                )
            conn.commit()
        except Exception as e:
            conn.execute("ROLLBACK")
            logger.error(f"Failed to persist system_settings: {e}")
            raise
        finally:
            conn.close()

    def delete(self, key: str) -> None:
        """Remove a specific setting override."""
        conn = self.conn_factory()
        try:
            conn.execute("DELETE FROM system_settings WHERE key = ?;", (key,))
            conn.commit()
        finally:
            conn.close()

    def clear_all(self) -> None:
        """Clear all stored settings overrides (reverting to system defaults)."""
        conn = self.conn_factory()
        try:
            conn.execute("DELETE FROM system_settings;")
            conn.commit()
        finally:
            conn.close()
