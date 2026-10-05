"""Chấm điểm dự báo theo mốc phiên (spec §10).

Tất định, không LLM: chạm cắt lỗ trước thì thoát ở cắt lỗ, chạm mục tiêu thì
thoát ở mục tiêu, không thì thoát ở cuối mốc. Nhãn không có lệnh (watch,
stay_out) không có entry/stop/target nên chấm bằng lợi suất vượt trội so
với VN30 trong cùng mốc.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal

import psycopg

from pipeline.calendar import NoCalendarDataError, trading_day_offset

ORDERED_LABELS = {"buy_accumulate", "hold", "reduce_exit"}


@dataclass
class GradingConfig:
    horizons_days: list[int]
    fill_window_days: int
    buy_fee_pct: float
    sell_fee_pct: float
    sell_tax_pct: float

    @classmethod
    def from_rules(cls, rules: dict) -> "GradingConfig":
        g = rules["grading"]
        return cls(
            horizons_days=g["horizons_days"],
            fill_window_days=g["fill_window_days"],
            buy_fee_pct=g["buy_fee_pct"],
            sell_fee_pct=g["sell_fee_pct"],
            sell_tax_pct=g["sell_tax_pct"],
        )


def _adjustment_factor(conn: psycopg.Connection, ticker: str, start: date, end: date) -> Decimal:
    """Cumulative price_adjustments factor for ex_dates in (start, end] (spec §10.3).
    Postgres has no builtin product() aggregate, so multiply in Python."""
    factors = conn.execute(
        "SELECT factor FROM price_adjustments WHERE ticker = %s AND ex_date > %s AND ex_date <= %s",
        (ticker, start, end),
    ).fetchall()
    result = Decimal(1)
    for (f,) in factors:
        result *= f
    return result


def _price_on(conn: psycopg.Connection, ticker: str, d: date) -> Decimal | None:
    row = conn.execute(
        "SELECT open, close FROM prices_daily WHERE ticker = %s AND trade_date = %s", (ticker, d)
    ).fetchone()
    return row


def _vn30_return(conn: psycopg.Connection, entry_date: date, exit_date: date) -> Decimal | None:
    entry = conn.execute(
        "SELECT close FROM prices_daily WHERE ticker = 'VN30' AND trade_date = %s", (entry_date,)
    ).fetchone()
    exit_ = conn.execute(
        "SELECT close FROM prices_daily WHERE ticker = 'VN30' AND trade_date = %s", (exit_date,)
    ).fetchone()
    if entry is None or exit_ is None:
        return None
    return (exit_[0] - entry[0]) / entry[0]


@dataclass
class GradingResult:
    filled: bool
    entry_price: Decimal | None
    exit_reason: str
    ret: Decimal | None
    excess_vs_vn30: Decimal | None
    thesis_status: str | None


def grade_prediction(
    conn: psycopg.Connection, prediction: dict, horizon_days: int, cfg: GradingConfig
) -> GradingResult | None:
    """Returns None if not yet gradeable (horizon_days sessions haven't
    elapsed since created_at)."""
    created_date = prediction["created_at"].date()
    try:
        horizon_date = trading_day_offset(conn, created_date, horizon_days)
        entry_signal_date = trading_day_offset(conn, created_date, 1)
    except NoCalendarDataError:
        return None
    if horizon_date is None or entry_signal_date is None:
        return None

    thesis_status = None
    if prediction["thesis_id"] is not None:
        row = conn.execute("SELECT status FROM theses WHERE id = %s", (prediction["thesis_id"],)).fetchone()
        thesis_status = row[0] if row else None

    ticker = prediction["ticker"]
    label = prediction["action_label"]

    if label not in ORDERED_LABELS:
        # watch / stay_out: no order, grade by excess return only.
        entry_bar = _price_on(conn, ticker, entry_signal_date)
        exit_bar = _price_on(conn, ticker, horizon_date)
        if entry_bar is None or exit_bar is None:
            return None
        raw_ret = (exit_bar[1] - entry_bar[0]) / entry_bar[0]  # entry open -> exit close
        vn30_ret = _vn30_return(conn, entry_signal_date, horizon_date)
        excess = raw_ret - vn30_ret if vn30_ret is not None else None
        return GradingResult(
            filled=True,
            entry_price=None,
            exit_reason="horizon",
            ret=raw_ret,
            excess_vs_vn30=excess,
            thesis_status=thesis_status,
        )

    entry_zone = prediction["entry_zone"]
    stop_loss = prediction["stop_loss"]
    target = prediction["target"]

    fill_deadline = trading_day_offset(conn, created_date, cfg.fill_window_days)
    if fill_deadline is None:
        fill_deadline = horizon_date  # window extends past horizon: cap at horizon

    rows = conn.execute(
        """
        SELECT trade_date, open, high, low, close FROM prices_daily
        WHERE ticker = %s AND trade_date > %s AND trade_date <= %s
        ORDER BY trade_date ASC
        """,
        (ticker, created_date, min(fill_deadline, horizon_date)),
    ).fetchall()
    if not rows:
        return None

    entry_price = None
    entry_date = None
    if entry_zone is None:
        entry_price, entry_date = rows[0][1], rows[0][0]  # first session's open
    else:
        lo, hi = entry_zone.lower, entry_zone.upper
        for trade_date, o, h, l, c in rows:
            day_lo = lo if lo is not None else l
            day_hi = hi if hi is not None else h
            if l <= day_hi and h >= day_lo:  # day's [low, high] overlaps entry_zone
                entry_price = min(max(o, day_lo), day_hi)  # clamp open into the zone
                entry_date = trade_date
                break

    if entry_price is None:
        return GradingResult(
            filled=False, entry_price=None, exit_reason="not_filled",
            ret=None, excess_vs_vn30=None, thesis_status=thesis_status,
        )

    exit_rows = conn.execute(
        """
        SELECT trade_date, open, high, low, close FROM prices_daily
        WHERE ticker = %s AND trade_date > %s AND trade_date <= %s
        ORDER BY trade_date ASC
        """,
        (ticker, entry_date, horizon_date),
    ).fetchall()

    exit_price = None
    exit_reason = "horizon"
    for trade_date, o, h, l, c in exit_rows:
        if stop_loss is not None and l <= stop_loss:
            exit_price, exit_reason = stop_loss, "stop"
            break
        if target is not None and h >= target:
            exit_price, exit_reason = target, "target"
            break
    if exit_price is None:
        if not exit_rows:
            exit_price, exit_reason = entry_price, "horizon"
        else:
            exit_price, exit_reason = exit_rows[-1][4], "horizon"

    adj_factor = _adjustment_factor(conn, ticker, entry_date, horizon_date)
    gross_ret = (exit_price * adj_factor - entry_price) / entry_price
    net_ret = gross_ret - Decimal(cfg.buy_fee_pct) - Decimal(cfg.sell_fee_pct) - Decimal(cfg.sell_tax_pct)

    vn30_ret = _vn30_return(conn, entry_date, horizon_date)
    excess = net_ret - vn30_ret if vn30_ret is not None else None

    return GradingResult(
        filled=True, entry_price=entry_price, exit_reason=exit_reason,
        ret=net_ret, excess_vs_vn30=excess, thesis_status=thesis_status,
    )


def grade_due_predictions(conn: psycopg.Connection, cfg: GradingConfig) -> int:
    """Scan open/closed predictions for horizons that are now due and not yet
    graded, grade them, and write prediction_outcomes. Returns count graded."""
    predictions = conn.execute(
        """
        SELECT id, ticker, action_label, thesis_id, entry_zone, stop_loss, target, created_at
        FROM predictions
        """
    ).fetchall()
    graded = 0
    for row in predictions:
        prediction = {
            "id": row[0], "ticker": row[1], "action_label": row[2], "thesis_id": row[3],
            "entry_zone": row[4], "stop_loss": row[5], "target": row[6], "created_at": row[7],
        }
        for horizon_days in cfg.horizons_days:
            already = conn.execute(
                "SELECT 1 FROM prediction_outcomes WHERE prediction_id = %s AND horizon_days = %s",
                (prediction["id"], horizon_days),
            ).fetchone()
            if already:
                continue
            result = grade_prediction(conn, prediction, horizon_days, cfg)
            if result is None:
                continue
            conn.execute(
                """
                INSERT INTO prediction_outcomes
                  (prediction_id, horizon_days, graded_at, filled, entry_price, exit_reason, ret, excess_vs_vn30, thesis_status)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    prediction["id"], horizon_days, datetime.now(timezone.utc),
                    result.filled, result.entry_price, result.exit_reason,
                    result.ret, result.excess_vs_vn30, result.thesis_status,
                ),
            )
            graded += 1
    conn.commit()
    return graded
