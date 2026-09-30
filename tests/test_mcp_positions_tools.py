from db.connection import get_conn
from mcp_server.tools.positions import clear_position_tool, set_position_tool
from tests.conftest import insert_ticker


def _cleanup(ticker):
    with get_conn() as conn:
        conn.execute("DELETE FROM positions WHERE ticker = %s", (ticker,))


def test_set_position_tool_marks_holding():
    with get_conn() as conn:
        insert_ticker(conn, "POSTEST")
    try:
        result = set_position_tool("POSTEST", avg_cost=25.5, declared_by="user123")
        assert result["data"]["status"] == "ok"
        assert result["data"]["holding_state"] == "holding"
        assert result["warnings"] == []
    finally:
        _cleanup("POSTEST")


def test_clear_position_tool_marks_none():
    with get_conn() as conn:
        insert_ticker(conn, "POSTEST2")
    try:
        set_position_tool("POSTEST2", avg_cost=10.0, declared_by="user123")
        result = clear_position_tool("POSTEST2", declared_by="user123")
        assert result["data"]["status"] == "ok"
        assert result["data"]["holding_state"] == "none"
    finally:
        _cleanup("POSTEST2")
