"""Cron-equivalent scheduler for run_analysis (spec §4.2).

Long-lived loop, separate process from worker/MCP: every POLL_INTERVAL_S it
checks whether the current UTC time has crossed one of SCHEDULE's trigger
times for today's trading day, and if so enqueues one job per watchlist
ticker (VN30 members + active watchlist_extra). Dedup and actual pipeline
execution are handled by pipeline.jobs/ops.worker — this process only
decides *when* and *for whom*.

Run as a long-lived process:
    python -m ops.scheduler
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from llm.macro import MACRO_JOB_TYPE
from mcp_server.connection import get_rw_conn
from ops.alerting import log_event
from pipeline.calendar import NoCalendarDataError, is_trading_day
from pipeline.jobs import enqueue
from pipeline.llm_gate import llm_pipeline_enabled

POLL_INTERVAL_S = 60

# (job_type, trigger hour:minute UTC, depth) — times per spec §4.2 table.
SCHEDULE = [
    ("scheduled_pre", (1, 30), "quick"),
    ("scheduled_post", (8, 15), "full"),
]

# Macro pre-market digest (spec §4.2) — fires alongside scheduled_pre, not a
# per-ticker job: it's one summary across VN30's overnight news. Enqueued
# like any other job (ticker="MARKET", no real ticker since it's market-wide)
# so ops/worker.py runs the slow news-fetch + LLM call, not this loop — a
# flaky news API stalling here would delay/skip scheduled_post and the
# Friday weekly job, which share this same process and thread.
MACRO_JOB_KEY_PREFIX = MACRO_JOB_TYPE
MACRO_TICKER = "MARKET"
MACRO_TRIGGER = (1, 30)

# Weekly deep-dive (spec §4.2 "Phân tích sâu theo tuần") — Friday, right
# after scheduled_post, so it reuses the week's freshest close instead of
# waiting out the weekend. VN30-only: §4.2 scopes this to VN30 theses.
WEEKLY_JOB_TYPE = "scheduled_weekly"
WEEKLY_WEEKDAY = 4  # Friday
WEEKLY_TRIGGER = (8, 30)
WEEKLY_DEPTH = "full"


# In-session refresh: Hermes answers from the DB (cache_hit, up to 6 h old per
# vn-rules snapshot_cache) and only a result older than that makes the MCP queue
# a worker + vnstock call. These fixed VN-time slots (every 2 h while the market
# is open) keep the DB well inside that window. First slot is 09:15, not 09:00:
# the ATO auction has no matched bar until then.
INTRADAY_JOB_TYPE = "scheduled_intraday"
INTRADAY_SLOTS = {(9, 15), (11, 0), (13, 0), (15, 0)}

# After the close (15:45 VN, behind scheduled_post's 15:15 batch): overwrite
# every bar stored mid-session with its closing OHLCV, so off-hours lookups,
# indicators and grading read final data. One market-wide job, run by the worker.
CLOSE_SYNC_JOB_TYPE = "close_sync"
CLOSE_SYNC_TRIGGER = (8, 45)
_VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")


def intraday_slot(now: datetime) -> str | None:
    """"HHMM" (VN time) if `now` falls on an intraday slot minute, else None."""
    vn = now.astimezone(_VN_TZ)
    if vn.weekday() >= 5 or (vn.hour, vn.minute) not in INTRADAY_SLOTS:
        return None
    return f"{vn.hour:02d}{vn.minute:02d}"


def _run_intraday_if_due(conn, now: datetime, already_fired_today: set[str]) -> None:
    slot = intraday_slot(now)
    if slot is None:
        return
    fired_key = f"{INTRADAY_JOB_TYPE}:{now.date().isoformat()}:{slot}"
    if fired_key in already_fired_today:
        return
    already_fired_today.add(fired_key)
    try:
        if not is_trading_day(conn, now.astimezone(_VN_TZ).date()):
            return
    except NoCalendarDataError:
        log_event("scheduler_no_calendar_data", date=now.date().isoformat())
        return

    # Don't pile up: a ticker whose previous slot is still queued/running (e.g.
    # during rate-limit backoff) skips this one.
    busy = {r[0] for r in conn.execute(
        "SELECT ticker FROM jobs WHERE job_type = %s AND status IN ('queued', 'running')",
        (INTRADAY_JOB_TYPE,),
    ).fetchall()}
    vn_date = now.astimezone(_VN_TZ).date()
    for ticker in get_watchlist(conn):
        if ticker in busy:
            continue
        job_key, created = enqueue(
            conn, ticker, job_type=INTRADAY_JOB_TYPE, depth="quick", requested_by="cron",
            schedule_date=vn_date, slot=slot,
        )
        if created:
            log_event("scheduler_enqueued", job_key=job_key, ticker=ticker, job_type=INTRADAY_JOB_TYPE)


def get_watchlist(conn) -> list[str]:
    rows = conn.execute(
        """
        SELECT ticker FROM index_membership
        WHERE index_code = 'VN30' AND (valid_to IS NULL OR valid_to >= CURRENT_DATE)
        UNION
        SELECT ticker FROM watchlist_extra WHERE status = 'active'
        """
    ).fetchall()
    return [r[0] for r in rows]


def get_vn30_tickers(conn) -> list[str]:
    rows = conn.execute(
        "SELECT ticker FROM index_membership"
        " WHERE index_code = 'VN30' AND (valid_to IS NULL OR valid_to >= CURRENT_DATE)"
    ).fetchall()
    return [r[0] for r in rows]


def run_due_jobs(conn, now: datetime, already_fired_today: set[str]) -> None:
    today_key_prefix = now.date().isoformat()
    for job_type, (hour, minute), depth in SCHEDULE:
        fired_key = f"{job_type}:{today_key_prefix}"
        if fired_key in already_fired_today:
            continue
        if (now.hour, now.minute) < (hour, minute):
            continue

        try:
            if not is_trading_day(conn, now.date()):
                already_fired_today.add(fired_key)
                continue
        except NoCalendarDataError:
            log_event("scheduler_no_calendar_data", date=today_key_prefix)
            continue

        tickers = get_watchlist(conn)
        for ticker in tickers:
            job_key, created = enqueue(
                conn, ticker, job_type=job_type, depth=depth, requested_by="cron",
                schedule_date=now.date(),
            )
            if created:
                log_event("scheduler_enqueued", job_key=job_key, ticker=ticker, job_type=job_type)
        already_fired_today.add(fired_key)

    _run_weekly_if_due(conn, now, already_fired_today)
    _run_intraday_if_due(conn, now, already_fired_today)
    _run_macro_premarket_if_due(conn, now, already_fired_today)
    _run_market_job_if_due(conn, now, already_fired_today, CLOSE_SYNC_JOB_TYPE, CLOSE_SYNC_TRIGGER)


def _run_weekly_if_due(conn, now: datetime, already_fired_today: set[str]) -> None:
    today_key_prefix = now.date().isoformat()
    fired_key = f"{WEEKLY_JOB_TYPE}:{today_key_prefix}"
    if fired_key in already_fired_today:
        return
    if now.weekday() != WEEKLY_WEEKDAY or (now.hour, now.minute) < WEEKLY_TRIGGER:
        return

    try:
        if not is_trading_day(conn, now.date()):
            already_fired_today.add(fired_key)
            return
    except NoCalendarDataError:
        log_event("scheduler_no_calendar_data", date=today_key_prefix)
        return

    for ticker in get_vn30_tickers(conn):
        job_key, created = enqueue(
            conn, ticker, job_type=WEEKLY_JOB_TYPE, depth=WEEKLY_DEPTH, requested_by="cron",
            schedule_date=now.date(),
        )
        if created:
            log_event("scheduler_enqueued", job_key=job_key, ticker=ticker, job_type=WEEKLY_JOB_TYPE)
    already_fired_today.add(fired_key)


def _run_macro_premarket_if_due(conn, now: datetime, already_fired_today: set[str]) -> None:
    if not llm_pipeline_enabled():  # the macro digest is an LLM call; Hermes answers market questions itself
        return
    _run_market_job_if_due(conn, now, already_fired_today, MACRO_JOB_TYPE, MACRO_TRIGGER)


def _run_market_job_if_due(conn, now: datetime, already_fired_today: set[str], job_type: str, trigger) -> None:
    """One market-wide (ticker=MARKET) job per trading day, at `trigger` UTC."""
    today_key_prefix = now.date().isoformat()
    fired_key = f"{job_type}:{today_key_prefix}"
    if fired_key in already_fired_today:
        return
    if (now.hour, now.minute) < trigger:
        return

    try:
        if not is_trading_day(conn, now.date()):
            already_fired_today.add(fired_key)
            return
    except NoCalendarDataError:
        log_event("scheduler_no_calendar_data", date=today_key_prefix)
        return

    job_key, created = enqueue(
        conn, MACRO_TICKER, job_type=job_type, requested_by="cron", schedule_date=now.date(),
    )
    if created:
        log_event("scheduler_enqueued", job_key=job_key, ticker=MACRO_TICKER, job_type=job_type)
    already_fired_today.add(fired_key)


def main() -> None:
    log_event("scheduler_started")
    already_fired_today: set[str] = set()
    last_date = None
    while True:
        now = datetime.now(timezone.utc)
        if last_date != now.date():
            already_fired_today.clear()
            last_date = now.date()
        with get_rw_conn() as conn:
            run_due_jobs(conn, now, already_fired_today)
        time.sleep(POLL_INTERVAL_S)


if __name__ == "__main__":
    main()
