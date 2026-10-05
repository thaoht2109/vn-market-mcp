import os
import psycopg
import pytest
from pathlib import Path
from db.migrate import apply_migrations

DATABASE_URL = os.environ["DATABASE_URL"]
MIGRATIONS_DIR = Path(__file__).parent.parent / "db" / "migrations"


@pytest.fixture
def isolated_schema_conn():
    # Runs migrations against a throwaway schema, never the shared `public`
    # schema other test files' fixtures depend on within the same session.
    conn = psycopg.connect(DATABASE_URL, autocommit=True)
    conn.execute("DROP SCHEMA IF EXISTS migration_test CASCADE; CREATE SCHEMA migration_test;")
    conn.execute("SET search_path TO migration_test")
    yield conn
    conn.execute("DROP SCHEMA IF EXISTS migration_test CASCADE")
    conn.close()


def test_apply_migrations_creates_all_tables(isolated_schema_conn):
    applied = apply_migrations(isolated_schema_conn, MIGRATIONS_DIR)
    assert len(applied) == len(list(MIGRATIONS_DIR.glob("*.sql")))

    tables = {
        row[0]
        for row in isolated_schema_conn.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema='migration_test'"
        ).fetchall()
    }
    assert {"tickers", "prices_daily", "runs", "predictions", "positions", "jobs"} <= tables


def test_apply_migrations_is_idempotent(isolated_schema_conn):
    apply_migrations(isolated_schema_conn, MIGRATIONS_DIR)
    second_run = apply_migrations(isolated_schema_conn, MIGRATIONS_DIR)
    assert second_run == []
