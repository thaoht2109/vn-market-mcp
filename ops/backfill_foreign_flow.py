"""Re-load foreign_flow_daily from CafeF's settled history, replacing KBS board snapshots.

KBS rows taken out of hours were wrong (0/0 before the open, sell volumes above the whole session's
traded volume). Dry run by default: prints every row that would change by more than 2 bn VND and 5%.

    PIPELINE_RW_DATABASE_URL=... python -m ops.backfill_foreign_flow            # compare only
    PIPELINE_RW_DATABASE_URL=... python -m ops.backfill_foreign_flow --apply    # write
    ... --since 2026-09-30 --tickers MCH HDB                                    # narrow it
"""
from __future__ import annotations

import argparse
import os
from datetime import date, timedelta

import psycopg

from pipeline.ingest import upsert_foreign_flow
from providers.cafef import get_foreign_flow_history

MIN_DIFF_VND = 2e9
MIN_DIFF_REL = 0.05


def _differs(old: float, new: float) -> bool:
    diff = abs(old - new)
    return diff > MIN_DIFF_VND and diff / max(abs(new), MIN_DIFF_VND) > MIN_DIFF_REL


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="write the CafeF rows (default: compare only)")
    ap.add_argument("--since", type=date.fromisoformat, help="first trade date (default: earliest stored)")
    ap.add_argument("--tickers", nargs="*", help="default: every ticker with stored flow")
    args = ap.parse_args()

    with psycopg.connect(os.environ["PIPELINE_RW_DATABASE_URL"]) as conn:
        since = args.since or conn.execute("SELECT min(trade_date) FROM foreign_flow_daily").fetchone()[0] or (date.today() - timedelta(days=30))
        tickers = args.tickers or [r[0] for r in conn.execute("SELECT DISTINCT ticker FROM foreign_flow_daily ORDER BY 1")]
        today = date.today()
        changed = written = failed = 0
        for t in tickers:
            try:
                records = get_foreign_flow_history(t, since, today)
            except Exception as exc:
                failed += 1
                print(f"  {t}: FAILED {exc}")
                continue
            old = {d: (float(b or 0), float(s or 0), float(n or 0), src) for d, b, s, n, src in conn.execute(
                "SELECT trade_date, buy_value, sell_value, net_value, source FROM foreign_flow_daily"
                " WHERE ticker = %s AND trade_date >= %s", (t, since))}
            for r in records:
                prev = old.get(r.trade_date)
                if prev and _differs(prev[2], r.net_value):
                    changed += 1
                    print(f"  {t} {r.trade_date}: net {prev[2] / 1e9:+,.1f} bn ({prev[3]}) -> {r.net_value / 1e9:+,.1f} bn (CAFEF)")
            if args.apply:
                written += upsert_foreign_flow(conn, records)
                conn.commit()
        print(f"{len(tickers)} tickers, {changed} rows differ materially, {failed} failed, "
              + (f"{written} rows written" if args.apply else "dry run, nothing written (use --apply)"))


if __name__ == "__main__":
    main()
