import csv
from datetime import date, timedelta

import mcp_server.tools.export_bars as export_bars
from db.connection import get_conn
from mcp_server.tools.export_bars import export_price_bars_tool

_TICKERS = ("EXPA", "EXPB")


def test_export_writes_last_n_bars_per_ticker_to_csv(tmp_path, monkeypatch):
    monkeypatch.setattr(export_bars, "_EXPORT_DIR", tmp_path)
    with get_conn() as conn:
        for t in _TICKERS:
            conn.execute("INSERT INTO tickers (ticker, exchange) VALUES (%s, 'HOSE') ON CONFLICT DO NOTHING", (t,))
            for i in range(5):
                conn.execute(
                    "INSERT INTO prices_daily (ticker, trade_date, open, high, low, close, volume, source, fetched_at)"
                    " VALUES (%s, %s, 10, 11, 9, %s, 1000, 'test', now()) ON CONFLICT DO NOTHING",
                    (t, date(2026, 3, 1) + timedelta(days=i), 10 + i),
                )
    try:
        data = export_price_bars_tool(days=3, tickers=[t.lower() for t in _TICKERS])["data"]
        assert data["rows"] == 6 and data["tickers"] == 2
        assert data["latest_trade_date"] == "2026-03-05"
        with open(data["path"]) as f:
            rows = list(csv.DictReader(f))
        assert [r["trade_date"] for r in rows if r["ticker"] == "EXPA"] == ["2026-03-03", "2026-03-04", "2026-03-05"]
    finally:
        with get_conn() as conn:
            conn.execute("DELETE FROM prices_daily WHERE ticker = ANY(%s)", (list(_TICKERS),))


def test_export_rejects_out_of_range_days(tmp_path, monkeypatch):
    monkeypatch.setattr(export_bars, "_EXPORT_DIR", tmp_path)
    try:
        export_price_bars_tool(days=0)
    except ValueError:
        return
    raise AssertionError("days=0 must be rejected")
