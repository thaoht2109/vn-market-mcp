from datetime import date, datetime, timezone

import pytest

import mcp_server.tools.digests as digests
from db.connection import get_conn
from mcp_server.tools.digests import get_market_digest_input_tool, get_weekly_digest_input_tool


class _FrozenDatetime(datetime):
    frozen = None

    @classmethod
    def now(cls, tz=None):
        return cls.frozen.astimezone(tz) if tz else cls.frozen


@pytest.fixture
def at(monkeypatch):
    def _set(year, month, day, hour=1, minute=30):  # UTC
        _FrozenDatetime.frozen = datetime(year, month, day, hour, minute, tzinfo=timezone.utc)
        monkeypatch.setattr(digests, "datetime", _FrozenDatetime)
    return _set


@pytest.fixture
def calendar():
    # 2026-10-05 Monday: trading day. 2026-10-10 Saturday: weekend. 2026-10-06: weekday marked as holiday.
    with get_conn() as conn:
        conn.execute("DELETE FROM trading_calendar WHERE trade_date IN ('2026-10-05', '2026-10-06', '2026-10-10')")
        conn.execute("INSERT INTO trading_calendar (trade_date, is_trading_day, note) VALUES"
                     " ('2026-10-05', true, NULL), ('2026-10-06', false, 'holiday'), ('2026-10-10', false, NULL)")
    yield
    with get_conn() as conn:
        conn.execute("DELETE FROM trading_calendar WHERE trade_date IN ('2026-10-05', '2026-10-06', '2026-10-10')")


def test_digest_inputs_return_structured_facts_on_a_trading_day(at, calendar):
    at(2026, 10, 5)
    market = get_market_digest_input_tool()["data"]
    assert market["is_trading_day"] is True
    assert {"market", "vn30_last_session", "foreign_flow", "overnight_headlines",
            "macro_headlines", "news_sources"} <= market.keys()
    week = get_weekly_digest_input_tool()["data"]
    assert week["is_trading_day"] is True
    assert {"market", "vn30_week", "foreign_flow", "open_label_counts", "week_headlines"} <= week.keys()


def test_market_block_has_precomputed_ma_gaps(at, calendar):
    at(2026, 10, 5)
    with get_conn() as conn:
        conn.execute("DELETE FROM market_regime_daily WHERE trade_date = '2099-01-01'")
        conn.execute("INSERT INTO market_regime_daily (trade_date, index_symbol, close, change_pct, ma20, ma50, ma200,"
                     " rsi14, trend, regime) VALUES ('2099-01-01', 'VNINDEX', 1700, -0.8, 1750, 1800, NULL, 24, 'down', 'risk_off')")
    try:
        market = get_market_digest_input_tool()["data"]["market"]
    finally:
        with get_conn() as conn:
            conn.execute("DELETE FROM market_regime_daily WHERE trade_date = '2099-01-01'")
    assert market["pct_vs_ma20"] == round((1700 / 1750 - 1) * 100, 2)
    assert market["pct_vs_ma50"] == round((1700 / 1800 - 1) * 100, 2)
    assert market["pct_to_ma20"] == round((1750 / 1700 - 1) * 100, 2)
    assert market["pct_vs_ma200"] is None  # no MA200 yet: null, not a made-up number


@pytest.mark.parametrize("day", [6, 10])  # holiday on a weekday, and a Saturday
def test_digests_on_a_non_trading_day_give_the_latest_session_flagged(at, calendar, day):
    at(2026, 10, day)
    for tool in (get_market_digest_input_tool, get_weekly_digest_input_tool):
        out = tool()
        assert out["data"]["is_trading_day"] is False  # the scheduled digests' prompts go [SILENT] on this
        assert "market" in out["data"]  # a user asking on a weekend still gets the last session
        assert any("không có phiên giao dịch" in w for w in out["warnings"])


def test_unknown_calendar_fails_open(at):
    at(2031, 6, 2)  # far beyond the seeded calendar
    assert get_market_digest_input_tool()["data"]["is_trading_day"] is True


