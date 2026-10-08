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
INTRADAY_EXPIRE = timedelta(minutes=90)  # intraday slots are 09:30 / 11:00 / 13:00

# Claim order: quick market-wide jobs (news collection, the 15-min price-alert check: one job each,
# must not sit behind the ~30 tickers queued at 15:05-15:30), then on_demand (a user is waiting in
# chat), then scheduled jobs.
COLLECT_RSS_JOB_TYPE = "collect_rss"
COLLECT_JOB_TYPES = (COLLECT_RSS_JOB_TYPE,)
ALERT_CHECK_JOB_TYPE = "alert_check"
_FIRST_JOB_TYPES = [*COLLECT_JOB_TYPES, ALERT_CHECK_JOB_TYPE]


# Postgres advisory locks take a single bigint key; hash (ticker, job_type) into one so concurrent
# runs of the same ticker+mode serialize (spec §4.4) without a separate lock table. Must be stable
# across processes: the builtin hash() is randomised per process, so two workers would take
# different locks for the same job.
# close_sync and its 18:00 retry do the same market-wide work: one lock, so they never run side by
# side spending the same vnstock budget twice.
_SHARED_LOCK = {"close_sync_retry": "close_sync"}


def _lock_key(ticker: str, job_type: str) -> int:
    job_type = _SHARED_LOCK.get(job_type, job_type)
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
        (_FIRST_JOB_TYPES,),
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


def expire_stale_jobs(conn: psycopg.Connection) -> list[str]:
    """Fail queued jobs whose result can no longer be used, so a backlog can't starve fresh work:
    a close_sync/close_sync_retry of a past day (the next close_sync re-syncs recent days), or an
    intraday slot that is INTRADAY_EXPIRE old (the next slot or the close supersedes it)."""
    rows = conn.execute(
        """
        UPDATE jobs SET status = 'failed', finished_at = now(), error = 'expired: superseded before it could run'
        WHERE status = 'queued' AND (
          (job_type IN ('close_sync', 'close_sync_retry')
           AND split_part(job_key, ':', 3)::date < (now() AT TIME ZONE 'Asia/Ho_Chi_Minh')::date)
          OR (job_type = 'scheduled_intraday' AND created_at < now() - %s))
        RETURNING job_key
        """,
        (INTRADAY_EXPIRE,),
    ).fetchall()
    conn.commit()
    return [r[0] for r in rows]


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
