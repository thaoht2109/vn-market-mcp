import os
from urllib.parse import urlsplit, urlunsplit

import psycopg
import pytest

from db.connection import get_conn
from db.setup_roles import setup_roles
from tests.conftest import insert_ticker


def _role_url(role: str, password_env: str) -> str:
    parts = urlsplit(os.environ["DATABASE_URL"])
    netloc = f"{role}:{os.environ[password_env]}@{parts.hostname}:{parts.port}"
    return urlunsplit((parts.scheme, netloc, parts.path, "", ""))


@pytest.fixture(scope="module", autouse=True)
def _ensure_roles():
    with get_conn() as conn:
        setup_roles(conn)


def test_mcp_ro_can_select_but_not_insert(db_conn):
    insert_ticker(db_conn, "ROTEST")
    db_conn.commit()  # must be visible on the separate connection below
    try:
        with psycopg.connect(_role_url("mcp_ro", "MCP_RO_PASSWORD")) as role_conn:
            rows = role_conn.execute("SELECT ticker FROM tickers WHERE ticker = 'ROTEST'").fetchall()
            assert rows == [("ROTEST",)]

            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                role_conn.execute("INSERT INTO tickers (ticker) VALUES ('ROTEST2')")
            role_conn.rollback()
    finally:
        db_conn.execute("DELETE FROM tickers WHERE ticker = 'ROTEST'")
        db_conn.commit()


def test_pipeline_rw_can_insert_but_not_delete(db_conn):
    try:
        with psycopg.connect(_role_url("pipeline_rw", "PIPELINE_RW_PASSWORD")) as role_conn:
            role_conn.execute("INSERT INTO tickers (ticker) VALUES ('RWTEST') ON CONFLICT DO NOTHING")
            role_conn.commit()

            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                role_conn.execute("DELETE FROM tickers WHERE ticker = 'RWTEST'")
            role_conn.rollback()
    finally:
        db_conn.execute("DELETE FROM tickers WHERE ticker = 'RWTEST'")
        db_conn.commit()


def test_retention_job_can_delete_but_not_insert(db_conn):
    insert_ticker(db_conn, "RETTEST")
    db_conn.commit()
    with psycopg.connect(_role_url("retention_job", "RETENTION_JOB_PASSWORD")) as role_conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            role_conn.execute("INSERT INTO tickers (ticker) VALUES ('RETTEST2')")
        role_conn.rollback()

        role_conn.execute("DELETE FROM tickers WHERE ticker = 'RETTEST'")
        role_conn.commit()
