import pytest

from mcp_server.tools.history import query_history_tool


def test_query_history_rejects_unknown_series():
    with pytest.raises(ValueError):
        query_history_tool("VNM", series="not_a_series")


def test_query_history_prices_empty_ticker_returns_empty_list_not_error():
    result = query_history_tool("ZZZNOPE", series="prices")
    assert result["data"]["rows"] == []
    assert result["warnings"] == ["không có dữ liệu cho ZZZNOPE"]


def test_query_history_prices_returns_rows_for_known_ticker():
    # VN30 seed data from db/migrations includes at least the tickers table;
    # this test seeds one price row directly to avoid depending on live vnstock.
    from db.connection import get_conn

    with get_conn() as conn:
        conn.execute(
            "INSERT INTO tickers (ticker, exchange) VALUES ('HISTTEST', 'HOSE') ON CONFLICT DO NOTHING"
        )
        conn.execute(
            """
            INSERT INTO prices_daily (ticker, trade_date, open, high, low, close, volume, source, fetched_at)
            VALUES ('HISTTEST', '2026-09-28', 10, 11, 9, 10.5, 1000, 'test', now())
            ON CONFLICT (ticker, trade_date) DO NOTHING
            """
        )
    try:
        result = query_history_tool("HISTTEST", series="prices")
        assert len(result["data"]["rows"]) == 1
        assert result["data"]["rows"][0]["close"] == 10.5
        assert result["warnings"] == []
    finally:
        with get_conn() as conn:
            conn.execute("DELETE FROM prices_daily WHERE ticker = 'HISTTEST'")
            conn.execute("DELETE FROM tickers WHERE ticker = 'HISTTEST'")


def test_query_history_respects_date_range():
    from db.connection import get_conn

    with get_conn() as conn:
        conn.execute(
            "INSERT INTO tickers (ticker, exchange) VALUES ('HISTTEST2', 'HOSE') ON CONFLICT DO NOTHING"
        )
        for d, c in [("2026-09-01", 10), ("2026-09-15", 11), ("2026-09-28", 12)]:
            conn.execute(
                """
                INSERT INTO prices_daily (ticker, trade_date, open, high, low, close, volume, source, fetched_at)
                VALUES ('HISTTEST2', %s, %s, %s, %s, %s, 1000, 'test', now())
                ON CONFLICT (ticker, trade_date) DO NOTHING
                """,
                (d, c, c, c, c),
            )
    try:
        result = query_history_tool(
            "HISTTEST2", series="prices", start_date="2026-09-10", end_date="2026-09-20"
        )
        assert len(result["data"]["rows"]) == 1
        assert result["data"]["rows"][0]["close"] == 11
    finally:
        with get_conn() as conn:
            conn.execute("DELETE FROM prices_daily WHERE ticker = 'HISTTEST2'")
            conn.execute("DELETE FROM tickers WHERE ticker = 'HISTTEST2'")


def test_query_history_flags_foreign_net_beyond_matched_value():
    from db.connection import get_conn

    with get_conn() as conn:
        conn.execute("INSERT INTO tickers (ticker, exchange) VALUES ('HISTFF', 'HOSE') ON CONFLICT DO NOTHING")
        for d, net in (("2026-09-28", 1e9), ("2026-09-29", -500e9)):
            conn.execute("INSERT INTO prices_daily (ticker, trade_date, open, high, low, close, volume, source, fetched_at)"
                         " VALUES ('HISTFF', %s, 10000, 10000, 10000, 10000, 1000000, 'test', now())", (d,))
            conn.execute("INSERT INTO foreign_flow_daily (ticker, trade_date, net_value, source, fetched_at)"
                         " VALUES ('HISTFF', %s, %s, 'test', now())", (d, net))
    try:
        result = query_history_tool("HISTFF", series="foreign_flow")
        assert [r["within_matched_value"] for r in result["data"]["rows"]] == [True, False]
        assert len(result["warnings"]) == 1 and "2026-09-29" in result["warnings"][0]
    finally:
        with get_conn() as conn:
            conn.execute("DELETE FROM foreign_flow_daily WHERE ticker = 'HISTFF'")
            conn.execute("DELETE FROM prices_daily WHERE ticker = 'HISTFF'")
