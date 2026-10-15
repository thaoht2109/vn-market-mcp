"""A multi-user host borrows connections from a pool (use_pools); everything else opens one per call."""
import pytest

from mcp_server import connection


def _pid(conn):
    return conn.execute("SELECT pg_backend_pid()").fetchone()[0]


def test_without_pools_each_call_opens_and_closes_its_own(monkeypatch):
    monkeypatch.setattr(connection, "_pools", {})
    with connection.get_ro_conn() as a:
        pass
    assert a.closed


def test_pools_reuse_connections_and_keep_commit_rollback(monkeypatch):
    monkeypatch.setattr(connection, "_pools", {})
    connection.use_pools(2)
    try:
        pids = set()
        for _ in range(10):
            with connection.get_ro_conn() as a:
                pids.add(_pid(a))
        assert len(pids) <= 2 and not a.closed  # 10 calls, at most max_size backends: connections are reused
        with pytest.raises(ZeroDivisionError), connection.get_rw_conn() as c:
            c.execute("CREATE TEMP TABLE pool_probe (x int)")
            1 / 0
        with connection.get_rw_conn() as c:  # the failed block was rolled back before the connection went back
            assert c.execute("SELECT to_regclass('pool_probe')").fetchone()[0] is None
    finally:
        for pool in connection._pools.values():
            pool.close()

