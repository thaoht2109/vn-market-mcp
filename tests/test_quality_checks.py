from datetime import date, datetime, timezone

from quality.checks import (
    check_abnormal_move,
    check_daily_completeness,
    check_freshness,
    check_fundamentals_freshness,
    check_price_series_units,
    check_price_unit_consistency,
    check_schema,
    check_volume,
    log_check,
)
from tests.conftest import insert_ticker


def test_check_daily_completeness_fails_when_ticker_missing(db_conn):
    insert_ticker(db_conn, "VNMTEST")
    insert_ticker(db_conn, "FPTTEST")
    db_conn.execute(
        "INSERT INTO prices_daily (ticker, trade_date, open, high, low, close, volume, source, fetched_at)"
        " VALUES ('VNMTEST', %s, 1, 1, 1, 1, 1, 'TCBS', now())",
        (date(2026, 9, 25),),
    )

    result = check_daily_completeness(db_conn, ["VNMTEST", "FPTTEST"], date(2026, 9, 25))

    assert result.result == "fail"
    assert result.detail["missing_tickers"] == ["FPTTEST"]


def test_check_schema_fails_on_missing_column():
    result = check_schema({"time", "open", "close"}, {"time", "open", "high", "low", "close", "volume"})
    assert result.result == "fail"
    assert set(result.detail["missing_columns"]) == {"high", "low", "volume"}


def test_check_price_unit_consistency_flags_1000x_jump():
    result = check_price_unit_consistency(prev_close=84.5, today_close=84500)
    assert result.result == "fail"


def test_check_price_unit_consistency_passes_normal_move():
    result = check_price_unit_consistency(prev_close=84500, today_close=85200)
    assert result.result == "pass"


def test_check_price_series_units_flags_mis_scaled_bar_mid_history():
    # Regression (real 2026-09-28 incident): one session stored in thousand-VND
    # between correctly-scaled neighbours. The old last-two-closes check never
    # saw it; scanning every consecutive pair must, and must name the date.
    days = [date(2026, 9, d) for d in (24, 25, 28, 29, 30)]
    closes = [21050.0, 21400.0, 21.1, 21100.0, 21100.0]

    result = check_price_series_units(days, closes)

    assert result.result == "fail"
    assert result.detail["trade_date"] == "2026-09-28"


def test_check_price_series_units_passes_clean_series():
    days = [date(2026, 9, d) for d in (24, 25, 28)]

    assert check_price_series_units(days, [21050.0, 21400.0, 21100.0]).result == "pass"


def test_check_fundamentals_freshness_fails_on_old_vintage():
    # Regression (real 2026-10-02 incident): every VN30 ticker was valued on
    # its 2018 quarters while 2026-Q2 existed.
    assert check_fundamentals_freshness("2018-Q4", date(2026, 10, 2)).result == "fail"
    assert check_fundamentals_freshness(None, date(2026, 10, 2)).result == "fail"


def test_check_fundamentals_freshness_passes_when_latest_quarter_published():
    # Q2 ends 30/6; by early October it is ~3 months old — the normal state.
    assert check_fundamentals_freshness("2026-Q2", date(2026, 10, 2)).result == "pass"
    # Q3 isn't out yet in mid-October either; Q2 is still the freshest possible.
    assert check_fundamentals_freshness("2026-Q2", date(2026, 10, 25)).result == "pass"


def test_check_abnormal_move_warns_when_unexplained(db_conn):
    insert_ticker(db_conn, "VNM")
    result = check_abnormal_move(
        db_conn, "VNM", date(2026, 9, 25), prev_close=84500, today_close=70000, band_pct=0.07
    )
    assert result.result == "warn"
    assert "reason" in result.detail


def test_check_abnormal_move_passes_when_corporate_action_booked(db_conn):
    insert_ticker(db_conn, "VNM")
    db_conn.execute(
        "INSERT INTO price_adjustments (ticker, ex_date, kind, factor) VALUES ('VNM', %s, 'stock_dividend', 0.83)",
        (date(2026, 9, 25),),
    )

    result = check_abnormal_move(
        db_conn, "VNM", date(2026, 9, 25), prev_close=84500, today_close=70000, band_pct=0.07
    )

    assert result.result == "pass"
    assert result.detail["explained_by"] == "stock_dividend"


def test_check_volume_warns_on_zero_for_high_liquidity():
    assert check_volume(0, is_high_liquidity=True).result == "warn"
    assert check_volume(0, is_high_liquidity=False).result == "pass"


def test_check_freshness_warns_when_stale():
    as_of = datetime(2026, 9, 24, 15, 0, tzinfo=timezone.utc)
    result = check_freshness(as_of, latest_official_trading_day=date(2026, 9, 25))
    assert result.result == "warn"


def test_log_check_writes_row(db_conn):
    insert_ticker(db_conn, "VNMTEST")
    check = check_price_unit_consistency(84500, 85200)
    log_check(db_conn, run_id=None, ticker="VNMTEST", check=check)

    row = db_conn.execute(
        "SELECT check_name, result FROM data_quality_log WHERE ticker = 'VNMTEST'"
    ).fetchone()
    assert row == ("price_unit", "pass")
