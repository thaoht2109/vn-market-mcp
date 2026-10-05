"""Market regime gate (spec §5.7.3 / §5.6).

Code computes the regime label from VN-Index vs its MA200 — the LLM (macro
role) only narrates it and may propose a *downgrade*, never sets regime
itself. action_label() gates buy_accumulate on regime != "risk_off".
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Literal
from zoneinfo import ZoneInfo

from pipeline.indicators import InsufficientHistoryError, technical_snapshot

MARKET_INDEX_SYMBOL = "VNINDEX"
_VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")

# technical_snapshot needs >=200 bars for ma200; fetch generously past
# weekends/holidays so a short network hiccup doesn't starve the window.
LOOKBACK_DAYS = 400

# Reuse a stored VN-Index row this long before refetching (cron refreshes every 2 h).
MAX_AGE = timedelta(minutes=60)


def market_snapshot(provider, as_of: date) -> dict:
    """VN-Index state + regime. Regime fails open to risk_on (never blocks
    buy_accumulate) when there isn't enough index history, with the numeric
    fields left None so readers can tell "no data" from "calm market"."""
    df = provider.get_market_index(MARKET_INDEX_SYMBOL, as_of - timedelta(days=LOOKBACK_DAYS), as_of)
    out = {"index_symbol": MARKET_INDEX_SYMBOL, "close": None, "change_pct": None, "ma20": None,
           "ma50": None, "ma200": None, "rsi14": None, "trend": None, "regime": "risk_on"}
    try:
        tech = technical_snapshot(df)
    except InsufficientHistoryError:
        return out
    closes = df.sort_values("trade_date")["close"].astype(float).tolist()
    out.update(
        close=closes[-1], ma20=tech.ma20, ma50=tech.ma50, ma200=tech.ma200, rsi14=tech.rsi14, trend=tech.trend,
        change_pct=(closes[-1] / closes[-2] - 1) * 100 if len(closes) > 1 else None,
    )
    if tech.ma200 is not None and tech.trend == "down":
        out["regime"] = "risk_off"
    return out


def get_market_regime(provider, as_of: date) -> Literal["risk_on", "risk_off"]:
    """risk_off when VNINDEX's close sits below its 200-day MA, else risk_on."""
    return market_snapshot(provider, as_of)["regime"]


_VN30 = "SELECT ticker FROM index_membership WHERE index_code = 'VN30' AND (valid_to IS NULL OR valid_to >= CURRENT_DATE)"


def market_breadth(conn, as_of: date, now: datetime) -> dict:
    """Advancers/decliners, share above MA50 and liquidity vs the 20-session mean, over VN30."""
    adv, dec, above = conn.execute(
        f"""
        WITH t AS (
          SELECT trade_date, close, lag(close) OVER w AS prev_close,
                 avg(close) OVER (w ROWS BETWEEN 49 PRECEDING AND CURRENT ROW) AS ma50,
                 count(*) OVER (w ROWS BETWEEN 49 PRECEDING AND CURRENT ROW) AS n50
          FROM prices_daily
          WHERE ticker IN ({_VN30}) AND trade_date BETWEEN %s AND %s
          WINDOW w AS (PARTITION BY ticker ORDER BY trade_date)
        )
        SELECT count(*) FILTER (WHERE close > prev_close), count(*) FILTER (WHERE close < prev_close),
               count(*) FILTER (WHERE n50 = 50 AND close > ma50)::numeric / nullif(count(*) FILTER (WHERE n50 = 50), 0)
        FROM t WHERE trade_date = %s
        """,
        (as_of - timedelta(days=120), as_of, as_of),
    ).fetchone()

    liquidity_ratio = None
    now_vn = now.astimezone(_VN_TZ)
    if now_vn.date() > as_of or now_vn.time() >= time(15, 0):  # today's value is final only after the close
        totals = [float(r[0]) for r in conn.execute(
            f"SELECT sum(value) FROM prices_daily WHERE ticker IN ({_VN30}) AND trade_date <= %s"
            " GROUP BY trade_date ORDER BY trade_date DESC LIMIT 21", (as_of,),
        ).fetchall() if r[0] is not None]
        if len(totals) == 21 and sum(totals[1:]):
            liquidity_ratio = totals[0] / (sum(totals[1:]) / 20)
    return {"advancers": adv, "decliners": dec,
            "pct_above_ma50": float(above) if above is not None else None, "liquidity_ratio": liquidity_ratio}


def get_market_context(conn, provider, as_of: date, now: datetime, max_age: timedelta = MAX_AGE) -> dict:
    """market_snapshot via the market_regime_daily table: reuse today's row if it
    is younger than max_age, else refetch VN-Index and upsert. One vnstock call per
    window instead of one per analysed ticker."""
    row = conn.execute(
        "SELECT index_symbol, close, change_pct, ma20, ma50, ma200, rsi14, trend, regime, computed_at,"
        " advancers, decliners, pct_above_ma50, liquidity_ratio FROM market_regime_daily WHERE trade_date = %s", (as_of,),
    ).fetchone()
    if row is not None and now - row[9] <= max_age:
        keys = ("index_symbol", "close", "change_pct", "ma20", "ma50", "ma200", "rsi14", "trend", "regime",
                "computed_at", "advancers", "decliners", "pct_above_ma50", "liquidity_ratio")
        ctx = {k: (float(v) if isinstance(v, Decimal) else v) for k, v in zip(keys, row) if k != "computed_at"}
    else:
        ctx = {**market_snapshot(provider, as_of), **market_breadth(conn, as_of, now)}
        conn.execute(
            """
            INSERT INTO market_regime_daily
              (trade_date, index_symbol, close, change_pct, ma20, ma50, ma200, rsi14, trend, regime, computed_at,
               advancers, decliners, pct_above_ma50, liquidity_ratio)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (trade_date) DO UPDATE SET
              close = EXCLUDED.close, change_pct = EXCLUDED.change_pct, ma20 = EXCLUDED.ma20,
              ma50 = EXCLUDED.ma50, ma200 = EXCLUDED.ma200, rsi14 = EXCLUDED.rsi14,
              trend = EXCLUDED.trend, regime = EXCLUDED.regime, computed_at = EXCLUDED.computed_at,
              advancers = EXCLUDED.advancers, decliners = EXCLUDED.decliners,
              pct_above_ma50 = EXCLUDED.pct_above_ma50, liquidity_ratio = EXCLUDED.liquidity_ratio
            """,
            (as_of, ctx["index_symbol"], ctx["close"], ctx["change_pct"], ctx["ma20"], ctx["ma50"],
             ctx["ma200"], ctx["rsi14"], ctx["trend"], ctx["regime"], now,
             ctx["advancers"], ctx["decliners"], ctx["pct_above_ma50"], ctx["liquidity_ratio"]),
        )
    return {**ctx, "as_of": as_of.isoformat()}
