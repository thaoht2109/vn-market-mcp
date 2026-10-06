from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from datetime import date, timedelta

import psycopg

# A job stuck in 'running' past this long means its worker crashed/was killed
# mid-job (Postgres already released the advisory lock when that connection
# died) — safe to requeue rather than leave it stuck forever.
STALE_RUNNING_AFTER = timedelta(minutes=15)

# Claim order: market-wide collection jobs (one quick job, must not sit behind the ~30 tickers
# queued at 15:05-15:30), then on_demand (a user is waiting in chat), then scheduled jobs.
COLLECT_RSS_JOB_TYPE = "collect_rss"
COLLECT_JOB_TYPES = (COLLECT_RSS_JOB_TYPE,)


# Postgres advisory locks take a single bigint key; hash (ticker, job_type) into one so concurrent
# runs of the same ticker+mode serialize (spec §4.4) without a separate lock table. Must be stable
# across processes: the builtin hash() is randomised per process, so two workers would take
# different locks for the same job.
def _lock_key(ticker: str, job_type: str) -> int:
    digest = hashlib.blake2b(f"{ticker}|{job_type}".encode(), digest_size=8).digest()
    return int.from_bytes(digest, "big") & 0x7FFFFFFFFFFFFFFF


@dataclass
class Job:
    id: int
    job_key: str
    job_type: str
    ticker: str
    style: str
    depth: str
    requested_by: str | None
    attempts: int


def enqueue(
    conn: psycopg.Connection,
    ticker: str,
    job_type: str = "on_demand",
    style: str = "long",
    depth: str = "quick",
    requested_by: str | None = None,
    schedule_date: date | None = None,
    slot: str | None = None,
) -> tuple[str, bool]:
    """Insert a queued job. Returns (job_key, created) — created=False means
    a job with this key already existed (caller re-used it, e.g. a retry)."""
    if job_type == "on_demand":
        job_key = f"{job_type}:{ticker}:{int(time.time() * 1000)}"
    else:
        # Scheduled jobs dedupe per calendar day (spec §4.2 example key
        # "scheduled_post:2026-09-29") — without the date, a ticker could
        # only ever get ONE scheduled_post job for its whole lifetime.
        job_key = f"{job_type}:{ticker}:{(schedule_date or date.today()).isoformat()}"
        if slot:  # intraday jobs repeat within a day: one key per time slot
            job_key += f":{slot}"
    row = conn.execute(
        """
        INSERT INTO jobs (job_key, job_type, ticker, style, depth, requested_by)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (job_key) DO NOTHING
        RETURNING job_key
        """,
        (job_key, job_type, ticker, style, depth, requested_by),
    ).fetchone()
    conn.commit()
    return job_key, row is not None


def reclaim_stale_running(conn: psycopg.Connection) -> int:
    """Requeue jobs stuck in 'running' from a worker that crashed mid-job."""
    rows = conn.execute(
        "UPDATE jobs SET status = 'queued' WHERE status = 'running' AND started_at < now() - %s"
        " RETURNING id",
        (STALE_RUNNING_AFTER,),
    ).fetchall()
    conn.commit()
    return len(rows)


def claim_next(conn: psycopg.Connection) -> Job | None:
    """Lock and claim the oldest queued job whose (ticker, job_type) isn't
    already running elsewhere. Returns None if nothing claimable right now."""
    row = conn.execute(
        "SELECT id, job_key, job_type, ticker, style, depth, requested_by, attempts"
        " FROM jobs WHERE status = 'queued'"
        " ORDER BY (job_type = ANY(%s::text[])) DESC, (job_type = 'on_demand') DESC, created_at LIMIT 20",
        (list(COLLECT_JOB_TYPES),),
    ).fetchall()
    for job_id, job_key, job_type, ticker, style, depth, requested_by, attempts in row:
        got_lock = conn.execute(
            "SELECT pg_try_advisory_lock(%s)", (_lock_key(ticker, job_type),)
        ).fetchone()[0]
        if not got_lock:
            continue
        updated = conn.execute(
            "UPDATE jobs SET status = 'running', attempts = attempts + 1, started_at = now()"
            " WHERE id = %s AND status = 'queued' RETURNING id",
            (job_id,),
        ).fetchone()
        conn.commit()
        if updated is None:
            conn.execute("SELECT pg_advisory_unlock(%s)", (_lock_key(ticker, job_type),))
            continue
        return Job(job_id, job_key, job_type, ticker, style, depth, requested_by, attempts + 1)
    return None


def release(conn: psycopg.Connection, job: Job) -> None:
    conn.execute("SELECT pg_advisory_unlock(%s)", (_lock_key(job.ticker, job.job_type),))
    conn.commit()


def mark_done(conn: psycopg.Connection, job: Job, run_id: str | None) -> None:
    conn.execute(
        "UPDATE jobs SET status = 'done', run_id = %s, finished_at = now() WHERE id = %s",
        (run_id, job.id),
    )
    conn.commit()


def requeue(conn: psycopg.Connection, job: Job) -> None:
    """Put a job back to 'queued' without counting it as failed (e.g. rate limit)."""
    conn.execute(
        "UPDATE jobs SET status = 'queued', started_at = NULL WHERE id = %s", (job.id,)
    )
    conn.commit()


def mark_failed(conn: psycopg.Connection, job: Job, error: str) -> None:
    conn.execute(
        "UPDATE jobs SET status = 'failed', error = %s, finished_at = now() WHERE id = %s",
        (error, job.id),
    )
    conn.commit()


def get_job(conn: psycopg.Connection, job_key: str) -> dict | None:
    row = conn.execute(
        "SELECT job_key, status, run_id, error, attempts FROM jobs WHERE job_key = %s",
        (job_key,),
    ).fetchone()
    if row is None:
        return None
    return {"job_key": row[0], "status": row[1], "run_id": row[2], "error": row[3], "attempts": row[4]}
