import os
import subprocess
import sys
from pathlib import Path

import psycopg

from pipeline.jobs import _lock_key, claim_next, enqueue, get_job, mark_done, mark_failed, reclaim_stale_running, release
from tests.conftest import insert_ticker

DATABASE_URL = os.environ["DATABASE_URL"]
_ROOT = Path(__file__).parent.parent


def _cleanup(ticker: str):
    conn = psycopg.connect(DATABASE_URL, autocommit=True)
    conn.execute("DELETE FROM jobs WHERE ticker = %s", (ticker,))
    conn.close()


def test_enqueue_then_claim_then_mark_done(db_conn):
    insert_ticker(db_conn, "JOBTEST1")
    db_conn.commit()
    try:
        conn = psycopg.connect(DATABASE_URL)
        job_key, created = enqueue(conn, "JOBTEST1", job_type="on_demand")
        assert created is True

        job = claim_next(conn)
        assert job is not None
        assert job.ticker == "JOBTEST1"
        assert job.attempts == 1

        mark_done(conn, job, run_id="on_demand:JOBTEST1:1")
        release(conn, job)

        status = get_job(conn, job_key)
        assert status == {
            "job_key": job_key, "status": "done", "run_id": "on_demand:JOBTEST1:1",
            "error": None, "attempts": 1,
        }
        conn.close()
    finally:
        _cleanup("JOBTEST1")


def test_mark_failed_records_error(db_conn):
    insert_ticker(db_conn, "JOBTEST2")
    db_conn.commit()
    try:
        conn = psycopg.connect(DATABASE_URL)
        job_key, _ = enqueue(conn, "JOBTEST2", job_type="on_demand")
        job = claim_next(conn)

        mark_failed(conn, job, "boom")
        release(conn, job)

        status = get_job(conn, job_key)
        assert status["status"] == "failed"
        assert status["error"] == "boom"
        conn.close()
    finally:
        _cleanup("JOBTEST2")


def test_claim_next_skips_ticker_already_running(db_conn):
    insert_ticker(db_conn, "JOBTEST3")
    db_conn.commit()
    try:
        conn_a = psycopg.connect(DATABASE_URL)
        conn_b = psycopg.connect(DATABASE_URL)

        enqueue(conn_a, "JOBTEST3", job_type="on_demand")
        enqueue(conn_a, "JOBTEST3", job_type="on_demand")

        job_a = claim_next(conn_a)
        assert job_a is not None

        job_b = claim_next(conn_b)
        assert job_b is None  # same (ticker, job_type) still locked by conn_a

        release(conn_a, job_a)
        conn_a.close()
        conn_b.close()
    finally:
        _cleanup("JOBTEST3")


def test_get_job_returns_none_for_unknown_key(db_conn):
    conn = psycopg.connect(DATABASE_URL)
    assert get_job(conn, "does-not-exist") is None
    conn.close()


def test_reclaim_stale_running_requeues_old_jobs_only(db_conn):
    insert_ticker(db_conn, "JOBTEST4")
    db_conn.commit()
    try:
        conn = psycopg.connect(DATABASE_URL)
        job_key, _ = enqueue(conn, "JOBTEST4", job_type="on_demand")
        job = claim_next(conn)
        release(conn, job)  # simulate the worker process dying without marking done/failed
        conn.execute("UPDATE jobs SET started_at = now() - interval '20 minutes' WHERE id = %s", (job.id,))
        conn.commit()

        reclaim_stale_running(conn)

        assert get_job(conn, job_key)["status"] == "queued"
        conn.close()
    finally:
        _cleanup("JOBTEST4")


def test_lock_key_is_identical_across_processes():
    """hash() is randomised per process, so two workers used to disagree on the lock for the same job."""
    code = "from pipeline.jobs import _lock_key; print(_lock_key('HPG', 'on_demand'))"
    outs = {
        subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True, cwd=_ROOT).stdout.strip()
        for _ in range(3)
    }
    assert outs == {str(_lock_key("HPG", "on_demand"))}


def test_claim_next_prefers_collect_jobs_over_older_analysis_jobs(db_conn):
    insert_ticker(db_conn, "JOBPRIO")
    db_conn.commit()
    conn = psycopg.connect(DATABASE_URL)
    try:
        conn.execute("DELETE FROM jobs WHERE status = 'queued'")  # dedicated *_test DB (conftest guards it)
        conn.commit()
        enqueue(conn, "JOBPRIO", job_type="on_demand")  # older
        enqueue(conn, "MARKET", job_type="collect_rss", requested_by="cron")  # newer, but must win
        job = claim_next(conn)
        assert job.job_type == "collect_rss"
        release(conn, job)
    finally:
        conn.execute("DELETE FROM jobs WHERE ticker IN ('JOBPRIO', 'MARKET')")
        conn.commit()
        conn.close()
