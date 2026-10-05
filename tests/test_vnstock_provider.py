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

    def history(self, symbol, start, end):
        return self._df


class _FakeFinance:
    """Mimics vnstock.explorer.vci.financial.Finance: the real
    _get_financial_report keeps only the first `limit` periods of an
    oldest-first history (default 4) — so a caller that forgets to pass a
    large limit silently gets the OLDEST quarters."""

    def __init__(self, df):
        self._df = df

    def _get_financial_report(self, report_type, period=None, lang="en", limit=None):
        meta = [c for c in self._df.columns if c in ("item", "item_en", "item_id")]
        periods = [c for c in self._df.columns if c not in meta][: limit if limit is not None else 4]
        return self._df[meta + periods]


class _FakeCompany:
    def __init__(self, events_df=None, news_df=None):
        self._events_df = events_df
        self._news_df = news_df

    def events(self):
        return self._events_df

    def news(self):
        return self._news_df


class _FakeTrading:
    def __init__(self, df):
        self._df = df

    def price_board(self, symbols_list):
        return self._df


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
    provider = VNStockProvider(source="TCBS", clients={"quote": _FakeQuote(df)}, scale_map={"TCBS": 1000})

    bars = provider.get_ohlcv("VNM", date(2026, 9, 25), date(2026, 9, 25))

    assert len(bars) == 1
    bar = bars[0]
    assert bar.ticker == "VNM"
    assert bar.trade_date == date(2026, 9, 25)
    assert bar.close == 84500
    assert bar.high == 85000
    assert bar.value == 84200
    assert bar.source == "TCBS"


def test_get_market_index_returns_unscaled_frame_renamed_to_trade_date():
    df = pd.DataFrame(
        [{"time": "2026-09-25", "open": 1780.0, "high": 1790.0, "low": 1770.0, "close": 1785.0, "volume": 600_000_000}]
    )
    provider = VNStockProvider(source="VCI", clients={"quote": _FakeQuote(df)}, scale_map={"VCI": 1000})

    out = provider.get_market_index("VNINDEX", date(2026, 9, 25), date(2026, 9, 25))

    assert list(out["trade_date"]) == ["2026-09-25"]
    assert out["close"].iloc[0] == 1785.0  # unscaled: an index point value, not a VND price


def test_get_ohlcv_missing_value_field_falls_back_to_close_times_volume():
    df = pd.DataFrame(
        [{"time": "2026-09-25", "open": 84.0, "high": 85.0, "low": 83.5, "close": 84.5, "volume": 1_000_000}]
    )
    provider = VNStockProvider(source="TCBS", clients={"quote": _FakeQuote(df)}, scale_map={"TCBS": 1000})

    bars = provider.get_ohlcv("VNM", date(2026, 9, 25), date(2026, 9, 25))

    assert bars[0].value == 84500 * 1_000_000


def test_get_fundamentals_transposes_wide_ratio_dataframe():
    # vnstock.api.financial.Finance.ratio() returns one row per metric,
    # one column per period — the opposite orientation of FundamentalRecord.
    # item_en values are vnstock's real display labels (e.g. "ROE (%)", not
    # "roe") — get_fundamentals must normalize them to the snake_case keys
    # pipeline/fundamentals.py expects (verified against real VCI data for
    # VCB/SSI/BVH, 2026-10-01 — see _FUNDAMENTAL_METRIC_ALIASES).
    df = pd.DataFrame(
        {
            "item": ["ROE (%)", "P/E"],
            "item_en": ["ROE (%)", "P/E"],
            "item_id": [1, 2],
            "2026-Q1": [18.5, 12.0],
            "2026-Q2": [19.0, 11.5],
        }
    )
    provider = VNStockProvider(source="VCI", clients={"finance": _FakeFinance(df)})

    records = provider.get_fundamentals("FPT", quarters=2)

    assert [r.period for r in records] == ["2026-Q1", "2026-Q2"]
    assert records[0].metrics == {"roe": 18.5, "pe": 12.0}
    assert records[1].metrics == {"roe": 19.0, "pe": 11.5}
    assert records[0].ticker == "FPT"
    assert records[0].published_date is None


def test_get_fundamentals_drops_unmapped_metric_labels():
    # A real item_en value with no entry in _FUNDAMENTAL_METRIC_ALIASES (e.g.
    # one of the many ratios this codebase doesn't use) must be dropped, not
    # leak through under its raw display label — downstream code only ever
    # looks up snake_case keys.
    df = pd.DataFrame(
        {"item": ["ROE (%)", "CAR"], "item_en": ["ROE (%)", "CAR"], "item_id": [1, 2], "2026-Q1": [18.5, 9.0]}
    )
    provider = VNStockProvider(source="VCI", clients={"finance": _FakeFinance(df)})

    records = provider.get_fundamentals("FPT", quarters=1)

    assert records[0].metrics == {"roe": 18.5}


def test_get_fundamentals_respects_quarters_limit():
    df = pd.DataFrame(
        {
            "item": ["ROE (%)"], "item_en": ["ROE (%)"], "item_id": [1],
            "2025-Q3": [17.0], "2025-Q4": [17.5], "2026-Q1": [18.0],
        }
    )
    provider = VNStockProvider(source="VCI", clients={"finance": _FakeFinance(df)})

    records = provider.get_fundamentals("FPT", quarters=2)

    assert [r.period for r in records] == ["2025-Q4", "2026-Q1"]


