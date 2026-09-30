from datetime import date, datetime, timezone

from quality.checks import (
    check_abnormal_move,
    check_daily_completeness,
    check_freshness,
    check_price_unit_consistency,
    check_schema,
    check_volume,
    log_check,
)
from tests.conftest import insert_ticker


def test_check_daily_completeness_fails_when_ticker_missing(db_conn):
    insert_ticker(db_conn, "VNM")
    insert_ticker(db_conn, "FPT")
    db_conn.execute(
        "INSERT INTO prices_daily (ticker, trade_date, open, high, low, close, volume, source, fetched_at)"
        " VALUES ('VNM', %s, 1, 1, 1, 1, 1, 'TCBS', now())",
        (date(2026, 9, 25),),
    )

    result = check_daily_completeness(db_conn, ["VNM", "FPT"], date(2026, 9, 25))

    assert result.result == "fail"
    assert result.detail["missing_tickers"] == ["FPT"]


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
    insert_ticker(db_conn, "VNM")
    check = check_price_unit_consistency(84500, 85200)
    log_check(db_conn, run_id=None, ticker="VNM", check=check)

    row = db_conn.execute(
        "SELECT check_name, result FROM data_quality_log WHERE ticker = 'VNM'"
    ).fetchone()
    assert row == ("price_unit", "pass")
