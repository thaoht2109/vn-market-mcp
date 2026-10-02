import os
from datetime import datetime, timezone
from pathlib import Path

import psycopg
import pytest

from db.migrate import apply_migrations

DATABASE_URL = os.environ.get("DATABASE_URL")
MIGRATIONS_DIR = Path(__file__).parent.parent / "db" / "migrations"


_DB_URL_VARS = ("DATABASE_URL", "MCP_RO_DATABASE_URL", "PIPELINE_RW_DATABASE_URL", "RETENTION_JOB_DATABASE_URL")


def _database_name(url: str) -> str:
    return url.rsplit("/", 1)[-1].split("?")[0]


def pytest_sessionstart(session):
    # Tests insert, delete and wipe rows. One of them once DELETEd every run and
    # prediction from the live database (2026-10-02) — so never run against it.
    live = [v for v in _DB_URL_VARS if os.environ.get(v) and not _database_name(os.environ[v]).endswith("_test")]
    if live:
        pytest.exit(
            f"refusing to run: {', '.join(live)} do not point at a *_test database "
            "(they would hit live data). Run ./run_tests.sh instead.",
            returncode=2,
        )


@pytest.fixture(scope="session", autouse=True)
def _migrated_schema():
    if DATABASE_URL is None:
        # Skip database setup if DATABASE_URL not set (for tests that don't need DB)
        return
    conn = psycopg.connect(DATABASE_URL, autocommit=True)
    apply_migrations(conn, MIGRATIONS_DIR)
    conn.close()


@pytest.fixture
def db_conn():
    conn = psycopg.connect(DATABASE_URL)
    yield conn
    conn.rollback()
    conn.close()


def insert_ticker(conn, ticker="VNM", industry_group="other", exchange="HOSE"):
    conn.execute(
        """
        INSERT INTO tickers (ticker, name, exchange, sector, industry_group)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (ticker) DO NOTHING
        """,
        (ticker, ticker, exchange, "test", industry_group),
    )


def utc_now():
    return datetime.now(timezone.utc)
