from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np

from pipeline.calendar import seed_calendar_from_weekdays
from pipeline.run_analysis import run_analysis
from providers.vnstock_provider import ForeignFlowRecord, FundamentalRecord, PriceBar
from tests.conftest import insert_ticker


def _seed_env(db_conn, ticker="VNM", exchange="HOSE", industry_group="other", history_days=520):
    insert_ticker(db_conn, ticker, industry_group=industry_group, exchange=exchange)
    db_conn.execute(
        "INSERT INTO index_membership (index_code, ticker, valid_from, valid_to) VALUES ('VN30', %s, %s, NULL)",
        (ticker, date(2020, 1, 1)),
    )

    today = datetime.now(timezone.utc).date()
    seed_calendar_from_weekdays(db_conn, today - timedelta(days=history_days * 2), today, holidays=set())

    trading_days = [
        row[0]
        for row in db_conn.execute(
            "SELECT trade_date FROM trading_calendar WHERE is_trading_day = true AND trade_date <= %s"
            " ORDER BY trade_date DESC LIMIT %s",
            (today, history_days),
        ).fetchall()
    ]
    trading_days.reverse()  # oldest first; last element is the most recent trading day

    closes = np.linspace(80000, 100000, len(trading_days))
    rows = [
        (ticker, d, c, c * 1.01, c * 0.99, c, 2_000_000, c * 2_000_000, "TCBS", datetime.now(timezone.utc))
        for d, c in zip(trading_days[:-1], closes[:-1])  # most recent day comes from the provider below
    ]
    with db_conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO prices_daily (ticker, trade_date, open, high, low, close, volume, value, source, fetched_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (ticker, trade_date) DO NOTHING
            """,
            rows,
        )

    return trading_days[-1], float(closes[-1])


class _StubProvider:
    def __init__(self, latest_day, latest_close):
        self.latest_day = latest_day
        self.latest_close = latest_close

    def get_ohlcv(self, ticker, start, end):
        return [
            PriceBar(
                ticker=ticker, trade_date=self.latest_day, open=self.latest_close,
                high=self.latest_close * 1.01, low=self.latest_close * 0.99, close=self.latest_close,
                volume=2_500_000, value=None, source="TCBS", fetched_at=datetime.now(timezone.utc),
            )
        ]

    def get_fundamentals(self, ticker, quarters):
        return [
            FundamentalRecord(
                ticker=ticker, period=f"2026Q{q}", report_type="audited", version=1,
                metrics={"roe": 0.15 + q * 0.01, "pb": 1.5 + q * 0.05},
                published_date=date(2026, min(3 * q, 12), 20),
                source="TCBS", fetched_at=datetime.now(timezone.utc),
            )
            for q in range(1, 5)
        ]

    def get_foreign_flow(self, ticker, start, end):
        return [
            ForeignFlowRecord(
                ticker=ticker, trade_date=self.latest_day, buy_value=1_000_000, sell_value=800_000,
                net_value=200_000, room_left=500_000, source="TCBS", fetched_at=datetime.now(timezone.utc),
            )
        ]


def test_run_analysis_happy_path_writes_run_and_prediction(db_conn, tmp_path):
    latest_day, latest_close = _seed_env(db_conn, "VNM")
    provider = _StubProvider(latest_day, latest_close)

    result = run_analysis(db_conn, provider, "VNM", tmp_path)

    assert result.status == "ok"
    assert result.action_label is not None

    run_row = db_conn.execute("SELECT snapshot_ref FROM runs WHERE run_id = %s", (result.run_id,)).fetchone()
    assert run_row is not None
    assert Path(run_row[0]).exists()

    prediction_row = db_conn.execute(
        "SELECT action_label, status FROM predictions WHERE ticker = 'VNM'"
    ).fetchone()
    assert prediction_row is not None
    assert prediction_row[0] == result.action_label
    assert prediction_row[1] == "open"


def test_run_analysis_rerun_with_unchanged_label_does_not_duplicate_prediction(db_conn, tmp_path):
    # Review focus #2: re-running for a ticker whose label/thesis hasn't
    # changed must not insert a second open predictions row.
    latest_day, latest_close = _seed_env(db_conn, "VNM")
    provider = _StubProvider(latest_day, latest_close)

    first = run_analysis(db_conn, provider, "VNM", tmp_path)
    second = run_analysis(db_conn, provider, "VNM", tmp_path)

    assert first.action_label == second.action_label
    count = db_conn.execute(
        "SELECT count(*) FROM predictions WHERE ticker = 'VNM' AND status = 'open'"
    ).fetchone()[0]
    assert count == 1


def test_run_analysis_unknown_ticker_is_rejected(db_conn, tmp_path):
    result = run_analysis(db_conn, object(), "NOTATICKER", tmp_path)
    assert result.status == "unknown_ticker"
    assert result.run_id is None

    run_count = db_conn.execute("SELECT count(*) FROM runs").fetchone()[0]
    assert run_count == 0


def test_run_analysis_insufficient_coverage_writes_run_but_no_prediction(db_conn, tmp_path):
    insert_ticker(db_conn, "ABC")
    today = datetime.now(timezone.utc).date()
    seed_calendar_from_weekdays(db_conn, today - timedelta(days=10), today, holidays=set())
    trading_day = db_conn.execute(
        "SELECT trade_date FROM trading_calendar WHERE is_trading_day = true ORDER BY trade_date DESC LIMIT 1"
    ).fetchone()[0]

    class _ThinProvider:
        def get_ohlcv(self, ticker, start, end):
            return [
                PriceBar(
                    ticker=ticker, trade_date=trading_day, open=10000, high=10100, low=9900, close=10000,
                    volume=100, value=None, source="TCBS", fetched_at=datetime.now(timezone.utc),
                )
            ]

        def get_fundamentals(self, ticker, quarters):
            return []

        def get_foreign_flow(self, ticker, start, end):
            return []

    result = run_analysis(db_conn, _ThinProvider(), "ABC", tmp_path)

    assert result.status == "insufficient_coverage"
    assert result.action_label is None

    run_row = db_conn.execute("SELECT run_id FROM runs WHERE run_id = %s", (result.run_id,)).fetchone()
    assert run_row is not None
    prediction_row = db_conn.execute("SELECT id FROM predictions WHERE ticker = 'ABC'").fetchone()
    assert prediction_row is None
