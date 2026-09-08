from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional
import sqlite3
from datahunt.db.connection import get_connection
from datahunt.logger import logger

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent.parent / "migrations"

def run_migrations(db_path: Optional[Path] = None, migrations_dir: Optional[Path] = None) -> List[str]:
    """
    Run all pending SQLite migrations idempotently.
    Returns the list of newly applied migration versions.
    """
    applied = []
    migration_folder = migrations_dir or MIGRATIONS_DIR
    
    conn = get_connection(db_path)
    try:
        # Ensure migrations tracking table exists
        conn.execute("""
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version TEXT PRIMARY KEY,
                applied_at TEXT NOT NULL
            );
        """)
        
        # Get already applied versions
        cursor = conn.execute("SELECT version FROM schema_migrations ORDER BY version ASC;")
        existing_versions = {row["version"] for row in cursor.fetchall()}
        
        # Discover migration files
        sql_files = sorted(migration_folder.glob("*.sql"))
        
        for sql_file in sql_files:
            version = sql_file.name
            if version in existing_versions:
                continue
                
            logger.info(f"Applying migration: {version}")
            sql_content = sql_file.read_text(encoding="utf-8")
            
            # executescript executes all statements in script
            conn.executescript(sql_content)
            now_str = datetime.now(timezone.utc).isoformat()
            conn.execute(
                "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?);",
                (version, now_str)
            )
            applied.append(version)
            logger.info(f"Successfully applied migration: {version}")
            
    finally:
        conn.close()
        
    return applied
