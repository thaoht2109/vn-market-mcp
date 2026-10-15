import os
from contextlib import contextmanager

import psycopg

# A long-running multi-user server calls use_pools() once per process: get_ro_conn /
# get_rw_conn then borrow a connection instead of opening one (~25 ms each, against Postgres' shared
# max_connections). Everything else (stdio server, worker, jobs, tests) keeps one connection per call.
_pools: dict = {}


def use_pools(max_size: int) -> None:
    from psycopg_pool import ConnectionPool

    for name, env in (("ro", "MCP_RO_DATABASE_URL"), ("rw", "PIPELINE_RW_DATABASE_URL")):
        if name not in _pools:  # lazy open: the first borrow connects, so importing the app needs no database
            _pools[name] = ConnectionPool(os.environ[env], min_size=1, max_size=max_size, open=False, name=f"vnmcp-{name}",
                                          check=ConnectionPool.check_connection)  # survives a Postgres restart


@contextmanager
def _borrow(name: str):
    pool = _pools[name]
    if pool.closed:
        pool.open(wait=False)
    with pool.connection() as conn:  # commits on success, rolls back on error, then hands it back
        yield conn


@contextmanager
def get_ro_conn():
    if "ro" in _pools:
        with _borrow("ro") as conn:
            yield conn
        return
    conn = psycopg.connect(os.environ["MCP_RO_DATABASE_URL"])
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def get_rw_conn():
    if "rw" in _pools:
        with _borrow("rw") as conn:
            yield conn
        return
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
