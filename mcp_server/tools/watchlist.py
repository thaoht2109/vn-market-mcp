"""Per-user watchlist (watchlist_extra). Watched tickers join VN30 in the scheduler's
daily/intraday runs (ops.scheduler.get_watchlist), so any listed ticker gets the same
treatment as a VN30 member once someone watches it."""
from __future__ import annotations

from datetime import datetime, timezone

from mcp_server.connection import get_ro_conn, get_rw_conn
from mcp_server.envelope import build_envelope
from pipeline.coverage import UnknownTickerError, classify_universe_tier, resolve_or_register_ticker
from pipeline.jobs import enqueue
from providers.vnstock_provider import VNStockProvider


def watch_ticker_tool(ticker: str, declared_by: str) -> dict:
    now = datetime.now(timezone.utc)
    with get_rw_conn() as conn:
        try:
            match = resolve_or_register_ticker(conn, VNStockProvider(source="VCI"), ticker)
        except UnknownTickerError as exc:
            return build_envelope(
                {"status": "not_found", "ticker": ticker.strip().upper()}, sources=["postgres", "vnstock"],
                as_of=now, warnings=[str(exc)],
            )
        conn.execute(
            """
            INSERT INTO watchlist_extra (ticker, added_by, confirmed_at, last_interaction_at, status)
            VALUES (%s, %s, now(), now(), 'active')
            ON CONFLICT (ticker, added_by) DO UPDATE SET status = 'active', last_interaction_at = now()
            """,
            (match.ticker, declared_by),
        )
        tier = classify_universe_tier(conn, match.ticker)
        # First analysis right away (it also backfills price history for a new ticker)
        # instead of waiting for the next scheduled slot. enqueue commits.
        job_id, _ = enqueue(conn, match.ticker, job_type="on_demand", requested_by=f"watch:{declared_by}")
    return build_envelope(
        {"status": "ok", "ticker": match.ticker, "name": match.name, "exchange": match.exchange,
         "universe_tier": tier, "job_id": job_id},
        sources=["postgres"], as_of=now,
    )


def unwatch_ticker_tool(ticker: str, declared_by: str) -> dict:
    now = datetime.now(timezone.utc)
    ticker = ticker.strip().upper()
    with get_rw_conn() as conn:
        row = conn.execute(
            """
            UPDATE watchlist_extra SET status = 'inactive', last_interaction_at = now()
            WHERE ticker = %s AND added_by = %s AND status = 'active'
            RETURNING ticker
            """,
            (ticker, declared_by),
        ).fetchone()
    if row is None:
        return build_envelope(
            {"status": "not_found", "ticker": ticker}, sources=["postgres"], as_of=now,
            warnings=[f"{ticker} không có trong danh sách theo dõi của người dùng này"],
        )
    return build_envelope({"status": "ok", "ticker": ticker}, sources=["postgres"], as_of=now)


def list_watchlist_tool(declared_by: str) -> dict:
    """The user's watched tickers with each one's latest verdict (None = no prediction
    yet: still queued, or insufficient_coverage — get_snapshot says which)."""
    now = datetime.now(timezone.utc)
    with get_ro_conn() as conn:
        rows = conn.execute(
            """
            SELECT w.ticker, t.name, t.exchange, p.action_label, p.confidence, p.created_at
            FROM watchlist_extra w
            JOIN tickers t USING (ticker)
            LEFT JOIN LATERAL (
              SELECT action_label, confidence, created_at FROM predictions
              WHERE ticker = w.ticker ORDER BY created_at DESC LIMIT 1
            ) p ON true
            WHERE w.added_by = %s AND w.status = 'active'
            ORDER BY w.ticker
            """,
            (declared_by,),
        ).fetchall()
    items = [
        {"ticker": r[0], "name": r[1], "exchange": r[2], "action_label": r[3],
         "confidence": float(r[4]) if r[4] is not None else None,
         "label_as_of": r[5].isoformat() if r[5] else None}
        for r in rows
    ]
    return build_envelope({"declared_by": declared_by, "items": items}, sources=["postgres"], as_of=now)
