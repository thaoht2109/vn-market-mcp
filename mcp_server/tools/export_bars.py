"""Bulk daily OHLCV export for screening criteria screen_tickers doesn't cover.

Writes a CSV next to the gateway (same container as Hermes' terminal tool) and returns only
its path, so the agent evaluates any criterion with one pandas script and the bars never pass
through the LLM context.
"""
from __future__ import annotations

import csv
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from mcp_server.connection import get_ro_conn
from mcp_server.envelope import build_envelope

_EXPORT_DIR = Path(os.environ.get("VNMCP_EXPORT_DIR", "/opt/data/cache/vn-market-mcp"))
_PYTHON = os.environ.get("VNMCP_ANALYSIS_PYTHON", "/opt/data/vn-market-mcp-venv/bin/python")
_COLUMNS = ["ticker", "trade_date", "open", "high", "low", "close", "volume"]
_MAX_AGE_SECONDS = 86400

_SQL = """
SELECT ticker, trade_date, open, high, low, close, volume FROM (
  SELECT ticker, trade_date, open, high, low, close, volume,
         row_number() OVER (PARTITION BY ticker ORDER BY trade_date DESC) rn
  FROM prices_daily WHERE {where}
) b WHERE rn <= %s ORDER BY ticker, trade_date
"""


def export_price_bars_tool(days: int = 250, tickers: list[str] | None = None) -> dict:
    if not 1 <= days <= 1000:
        raise ValueError("days phải trong khoảng 1-1000")
    now = datetime.now(timezone.utc)
    if tickers:
        sql, params = _SQL.format(where="ticker = ANY(%s)"), [[t.upper() for t in tickers], days]
    else:
        sql, params = _SQL.format(where="TRUE"), [days]
    with get_ro_conn() as conn:
        rows = conn.execute(sql, params).fetchall()

    _EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    for old in _EXPORT_DIR.glob("bars_*.csv"):
        if old.stat().st_mtime < now.timestamp() - _MAX_AGE_SECONDS:
            old.unlink(missing_ok=True)
    path = _EXPORT_DIR / f"bars_{now:%Y%m%d_%H%M%S}_{uuid.uuid4().hex[:6]}.csv"
    with path.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(_COLUMNS)
        writer.writerows(rows)

    latest = max((r[1] for r in rows), default=None)
    data = {
        "path": str(path), "rows": len(rows), "tickers": len({r[0] for r in rows}),
        "latest_trade_date": str(latest) if latest else None, "columns": _COLUMNS, "python": _PYTHON,
    }
    return build_envelope(data, sources=["postgres"], as_of=now)
