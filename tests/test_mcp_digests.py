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


@pytest.mark.parametrize("day", [6, 10])  # holiday on a weekday, and a Saturday
def test_digests_tell_hermes_to_stay_silent_when_not_a_trading_day(at, calendar, day):
    at(2026, 10, day)
    for tool in (get_market_digest_input_tool, get_weekly_digest_input_tool):
        data = tool()["data"]
        assert data["is_trading_day"] is False and "[SILENT]" in data["instruction"]
        assert "market" not in data  # nothing to narrate, so no tokens spent reading it


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
