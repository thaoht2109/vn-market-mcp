from datetime import date, timedelta

import pandas as pd
import pytest

from pipeline.regime import get_market_regime


def _index_df(closes: list[float], end: date) -> pd.DataFrame:
    dates = [end - timedelta(days=len(closes) - 1 - i) for i in range(len(closes))]
    return pd.DataFrame(
        {
            "trade_date": dates,
            "open": closes, "high": [c * 1.01 for c in closes], "low": [c * 0.99 for c in closes],
            "close": closes, "volume": [1_000_000] * len(closes),
        }
    )


class _StubIndexProvider:
    def __init__(self, df: pd.DataFrame):
        self.df = df

    def get_market_index(self, symbol, start, end):
        return self.df


@pytest.mark.parametrize(
    "closes, expected",
    [
        ([1000.0] * 199 + [1200.0], "risk_on"),  # last close well above flat MA200 -> uptrend
        ([1000.0] * 199 + [800.0], "risk_off"),  # last close well below flat MA200 -> downtrend
    ],
)
def test_get_market_regime_follows_vnindex_vs_ma200(closes, expected):
    today = date(2026, 10, 1)
    provider = _StubIndexProvider(_index_df(closes, today))
    assert get_market_regime(provider, today) == expected


def test_get_market_regime_fails_open_to_risk_on_when_not_enough_history():
    today = date(2026, 10, 1)
    provider = _StubIndexProvider(_index_df([1000.0] * 10, today))
    assert get_market_regime(provider, today) == "risk_on"


def test_market_context_is_stored_and_reused_within_max_age(db_conn):
    from datetime import datetime, timezone
    from pipeline.regime import get_market_context

    today = date(2026, 9, 30)
    closes = [1500.0 - i for i in range(250)]  # steady decline → trend down, close < MA200
    calls = []

    class _Counting(_StubIndexProvider):
        def get_market_index(self, symbol, start, end):
            calls.append(1)
            return self.df

    provider = _Counting(_index_df(closes, today))
    now = datetime(2026, 9, 30, 3, 0, tzinfo=timezone.utc)

    first = get_market_context(db_conn, provider, today, now)
    assert first["regime"] == "risk_off" and first["trend"] == "down"
    assert first["close"] == closes[-1] and first["change_pct"] < 0

    again = get_market_context(db_conn, provider, today, now.replace(minute=30))  # +30 min: reuse stored row
    assert again["regime"] == "risk_off" and again["close"] == first["close"]
    assert len(calls) == 1

    get_market_context(db_conn, provider, today, now.replace(hour=5))  # +2 h: stale → refetch
    assert len(calls) == 2


def test_market_context_without_history_is_risk_on_with_null_numbers(db_conn):
    from datetime import datetime, timezone
    from pipeline.regime import get_market_context

    today = date(2026, 9, 30)
    ctx = get_market_context(db_conn, _StubIndexProvider(_index_df([1000.0] * 10, today)), today,
                             datetime(2026, 9, 30, 3, 0, tzinfo=timezone.utc))
    assert ctx["regime"] == "risk_on" and ctx["close"] is None and ctx["trend"] is None


def test_market_breadth_counts_vn30_advancers_above_ma50_and_final_liquidity(db_conn):
    from datetime import datetime, timedelta, timezone
    from pipeline.regime import market_breadth
    from tests.conftest import insert_ticker

    as_of = date(2020, 3, 27)  # weekday-agnostic: breadth uses whatever bars exist
    days = [as_of - timedelta(days=i) for i in range(59, -1, -1)]
    # UP rises every day (above its MA50, advancer); DOWN falls every day (below MA50, decliner)
    for tk, step in (("BRUP", 1.0), ("BRDN", -1.0)):
        insert_ticker(db_conn, tk)
        db_conn.execute("INSERT INTO index_membership (index_code, ticker, valid_from) VALUES ('VN30', %s, '2019-01-01')", (tk,))
        for i, d in enumerate(days):
            c = 1000 + step * i
            v = 1_000_000.0 * (3 if d == as_of else 1)  # today's traded value = 3x the 20-day mean
            db_conn.execute(
                "INSERT INTO prices_daily (ticker, trade_date, open, high, low, close, volume, value, source, fetched_at)"
                " VALUES (%s, %s, %s, %s, %s, %s, 1000, %s, 't', now())", (tk, d, c, c, c, c, v))

    after_close = datetime(2020, 3, 27, 9, 0, tzinfo=timezone.utc)   # 16:00 VN
    b = market_breadth(db_conn, as_of, after_close)
    assert (b["advancers"], b["decliners"]) == (1, 1) and b["pct_above_ma50"] == 0.5
    assert abs(b["liquidity_ratio"] - 3.0) < 1e-9

    mid_session = datetime(2020, 3, 27, 3, 0, tzinfo=timezone.utc)   # 10:00 VN: partial value must not be compared
    assert market_breadth(db_conn, as_of, mid_session)["liquidity_ratio"] is None
