from __future__ import annotations

from datetime import datetime, timezone

from mcp_server.connection import get_rw_conn
from mcp_server.envelope import build_envelope
from mcp_server.identity import no_personal_scope, pinned_user
from pipeline.positions import clear_position, get_holding_state, set_position


def set_position_tool(ticker: str, avg_cost: float | None) -> dict:
    now = datetime.now(timezone.utc)
    user = pinned_user()
    if user is None:
        return no_personal_scope(now)
    with get_rw_conn() as conn:
        set_position(conn, ticker, avg_cost, user)
        holding_state = get_holding_state(conn, ticker, user)
    return build_envelope(
        {"status": "ok", "ticker": ticker, "holding_state": holding_state},
        sources=["postgres"], as_of=now,
    )


def clear_position_tool(ticker: str) -> dict:
    now = datetime.now(timezone.utc)
    user = pinned_user()
    if user is None:
        return no_personal_scope(now)
    with get_rw_conn() as conn:
        clear_position(conn, ticker, user)
        holding_state = get_holding_state(conn, ticker, user)
    return build_envelope(
        {"status": "ok", "ticker": ticker, "holding_state": holding_state},
        sources=["postgres"], as_of=now,
    )
