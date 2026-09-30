from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Literal

import psycopg

Result = Literal["pass", "warn", "fail"]


@dataclass
class CheckResult:
    name: str
    result: Result
    detail: dict = field(default_factory=dict)


def check_daily_completeness(conn: psycopg.Connection, tickers: list[str], trade_date: date) -> CheckResult:
    rows = conn.execute(
        "SELECT ticker FROM prices_daily WHERE trade_date = %s AND ticker = ANY(%s)",
        (trade_date, tickers),
    ).fetchall()
    present = {r[0] for r in rows}
    missing = sorted(set(tickers) - present)
    if missing:
        return CheckResult("daily_completeness", "fail", {"missing_tickers": missing, "trade_date": str(trade_date)})
    return CheckResult("daily_completeness", "pass", {"trade_date": str(trade_date)})


def check_schema(columns: set[str], expected_columns: set[str]) -> CheckResult:
    missing = expected_columns - columns
    extra = columns - expected_columns
    if missing:
        return CheckResult("schema", "fail", {"missing_columns": sorted(missing), "extra_columns": sorted(extra)})
    return CheckResult("schema", "pass", {"extra_columns": sorted(extra)} if extra else {})


def check_price_unit_consistency(prev_close: float, today_close: float, threshold_ratio: float = 100) -> CheckResult:
    if prev_close <= 0 or today_close <= 0:
        return CheckResult("price_unit", "warn", {"prev_close": prev_close, "today_close": today_close})
    ratio = today_close / prev_close
    if ratio >= threshold_ratio or ratio <= 1 / threshold_ratio:
        return CheckResult(
            "price_unit", "fail", {"prev_close": prev_close, "today_close": today_close, "ratio": ratio}
        )
    return CheckResult("price_unit", "pass", {})


def check_abnormal_move(
    conn: psycopg.Connection,
    ticker: str,
    trade_date: date,
    prev_close: float,
    today_close: float,
    band_pct: float,
) -> CheckResult:
    if prev_close <= 0:
        return CheckResult("abnormal_move", "warn", {"reason": "no prior close"})

    move_pct = abs(today_close - prev_close) / prev_close
    if move_pct <= band_pct:
        return CheckResult("abnormal_move", "pass", {"move_pct": move_pct})

    # A booked corporate action explains a large raw-price move — check
    # before flagging, or every ex-dividend/bonus-share day looks like bad
    # data (§8, §4.8 — review focus #5).
    booked_adjustment = conn.execute(
        "SELECT kind FROM price_adjustments WHERE ticker = %s AND ex_date = %s",
        (ticker, trade_date),
    ).fetchone()
    if booked_adjustment is not None:
        return CheckResult("abnormal_move", "pass", {"move_pct": move_pct, "explained_by": booked_adjustment[0]})

    return CheckResult(
        "abnormal_move", "warn",
        {
            "move_pct": move_pct,
            "band_pct": band_pct,
            "reason": "move exceeds board band with no booked corporate action",
        },
    )


def check_volume(volume: int, is_high_liquidity: bool) -> CheckResult:
    if volume <= 0 and is_high_liquidity:
        return CheckResult("volume", "warn", {"volume": volume})
    return CheckResult("volume", "pass", {"volume": volume})


def check_freshness(as_of: datetime, latest_official_trading_day: date) -> CheckResult:
    if as_of.date() < latest_official_trading_day:
        return CheckResult(
            "freshness", "warn",
            {"as_of": as_of.isoformat(), "latest_official_trading_day": str(latest_official_trading_day)},
        )
    return CheckResult("freshness", "pass", {})


def log_check(conn: psycopg.Connection, run_id: str | None, ticker: str | None, check: CheckResult) -> None:
    conn.execute(
        """
        INSERT INTO data_quality_log (run_id, ticker, check_name, result, detail)
        VALUES (%s, %s, %s, %s, %s)
        """,
        (run_id, ticker, check.name, check.result, json.dumps(check.detail, default=str)),
    )
