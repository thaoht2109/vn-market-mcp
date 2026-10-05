"""One-off backfill of price history + fundamentals for all VN30 tickers.

coverage_check (pipeline/coverage.py) requires >=500 price days and >=4
fundamental quarters before run_analysis will emit a prediction — a fresh
seed only has calendar/ticker rows, so every run_analysis call returns
insufficient_coverage until this has run once.

Run inside the Hermes venv (has vnstock installed), pointed at the real DB:

    /opt/data/vn-market-mcp-venv/bin/python ops/backfill_prices.py
"""

from __future__ import annotations

import os
import time
from datetime import date, timedelta

import psycopg

from pipeline.ingest import ingest_fundamentals_and_flow, upsert_prices
from providers.vnstock_provider import VNStockProvider

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://vnmcp_admin:changeme_local_only@vn-market-mcp-postgres-1:5433/vnmcp"
)

# 500 trading days is ~2 calendar years; pull 3 for margin (holidays, gaps).
BACKFILL_START = date.today() - timedelta(days=3 * 365)
BACKFILL_END = date.today()


def _already_covered(conn: psycopg.Connection, ticker: str) -> bool:
    price_days = conn.execute("SELECT count(*) FROM prices_daily WHERE ticker = %s", (ticker,)).fetchone()[0]
    quarters = conn.execute(
        "SELECT count(DISTINCT period) FROM fundamentals_quarterly WHERE ticker = %s", (ticker,)
    ).fetchone()[0]
    return price_days >= 500 and quarters >= 4


def main() -> None:
    provider = VNStockProvider(source="VCI")
    with psycopg.connect(DATABASE_URL) as conn:
        tickers = [r[0] for r in conn.execute("SELECT ticker FROM tickers ORDER BY ticker").fetchall()]
        i = 0
        while i < len(tickers):
            ticker = tickers[i]
            if _already_covered(conn, ticker):
                print(f"  {ticker}: already covered, skipping")
                i += 1
                continue
            try:
                bars = provider.get_ohlcv(ticker, BACKFILL_START, BACKFILL_END)
                n_prices = upsert_prices(conn, bars)
                conn.commit()

                fund_outcome = ingest_fundamentals_and_flow(conn, provider, ticker, quarters=8)
                conn.commit()

                print(f"  {ticker}: {n_prices} price rows, fundamentals={fund_outcome.status}")
            except BaseException as exc:  # rate-limit exits raise SystemExit, not Exception
                conn.rollback()
                if isinstance(exc, SystemExit):
                    print(f"  {ticker}: rate limit hit — sleeping 65s, will retry")
                    time.sleep(65)
                    continue  # retry same ticker, don't advance i
                print(f"  {ticker}: FAILED — {exc}")
            i += 1
            time.sleep(10)  # Guest tier caps at 20 req/min; leave margin over the 6s floor


if __name__ == "__main__":
    main()
