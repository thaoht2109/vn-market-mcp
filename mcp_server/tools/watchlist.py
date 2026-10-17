"""Per-user watchlist (watchlist_extra). Watched tickers join VN30 in the scheduler's
daily/intraday runs (ops.scheduler.get_watchlist), so any listed ticker gets the same
treatment as a VN30 member once someone watches it."""
from __future__ import annotations

from datetime import datetime, timezone

from mcp_server.connection import get_ro_conn, get_rw_conn
from mcp_server.envelope import build_envelope
from mcp_server.identity import no_personal_scope, pinned_user
from pipeline.coverage import UnknownTickerError, classify_universe_tier, resolve_or_register_ticker
from pipeline.jobs import enqueue
from pipeline.positions import personalize
from pipeline.snapshot import load_snapshot
from providers.vnstock_provider import VNStockProvider


# Max tickers one user watches (each is analysed every session). None (Telegram) = no cap; a host serving
# outside users sets it at startup.
MAX_WATCHES_PER_USER: int | None = None


def watch_ticker_tool(ticker: str) -> dict:
    now = datetime.now(timezone.utc)
    user = pinned_user()
    if user is None:
        return no_personal_scope(now)
    with get_rw_conn() as conn:
        if (limit := MAX_WATCHES_PER_USER) is not None:
            symbol = ticker.strip().upper()
            held = {r[0] for r in conn.execute(
                "SELECT ticker FROM watchlist_extra WHERE added_by = %s AND status = 'active'", (user,))}
            if symbol not in held and len(held) >= limit:
                return build_envelope(
                    {"status": "watch_limit", "ticker": symbol, "limit": limit}, sources=["postgres"], as_of=now,
                    warnings=[f"Bạn đã theo dõi tối đa {limit} mã; bỏ bớt bằng unwatch_ticker."],
                )
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
            (match.ticker, user),
        )
        tier = classify_universe_tier(conn, match.ticker)
        # First analysis right away (it also backfills price history for a new ticker)
        # instead of waiting for the next scheduled slot. enqueue commits.
        job_id, _ = enqueue(conn, match.ticker, job_type="on_demand", requested_by=f"watch:{user}")
    return build_envelope(
        {"status": "ok", "ticker": match.ticker, "name": match.name, "exchange": match.exchange,
         "universe_tier": tier, "job_id": job_id},
        sources=["postgres"], as_of=now,
    )


def unwatch_ticker_tool(ticker: str) -> dict:
    now = datetime.now(timezone.utc)
    user = pinned_user()
    if user is None:
        return no_personal_scope(now)
    ticker = ticker.strip().upper()
    with get_rw_conn() as conn:
        row = conn.execute(
            """
            UPDATE watchlist_extra SET status = 'inactive', last_interaction_at = now()
            WHERE ticker = %s AND added_by = %s AND status = 'active'
            RETURNING ticker
            """,
            (ticker, user),
        ).fetchone()
    if row is None:
        return build_envelope(
            {"status": "not_found", "ticker": ticker}, sources=["postgres"], as_of=now,
            warnings=[f"{ticker} không có trong danh sách theo dõi của người dùng này"],
        )
    return build_envelope({"status": "ok", "ticker": ticker}, sources=["postgres"], as_of=now)


def list_watchlist_tool() -> dict:
    """The user's watched tickers with each one's latest verdict as this user sees it (None = no
    prediction yet: still queued, or insufficient_coverage — get_snapshot says which)."""
    now = datetime.now(timezone.utc)
    user = pinned_user()
    if user is None:
        return no_personal_scope(now)
    items = []
    with get_ro_conn() as conn:
        rows = conn.execute(
            """
            SELECT w.ticker, t.name, t.exchange, p.action_label, p.confidence, p.created_at, r.snapshot_ref, r.snapshot
            FROM watchlist_extra w
            JOIN tickers t USING (ticker)
            LEFT JOIN LATERAL (
              SELECT run_id, action_label, confidence, created_at FROM predictions
              WHERE ticker = w.ticker ORDER BY created_at DESC LIMIT 1
            ) p ON true
            LEFT JOIN runs r ON r.run_id = p.run_id
            WHERE w.added_by = %s AND w.status = 'active'
            ORDER BY w.ticker
            """,
            (user,),
        ).fetchall()
        for ticker, name, exchange, label, conf, label_at, snapshot_ref, stored in rows:
            snapshot = {"action_label": label}
            if (full := load_snapshot(stored, snapshot_ref)) is not None:
                snapshot = {**full, "action_label": label}
            view = personalize(conn, snapshot, ticker, user)
            items.append({
                "ticker": ticker, "name": name, "exchange": exchange, "action_label": view["action_label"],
                "holding_state": view["holding_state"], "confidence": float(conf) if conf is not None else None,
                "label_as_of": label_at.isoformat() if label_at else None,
            })
    return build_envelope({"user_id": user, "items": items}, sources=["postgres"], as_of=now)
