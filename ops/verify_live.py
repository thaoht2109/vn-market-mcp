"""Live checks against the real vnstock API and the production DB — read-only.

Unit tests mock the provider with the code's own assumptions; these checks test the assumptions
themselves (vnstock respects `end`? news time zone?) and the data the jobs actually left behind.
The DB session is READ ONLY (enforced by Postgres), so running this can never change data.

    docker exec vn-market-mcp-pipeline-worker-1 python -m ops.verify_live [--date YYYY-MM-DD] [--ticker FPT]

Prints one line per check (PASS/WARN/FAIL) and exits 1 if any check failed.
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import date, datetime, timedelta, timezone

import psycopg

from ops.scheduler import _VN_TZ, freshness_problems
from pipeline.calendar import is_provisional_session, latest_trading_day
from providers.vnstock_provider import VNStockProvider

results: list[tuple[str, str, str]] = []


def report(name: str, status: str, detail: str = "") -> None:
    results.append((name, status, detail))
    print(f"{status:4}  {name}: {detail}", flush=True)


def check(name: str):
    """Run a check; an exception is a FAIL of that check, not a crash of the whole run."""
    def wrap(fn):
        def run(*args):
            try:
                fn(*args)
            except Exception as exc:
                report(name, "FAIL", f"{type(exc).__name__}: {exc}")
        return run
    return wrap


@check("vnstock.end_respected")
def vnstock_end(provider: VNStockProvider, ticker: str, past_day: date) -> None:
    bars = provider.get_ohlcv(ticker, past_day - timedelta(days=20), past_day)
    if not bars:
        return report("vnstock.end_respected", "FAIL", f"không có bar nào cho {ticker} tới {past_day}")
    last = max(b.trade_date for b in bars)
    status = "PASS" if last <= past_day else "FAIL"
    report("vnstock.end_respected", status, f"end={past_day}, bar cuối={last}")
    # vnstock only ignores an `end` close to today (an end a week back is honoured), so past_day is the previous session.
    raw = provider._quote(ticker).history(symbol=ticker, start=str(past_day - timedelta(days=5)), end=str(past_day))
    leaks = sum(1 for t in raw["time"] if str(t)[:10] > str(past_day)) if len(raw) else 0
    report("vnstock.raw_ignores_end", "WARN" if leaks else "PASS",
           f"thư viện vẫn trả {leaks} bar sau end (bộ lọc trong provider còn cần)" if leaks else "thư viện đã tôn trọng end")


@check("vnstock.news_time")
def vnstock_news(provider: VNStockProvider, ticker: str, today: date) -> None:
    items = provider.get_news(ticker, today - timedelta(days=7), today)
    now = datetime.now(timezone.utc)
    future = [i for i in items if i.published_at > now + timedelta(minutes=5)]
    hours = sorted({i.published_at.astimezone(_VN_TZ).hour for i in items})
    status = "FAIL" if future else "WARN" if not items else "PASS"  # vnstock logs fetch errors and returns []
    report("vnstock.news_time", status, f"{len(items)} tin, {len(future)} tin ở tương lai, giờ VN: {hours}")


@check("db.freshness")
def db_freshness(conn, day: date) -> None:
    problems = freshness_problems(conn, day)
    report("db.freshness", "FAIL" if problems else "PASS", "; ".join(problems) or f"đủ dữ liệu phiên {day}")


@check("db.jobs")
def db_jobs(conn, day: date) -> None:
    rows = conn.execute(
        "SELECT job_type, status, count(*) FROM jobs WHERE created_at >= %s AND created_at < %s"
        " GROUP BY 1, 2 ORDER BY 1, 2",
        (datetime.combine(day, datetime.min.time(), _VN_TZ), datetime.combine(day + timedelta(days=1), datetime.min.time(), _VN_TZ)),
    ).fetchall()
    bad = [f"{t} {s}={n}" for t, s, n in rows if s in ("failed", "expired")]
    report("db.jobs", "FAIL" if bad else "PASS", ", ".join(bad) or f"{sum(n for *_, n in rows)} job, không có failed/expired")


@check("db.news_future")
def db_news_future(conn) -> None:
    rows = conn.execute(
        "SELECT source, count(*) FROM news_items WHERE published_at > now() + interval '5 minutes' GROUP BY 1"
    ).fetchall()
    report("db.news_future", "FAIL" if rows else "PASS", ", ".join(f"{s}={n}" for s, n in rows) or "không có tin ở tương lai")


@check("db.regime_matches_index")
def db_regime(conn, provider: VNStockProvider, day: date) -> None:
    if is_provisional_session(day, datetime.now(timezone.utc)):
        return report("db.regime_matches_index", "WARN", f"phiên {day} chưa đóng cửa, bỏ qua so sánh")
    row = conn.execute("SELECT close FROM market_regime_daily WHERE trade_date = %s", (day,)).fetchone()
    if row is None or row[0] is None:
        return report("db.regime_matches_index", "FAIL", f"không có close VN-Index phiên {day}")
    df = provider.get_market_index("VNINDEX", day - timedelta(days=7), day)
    live = float(df[df["trade_date"].astype(str).str[:10] == str(day)]["close"].iloc[-1])
    diff = abs(float(row[0]) - live) / live
    report("db.regime_matches_index", "PASS" if diff < 0.005 else "FAIL", f"DB={float(row[0])}, vnstock={live}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", type=date.fromisoformat, help="phiên cần kiểm tra (mặc định: phiên gần nhất)")
    parser.add_argument("--ticker", default="FPT")
    args = parser.parse_args()

    provider = VNStockProvider(source="VCI")
    with psycopg.connect(os.environ["PIPELINE_RW_DATABASE_URL"]) as conn:
        conn.read_only = True
        day = args.date or latest_trading_day(conn, datetime.now(timezone.utc))
        past_day = conn.execute(
            "SELECT max(trade_date) FROM trading_calendar WHERE is_trading_day AND trade_date < %s", (day,),
        ).fetchone()[0]
        db_freshness(conn, day)
        db_jobs(conn, day)
        db_news_future(conn)
        db_regime(conn, provider, day)
        conn.rollback()
    vnstock_end(provider, args.ticker, past_day)
    vnstock_news(provider, args.ticker, day)

    failed = [n for n, s, _ in results if s == "FAIL"]
    print(f"\n{len(results)} kiểm tra, {len(failed)} FAIL" + (f": {', '.join(failed)}" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
