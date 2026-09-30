from datetime import date
import pandas as pd
import pytest
from providers.vnstock_provider import VNStockProvider, normalize_price_unit


def test_normalize_price_unit_scales_by_source():
    scale_map = {"VCI": 1, "TCBS": 1000}
    assert normalize_price_unit(84.5, "TCBS", scale_map) == 84500
    assert normalize_price_unit(84500, "VCI", scale_map) == 84500


def test_normalize_price_unit_unknown_source_raises():
    with pytest.raises(ValueError):
        normalize_price_unit(1.0, "UNKNOWN", {"VCI": 1})


class _FakeQuote:
    def __init__(self, df):
        self._df = df

    def history(self, start, end):
        return self._df


class _FakeStock:
    def __init__(self, df):
        self.quote = _FakeQuote(df)


class _FakeClient:
    def __init__(self, df):
        self._df = df

    def stock(self, symbol, source):
        return _FakeStock(self._df)


def test_get_ohlcv_normalizes_and_returns_pricebars():
    df = pd.DataFrame(
        [
            {
                "time": "2026-09-25",
                "open": 84.0,
                "high": 85.0,
                "low": 83.5,
                "close": 84.5,
                "volume": 1_000_000,
                "value": 84.2,
            }
        ]
    )
    provider = VNStockProvider(source="TCBS", client=_FakeClient(df), scale_map={"TCBS": 1000})

    bars = provider.get_ohlcv("VNM", date(2026, 9, 25), date(2026, 9, 25))

    assert len(bars) == 1
    bar = bars[0]
    assert bar.ticker == "VNM"
    assert bar.trade_date == date(2026, 9, 25)
    assert bar.close == 84500
    assert bar.high == 85000
    assert bar.value == 84200
    assert bar.source == "TCBS"


def test_get_ohlcv_missing_value_field_stays_none():
    df = pd.DataFrame(
        [{"time": "2026-09-25", "open": 84.0, "high": 85.0, "low": 83.5, "close": 84.5, "volume": 1_000_000}]
    )
    provider = VNStockProvider(source="TCBS", client=_FakeClient(df), scale_map={"TCBS": 1000})

    bars = provider.get_ohlcv("VNM", date(2026, 9, 25), date(2026, 9, 25))

    assert bars[0].value is None
