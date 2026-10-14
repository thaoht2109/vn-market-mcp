from datetime import date, datetime, timedelta, timezone

from providers.vnstock_provider import PriceBar
from pipeline.ingest import (
    IngestBatchError,
    assert_batch_ok,
    ingest_batch,
    ingest_ticker_day,
    sync_recent_prices,
    upsert_prices,
)
from tests.conftest import insert_ticker


def _bar(ticker, trade_date, close=84500.0):
    return PriceBar(
        ticker=ticker, trade_date=trade_date, open=close, high=close, low=close,
        close=close, volume=1_000_000, value=None, source="TCBS",
        fetched_at=datetime.now(timezone.utc),
    )


def test_upsert_prices_is_idempotent(db_conn):
    insert_ticker(db_conn, "VNM")
    bar = _bar("VNM", date(2026, 9, 25))

    upsert_prices(db_conn, [bar])
    upsert_prices(db_conn, [bar])  # re-run must not duplicate

    rows = db_conn.execute(
        "SELECT close FROM prices_daily WHERE ticker = %s AND trade_date = %s",
        ("VNM", date(2026, 9, 25)),
    ).fetchall()
    assert len(rows) == 1
    assert rows[0][0] == 84500.0


class _SelectiveFailProvider:
    def __init__(self, fail_ticker, bars_by_ticker):
        self.fail_ticker = fail_ticker
        self.bars_by_ticker = bars_by_ticker

    def get_ohlcv(self, ticker, start, end):
        if ticker == self.fail_ticker:
            raise RuntimeError("vnstock returned HTTP 500")
        return self.bars_by_ticker[ticker]


def test_ingest_batch_surfaces_partial_failure_without_dropping_it(db_conn):
    for t in ["VNM", "FPT", "HPG"]:
        insert_ticker(db_conn, t)
    trade_date = date(2026, 9, 25)
    provider = _SelectiveFailProvider(
        fail_ticker="FPT",
        bars_by_ticker={"VNM": [_bar("VNM", trade_date)], "HPG": [_bar("HPG", trade_date)]},
    )

    outcomes = ingest_batch(db_conn, provider, ["VNM", "FPT", "HPG"], trade_date)

    assert len(outcomes) == 3  # the failing ticker is present, not silently dropped
    by_ticker = {o.ticker: o for o in outcomes}
    assert by_ticker["VNM"].status == "ok"
    assert by_ticker["HPG"].status == "ok"
    assert by_ticker["FPT"].status == "error"
    assert "HTTP 500" in by_ticker["FPT"].detail

    try:
        assert_batch_ok(outcomes)
        assert False, "expected IngestBatchError"
    except IngestBatchError as exc:
        assert "FPT" in str(exc)


def test_ingest_ticker_day_flags_empty_response_as_error(db_conn):
    insert_ticker(db_conn, "VNM")

    class _EmptyProvider:
        def get_ohlcv(self, ticker, start, end):
            return []

    outcome = ingest_ticker_day(db_conn, _EmptyProvider(), "VNM", date(2026, 9, 25))
    assert outcome.status == "error"
    assert outcome.detail == "empty response"


def test_ingest_fundamentals_and_flow_writes_both_tables(db_conn):
    from providers.vnstock_provider import FundamentalRecord, ForeignFlowRecord
    from pipeline.ingest import ingest_fundamentals_and_flow

    insert_ticker(db_conn, "VNM")

    class _FundamentalsProvider:
        def get_fundamentals(self, ticker, quarters):
            return [
                FundamentalRecord(
                    ticker=ticker, period="2026Q2", report_type="audited", version=1,
                    metrics={"roe": 0.2, "pb": 1.5}, published_date=date(2026, 7, 20),
                    source="TCBS", fetched_at=datetime.now(timezone.utc),
                )
            ]

        def get_foreign_flow(self, ticker, start, end):
            return [
                ForeignFlowRecord(
                    ticker=ticker, trade_date=date(2026, 9, 25), buy_value=1_000_000,
                    sell_value=500_000, net_value=500_000, room_left=100_000,
                    source="TCBS", fetched_at=datetime.now(timezone.utc),
                )
            ]

    outcome = ingest_fundamentals_and_flow(db_conn, _FundamentalsProvider(), "VNM")

    assert outcome.status == "ok"
    fund_row = db_conn.execute(
        "SELECT metrics FROM fundamentals_quarterly WHERE ticker = 'VNM' AND period = '2026Q2'"
    ).fetchone()
    assert fund_row[0]["roe"] == 0.2
    flow_row = db_conn.execute(
        "SELECT net_value FROM foreign_flow_daily WHERE ticker = 'VNM' AND trade_date = '2026-09-25'"
    ).fetchone()
    assert flow_row[0] == 500_000


