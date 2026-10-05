import pytest

from db.connection import get_conn
from mcp_server.tools.positions import clear_position_tool, set_position_tool
from tests.conftest import insert_ticker


def _cleanup(ticker):
    with get_conn() as conn:
        conn.execute("DELETE FROM positions WHERE ticker = %s", (ticker,))


@pytest.fixture(autouse=True)
def _own_chat(monkeypatch):
    monkeypatch.setenv("VNMCP_USER_ID", "user123")


def test_set_position_tool_marks_holding():
    with get_conn() as conn:
        insert_ticker(conn, "POSTEST")
    try:
        result = set_position_tool("POSTEST", avg_cost=25.5)
        assert result["data"]["status"] == "ok"
        assert result["data"]["holding_state"] == "holding"
        assert result["warnings"] == []
        with get_conn() as conn:
            assert conn.execute("SELECT declared_by FROM positions WHERE ticker = 'POSTEST'").fetchone()[0] == "user123"
    finally:
        _cleanup("POSTEST")


def test_clear_position_tool_marks_none():
    with get_conn() as conn:
        insert_ticker(conn, "POSTEST2")
    try:
        set_position_tool("POSTEST2", avg_cost=10.0)
        result = clear_position_tool("POSTEST2")
        assert result["data"]["status"] == "ok"
        assert result["data"]["holding_state"] == "none"
    finally:
        _cleanup("POSTEST2")
