from __future__ import annotations

from datetime import datetime, timezone

from mcp_server.connection import get_ro_conn
from mcp_server.envelope import build_envelope

_SERIES_QUERIES = {
    "prices": (
        "SELECT trade_date, open, high, low, close, volume FROM prices_daily"
        " WHERE ticker = %s AND trade_date >= COALESCE(%s, trade_date)"
        " AND trade_date <= COALESCE(%s, trade_date) ORDER BY trade_date",
        ["trade_date", "open", "high", "low", "close", "volume"],
    ),
    "fundamentals": (
        "SELECT period, metrics FROM fundamentals_quarterly"
        " WHERE ticker = %s AND period >= COALESCE(%s, period)"
        " AND period <= COALESCE(%s, period) ORDER BY period",
        ["period", "metrics"],
    ),
    "foreign_flow": (
        "SELECT trade_date, net_value FROM foreign_flow_daily"
        " WHERE ticker = %s AND trade_date >= COALESCE(%s, trade_date)"
        " AND trade_date <= COALESCE(%s, trade_date) ORDER BY trade_date",
        ["trade_date", "net_value"],
    ),
}


def query_history_tool(
    ticker: str, series: str, start_date: str | None = None, end_date: str | None = None
) -> dict:
    if series not in _SERIES_QUERIES:
        raise ValueError(f"series phải là một trong {sorted(_SERIES_QUERIES)}, nhận '{series}'")

    sql, columns = _SERIES_QUERIES[series]
    now = datetime.now(timezone.utc)
    with get_ro_conn() as conn:
        rows = conn.execute(sql, (ticker, start_date, end_date)).fetchall()

    dict_rows = [dict(zip(columns, row)) for row in rows]
    warnings = [] if dict_rows else [f"không có dữ liệu cho {ticker}"]
    return build_envelope(
        {"ticker": ticker, "series": series, "rows": dict_rows},
        sources=["postgres"], as_of=now, warnings=warnings,
    )