def test_ingest_fundamentals_failure_still_records_foreign_flow(db_conn):
    from providers.vnstock_provider import ForeignFlowRecord
    from pipeline.ingest import ingest_fundamentals_and_flow

    insert_ticker(db_conn, "VNM")

    class _FlakyFundamentals:
        def get_fundamentals(self, ticker, quarters):
            raise TimeoutError("VCI read timed out")

        def get_foreign_flow(self, ticker, start, end):
            return [
                ForeignFlowRecord(
                    ticker=ticker, trade_date=date(2026, 9, 25), buy_value=2, sell_value=1,
                    net_value=1, room_left=1, source="KBS", fetched_at=datetime.now(timezone.utc),
                )
            ]

    outcome = ingest_fundamentals_and_flow(db_conn, _FlakyFundamentals(), "VNM")

    assert outcome.status == "error" and "timed out" in outcome.detail
    assert db_conn.execute(
        "SELECT net_value FROM foreign_flow_daily WHERE ticker = 'VNM' AND trade_date = '2026-09-25'"
    ).fetchone()[0] == 1


def test_ingest_news_stores_raw_headlines_dedupes_and_survives_bad_provider(db_conn):
    from providers.vnstock_provider import NewsItem
    from pipeline.ingest import ingest_news

    insert_ticker(db_conn, "VNM")
    published = datetime(2026, 10, 1, 3, 0, tzinfo=timezone.utc)
    item = NewsItem(ticker="VNM", published_at=published, source="vnstock", url="http://x/1",
                    title="VNM công bố kết quả kinh doanh", summary=None, fetched_at=published)

    class _News:
        def get_news(self, ticker, start, end):
            return [item]

    class _Broken:
        def get_news(self, ticker, start, end):
            raise TimeoutError("news API down")

    assert ingest_news(db_conn, _News(), "VNM", date(2026, 9, 25), date(2026, 10, 2)) == 1
    ingest_news(db_conn, _News(), "VNM", date(2026, 9, 25), date(2026, 10, 2))  # same article again → no duplicate
    assert db_conn.execute("SELECT count(*) FROM news_items WHERE title = %s", (item.title,)).fetchone()[0] == 1
    assert ingest_news(db_conn, _Broken(), "VNM", date(2026, 9, 25), date(2026, 10, 2)) == 0
    far = NewsItem(ticker="VNM", published_at=datetime(2031, 1, 1, tzinfo=timezone.utc), source="v", url="http://x/2",
                   title="no partition for this month", summary=None, fetched_at=published)

    class _Far:
        def get_news(self, ticker, start, end):
            return [far]

    assert ingest_news(db_conn, _Far(), "VNM", date(2030, 12, 30), date(2031, 1, 2)) == 0  # missing partition skipped
    db_conn.execute("SELECT 1")  # transaction still usable


def test_fundamentals_are_not_refetched_while_the_stored_ones_are_fresh(db_conn):
    from providers.vnstock_provider import FundamentalRecord
    from pipeline.ingest import ingest_fundamentals_and_flow

    insert_ticker(db_conn, "VNM")
    calls = []

    class _P:
        def get_fundamentals(self, ticker, quarters):
            calls.append(1)
            return [FundamentalRecord(ticker=ticker, period="2026-Q2", report_type="audited", version=1,
                                      metrics={"roe": 0.2}, published_date=date(2026, 7, 20), source="VCI",
                                      fetched_at=datetime.now(timezone.utc))]

        def get_foreign_flow(self, ticker, start, end):
            return []

    assert ingest_fundamentals_and_flow(db_conn, _P(), "VNM").status == "ok"
    assert ingest_fundamentals_and_flow(db_conn, _P(), "VNM").status == "ok"   # same day → skip VCI
    assert calls == [1]
    db_conn.execute("UPDATE fundamentals_quarterly SET fetched_at = now() - interval '2 days' WHERE ticker = 'VNM'")
    ingest_fundamentals_and_flow(db_conn, _P(), "VNM")                           # stale → refetch
    assert calls == [1, 1]


