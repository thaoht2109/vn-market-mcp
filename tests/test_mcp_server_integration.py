import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from mcp_server.server import mcp


@pytest.mark.asyncio
async def test_server_lists_all_twenty_one_tools():
    async with create_connected_server_and_client_session(mcp._mcp_server) as client:
        tools = await client.list_tools()
        names = {t.name for t in tools.tools}
        assert names == {
            "run_analysis", "get_job_status", "get_snapshot", "query_history", "explain_run",
            "list_predictions", "get_stats", "set_position", "clear_position",
            "watch_ticker", "unwatch_ticker", "list_watchlist",
            "get_stock_report", "save_commentary", "get_market_digest_input", "get_weekly_digest_input",
            "get_macro_context", "set_price_alerts", "judge_news",
            "get_advisor_input", "save_advice",
        }


@pytest.mark.asyncio
async def test_server_get_stats_tool_call_returns_structured_content():
    async with create_connected_server_and_client_session(mcp._mcp_server) as client:
        result = await client.call_tool("get_stats", {})
        assert result.isError is False
        assert result.structuredContent is not None
        assert "total_runs" in result.structuredContent["data"]
