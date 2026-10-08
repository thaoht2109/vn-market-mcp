"""The 07-08/10/2026 livelock: close_sync blew vnstock's rate limit, lost all progress on every
requeue (1,024 attempts) and, with its retry, held both workers so intraday jobs never ran."""
import os
import time
from datetime import date, datetime, timezone
from unittest.mock import patch

from tests.conftest import insert_ticker
from db.connection import get_conn
from pipeline.ingest import sync_recent_prices, upsert_prices
from pipeline.jobs import _lock_key, enqueue, expire_stale_jobs, get_job
from providers.rate_limit import wait_for_slot
from providers.vnstock_provider import PriceBar, VNStockProvider

DAY = date(2021, 3, 1)


def test_requests_from_every_caller_are_spaced_by_the_shared_budget():
    with get_conn() as conn:
        conn.execute("UPDATE api_budget SET next_slot = now() WHERE name = 'vnstock'")
    url = os.environ["PIPELINE_RW_DATABASE_URL"]
    start = time.monotonic()
    waits = [wait_for_slot(spacing_s=0.3, url=url) for _ in range(3)]
    assert waits[0] < 0.1 and 0.15 < waits[1] < 0.45 and time.monotonic() - start >= 0.55
    assert VNStockProvider(clients={})._throttle() is None  # injected test clients: no budget, no DB
    assert VNStockProvider()._throttle is wait_for_slot


def _bar(ticker, close, fetched_at):
    return PriceBar(ticker=ticker, trade_date=DAY, open=close, high=close, low=close, close=close, volume=100,
                    value=close * 100, source="t", fetched_at=fetched_at)


class _Flaky:
    """vnstock stand-in: exits (like its rate limiter) on the first request for `exit_on`."""

    def __init__(self, exit_on=None):
        self.exit_on, self.calls, self.boards = exit_on, [], 0

    def get_ohlcv(self, ticker, start, end):
        self.calls.append(ticker)
        if ticker == self.exit_on:
            self.exit_on = None
            raise SystemExit("rate limited")
        return [_bar(ticker, 11.0, datetime.now(timezone.utc))]

    def get_foreign_flow_board(self, tickers):
        self.boards += 1
        return {}


def test_close_sync_keeps_finished_tickers_across_a_rate_limit_exit():
    tickers = ("RSYNA", "RSYNB")
    mid_session = datetime(2021, 3, 1, 3, 0, tzinfo=timezone.utc)  # 10:00 VN
    with get_conn() as conn:
        for t in tickers:
            insert_ticker(conn, t)
        upsert_prices(conn, [_bar(t, 10.0, mid_session) for t in tickers])
    try:
        first = _Flaky(exit_on="RSYNB")
        with get_conn() as conn:
            try:
                sync_recent_prices(conn, first, DAY, commit_each=True)
            except SystemExit:
                conn.rollback()  # what the worker does before requeueing
        assert first.boards == 1  # one price-board request for every pending ticker

        second = _Flaky()
        with get_conn() as conn:
            outcomes = sync_recent_prices(conn, second, DAY, commit_each=True)
            closes = dict(conn.execute("SELECT ticker, close FROM prices_daily WHERE ticker = ANY(%s) AND trade_date = %s",
                                       (list(tickers), DAY)).fetchall())
        assert "RSYNA" not in second.calls and "RSYNB" in second.calls  # finished ticker not redone
        assert [o.ticker for o in outcomes if o.ticker in tickers] == ["RSYNB"]
        assert closes == {"RSYNA": 11.0, "RSYNB": 11.0}
    finally:
        with get_conn() as conn:
            conn.execute("DELETE FROM prices_daily WHERE ticker = ANY(%s)", (list(tickers),))
            conn.execute("DELETE FROM tickers WHERE ticker = ANY(%s)", (list(tickers),))


def test_past_close_syncs_and_old_intraday_slots_expire_but_fresh_jobs_stay():
    keys = ("close_sync:MARKET:2020-01-01", "scheduled_intraday:EXPA:2020-01-01:0930", "scheduled_intraday:EXPB:fresh")
    with get_conn() as conn:
        conn.execute("DELETE FROM jobs WHERE job_key = ANY(%s)", (list(keys),))
        conn.execute("INSERT INTO jobs (job_key, job_type, ticker) VALUES (%s, 'close_sync', 'MARKET')", (keys[0],))
        conn.execute("INSERT INTO jobs (job_key, job_type, ticker, created_at)"
                     " VALUES (%s, 'scheduled_intraday', 'EXPA', now() - interval '2 hours')", (keys[1],))
        conn.execute("INSERT INTO jobs (job_key, job_type, ticker) VALUES (%s, 'scheduled_intraday', 'EXPB')", (keys[2],))
    try:
        with get_conn() as conn:
            expired = expire_stale_jobs(conn)
            assert set(keys[:2]) <= set(expired) and keys[2] not in expired
            assert get_job(conn, keys[2])["status"] == "queued"
        assert _lock_key("MARKET", "close_sync_retry") == _lock_key("MARKET", "close_sync")
    finally:
        with get_conn() as conn:
            conn.execute("DELETE FROM jobs WHERE job_key = ANY(%s)", (list(keys),))


def test_worker_gives_up_and_tells_ops_after_repeated_rate_limits():
    from ops.worker import MAX_RATE_LIMITED_ATTEMPTS, run_one

    with get_conn() as conn:
        insert_ticker(conn, "RLCAP")
        conn.execute("DELETE FROM jobs WHERE ticker = 'RLCAP'")
        job_key, _ = enqueue(conn, "RLCAP", job_type="on_demand")
        conn.execute("UPDATE jobs SET attempts = %s WHERE job_key = %s", (MAX_RATE_LIMITED_ATTEMPTS - 1, job_key))
        conn.commit()
        try:
            with patch("ops.worker.run_analysis", side_effect=SystemExit("rate limited")), \
                 patch("ops.worker.send_ops_alert") as alert, patch("ops.worker.time.sleep") as sleep:
                assert run_one(conn) is True
            job = get_job(conn, job_key)
            assert job["status"] == "failed" and "rate limited" in job["error"]
            assert alert.called and not sleep.called
        finally:
            conn.execute("DELETE FROM jobs WHERE ticker = 'RLCAP'")
            conn.execute("DELETE FROM tickers WHERE ticker = 'RLCAP'")
            conn.commit()