def test_news_fetch_pauses_after_a_failure_so_a_dead_host_is_not_hammered(db_conn, monkeypatch):
    import pipeline.ingest as ingest

    monkeypatch.setattr(ingest, "_news_pause_until", 0.0)
    calls = []

    class _Down:
        def get_news(self, ticker, start, end):
            calls.append(ticker)
            raise TimeoutError("iq.vietcap.com.vn read timed out")

    for t in ("ACB", "BID", "CTG"):
        assert ingest.ingest_news(db_conn, _Down(), t, date(2026, 10, 1), date(2026, 10, 2)) == 0
    assert calls == ["ACB"]  # one failure, then the breaker skips the rest of the batch


class _SyncProvider:
    """Serves `bars` (date -> close) filtered to the requested window; records calls."""

    def __init__(self, closes):
        self.closes, self.calls = closes, []

    def get_ohlcv(self, ticker, start, end):
        self.calls.append((ticker, start))
        return [_bar(ticker, d, close=c) for d, c in self.closes.items() if start <= d <= end]

    def get_foreign_flow_board(self, tickers):
        return {}


def _stored(ticker, d, close, fetched_at):
    b = _bar(ticker, d, close=close)
    b.fetched_at = fetched_at
    return b


def test_sync_recent_prices_finalises_mid_session_bar(db_conn):
    insert_ticker(db_conn, "SYNCA")
    d = date(2020, 1, 7)
    upsert_prices(db_conn, [_stored("SYNCA", d, 10000.0, datetime(2020, 1, 7, 3, 0, tzinfo=timezone.utc))])  # 10:00 VN
    provider = _SyncProvider({d: 10500.0})

    outcomes = [o for o in sync_recent_prices(db_conn, provider, d) if o.ticker == "SYNCA"]

    assert [o.status for o in outcomes] == ["ok"] and outcomes[0].detail is None
    assert len(provider.calls) >= 1 and all(c[1] == d - timedelta(days=10) for c in provider.calls)  # no full reload
    assert db_conn.execute("SELECT close FROM prices_daily WHERE ticker = 'SYNCA'").fetchone()[0] == 10500.0


def test_sync_recent_prices_reloads_whole_history_when_vendor_rebased_it(db_conn):
    insert_ticker(db_conn, "SYNCB")
    after_close = datetime(2020, 1, 7, 9, 30, tzinfo=timezone.utc)  # 16:30 VN
    old, recent = date(2019, 6, 3), date(2020, 1, 7)
    upsert_prices(db_conn, [_stored("SYNCB", old, 100.0, after_close), _stored("SYNCB", recent, 100.0, after_close)])
    provider = _SyncProvider({old: 50.0, recent: 50.0})  # vendor halved history after a split

    outcome = [o for o in sync_recent_prices(db_conn, provider, date(2020, 1, 8)) if o.ticker == "SYNCB"][0]

    assert outcome.detail.startswith("rebased")
    closes = dict(db_conn.execute("SELECT trade_date, close FROM prices_daily WHERE ticker = 'SYNCB'").fetchall())
    assert closes == {old: 50.0, recent: 50.0}


def test_sync_recent_prices_ignores_normal_noise_and_mid_session_reference(db_conn):
    insert_ticker(db_conn, "SYNCC")
    d = date(2020, 1, 7)
    # stored mid-session at 90, final is 100: legitimate, must NOT trigger a full reload
    upsert_prices(db_conn, [_stored("SYNCC", d, 90.0, datetime(2020, 1, 7, 3, 0, tzinfo=timezone.utc))])
    provider = _SyncProvider({d: 100.0})

    outcome = [o for o in sync_recent_prices(db_conn, provider, date(2020, 1, 8)) if o.ticker == "SYNCC"][0]
    assert outcome.detail is None


