"""Per-user opt-out from price alerts (pipeline/price_alerts.py), from chat: "tắt cảnh báo VNM",
"tắt mọi cảnh báo", "bật lại cảnh báo"."""
from __future__ import annotations

from datetime import datetime, timezone

from mcp_server.connection import get_rw_conn
from mcp_server.envelope import build_envelope
from mcp_server.identity import no_personal_scope, pinned_user
from pipeline.price_alerts import set_alerts


def set_price_alerts_tool(enabled: bool, ticker: str | None = None) -> dict:
    now = datetime.now(timezone.utc)
    user = pinned_user()
    if user is None:
        return no_personal_scope(now)
    ticker = ticker.strip().upper() if ticker and ticker.strip() else None
    with get_rw_conn() as conn:
        set_alerts(conn, user, enabled, ticker)
    return build_envelope(
        {"status": "ok", "enabled": enabled, "ticker": ticker or "*"}, sources=["postgres"], as_of=now,
    )
