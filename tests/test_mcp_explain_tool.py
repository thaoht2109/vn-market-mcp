import pytest

from db.connection import get_conn
from mcp_server.tools.explain import explain_run_tool


@pytest.fixture
def seeded_run_with_checks():
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO runs (run_id, mode, tickers, style, depth, as_of, snapshot_ref, warnings)
            VALUES ('explain:TEST:1', 'on_demand', ARRAY['EXPTEST'], 'long', 'quick', now(), '/tmp/x.json', '[]')
            """
        )
        conn.execute(
            "INSERT INTO data_quality_log (run_id, ticker, check_name, result, detail)"
            " VALUES ('explain:TEST:1', 'EXPTEST', 'freshness', 'pass', '{}')"
        )
    yield "explain:TEST:1"
    with get_conn() as conn:
        conn.execute("DELETE FROM data_quality_log WHERE run_id = 'explain:TEST:1'")
        conn.execute("DELETE FROM predictions WHERE run_id = 'explain:TEST:1'")
        conn.execute("DELETE FROM runs WHERE run_id = 'explain:TEST:1'")


def test_explain_run_returns_run_and_checks(seeded_run_with_checks):
    result = explain_run_tool(seeded_run_with_checks)
    assert result["data"]["status"] == "ok"
    assert result["data"]["run"]["run_id"] == "explain:TEST:1"
    assert len(result["data"]["quality_checks"]) == 1
    assert result["data"]["quality_checks"][0]["check_name"] == "freshness"
    assert result["data"]["predictions"] == []


def test_explain_run_unknown_run_id_returns_not_found():
    result = explain_run_tool("does:not:exist")
    assert result["data"]["status"] == "not_found"
    assert result["warnings"] != []
