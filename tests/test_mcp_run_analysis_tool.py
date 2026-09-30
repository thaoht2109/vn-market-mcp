from pathlib import Path
from unittest.mock import MagicMock, patch

from mcp_server.tools.run_analysis import run_analysis_tool
from pipeline.run_analysis import RunResult


def test_run_analysis_tool_returns_envelope_on_success(tmp_path):
    fake_result = RunResult(
        run_id="on_demand:VNM:123", status="ok", ticker="VNM",
        action_label="watch", message="trigger=first",
    )
    with patch("mcp_server.tools.run_analysis.get_rw_conn") as mock_conn, \
         patch("mcp_server.tools.run_analysis.run_analysis", return_value=fake_result) as mock_run, \
         patch("mcp_server.tools.run_analysis.log_event") as mock_log, \
         patch("mcp_server.tools.run_analysis.VNStockProvider"):
        mock_conn.return_value.__enter__.return_value = MagicMock()
        result = run_analysis_tool("VNM", style="long", depth="quick")

    assert result["data"]["status"] == "ok"
    assert result["data"]["action_label"] == "watch"
    assert result["data"]["run_id"] == "on_demand:VNM:123"
    assert result["sources"] == ["vnstock", "postgres"]
    assert result["warnings"] == []
    mock_run.assert_called_once()
    mock_log.assert_any_call("mcp_run_analysis_started", ticker="VNM", style="long", depth="quick")
    mock_log.assert_any_call(
        "mcp_run_analysis_finished", ticker="VNM", status="ok", action_label="watch"
    )


def test_run_analysis_tool_returns_warning_envelope_on_non_ok_status():
    fake_result = RunResult(
        run_id=None, status="unknown_ticker", ticker="ZZZ", action_label=None,
        message="mã ZZZ không tồn tại",
    )
    with patch("mcp_server.tools.run_analysis.get_rw_conn") as mock_conn, \
         patch("mcp_server.tools.run_analysis.run_analysis", return_value=fake_result), \
         patch("mcp_server.tools.run_analysis.log_event"), \
         patch("mcp_server.tools.run_analysis.send_ops_alert") as mock_alert, \
         patch("mcp_server.tools.run_analysis.VNStockProvider"):
        mock_conn.return_value.__enter__.return_value = MagicMock()
        result = run_analysis_tool("ZZZ")

    assert result["data"]["status"] == "unknown_ticker"
    assert result["warnings"] == ["mã ZZZ không tồn tại"]
    mock_alert.assert_called_once()


def test_run_analysis_tool_catches_exception_and_alerts():
    with patch("mcp_server.tools.run_analysis.get_rw_conn") as mock_conn, \
         patch("mcp_server.tools.run_analysis.run_analysis", side_effect=RuntimeError("db down")), \
         patch("mcp_server.tools.run_analysis.log_event") as mock_log, \
         patch("mcp_server.tools.run_analysis.send_ops_alert") as mock_alert, \
         patch("mcp_server.tools.run_analysis.VNStockProvider"):
        mock_conn.return_value.__enter__.return_value = MagicMock()
        result = run_analysis_tool("VNM")

    assert result["data"]["status"] == "error"
    assert "db down" in result["warnings"][0]
    mock_alert.assert_called_once()
    mock_log.assert_any_call(
        "mcp_run_analysis_failed", ticker="VNM", error="db down", error_type="RuntimeError"
    )
