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