def test_ingest_news_failures_are_recorded_in_source_health_not_swallowed(db_conn, monkeypatch):
    import pipeline.ingest as ingest
    from providers.vnstock_provider import NewsItem

    monkeypatch.setattr(ingest, "_news_pause_until", 0.0)

    class _Down:
        def get_news(self, ticker, start, end):
            raise TimeoutError("iq.vietcap.com.vn read timed out")

    assert ingest.ingest_news(db_conn, _Down(), "ACB", date(2026, 10, 1), date(2026, 10, 2)) == 0
    failures, error = db_conn.execute(
        "SELECT consecutive_failures, last_error FROM source_health WHERE source = 'vnstock_news'").fetchone()
    assert failures == 1 and "timed out" in error

    monkeypatch.setattr(ingest, "_news_pause_until", 0.0)
    published = datetime(2031, 1, 1, tzinfo=timezone.utc)  # no partition: the insert fails
    far = NewsItem(ticker="ACB", published_at=published, source="v", url="http://x/h1", title="no partition",
                   summary=None, fetched_at=published)

    class _Far:
        def get_news(self, ticker, start, end):
            return [far]

    assert ingest.ingest_news(db_conn, _Far(), "ACB", date(2030, 12, 30), date(2031, 1, 2)) == 0
    failures, error = db_conn.execute(
        "SELECT consecutive_failures, last_error FROM source_health WHERE source = 'vnstock_news'").fetchone()
    assert failures == 2 and "insert failed" in error

    monkeypatch.setattr(ingest, "_news_pause_until", 0.0)

    class _Empty:
        def get_news(self, ticker, start, end):
            return []

    ingest.ingest_news(db_conn, _Empty(), "ACB", date(2026, 10, 1), date(2026, 10, 2))
    assert db_conn.execute(
        "SELECT consecutive_failures FROM source_health WHERE source = 'vnstock_news'").fetchone()[0] == 0


# --- settled foreign flow (CafeF) -------------------------------------------------------------

from providers.vnstock_provider import ForeignFlowRecord  # noqa: E402


def _flow(ticker, d, buy, sell, source="CAFEF", fetched_at=None):
    return ForeignFlowRecord(ticker=ticker, trade_date=d, buy_value=buy, sell_value=sell, net_value=buy - sell,
                             room_left=1.0, source=source, fetched_at=fetched_at or datetime.now(timezone.utc))


class _HistProvider(_SyncProvider):
    """Like _SyncProvider but with a settled flow history; a ticker listed in `fail` raises."""

    def __init__(self, closes, flows, fail=()):
        super().__init__(closes)
        self.flows, self.fail, self.flow_calls = flows, set(fail), []

    def get_foreign_flow_history(self, ticker, start, end):
        self.flow_calls.append((ticker, start, end))
        if ticker in self.fail:
            raise ValueError("cafef down")
        return self.flows.get(ticker, [])

    def get_foreign_flow_board(self, tickers):
        raise AssertionError("the KBS board must not be used when settled history exists")


def test_sync_recent_prices_stores_settled_flow_per_ticker_and_one_failure_does_not_stop_the_rest(db_conn):
    d = date(2020, 1, 7)
    for t in ("CFA", "CFB"):
        insert_ticker(db_conn, t)
        upsert_prices(db_conn, [_stored(t, d, 100.0, datetime(2020, 1, 7, 3, 0, tzinfo=timezone.utc))])
    # CFA already carries a wrong KBS row for the day; the settled CafeF row must replace it
    db_conn.execute("INSERT INTO foreign_flow_daily (ticker, trade_date, buy_value, sell_value, net_value, source, fetched_at)"
                    " VALUES ('CFA', %s, 0, 99999, -99999, 'KBS', now())", (d,))
    provider = _HistProvider({d: 100.0}, {"CFA": [_flow("CFA", d, 5e9, 2e9)], "CFB": [_flow("CFB", d, 1e9, 1e9)]}, fail={"CFB"})

    outcomes = {o.ticker: o for o in sync_recent_prices(db_conn, provider, d) if o.ticker in ("CFA", "CFB")}

    assert outcomes["CFA"].status == "ok" and outcomes["CFB"].status == "error" and "cafef down" in outcomes["CFB"].detail
    assert db_conn.execute("SELECT net_value, source FROM foreign_flow_daily WHERE ticker = 'CFA' AND trade_date = %s", (d,)).fetchone() == (3e9, "CAFEF")
    assert all(call[2] == d and call[1] == d - timedelta(days=10) for call in provider.flow_calls)


