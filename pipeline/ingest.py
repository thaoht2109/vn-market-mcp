from __future__ import annotations

import json
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


def assert_batch_ok(outcomes: list[IngestOutcome]) -> None:
    failed = [o for o in outcomes if o.status == "error"]
    if failed:
        detail = ", ".join(f"{o.ticker}: {o.detail}" for o in failed)
        raise IngestBatchError(f"{len(failed)} ticker(s) failed to ingest: {detail}")


def ingest_fundamentals_and_flow(conn: psycopg.Connection, provider, ticker: str, quarters: int = 8) -> IngestOutcome:
    """Refresh fundamentals + foreign flow for one ticker (called once per
    on-demand run, not per trading day — fundamentals only change quarterly).
    Kept separate from ingest_ticker_day/ingest_batch (OHLCV, daily,
    batch-checked for review focus #1) since the failure-handling shape
    differs: a ticker with no fundamentals yet is common for a first-time
    non-VN30 lookup and should not abort the whole batch."""
    try:
        fundamentals = provider.get_fundamentals(ticker, quarters)
        upsert_fundamentals(conn, fundamentals)
        end = date.today()
        flow = provider.get_foreign_flow(ticker, end - timedelta(days=5), end)
        upsert_foreign_flow(conn, flow)
    except Exception as exc:
        return IngestOutcome(ticker=ticker, status="error", detail=str(exc), rows_written=0)
    return IngestOutcome(ticker=ticker, status="ok", detail=None, rows_written=len(fundamentals))
