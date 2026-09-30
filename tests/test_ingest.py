from datetime import date, datetime, timezone

from providers.vnstock_provider import PriceBar
from pipeline.ingest import (
    IngestBatchError,
    assert_batch_ok,
    ingest_batch,
    ingest_ticker_day,
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
