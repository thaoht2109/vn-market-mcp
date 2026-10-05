"""One-off seed of tickers/index_membership/trading_calendar from real vnstock data.

Run inside the Hermes venv (has vnstock installed), pointed at the real DB:

    /opt/data/vn-market-mcp-venv/bin/python ops/seed_market_data.py

ponytail: no CLI flags, no config — this is a run-once bootstrap script, not a
recurring job. Re-running is safe (all upserts), but there's no scheduled
re-seed; VN30 membership drifts, re-run manually when it does.
"""

from __future__ import annotations

import os
from datetime import date, timedelta

import psycopg
from vnstock.api.company import Company
from vnstock.api.listing import Listing
from vnstock.api.quote import Quote

from pipeline.calendar import seed_calendar_from_weekdays

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://vnmcp_admin:changeme_local_only@vn-market-mcp-postgres-1:5432/vnmcp"
)

# Liquid large-caps that have traded every session since 2020 — used only to
# discover which weekdays were real holidays (union of their trading days).
CALENDAR_PROBE_TICKERS = ["VNM", "FPT", "VIC"]
CALENDAR_START = date(2020, 1, 1)
CALENDAR_END = date(2026, 12, 31)


def seed_tickers_and_vn30(conn: psycopg.Connection) -> None:
    listing = Listing()
    vn30 = listing.symbols_by_group("VN30").tolist()
    exch_df = listing.symbols_by_exchange()
    exch_map = dict(zip(exch_df["symbol"], exch_df["exchange"]))

    today = date.today()
    with conn.cursor() as cur:
        for ticker in vn30:
            overview = Company(source="VCI", symbol=ticker).overview()
            row = overview.iloc[0]
            listing_date = row.get("listing_date")
            listed = str(listing_date)[:10] if listing_date else None
            cur.execute(
                """
                INSERT INTO tickers (ticker, name, exchange, sector, industry_group, listed_date)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (ticker) DO UPDATE
                  SET name = EXCLUDED.name, exchange = EXCLUDED.exchange,
                      sector = EXCLUDED.sector, listed_date = EXCLUDED.listed_date
                """,
                (ticker, row.get("organ_short_name") or row.get("organ_name"),
                 exch_map.get(ticker, "HOSE"), row.get("sector"), row.get("sector"), listed),
            )
            cur.execute(
                """
                INSERT INTO index_membership (index_code, ticker, valid_from, valid_to)
                VALUES ('VN30', %s, %s, NULL)
                ON CONFLICT (index_code, ticker, valid_from) DO NOTHING
                """,
                (ticker, today),
            )
            print(f"  seeded ticker {ticker}")
    conn.commit()
    print(f"tickers + VN30 membership seeded: {len(vn30)} symbols")


def seed_trading_calendar(conn: psycopg.Connection) -> None:
    trading_days: set[date] = set()
    for ticker in CALENDAR_PROBE_TICKERS:
        df = Quote(symbol=ticker, source="VCI").history(
            symbol=ticker, start=CALENDAR_START.isoformat(), end=CALENDAR_END.isoformat()
        )
        for raw in df["time"]:
            d = raw.date() if hasattr(raw, "date") else date.fromisoformat(str(raw)[:10])
            trading_days.add(d)

    holidays: set[date] = set()
    d = CALENDAR_START
    while d <= CALENDAR_END:
        if d.weekday() < 5 and d not in trading_days and d <= date.today():
            holidays.add(d)
        d += timedelta(days=1)

    n = seed_calendar_from_weekdays(conn, CALENDAR_START, CALENDAR_END, holidays)
    conn.commit()
    print(f"trading_calendar seeded: {n} rows, {len(holidays)} holidays inferred")


def main() -> None:
    with psycopg.connect(DATABASE_URL) as conn:
        seed_tickers_and_vn30(conn)
        seed_trading_calendar(conn)


if __name__ == "__main__":
    main()
