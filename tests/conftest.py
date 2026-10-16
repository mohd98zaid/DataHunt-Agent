"""
Global Pytest Configuration and Database Isolation.
Redirects all database operations during test runs to an isolated test database
so that production data in `data/datahunt.sqlite3` is never touched or contaminated.
"""
import os
from pathlib import Path
import pytest

TEST_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "test_datahunt.sqlite3"
os.environ["DATABASE_PATH"] = str(TEST_DB_PATH)

from datahunt.config import settings
settings.DATABASE_PATH = TEST_DB_PATH

from datahunt.db import run_migrations, get_connection

@pytest.fixture(scope="session", autouse=True)
def isolated_test_database():
    """Ensure test database exists, is migrated, and clean for the test session."""
    TEST_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    # Ensure fresh schema migrations on test db
    run_migrations(TEST_DB_PATH)
    yield
