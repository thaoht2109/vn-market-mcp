import os
from datetime import datetime, timezone
from pathlib import Path

import psycopg
import pytest

from db.migrate import apply_migrations

DATABASE_URL = os.environ.get("DATABASE_URL")
MIGRATIONS_DIR = Path(__file__).parent.parent / "db" / "migrations"


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