def test_market_digest_has_macro_headlines_and_source_freshness_and_hides_dropped_news(at, calendar, monkeypatch):
    from datetime import timedelta
    from pipeline.news import ensure_news_partitions, store_news_item

    monkeypatch.setattr(digests, "load_sources", lambda: [{"name": "test_dig"}])
    at(2026, 10, 5)
    now = datetime.now(timezone.utc)
    with get_conn() as conn:
        ensure_news_partitions(conn, date.today())
        conn.execute("DELETE FROM news_items WHERE source = 'test_dig'")
        conn.execute("INSERT INTO tickers (ticker, name, exchange, sector, industry_group)"
                     " VALUES ('TSTDIG', 'x', 'HOSE', 't', 'other') ON CONFLICT DO NOTHING")
        conn.execute("DELETE FROM index_membership WHERE ticker = 'TSTDIG'")
        conn.execute("INSERT INTO index_membership (index_code, ticker, valid_from) VALUES ('VN30', 'TSTDIG', '2026-01-01')")
        for url, title, status in (("http://t/d1", "Tin giữ lại", "kept"), ("http://t/d2", "Tin bị loại", "dropped")):
            store_news_item(conn, source="test_dig", url=url, title=title, summary=None,
                            published_at=now - timedelta(hours=1), fetched_at=now, tickers=["TSTDIG"],
                            pillars=["tien_te"], stream="A", filter_status=status, filter_reason=None)
    try:
        data = get_market_digest_input_tool()["data"]
        titles = [h["title"] for h in data["overnight_headlines"]]
        assert "Tin giữ lại" in titles and "Tin bị loại" not in titles
        assert [h["title"] for h in data["macro_headlines"]] == ["Tin giữ lại"]
        assert data["news_sources"][0]["source"] == "test_dig"
    finally:
        with get_conn() as conn:
            conn.execute("DELETE FROM news_items WHERE source = 'test_dig'")
            conn.execute("DELETE FROM index_membership WHERE ticker = 'TSTDIG'")
            conn.execute("DELETE FROM tickers WHERE ticker = 'TSTDIG'")


def test_flow_total_is_returned_and_a_row_beyond_matched_value_is_listed_not_summed():
    from mcp_server.connection import get_ro_conn
    day = "2099-01-02"
    seed = (("TSTFA", 20000, 2_000_000, 28e9), ("TSTFB", 100000, 1_150_000, -9144e9))  # second: net >> close*volume
    with get_conn() as conn:
        for t, close, vol, net in seed:
            conn.execute("INSERT INTO tickers (ticker, exchange) VALUES (%s, 'HOSE') ON CONFLICT DO NOTHING", (t,))
            conn.execute("INSERT INTO index_membership (index_code, ticker, valid_from) VALUES ('VN30', %s, '2026-01-01')"
                         " ON CONFLICT DO NOTHING", (t,))
            conn.execute("INSERT INTO prices_daily (ticker, trade_date, open, high, low, close, volume, source, fetched_at)"
                         " VALUES (%s, %s, %s, %s, %s, %s, %s, 'test', now())", (t, day, close, close, close, close, vol))
            conn.execute("INSERT INTO foreign_flow_daily (ticker, trade_date, net_value, source, fetched_at)"
                         " VALUES (%s, %s, %s, 'test', now())", (t, day, net))
        conn.execute("INSERT INTO market_regime_daily (trade_date, index_symbol, close, change_pct, regime, computed_at)"
                     " VALUES (%s, 'VNINDEX', 1710.88, -0.5, 'risk_off', %s)", (day, f"{day} 12:46:00+07"))
    try:
        with get_ro_conn() as conn:
            flow, market = digests._flow(conn, 1), digests._market(conn)
        assert flow["total_bn_vnd"] == 28.0  # the key used to sit inside a comment and never reached the digest
        assert flow["left_out"] == [{"ticker": "TSTFB", "date": "02/01", "net_bn_vnd": -9144.0}]
        assert market["provisional"] is True
        warns = digests._data_warnings({"market": market, "foreign_flow": flow})
        assert any("tạm tính" in w for w in warns) and any("TSTFB 02/01" in w for w in warns)
    finally:
        with get_conn() as conn:
            for t, *_ in seed:
                conn.execute("DELETE FROM foreign_flow_daily WHERE ticker = %s", (t,))
                conn.execute("DELETE FROM prices_daily WHERE ticker = %s", (t,))
                conn.execute("DELETE FROM index_membership WHERE ticker = %s", (t,))
            conn.execute("DELETE FROM market_regime_daily WHERE trade_date = %s", (day,))