def test_sync_recent_prices_is_not_done_until_the_flow_is_settled_after_15h30(db_conn):
    d = date(2020, 1, 7)
    insert_ticker(db_conn, "CFC")
    upsert_prices(db_conn, [_stored("CFC", d, 100.0, datetime(2020, 1, 7, 9, 0, tzinfo=timezone.utc))])  # closing bar, 16:00 VN
    provider = _HistProvider({d: 100.0}, {"CFC": [_flow("CFC", d, 2e9, 1e9, fetched_at=datetime(2020, 1, 7, 8, 5, tzinfo=timezone.utc))]})

    def pending():
        provider.flow_calls.clear()
        sync_recent_prices(db_conn, provider, d)
        return [c[0] for c in provider.flow_calls if c[0] == "CFC"]

    assert pending() == ["CFC"]  # bar final but no CAFEF row yet: still pending
    db_conn.execute("UPDATE foreign_flow_daily SET fetched_at = '2020-01-07 08:05:00+00' WHERE ticker = 'CFC'")  # 15:05 VN
    assert pending() == ["CFC"]  # stored at the 15:05 close_sync: the 18:00 retry pulls it again
    db_conn.execute("UPDATE foreign_flow_daily SET fetched_at = '2020-01-07 11:00:00+00' WHERE ticker = 'CFC'")  # 18:00 VN
    assert pending() == []


def test_on_demand_flow_uses_settled_history_out_of_hours_and_the_live_snapshot_in_session(db_conn, monkeypatch):
    import pipeline.ingest as ingest

    insert_ticker(db_conn, "CFD")
    day = date.today()

    class _P:
        def get_fundamentals(self, ticker, quarters):
            return []

        def get_foreign_flow(self, ticker, start, end):
            return [_flow(ticker, day, 1e9, 0, source="KBS")]

        def get_foreign_flow_history(self, ticker, start, end):
            return [_flow(ticker, day, 7e9, 2e9)]

    monkeypatch.setattr(ingest, "market_is_open", lambda conn, now: False)
    assert ingest.ingest_fundamentals_and_flow(db_conn, _P(), "CFD").status == "ok"
    assert db_conn.execute("SELECT source, net_value FROM foreign_flow_daily WHERE ticker = 'CFD'").fetchone() == ("CAFEF", 5e9)
    monkeypatch.setattr(ingest, "market_is_open", lambda conn, now: True)
    ingest.ingest_fundamentals_and_flow(db_conn, _P(), "CFD")
    assert db_conn.execute("SELECT source FROM foreign_flow_daily WHERE ticker = 'CFD'").fetchone() == ("KBS",)


def test_market_is_open_only_in_continuous_hours_of_a_trading_day(db_conn):
    from pipeline.ingest import market_is_open

    db_conn.execute("INSERT INTO trading_calendar (trade_date, is_trading_day, note) VALUES ('2099-01-05', true, NULL), "
                    "('2099-01-06', false, 'holiday') ON CONFLICT (trade_date) DO UPDATE SET is_trading_day = EXCLUDED.is_trading_day")

    def at(day, hh, mm):
        return datetime(2099, 1, day, hh - 7, mm, tzinfo=timezone.utc)

    assert not market_is_open(db_conn, at(5, 8, 59)) and market_is_open(db_conn, at(5, 9, 0))
    assert market_is_open(db_conn, at(5, 12, 0)) and not market_is_open(db_conn, at(5, 15, 0))  # lunch break counts as open
    assert not market_is_open(db_conn, at(6, 10, 0))  # holiday
    assert not market_is_open(db_conn, datetime(2150, 1, 5, 3, 0, tzinfo=timezone.utc))  # calendar not seeded that far
