from datetime import date, timedelta

from db.connection import get_conn
from mcp_server.tools.screen import screen_tickers_tool

_TICKERS = ("SCRUP", "SCRFLAT")


def _seed():
    with get_conn() as conn:
        for t in _TICKERS:
            conn.execute("INSERT INTO tickers (ticker, exchange) VALUES (%s, 'HOSE') ON CONFLICT DO NOTHING", (t,))
        start = date(2026, 1, 1)
        rows = []
        # SCRUP: 53 sessions of a steady uptrend, then a red day, then a bullish-engulfing
        # green day -> RSI high, close above ma20/ma50, pattern on the last bar.
        price = 10.0
        for i in range(53):
            d = start + timedelta(days=i)
            o, c = price, price + 0.1
            rows.append(("SCRUP", d, o, c + 0.05, o - 0.05, c, 1000))
            price = c
        rows.append(("SCRUP", start + timedelta(days=53), price, price + 0.2, price - 2.2, price - 2.0, 1000))
        rows.append(("SCRUP", start + timedelta(days=54), price - 2.5, price + 0.5, price - 2.7, price + 0.3, 5000))
        # SCRFLAT: alternating up/down ticks around 10 -> no pattern, RSI near neutral.
        for i in range(55):
            d = start + timedelta(days=i)
            c = 10.1 if i % 2 == 0 else 9.9
            rows.append(("SCRFLAT", d, 10.0, max(10.0, c) + 0.1, min(10.0, c) - 0.1, c, 1000))
        for ticker, d, o, h, l, c, v in rows:
            conn.execute(
                "INSERT INTO prices_daily (ticker, trade_date, open, high, low, close, volume, source, fetched_at)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, 'test', now())"
                " ON CONFLICT (ticker, trade_date) DO UPDATE SET open = EXCLUDED.open, high = EXCLUDED.high,"
                " low = EXCLUDED.low, close = EXCLUDED.close, volume = EXCLUDED.volume",
                (ticker, d, o, h, l, c, v),
            )


def _cleanup():
    with get_conn() as conn:
        conn.execute("DELETE FROM prices_daily WHERE ticker = ANY(%s)", (list(_TICKERS),))


def test_screen_with_no_filters_returns_whole_universe_with_metrics():
    _seed()
    try:
        result = screen_tickers_tool(list(_TICKERS))
        by_ticker = {m["ticker"]: m for m in result["data"]["matches"]}
        assert set(by_ticker) == set(_TICKERS)
        assert by_ticker["SCRUP"]["ma50"] is not None
        assert by_ticker["SCRUP"]["rsi14"] is not None
    finally:
        _cleanup()


def test_screen_filters_by_candle_pattern():
    _seed()
    try:
        result = screen_tickers_tool(list(_TICKERS), candle_pattern="bullish_engulfing")
        matches = {m["ticker"] for m in result["data"]["matches"]}
        assert matches == {"SCRUP"}
    finally:
        _cleanup()


def test_screen_any_pattern_and_precomputed_ratios():
    _seed()
    try:
        matches = screen_tickers_tool(list(_TICKERS), candle_pattern="any")["data"]["matches"]
        assert [m["ticker"] for m in matches] == ["SCRUP"]
        m = matches[0]
        assert m["volume_ratio"] == round(5000 / m["volume_avg20"], 2)
        assert abs(m["pct_vs_ma50"] - (m["close"] / m["ma50"] - 1) * 100) < 0.05
    finally:
        _cleanup()


def test_screen_filters_by_rsi_and_ma():
    _seed()
    try:
        all_rows = {m["ticker"]: m for m in screen_tickers_tool(list(_TICKERS))["data"]["matches"]}
        threshold = all_rows["SCRFLAT"]["rsi14"] + 1  # alternating series sits near neutral RSI
        result = screen_tickers_tool(list(_TICKERS), rsi_above=threshold, above_ma50=True)
        matches = {m["ticker"] for m in result["data"]["matches"]}
        assert matches == {"SCRUP"}
    finally:
        _cleanup()


def test_screen_filters_by_volume_ratio():
    _seed()
    try:
        result = screen_tickers_tool(list(_TICKERS), min_volume_ratio=2.0)
        matches = {m["ticker"] for m in result["data"]["matches"]}
        assert matches == {"SCRUP"}
    finally:
        _cleanup()


def test_screen_empty_universe_returns_no_matches():
    result = screen_tickers_tool(["ZZZNOPE"])
    assert result["data"]["matches"] == []


def test_screen_treats_ticker_text_as_data_not_sql():
    result = screen_tickers_tool(["X') OR TRUE OR ticker IN ('"])
    assert result["data"]["matches"] == []
