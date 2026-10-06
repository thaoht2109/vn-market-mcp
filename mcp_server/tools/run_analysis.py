"""MCP tool wrapping pipeline.jobs.enqueue.

§4.1 of the spec requires run_analysis to return a job_id immediately and
have a background worker run the pipeline (results pushed via Telegram),
so the MCP call never blocks for the duration of one full pipeline run.
The worker itself lives in ops/worker.py and polls the `jobs` table.

§4.4 cache: before enqueueing, check whether the most recent run for this
ticker is still fresh enough to reuse (chat shouldn't burn a fresh pipeline
run — and the vnstock rate limit — just to repeat what a cron run already
computed moments ago).
"""
from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import yaml

from mcp_server.connection import get_rw_conn
from mcp_server.envelope import build_envelope
from mcp_server.identity import pinned_user
from ops.alerting import log_event
from pipeline.calendar import NoCalendarDataError, latest_trading_day
from pipeline.jobs import enqueue, get_job
from pipeline.run_analysis import CONFIG_PATH


def _latest_run(conn, ticker: str, style: str, depth: str):
    # style picks the scoring weights, so a run made for another style/depth is a different answer.
    return conn.execute(
        "SELECT run_id, as_of FROM runs WHERE %s = ANY(tickers) AND style = %s AND depth = %s"
        " ORDER BY as_of DESC LIMIT 1",
        (ticker, style, depth),
    ).fetchone()


def _pending_job(conn, ticker: str, style: str, depth: str):
    """A queued/running job that will produce the same run — another user's request
    for the same ticker+style+depth joins it instead of paying for a second pipeline run."""
    return conn.execute(
        "SELECT job_key, status FROM jobs WHERE ticker = %s AND style = %s AND depth = %s"
        " AND status IN ('queued', 'running') ORDER BY created_at LIMIT 1",
        (ticker, style, depth),
    ).fetchone()


def _cache_is_fresh(conn, run_as_of: datetime, now: datetime, rules: dict) -> bool:
    """§4.4: prices only move while matching runs, so a run made after the last
    price move stays valid until the next one:
    - in matching: reuse within max_age_minutes;
    - lunch break: reuse a run made after the morning close, until the afternoon opens;
    - after the close: reuse a run made after the close; once close_sync has had
      close_settle_minutes to settle bars/flow, only a run made after that —
      valid until the next session's first bar."""
    try:
        session = latest_trading_day(conn, now)
    except NoCalendarDataError:
        return False  # fail closed: can't tell, don't risk serving stale data silently

    hours = rules["trading_hours"]
    vn_tz = ZoneInfo("Asia/Ho_Chi_Minh")
    lunch_start = datetime.combine(session, time.fromisoformat(hours["morning_end"]), vn_tz)
    lunch_end = datetime.combine(session, time.fromisoformat(hours["afternoon_start"]), vn_tz)
    close_at = datetime.combine(session, time.fromisoformat(hours["afternoon_end"]), vn_tz)
    settled_at = close_at + timedelta(minutes=rules["snapshot_cache"]["close_settle_minutes"])

    if now >= settled_at:
        return run_as_of >= settled_at  # a 10:00 run or a pre-close_sync run must not answer all night
    if now >= close_at:
        return run_as_of >= close_at  # closing price is final; flow still settling for a few minutes
    if lunch_start <= now < lunch_end and run_as_of >= lunch_start:
        return True
    age_minutes = (now - run_as_of).total_seconds() / 60
    return age_minutes <= rules["snapshot_cache"]["max_age_minutes"]


def run_analysis_tool(ticker: str, style: str = "long", depth: str = "quick") -> dict:
    now = datetime.now(timezone.utc)
    rules = yaml.safe_load(CONFIG_PATH.read_text())

    with get_rw_conn() as conn:
        latest = _latest_run(conn, ticker, style, depth)
        if latest is not None:
            run_id, run_as_of = latest
            if _cache_is_fresh(conn, run_as_of, now, rules):
                log_event("mcp_run_analysis_cache_hit", ticker=ticker, run_id=run_id)
                return build_envelope(
                    {"job_id": None, "run_id": run_id, "status": "cache_hit", "ticker": ticker},
                    sources=["postgres"], as_of=run_as_of,
                )

        # ponytail: check-then-insert, two calls in the same millisecond can still both enqueue;
        # the cost is one duplicate run, add an advisory xact lock if that ever shows up in logs.
        pending = _pending_job(conn, ticker, style, depth)
        if pending is not None:
            job_key, status = pending
            log_event("mcp_run_analysis_joined", ticker=ticker, job_key=job_key)
            return build_envelope(
                {"job_id": job_key, "status": status, "ticker": ticker},
                sources=["postgres"], as_of=now,
            )

        job_key, created = enqueue(
            conn, ticker, job_type="on_demand", style=style, depth=depth,
            requested_by=f"user:{pinned_user()}" if pinned_user() else "mcp",
        )

    log_event("mcp_run_analysis_enqueued", ticker=ticker, job_key=job_key, created=created)
    return build_envelope(
        {"job_id": job_key, "status": "queued", "ticker": ticker},
        sources=["postgres"], as_of=now,
    )


def get_job_status_tool(job_id: str) -> dict:
    now = datetime.now(timezone.utc)
    with get_rw_conn() as conn:
        job = get_job(conn, job_id)

    if job is None:
        return build_envelope(
            {"job_id": job_id, "status": "not_found"}, sources=["postgres"], as_of=now,
            warnings=["job_id không tồn tại"],
        )
    return build_envelope({"job_id": job_id, **job}, sources=["postgres"], as_of=now)
