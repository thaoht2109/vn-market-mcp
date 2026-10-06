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

    def get_foreign_flow(self, ticker, start, end):
        return []


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
