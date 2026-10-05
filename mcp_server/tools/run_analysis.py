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

from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo

import yaml

from mcp_server.connection import get_rw_conn
from mcp_server.envelope import build_envelope
from ops.alerting import log_event
from pipeline.calendar import NoCalendarDataError, latest_trading_day
from pipeline.jobs import enqueue, get_job
from pipeline.run_analysis import CONFIG_PATH


def _latest_run(conn, ticker: str):
    return conn.execute(
        "SELECT run_id, as_of FROM runs WHERE %s = ANY(tickers) ORDER BY as_of DESC LIMIT 1",
        (ticker,),
    ).fetchone()


def _cache_is_fresh(conn, run_as_of: datetime, now: datetime, rules: dict) -> bool:
    """§4.4: while the session runs, reuse within max_age_minutes; after the
    close, reuse only a snapshot built on closing prices (made after the
    close) until the next session's first bar."""
    try:
        session = latest_trading_day(conn, now)
    except NoCalendarDataError:
        return False  # fail closed: can't tell, don't risk serving stale data silently

    close_at = datetime.combine(
        session, time.fromisoformat(rules["trading_hours"]["afternoon_end"]), ZoneInfo("Asia/Ho_Chi_Minh"),
    )
    if now < close_at:  # session (incl. lunch break) still running
        age_minutes = (now - run_as_of).total_seconds() / 60
        return age_minutes <= rules["snapshot_cache"]["max_age_minutes"]

    # Off-hours: reuse only a run made after that session closed — i.e. built
    # on closing prices. A 10:00 intraday run must not answer at 20:00.
    return run_as_of >= close_at


def run_analysis_tool(ticker: str, style: str = "long", depth: str = "quick") -> dict:
    now = datetime.now(timezone.utc)
    rules = yaml.safe_load(CONFIG_PATH.read_text())

    with get_rw_conn() as conn:
        latest = _latest_run(conn, ticker)
        if latest is not None:
            run_id, run_as_of = latest
            if _cache_is_fresh(conn, run_as_of, now, rules):
                log_event("mcp_run_analysis_cache_hit", ticker=ticker, run_id=run_id)
                return build_envelope(
                    {"job_id": None, "run_id": run_id, "status": "cache_hit", "ticker": ticker},
                    sources=["postgres"], as_of=run_as_of,
                )

        job_key, created = enqueue(conn, ticker, job_type="on_demand", style=style, depth=depth, requested_by="mcp")

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
