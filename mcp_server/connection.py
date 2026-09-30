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
