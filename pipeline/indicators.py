from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import pandas as pd

MIN_BARS = 50


class InsufficientHistoryError(Exception):
    pass


@dataclass
class TechnicalSnapshot:
    trend: Literal["up", "down", "sideways"]
    ma20: float
    ma50: float
    ma200: float | None
    rsi14: float
    macd: float
    macd_signal: float
    bollinger_upper: float
    bollinger_lower: float
    atr14: float
    support: list[float]
    resistance: list[float]
    volume_avg20: float
    volume_anomaly: bool


def _rsi(close: pd.Series, period: int = 14) -> float:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(period).mean()
    avg_loss = loss.rolling(period).mean()
    rs = avg_gain / avg_loss.replace(0, float("nan"))
    rsi = 100 - (100 / (1 + rs))
    value = rsi.iloc[-1]
    return float(value) if not pd.isna(value) else 50.0


def _atr(df: pd.DataFrame, period: int = 14) -> float:
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)
    true_range = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    return float(true_range.rolling(period).mean().iloc[-1])


def technical_snapshot(df: pd.DataFrame) -> TechnicalSnapshot:
    if len(df) < MIN_BARS:
        raise InsufficientHistoryError(f"need at least {MIN_BARS} bars, got {len(df)}")

    df = df.sort_values("trade_date").reset_index(drop=True)
    close, high, low, volume = df["close"], df["high"], df["low"], df["volume"]

    ma20 = float(close.rolling(20).mean().iloc[-1])
    ma50 = float(close.rolling(50).mean().iloc[-1])
    ma200 = float(close.rolling(200).mean().iloc[-1]) if len(df) >= 200 else None

    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd_line = ema12 - ema26
    macd_signal_line = macd_line.ewm(span=9, adjust=False).mean()

    # ponytail: naive trailing min/max as support/resistance, not a real
    # swing-pivot detector. Upgrade if backtests show it's too noisy.
    window = min(60, len(df))
    support = [float(low.tail(window).min())]
    resistance = [float(high.tail(window).max())]

    std20 = float(close.rolling(20).std().iloc[-1])
    volume_avg20 = float(volume.rolling(20).mean().iloc[-1])
    last_volume = float(volume.iloc[-1])

    last_close = float(close.iloc[-1])
    trend_ref = ma200 if ma200 is not None else ma50
    if last_close > trend_ref * 1.01:
        trend = "up"
    elif last_close < trend_ref * 0.99:
        trend = "down"
    else:
        trend = "sideways"

    return TechnicalSnapshot(
        trend=trend,
        ma20=ma20,
        ma50=ma50,
        ma200=ma200,
        rsi14=_rsi(close),
        macd=float(macd_line.iloc[-1]),
        macd_signal=float(macd_signal_line.iloc[-1]),
        bollinger_upper=ma20 + 2 * std20,
        bollinger_lower=ma20 - 2 * std20,
        atr14=_atr(df),
        support=support,
        resistance=resistance,
        volume_avg20=volume_avg20,
        volume_anomaly=last_volume > 2 * volume_avg20,
    )