def test_get_fundamentals_returns_newest_quarters_not_oldest():
    # Regression (real 2026-10-02 incident): VCI's statistics-financial
    # endpoint returns the whole history oldest-first, and vnstock's ratio()
    # cuts it to head(4) — every VN30 ticker was valued on its first four
    # quarters (2018-Q1..Q4 for ACB) while 2026-Q2 data was available. The
    # history also interleaves annual columns ("2025"), which aren't quarters.
    periods = [f"{y}-Q{q}" for y in range(2018, 2026) for q in range(1, 5)]
    periods.insert(periods.index("2025-Q4") + 1, "2025")
    periods += ["2026-Q1", "2026-Q2"]
    df = pd.DataFrame(
        {"item": ["P/B"], "item_en": ["P/B"], "item_id": [1], **{p: [float(i)] for i, p in enumerate(periods)}}
    )
    provider = VNStockProvider(source="VCI", clients={"finance": _FakeFinance(df)})

    records = provider.get_fundamentals("ACB", quarters=4)

    assert [r.period for r in records] == ["2025-Q3", "2025-Q4", "2026-Q1", "2026-Q2"]
    assert records[-1].metrics == {"pb": float(periods.index("2026-Q2"))}


def test_get_corporate_events_filters_by_date_range():
    df = pd.DataFrame(
        [
            {"event_code": "DIV", "public_date": "2026-05-01", "category": "DIVIDEND"},
            {"event_code": "ISS", "public_date": "2026-09-15", "category": "OTHER"},
        ]
    )
    provider = VNStockProvider(source="VCI", clients={"company": _FakeCompany(events_df=df)})

    events = provider.get_corporate_events("FPT", date(2026, 9, 1), date(2026, 9, 30))

    assert len(events) == 1
    assert events[0].event_type == "ISS"
    assert events[0].event_date == date(2026, 9, 15)
    assert events[0].source_url is None


def test_get_corporate_events_skips_rows_without_date():
    df = pd.DataFrame([{"event_code": "DIV", "public_date": None, "display_date1": None, "category": "DIVIDEND"}])
    provider = VNStockProvider(source="VCI", clients={"company": _FakeCompany(events_df=df)})

    events = provider.get_corporate_events("FPT", date(2026, 1, 1), date(2026, 12, 31))

    assert events == []


def test_get_foreign_flow_reads_kbs_price_board_snapshot():
    df = pd.DataFrame(
        [
            {
                "time": 1790769107337,  # ms epoch
                "close_price": 63000,
                "foreign_buy_volume": 547024,
                "foreign_sell_volume": 954740,
                "foreign_room": 352044832,
            }
        ]
    )
    provider = VNStockProvider(source="VCI", clients={"trading": _FakeTrading(df)}, scale_map={"VCI": 1000})

    records = provider.get_foreign_flow("FPT", date(2026, 9, 1), date(2026, 9, 30))

    assert len(records) == 1
    r = records[0]
    assert r.ticker == "FPT"
    assert r.source == "KBS"
    assert r.buy_value == 547024 * 63000
    assert r.sell_value == 954740 * 63000
    assert r.net_value == r.buy_value - r.sell_value
    assert r.room_left == 352044832


def test_get_foreign_flow_empty_board_returns_empty_list():
    provider = VNStockProvider(source="VCI", clients={"trading": _FakeTrading(pd.DataFrame())})

    records = provider.get_foreign_flow("FPT", date(2026, 9, 1), date(2026, 9, 30))

    assert records == []


def test_get_news_filters_by_date_and_skips_untitled_rows():
    df = pd.DataFrame(
        [
            {"news_title": "GAS: Nghị quyết HĐQT", "public_date": "2026-09-15T08:00:00",
             "news_source": "HOSE", "news_source_link": "https://x", "news_short_content": None, "news_full_content": None},
            {"news_title": "Tin ngoài khoảng ngày", "public_date": "2026-05-01T08:00:00",
             "news_source": "HOSE", "news_source_link": None, "news_short_content": None, "news_full_content": None},
            {"news_title": None, "public_date": "2026-09-16T08:00:00",
             "news_source": "HOSE", "news_source_link": None, "news_short_content": None, "news_full_content": None},
        ]
    )
    provider = VNStockProvider(source="VCI", clients={"company": _FakeCompany(news_df=df)})

    items = provider.get_news("GAS", date(2026, 9, 1), date(2026, 9, 30))

    assert len(items) == 1
    assert items[0].title == "GAS: Nghị quyết HĐQT"
    assert items[0].ticker == "GAS"
    assert items[0].summary is None  # Community tier: content fields are None in practice


def test_get_news_falls_back_to_short_content_as_summary():
    df = pd.DataFrame(
        [{"news_title": "Có tóm tắt", "public_date": "2026-09-15T08:00:00", "news_source": "HOSE",
          "news_source_link": None, "news_short_content": "Tóm tắt ngắn", "news_full_content": None}]
    )
    provider = VNStockProvider(source="VCI", clients={"company": _FakeCompany(news_df=df)})

    items = provider.get_news("GAS", date(2026, 9, 1), date(2026, 9, 30))

    assert items[0].summary == "Tóm tắt ngắn"


def test_normalize_fundamental_metrics_nulls_bank_only_zeros_and_nan():
    from providers.vnstock_provider import _FUNDAMENTAL_METRIC_ALIASES, _normalize_fundamental_metrics

    names = {v: k for k, v in _FUNDAMENTAL_METRIC_ALIASES.items()}
    raw = {names["nim"]: 0.0, names["npl_ratio"]: 0, names["roe"]: 0.3, names["pe"]: float("nan"), names["pb"]: 0.0}
    out = _normalize_fundamental_metrics(raw)
    assert out["nim"] is None and out["npl_ratio"] is None  # vendor's 0 = not applicable
    assert out["roe"] == 0.3 and out["pe"] is None
    assert out["pb"] == 0.0  # only bank-only ratios are treated as not-applicable at 0
