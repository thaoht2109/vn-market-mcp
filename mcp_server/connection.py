import os
from contextlib import contextmanager

import psycopg


@contextmanager
def get_ro_conn():
    conn = psycopg.connect(os.environ["MCP_RO_DATABASE_URL"])
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def get_rw_conn():
    conn = psycopg.connect(os.environ["PIPELINE_RW_DATABASE_URL"])
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@contextmanager
def get_retention_conn():
    conn = psycopg.connect(os.environ["RETENTION_JOB_DATABASE_URL"])
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@contextmanager
def get_admin_conn():
    """Table owner. Used only for DDL the pipeline role cannot do (creating news_items partitions)."""
    conn = psycopg.connect(os.environ["ADMIN_DATABASE_URL"])
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
