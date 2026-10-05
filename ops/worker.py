"""Background worker for the `jobs` queue (spec §4.8).

Polls for queued jobs, runs pipeline.run_analysis synchronously per job, and
pushes the result to Telegram — so MCP's run_analysis tool never blocks.

Run as a long-lived process, separate from the Hermes/MCP process:
    python -m ops.worker
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from llm.client import LLMCallError
from llm.config import ModelsConfig
from llm.macro import MACRO_JOB_TYPE, MacroDigestValidationError, run_macro_daily
from mcp_server.connection import get_rw_conn
from ops.alerting import log_event, send_ops_alert, send_user_message
from ops.scheduler import CLOSE_SYNC_JOB_TYPES, get_vn30_tickers
from pipeline.ingest import sync_recent_prices
from pipeline.positions import personalize
from pipeline.jobs import claim_next, mark_done, mark_failed, reclaim_stale_running, release, requeue
from pipeline.run_analysis import run_analysis
from providers.vnstock_provider import VNStockProvider

SNAPSHOT_DIR = Path("snapshots")
POLL_INTERVAL_S = 5
RATE_LIMIT_BACKOFF_S = 65


def _setup_vnstock_api_key() -> None:
    """Register VNSTOCK_API_KEY with vnai (raises the 20/min Guest rate limit).

    vnai persists this to /root/.vnstock, which isn't a mounted volume — it's
    wiped on every container recreate, so this must re-run on every startup.
    """
    api_key = os.environ.get("VNSTOCK_API_KEY")
    if not api_key:
        return
    import vnai

    vnai.setup_api_key(api_key)
    log_event("vnstock_tier", tier=vnai.get_user_tier())


def _run_macro_premarket(conn) -> None:
    """Fetch overnight VN30 news and run the macro digest LLM role.

    Runs here (not in ops/scheduler.py) so a slow/flaky news API stalls only
    this worker's job loop, never the scheduler's enqueue loop that also
    gates scheduled_post/scheduled_weekly.
    """
    provider = VNStockProvider(source="VCI")
    now = datetime.now(timezone.utc)
    window_start = (now - timedelta(days=1)).date()
    window_end = now.date()
    news_items = []
    for ticker in get_vn30_tickers(conn):
        try:
            news_items.extend(provider.get_news(ticker, window_start, window_end))
        except Exception as exc:  # one flaky ticker's API call must not block the whole digest
            log_event("macro_premarket_news_fetch_failed", ticker=ticker, error=str(exc))

    try:
        result = run_macro_daily(conn, ModelsConfig.load(), news_items)
    except (LLMCallError, MacroDigestValidationError) as exc:
        log_event("macro_premarket_skipped", error=str(exc))
        return

    log_event("macro_premarket_done", noteworthy=result.noteworthy, news_count=len(news_items))
    if result.noteworthy:
        send_ops_alert(f"[Trước phiên] {result.summary}")


def _run_close_sync(conn) -> None:
    today = datetime.now(ZoneInfo("Asia/Ho_Chi_Minh")).date()
    outcomes = sync_recent_prices(conn, VNStockProvider(source="VCI"), today)
    failed = [f"{o.ticker}: {o.detail}" for o in outcomes if o.status == "error"]
    rebased = [o.ticker for o in outcomes if o.detail and o.detail.startswith("rebased")]
    log_event("close_sync_done", synced=len(outcomes) - len(failed), rebased=rebased, failed=failed)


def _requester(job) -> str | None:
    """The user behind an on-demand job: requested_by is "user:<id>" (run_analysis from a
    pinned profile) or "watch:<id>"; "mcp"/"cron" have none."""
    kind, _, user = (job.requested_by or "").partition(":")
    return user if kind in ("user", "watch") and user else None


def _notify(conn, job, text: str) -> None:
    """A user's result goes only to that user's own chat. The shared ops chat gets it only
    when nobody registered asked (legacy single-user setup)."""
    user = _requester(job)
    if user is None or send_user_message(conn, user, text) is None:
        send_ops_alert(text)


def _label_for(conn, job, run_id: str, label: str | None) -> str | None:
    user = _requester(job)
    if user is None:
        return label
    row = conn.execute("SELECT snapshot_ref FROM runs WHERE run_id = %s", (run_id,)).fetchone()
    path = Path(row[0]) if row else None
    snapshot = json.loads(path.read_text()) if path and path.exists() else {"action_label": label}
    return personalize(conn, snapshot, job.ticker, user)["action_label"]


def run_one(conn) -> bool:
    """Claim and process one job. Returns False if the queue was empty."""
    job = claim_next(conn)
    if job is None:
        return False

    log_event("worker_job_started", job_key=job.job_key, ticker=job.ticker, attempts=job.attempts)
    try:
        if job.job_type == MACRO_JOB_TYPE or job.job_type in CLOSE_SYNC_JOB_TYPES:
            if job.job_type == MACRO_JOB_TYPE:
                _run_macro_premarket(conn)
            else:
                _run_close_sync(conn)
            mark_done(conn, job, None)
            release(conn, job)
            log_event("worker_job_finished", job_key=job.job_key, ticker=job.ticker, status="ok")
            return True
        result = run_analysis(
            conn, VNStockProvider(source="VCI"), job.ticker, SNAPSHOT_DIR,
            mode=job.job_type, style=job.style, depth=job.depth,
        )
    except BaseException as exc:  # vnstock rate-limit exits raise SystemExit, not Exception
        conn.rollback()
        if isinstance(exc, SystemExit):
            requeue(conn, job)
            release(conn, job)
            log_event("worker_job_rate_limited", job_key=job.job_key, ticker=job.ticker)
            time.sleep(RATE_LIMIT_BACKOFF_S)
            return True
        mark_failed(conn, job, f"{type(exc).__name__}: {exc}")
        release(conn, job)
        log_event("worker_job_crashed", job_key=job.job_key, ticker=job.ticker, error=str(exc))
        send_ops_alert(f"[vn-market-mcp] LỖI job {job.job_key}: {type(exc).__name__}: {exc}")
        return True

    if result.status == "ok":
        mark_done(conn, job, result.run_id)
        release(conn, job)
        log_event("worker_job_finished", job_key=job.job_key, ticker=job.ticker, status=result.status)
        # Scheduled runs cover ~30 tickers: one Telegram message each is spam.
        # Results stay in the DB/snapshots; only on-demand runs ping the chat.
        if job.job_type == "on_demand":
            label = _label_for(conn, job, result.run_id, result.action_label)
            header = f"[{job.ticker}] Phân tích xong — nhãn: {label}. run_id={result.run_id}"
            _notify(conn, job, f"{header}\n\n{result.report_text}" if result.report_text else header)
    else:
        mark_failed(conn, job, f"{result.status}: {result.message}")
        release(conn, job)
        log_event("worker_job_finished", job_key=job.job_key, ticker=job.ticker, status=result.status)
        # A watched small cap fails coverage every scheduled run; that's expected, not an incident.
        if job.job_type == "on_demand":
            _notify(conn, job, f"[{job.ticker}] {result.status}: {result.message}")
        elif result.status != "insufficient_coverage":
            send_ops_alert(f"[{job.ticker}] {result.status}: {result.message}")
    return True


def main() -> None:
    log_event("worker_started")
    _setup_vnstock_api_key()
    while True:
        with get_rw_conn() as conn:
            reclaim_stale_running(conn)
            processed = run_one(conn)
        if not processed:
            time.sleep(POLL_INTERVAL_S)


if __name__ == "__main__":
    main()
