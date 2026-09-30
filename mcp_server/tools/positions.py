from __future__ import annotations

from datetime import datetime, timezone

from mcp_server.connection import get_rw_conn
from mcp_server.envelope import build_envelope
from pipeline.positions import clear_position, get_holding_state, set_position


def set_position_tool(ticker: str, avg_cost: float | None, declared_by: str) -> dict:
    now = datetime.now(timezone.utc)
    with get_rw_conn() as conn:
        set_position(conn, ticker, avg_cost, declared_by)
        holding_state = get_holding_state(conn, ticker)
    return build_envelope(
        {"status": "ok", "ticker": ticker, "holding_state": holding_state},
        sources=["postgres"], as_of=now,
    )


def clear_position_tool(ticker: str, declared_by: str) -> dict:
    now = datetime.now(timezone.utc)
    with get_rw_conn() as conn:
        clear_position(conn, ticker, declared_by)
        holding_state = get_holding_state(conn, ticker)
    return build_envelope(
        {"status": "ok", "ticker": ticker, "holding_state": holding_state},
        sources=["postgres"], as_of=now,
    )
