import os
import pytest
import psycopg

from mcp_server.connection import get_ro_conn, get_rw_conn


def test_get_ro_conn_can_select():
    with get_ro_conn() as conn:
        row = conn.execute("SELECT 1").fetchone()
        assert row == (1,)


def test_get_ro_conn_cannot_insert():
    with get_ro_conn() as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute(
                "INSERT INTO tickers (ticker, exchange) VALUES ('ZZZTEST', 'HOSE')"
            )
            conn.execute("SELECT 1")  # force flush of the failed statement


def test_get_rw_conn_can_insert_and_rolls_back_on_error():
    with pytest.raises(RuntimeError):
        with get_rw_conn() as conn:
            conn.execute(
                "INSERT INTO tickers (ticker, exchange) VALUES ('ZZZTEST2', 'HOSE')"
            )
            raise RuntimeError("boom")

    with get_ro_conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM tickers WHERE ticker = 'ZZZTEST2'"
        ).fetchone()
        assert row is None
