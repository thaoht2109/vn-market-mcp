from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from pipeline.indicators import InsufficientHistoryError, technical_snapshot


def _make_df(n=60, trend="up", spike_volume=False):
    dates = [date(2026, 1, 1) + timedelta(days=i) for i in range(n)]
    close = np.linspace(50, 80, n) if trend == "up" else np.linspace(80, 50, n)
    high = close + 1
    low = close - 1
    open_ = close - 0.5
    volume = np.full(n, 1_000_000.0)
    if spike_volume:
        volume[-1] = 5_000_000.0
    return pd.DataFrame(
        {"trade_date": dates, "open": open_, "high": high, "low": low, "close": close, "volume": volume}
    )


def test_technical_snapshot_detects_uptrend():
    snap = technical_snapshot(_make_df(n=60, trend="up"))
    assert snap.trend == "up"
    assert snap.ma200 is None  # fewer than 200 bars supplied
    assert 0 <= snap.rsi14 <= 100
    assert snap.bollinger_upper > snap.bollinger_lower
    assert snap.atr14 > 0
    assert snap.support[0] < snap.resistance[0]


def test_technical_snapshot_detects_downtrend():
    snap = technical_snapshot(_make_df(n=60, trend="down"))
    assert snap.trend == "down"


def test_technical_snapshot_flags_volume_anomaly():
    snap = technical_snapshot(_make_df(n=60, trend="up", spike_volume=True))
    assert snap.volume_anomaly is True


def test_technical_snapshot_no_anomaly_on_steady_volume():
    snap = technical_snapshot(_make_df(n=60, trend="up", spike_volume=False))
    assert snap.volume_anomaly is False


def test_technical_snapshot_raises_on_insufficient_history():
    with pytest.raises(InsufficientHistoryError):
        technical_snapshot(_make_df(n=10))
