from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Literal

import psycopg

from providers.vnstock_provider import (
    CorporateEvent,
    ForeignFlowRecord,
    FundamentalRecord,
    PriceBar,
)


@dataclass
class PriceAdjustment:
    ticker: str
    ex_date: date
    kind: str
    factor: float


@dataclass
class IngestOutcome:
    ticker: str
    status: Literal["ok", "error"]
    detail: str | None
    rows_written: int


class IngestBatchError(Exception):
    pass


def upsert_prices(conn: psycopg.Connection, bars: list[PriceBar]) -> int:
    if not bars:
        return 0
    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO prices_daily (ticker, trade_date, open, high, low, close, volume, value, source, fetched_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (ticker, trade_date) DO UPDATE SET
              open = EXCLUDED.open, high = EXCLUDED.high, low = EXCLUDED.low, close = EXCLUDED.close,
              volume = EXCLUDED.volume, value = EXCLUDED.value, source = EXCLUDED.source, fetched_at = EXCLUDED.fetched_at
            """,
            [
                (b.ticker, b.trade_date, b.open, b.high, b.low, b.close, b.volume, b.value, b.source, b.fetched_at)
                for b in bars
            ],
        )
    return len(bars)


def upsert_price_adjustments(conn: psycopg.Connection, adjustments: list[PriceAdjustment]) -> int:
    if not adjustments:
        return 0
    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO price_adjustments (ticker, ex_date, kind, factor)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (ticker, ex_date, kind) DO UPDATE SET factor = EXCLUDED.factor
            """,
            [(a.ticker, a.ex_date, a.kind, a.factor) for a in adjustments],
        )
    return len(adjustments)


def upsert_foreign_flow(conn: psycopg.Connection, records: list[ForeignFlowRecord]) -> int:
    if not records:
        return 0
    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO foreign_flow_daily (ticker, trade_date, buy_value, sell_value, net_value, room_left, source, fetched_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (ticker, trade_date) DO UPDATE SET
              buy_value = EXCLUDED.buy_value, sell_value = EXCLUDED.sell_value, net_value = EXCLUDED.net_value,
              room_left = EXCLUDED.room_left, source = EXCLUDED.source, fetched_at = EXCLUDED.fetched_at
            """,
            [
                (r.ticker, r.trade_date, r.buy_value, r.sell_value, r.net_value, r.room_left, r.source, r.fetched_at)
                for r in records
            ],
        )
    return len(records)


def upsert_fundamentals(conn: psycopg.Connection, records: list[FundamentalRecord]) -> int:
    if not records:
        return 0
    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO fundamentals_quarterly (ticker, period, report_type, version, metrics, published_date, source, fetched_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (ticker, period, report_type, version) DO UPDATE SET
              metrics = EXCLUDED.metrics, published_date = EXCLUDED.published_date,
              source = EXCLUDED.source, fetched_at = EXCLUDED.fetched_at
            """,
            [
                (
                    r.ticker, r.period, r.report_type, r.version, json.dumps(r.metrics),
                    r.published_date, r.source, r.fetched_at,
                )
                for r in records
            ],
        )
    return len(records)


def upsert_corporate_events(conn: psycopg.Connection, events: list[CorporateEvent]) -> int:
    if not events:
        return 0
    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO corporate_events (ticker, event_type, event_date, payload, source_url, fetched_at)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            [
                (e.ticker, e.event_type, e.event_date, json.dumps(e.payload, default=str), e.source_url, e.fetched_at)
                for e in events
            ],
        )
    return len(events)


def ingest_ticker_day(conn: psycopg.Connection, provider, ticker: str, trade_date: date) -> IngestOutcome:
    try:
        bars = provider.get_ohlcv(ticker, trade_date, trade_date)
    except Exception as exc:  # network/schema error from vnstock — surface, never swallow
        return IngestOutcome(ticker=ticker, status="error", detail=str(exc), rows_written=0)

    if not bars:
        return IngestOutcome(ticker=ticker, status="error", detail="empty response", rows_written=0)

    rows = upsert_prices(conn, bars)
    return IngestOutcome(ticker=ticker, status="ok", detail=None, rows_written=rows)


def ingest_batch(conn: psycopg.Connection, provider, tickers: list[str], trade_date: date) -> list[IngestOutcome]:
    return [ingest_ticker_day(conn, provider, t, trade_date) for t in tickers]


RECENT_DAYS = 10
# Tick-size rounding stays far below this; even a 1% cash dividend re-base exceeds it.
ADJUSTMENT_TOLERANCE = 0.001


