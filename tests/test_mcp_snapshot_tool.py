import json
from pathlib import Path

import pytest

from db.connection import get_conn
from mcp_server.tools.snapshot import get_snapshot_tool


@pytest.fixture
def seeded_run(tmp_path):
    snapshot_path = tmp_path / "test_run.json"
    snapshot_path.write_text(json.dumps({"ticker": "VNMTEST", "action_label": "watch"}))
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO runs (run_id, mode, tickers, style, depth, as_of, snapshot_ref, warnings)
            VALUES ('test:VNMTEST:1', 'on_demand', ARRAY['VNMTEST'], 'long', 'quick', now(), %s, '[]')
            """,
            (str(snapshot_path),),
        )
    yield "test:VNMTEST:1", str(snapshot_path)
    with get_conn() as conn:
        conn.execute("DELETE FROM runs WHERE run_id = 'test:VNMTEST:1'")


def test_get_snapshot_by_run_id(seeded_run):
    run_id, _ = seeded_run
    result = get_snapshot_tool(run_id=run_id)
    assert result["data"]["run_id"] == run_id
    assert result["data"]["snapshot"]["ticker"] == "VNMTEST"
    assert result["warnings"] == []


def test_get_snapshot_by_ticker_returns_most_recent(seeded_run):
    result = get_snapshot_tool(ticker="VNMTEST")
    assert result["data"]["snapshot"]["ticker"] == "VNMTEST"


def test_get_snapshot_unknown_run_id_returns_not_found():
    result = get_snapshot_tool(run_id="does:not:exist")
    assert result["data"]["status"] == "not_found"
    assert result["warnings"] != []


def test_get_snapshot_requires_exactly_one_arg():
    with pytest.raises(ValueError):
        get_snapshot_tool()
    with pytest.raises(ValueError):
        get_snapshot_tool(ticker="VNM", run_id="x:y:1")


def _insert_run(conn, run_id, ref, snapshot=None):
    from psycopg.types.json import Jsonb
    conn.execute(
        "INSERT INTO runs (run_id, mode, tickers, style, depth, as_of, snapshot_ref, warnings, snapshot)"
        " VALUES (%s, 'on_demand', ARRAY['SNAPCOL'], 'long', 'quick', now(), %s, '[]', %s)",
        (run_id, ref, Jsonb(snapshot) if snapshot is not None else None))


def test_stored_snapshot_wins_and_needs_no_file():
    with get_conn() as conn:
        _insert_run(conn, "snapcol:1", "snapshots/does-not-exist.json", {"ticker": "SNAPCOL", "action_label": "watch"})
    try:
        got = get_snapshot_tool(run_id="snapcol:1")["data"]
        assert got["status"] == "ok" and got["snapshot"]["ticker"] == "SNAPCOL"
    finally:
        with get_conn() as conn:
            conn.execute("DELETE FROM runs WHERE run_id = 'snapcol:1'")


def test_no_column_and_no_file_is_snapshot_file_missing():
    with get_conn() as conn:
        _insert_run(conn, "snapcol:2", "snapshots/does-not-exist.json")
    try:
        assert get_snapshot_tool(run_id="snapcol:2")["data"]["status"] == "snapshot_file_missing"
    finally:
        with get_conn() as conn:
            conn.execute("DELETE FROM runs WHERE run_id = 'snapcol:2'")


def test_backfill_loads_files_once_and_lists_the_missing(tmp_path):
    from ops.backfill_snapshots import backfill
    (tmp_path / "snapshots").mkdir()
    (tmp_path / "snapshots/a.json").write_text(json.dumps({"ticker": "SNAPCOL", "rsi": 1.5}))
    (tmp_path / "snapshots/bad.json").write_text("{not json")
    with get_conn() as conn:
        conn.execute("DELETE FROM runs WHERE run_id LIKE 'snapcol:%'")
        _insert_run(conn, "snapcol:a", "snapshots/a.json")
        _insert_run(conn, "snapcol:bad", "snapshots/bad.json")
        _insert_run(conn, "snapcol:gone", "snapshots/gone.json")
        _insert_run(conn, "snapcol:done", "snapshots/a.json", {"already": True})
        loaded, skipped = backfill(conn, tmp_path)
        rows = dict(conn.execute("SELECT run_id, snapshot FROM runs WHERE run_id LIKE 'snapcol:%'").fetchall())
        assert backfill(conn, tmp_path)[0] == 0  # again: nothing left that can be loaded
        conn.execute("DELETE FROM runs WHERE run_id LIKE 'snapcol:%'")
    assert loaded >= 1 and set(skipped) >= {"snapcol:bad", "snapcol:gone"}
    assert rows["snapcol:a"] == {"ticker": "SNAPCOL", "rsi": 1.5} and rows["snapcol:done"] == {"already": True}
    assert rows["snapcol:bad"] is None


def test_serialize_snapshot_turns_nan_into_null():
    from pipeline.snapshot import serialize_snapshot
    assert serialize_snapshot({"a": float("nan"), "b": [float("inf"), 1.5]}) == {"a": None, "b": [None, 1.5]}
