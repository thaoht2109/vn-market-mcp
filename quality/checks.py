from __future__ import annotations

import json
import re
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


def check_price_series_units(trade_dates: list[date], closes: list[float]) -> CheckResult:
    """Unit-scale check over the WHOLE close series, not just the last two.

    One session stored in thousand-VND between correctly-scaled neighbours
    (real 2026-09-28 incident: ACB 21.1 among 21,100s) passes a
    prev-vs-today check but inflates ATR14 ~8x and every ATR-derived level.
    """
    for i in range(1, len(closes)):
        pair = check_price_unit_consistency(closes[i - 1], closes[i])
        if pair.result == "fail":
            pair.detail["trade_date"] = str(trade_dates[i])
            return pair
    return CheckResult("price_unit", "pass", {})


_FUNDAMENTALS_MAX_AGE_DAYS = 200  # a quarter ends, ~45d to publish, +1 missed quarter of slack
_QUARTER = re.compile(r"(\d{4})-?Q([1-4])")


def check_fundamentals_freshness(latest_period: str | None, as_of: date) -> CheckResult:
    """Fail when the newest stored fundamentals quarter is implausibly old.

    Real 2026-10-02 incident: every VN30 ticker was valued on 2018 quarters
    (vnstock kept the 4 OLDEST periods) while 2026-Q2 existed.
    """
    match = _QUARTER.fullmatch(latest_period or "")
    if match is None:
        return CheckResult("fundamentals_freshness", "fail", {"latest_period": latest_period, "reason": "no usable period"})
    year, quarter = int(match.group(1)), int(match.group(2))
    quarter_end = date(year, quarter * 3, 30 if quarter in (2, 3) else 31)
    age_days = (as_of - quarter_end).days
    if age_days > _FUNDAMENTALS_MAX_AGE_DAYS:
        return CheckResult(
            "fundamentals_freshness", "fail",
            {"latest_period": latest_period, "age_days": age_days, "max_age_days": _FUNDAMENTALS_MAX_AGE_DAYS},
        )
    return CheckResult("fundamentals_freshness", "pass", {})


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