def _sync_ticker(conn: psycopg.Connection, provider, ticker: str, today: date) -> IngestOutcome:
    start = today - timedelta(days=RECENT_DAYS)
    # Only bars stored AFTER their own close count as reference: mid-session
    # bars legitimately differ from the final ones.
    final_closes = {
        d: float(c) for d, c in conn.execute(
            """
            SELECT trade_date, close FROM prices_daily
            WHERE ticker = %s AND trade_date >= %s AND close > 0
              AND fetched_at >= (trade_date + time '15:00') AT TIME ZONE 'Asia/Ho_Chi_Minh'
            """,
            (ticker, start),
        ).fetchall()
    }
    try:
        bars = provider.get_ohlcv(ticker, start, today)
    except Exception as exc:
        return IngestOutcome(ticker=ticker, status="error", detail=str(exc), rows_written=0)
    if not bars:
        return IngestOutcome(ticker=ticker, status="error", detail="empty response", rows_written=0)

    # vnstock returns dividend/split-adjusted history, re-based after every ex-date, while
    # we only ever append single days. A closed bar that no longer matches => the stored
    # history is on the old basis (fake gap at the ex-date): reload it whole.
    detail = None
    if any(b.trade_date in final_closes and abs(b.close / final_closes[b.trade_date] - 1) > ADJUSTMENT_TOLERANCE
           for b in bars):
        first = conn.execute("SELECT min(trade_date) FROM prices_daily WHERE ticker = %s", (ticker,)).fetchone()[0]
        try:
            bars = provider.get_ohlcv(ticker, first, today)
        except Exception as exc:
            return IngestOutcome(ticker=ticker, status="error", detail=f"rebase reload failed: {exc}", rows_written=0)
        detail = f"rebased: history reloaded from {first}"
    rows = upsert_prices(conn, bars)

    # The KBS board has no history, only "now": taken after the close it is the final
    # figure and overwrites the mid-session snapshot stored for today.
    try:
        upsert_foreign_flow(conn, provider.get_foreign_flow(ticker, start, today))
    except Exception as exc:
        return IngestOutcome(ticker=ticker, status="error", detail=f"foreign flow: {exc}", rows_written=rows)
    return IngestOutcome(ticker=ticker, status="ok", detail=detail, rows_written=rows)


def sync_recent_prices(conn: psycopg.Connection, provider, today: date) -> list[IngestOutcome]:
    """After-close sync for every ticker with a recent bar: final OHLCV and foreign
    flow, plus detection/repair of vendor price re-basing (see _sync_ticker)."""
    tickers = [r[0] for r in conn.execute(
        "SELECT DISTINCT ticker FROM prices_daily WHERE trade_date >= %s ORDER BY ticker",
        (today - timedelta(days=RECENT_DAYS),),
    ).fetchall()]
    return [_sync_ticker(conn, provider, t, today) for t in tickers]


def assert_batch_ok(outcomes: list[IngestOutcome]) -> None:
    failed = [o for o in outcomes if o.status == "error"]
    if failed:
        detail = ", ".join(f"{o.ticker}: {o.detail}" for o in failed)
        raise IngestBatchError(f"{len(failed)} ticker(s) failed to ingest: {detail}")


FUNDAMENTALS_MAX_AGE = timedelta(hours=24)


def ingest_fundamentals_and_flow(conn: psycopg.Connection, provider, ticker: str, quarters: int = 8) -> IngestOutcome:
    """Refresh fundamentals + foreign flow for one ticker (called once per
    on-demand run, not per trading day — fundamentals only change quarterly).
    Kept separate from ingest_ticker_day/ingest_batch (OHLCV, daily,
    batch-checked for review focus #1) since the failure-handling shape
    differs: a ticker with no fundamentals yet is common for a first-time
    non-VN30 lookup and should not abort the whole batch."""
    # Independent sources (VCI fundamentals vs KBS board): a flaky VCI timeout
    # must not also drop today's foreign flow, so each gets its own try.
    fundamentals: list = []
    errors: list[str] = []
    # Quarterly figures: re-fetching on every run only buys VCI timeouts (30 s x retries per
    # ticker) for data that cannot have changed. Refresh at most once per FUNDAMENTALS_MAX_AGE.
    fresh = conn.execute(
        "SELECT count(*) FROM fundamentals_quarterly WHERE ticker = %s AND fetched_at > now() - %s",
        (ticker, FUNDAMENTALS_MAX_AGE),
    ).fetchone()[0]
    if not fresh:
        try:
            fundamentals = provider.get_fundamentals(ticker, quarters)
            upsert_fundamentals(conn, fundamentals)
        except Exception as exc:
            errors.append(str(exc))
    try:
        end = date.today()
        flow = provider.get_foreign_flow(ticker, end - timedelta(days=5), end)
        upsert_foreign_flow(conn, flow)
    except Exception as exc:
        errors.append(str(exc))
    if errors:
        return IngestOutcome(ticker=ticker, status="error", detail="; ".join(errors), rows_written=0)
    return IngestOutcome(ticker=ticker, status="ok", detail=None, rows_written=len(fundamentals))


_news_pause_until = 0.0
NEWS_PAUSE_AFTER_FAILURE_S = 600.0


def ingest_news(conn: psycopg.Connection, provider, ticker: str, start: date, end: date) -> int:
    """Store raw headlines (no LLM) so the chat model can read them. Best effort: a flaky news
    API, or an item whose month has no partition yet, never fails the analysis run."""
    import hashlib

    global _news_pause_until
    if time.monotonic() < _news_pause_until:
        return 0
    try:
        items = provider.get_news(ticker, start, end)
    except Exception:
        # iq.vietcap.com.vn times out in 30 s x retries when saturated; stop hammering it for a while
        # instead of paying that per ticker for the rest of the batch.
        _news_pause_until = time.monotonic() + NEWS_PAUSE_AFTER_FAILURE_S
        return 0
    stored = 0
    for it in items:
        key = it.url or f"{it.title}|{it.published_at.date()}"
        try:
            with conn.transaction():  # savepoint: one bad row must not poison the run's transaction
                row = conn.execute(
                    """
                    INSERT INTO news_items (published_at, tickers, source, url, url_hash, title, summary, fetched_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (url_hash, published_at) DO UPDATE
                      SET tickers = (SELECT ARRAY(SELECT DISTINCT unnest(news_items.tickers || EXCLUDED.tickers)))
                    RETURNING 1
                    """,
                    (it.published_at, [ticker], it.source, it.url, hashlib.sha256(key.encode()).hexdigest(),
                     it.title, it.summary, it.fetched_at),
                ).fetchone()
            stored += 1 if row else 0
        except psycopg.Error:
            continue
    return stored
