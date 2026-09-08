import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Generator, Optional
from datahunt.config import settings

def get_connection(db_path: Optional[Path] = None) -> sqlite3.Connection:
    """Create a SQLite connection configured with WAL mode, foreign keys, and busy timeout."""
    path = db_path or settings.DATABASE_PATH
    if path.parent:
        path.parent.mkdir(parents=True, exist_ok=True)
        
    conn = sqlite3.connect(
        str(path),
        timeout=10.0,
        autocommit=True
    )
    conn.row_factory = sqlite3.Row
    
    # Required SQLite Pragmas as specified in Database.md
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA synchronous = NORMAL;")
    conn.execute("PRAGMA busy_timeout = 5000;")
    
    return conn

@contextmanager
def db_transaction(conn: Optional[sqlite3.Connection] = None, db_path: Optional[Path] = None) -> Generator[sqlite3.Connection, None, None]:
    """Transaction context manager with automatic rollback on error."""
    owns_conn = False
    if conn is None:
        conn = get_connection(db_path)
        owns_conn = True
        
    conn.execute("BEGIN;")
    try:
        yield conn
        conn.execute("COMMIT;")
    except Exception:
        conn.execute("ROLLBACK;")
        raise
    finally:
        if owns_conn:
            conn.close()
