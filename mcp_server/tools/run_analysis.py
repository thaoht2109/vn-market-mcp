"""MCP tool wrapping pipeline.run_analysis.run_analysis.

Note: this calls the existing SYNCHRONOUS run_analysis() and blocks until
it returns. The spec (§3, §4.1) describes an async job_id + background
worker + Telegram push; that worker does not exist yet in this codebase
(Phase 0+1 only built the on-demand CLI). Callers of this tool should
expect it to block for the duration of one full pipeline run.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from mcp_server.connection import get_rw_conn
from mcp_server.envelope import build_envelope
from ops.alerting import log_event, send_ops_alert
from pipeline.run_analysis import run_analysis
from providers.vnstock_provider import VNStockProvider

SNAPSHOT_DIR = Path("snapshots")


def run_analysis_tool(ticker: str, style: str = "long", depth: str = "quick") -> dict:
    now = datetime.now(timezone.utc)
    log_event("mcp_run_analysis_started", ticker=ticker, style=style, depth=depth)

    try:
        with get_rw_conn() as conn:
            result = run_analysis(
                conn, VNStockProvider(source="VCI"), ticker, SNAPSHOT_DIR,
                mode="on_demand", style=style, depth=depth,
            )
    except Exception as exc:
        log_event(
            "mcp_run_analysis_failed", ticker=ticker, error=str(exc), error_type=type(exc).__name__,
        )
        send_ops_alert(f"[vn-market-mcp/mcp] LỖI khi chạy {ticker}: {type(exc).__name__}: {exc}")
        return build_envelope(
            {"status": "error", "ticker": ticker, "run_id": None, "action_label": None},
            sources=["vnstock", "postgres"], as_of=now, warnings=[str(exc)],
        )

    if result.status == "ok":
        log_event(
            "mcp_run_analysis_finished", ticker=ticker, status=result.status, action_label=result.action_label,
        )
        warnings: list[str] = []
    else:
        log_event("mcp_run_analysis_finished_with_issue", ticker=ticker, status=result.status, message=result.message)
        send_ops_alert(f"[vn-market-mcp/mcp] {ticker}: {result.status} — {result.message}")
        warnings = [result.message]

    return build_envelope(
        {
            "status": result.status, "ticker": result.ticker, "run_id": result.run_id,
            "action_label": result.action_label, "message": result.message,
        },
        sources=["vnstock", "postgres"], as_of=now, warnings=warnings,
    )
