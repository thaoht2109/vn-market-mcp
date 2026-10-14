"""Batch ticker screener. One SQL pass over the whole universe computing price/volume/
RSI14/MA20/MA50 + a simple daily-candlestick pattern, then filtering — so any
"find stocks where X" question (bullish pattern, oversold, breakout, volume spike)
is one tool call instead of looping run_analysis per ticker and burning a
delegate round-trip per batch (see scan_bullish_candlesticks's prior incarnation).

RSI14 here mirrors pipeline/indicators.py's simple-rolling-mean RSI, not Wilder's
smoothed version, so values match what run_analysis would report for the same ticker.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from mcp_server.connection import get_ro_conn
from mcp_server.envelope import build_envelope

_VN30 = (
    "SELECT ticker FROM index_membership WHERE index_code = 'VN30'"
    " AND (valid_to IS NULL OR valid_to >= CURRENT_DATE)"
)

# Needs 50 bars for ma50 to be non-null; matches pipeline.indicators.MIN_BARS.
_METRICS_SQL = """
WITH bars AS (
  SELECT ticker, trade_date, open, high, low, close, volume,
         row_number() OVER (PARTITION BY ticker ORDER BY trade_date DESC) rn
  FROM prices_daily
  WHERE {universe}
),
gainloss AS (
  SELECT ticker, trade_date, rn, open, high, low, close, volume,
         GREATEST(close - LAG(close) OVER (PARTITION BY ticker ORDER BY trade_date), 0) AS gain,
         GREATEST(LAG(close) OVER (PARTITION BY ticker ORDER BY trade_date) - close, 0) AS loss
  FROM bars WHERE rn <= 50
),
rolled AS (
  SELECT ticker, trade_date, rn, open, high, low, close, volume,
         AVG(close) OVER (PARTITION BY ticker ORDER BY trade_date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) AS ma20,
         AVG(close) OVER (PARTITION BY ticker ORDER BY trade_date ROWS BETWEEN 49 PRECEDING AND CURRENT ROW) AS ma50_raw,
         COUNT(*) OVER (PARTITION BY ticker) AS bar_count,
         AVG(volume) OVER (PARTITION BY ticker ORDER BY trade_date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) AS volume_avg20,
         AVG(gain) OVER (PARTITION BY ticker ORDER BY trade_date ROWS BETWEEN 13 PRECEDING AND CURRENT ROW) AS avg_gain,
         AVG(loss) OVER (PARTITION BY ticker ORDER BY trade_date ROWS BETWEEN 13 PRECEDING AND CURRENT ROW) AS avg_loss,
         LAG(open) OVER (PARTITION BY ticker ORDER BY trade_date) AS prev_open,
         LAG(close) OVER (PARTITION BY ticker ORDER BY trade_date) AS prev_close
  FROM gainloss
)
SELECT ticker, trade_date, open, high, low, close, volume,
       ma20,
       CASE WHEN bar_count >= 50 THEN ma50_raw END AS ma50,
       volume_avg20,
       CASE WHEN avg_loss = 0 THEN 100
            ELSE 100 - 100 / (1 + avg_gain / NULLIF(avg_loss, 0)) END AS rsi14,
       CASE
         WHEN prev_close < prev_open AND close > open AND close >= prev_open AND open <= prev_close
           THEN 'bullish_engulfing'
         WHEN (high - low) > 0
              AND (close - low) / (high - low) > 0.6
              AND (LEAST(open, close) - low) >= 2 * ABS(close - open)
              AND (high - GREATEST(open, close)) <= 0.3 * (high - low)
           THEN 'hammer'
         WHEN (high - low) > 0 AND ABS(close - open) / (high - low) >= 0.9 AND close > open
           THEN 'bullish_marubozu'
         ELSE NULL
       END AS candle_pattern
FROM rolled
WHERE rn = 1
"""


def screen_tickers_tool(
    tickers: list[str] | None = None,
    candle_pattern: str | None = None,
    rsi_below: float | None = None,
    rsi_above: float | None = None,
    above_ma20: bool | None = None,
    above_ma50: bool | None = None,
    min_volume_ratio: float | None = None,
) -> dict[str, Any]:
    """Quét nhiều mã cùng lúc theo điều kiện kỹ thuật, trong một lượt SQL.

    tickers: danh sách mã, hoặc bỏ trống để quét VN30.
    candle_pattern: 'any' (mọi mẫu nến tăng) | 'bullish_engulfing' | 'hammer' | 'bullish_marubozu'.
    rsi_below / rsi_above: lọc theo RSI14.
    above_ma20 / above_ma50: True = giá đóng cửa trên MA tương ứng.
    min_volume_ratio: khối lượng phiên gần nhất / trung bình 20 phiên tối thiểu.

    Mọi điều kiện truyền vào là AND với nhau. Không truyền gì -> trả toàn bộ universe.
    Mỗi dòng kèm sẵn volume_ratio, pct_vs_ma20, pct_vs_ma50, range_pct (không cần tự tính lại).
    """
    now = datetime.now(timezone.utc)
    if tickers:
        sql, params = _METRICS_SQL.format(universe="ticker = ANY(%s)"), [[t.upper() for t in tickers]]
    else:
        sql, params = _METRICS_SQL.format(universe=f"ticker IN ({_VN30})"), []
    with get_ro_conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    cols = ("ticker", "trade_date", "open", "high", "low", "close", "volume",
            "ma20", "ma50", "volume_avg20", "rsi14", "candle_pattern")
    all_rows = [
        {k: (str(v) if k == "trade_date" else float(v) if isinstance(v, (int, float, Decimal)) else v)
         for k, v in zip(cols, row)}
        for row in rows
    ]
    for r in all_rows:
        r["volume_ratio"] = round(r["volume"] / r["volume_avg20"], 2) if r["volume_avg20"] else None
        r["pct_vs_ma20"] = round((r["close"] / r["ma20"] - 1) * 100, 2) if r["ma20"] else None
        r["pct_vs_ma50"] = round((r["close"] / r["ma50"] - 1) * 100, 2) if r["ma50"] else None
        r["range_pct"] = round((r["high"] - r["low"]) / r["close"] * 100, 2) if r["close"] else None
        for k in ("ma20", "ma50", "volume_avg20", "rsi14"):
            if r[k] is not None:
                r[k] = round(r[k], 2)

    def keep(r: dict) -> bool:
        if candle_pattern == "any":
            if r["candle_pattern"] is None:
                return False
        elif candle_pattern is not None and r["candle_pattern"] != candle_pattern:
            return False
        if rsi_below is not None and (r["rsi14"] is None or r["rsi14"] >= rsi_below):
            return False
        if rsi_above is not None and (r["rsi14"] is None or r["rsi14"] <= rsi_above):
            return False
        if above_ma20 is not None and (r["ma20"] is None or (r["close"] > r["ma20"]) != above_ma20):
            return False
        if above_ma50 is not None and (r["ma50"] is None or (r["close"] > r["ma50"]) != above_ma50):
            return False
        if min_volume_ratio is not None and (r["volume_ratio"] is None or r["volume_ratio"] < min_volume_ratio):
            return False
        return True

    matches = [r for r in all_rows if keep(r)]
    data = {"scanned": len(all_rows), "matches": matches}
    return build_envelope(data, sources=["postgres"], as_of=now)
