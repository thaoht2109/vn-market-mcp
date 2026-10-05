from datetime import date

import pytest

from pipeline.coverage import (
    CoverageConfig,
    CoverageInputs,
    UnknownTickerError,
    classify_universe_tier,
    coverage_check,
    gather_coverage_inputs,
    resolve_or_register_ticker,
    resolve_ticker,
)
from tests.conftest import insert_ticker

DEFAULT_CFG = CoverageConfig(
    min_price_history_days=500,
    min_avg_liquidity_value_20d=5.0e9,
    min_fundamental_quarters=4,
    min_weight_coverage=0.70,
)


def test_resolve_ticker_returns_match_for_exact_ticker(db_conn):
    insert_ticker(db_conn, "VNM", exchange="HOSE")
    match = resolve_ticker(db_conn, "vnm")
    assert match.ticker == "VNM"
    assert match.exchange == "HOSE"


def test_resolve_ticker_raises_with_suggestions_for_unknown_ticker(db_conn):
    insert_ticker(db_conn, "VNM")
    insert_ticker(db_conn, "VNI")

    with pytest.raises(UnknownTickerError) as exc_info:
        resolve_ticker(db_conn, "VNX")  # typo, not a real ticker — must reject, never proceed silently

    assert exc_info.value.raw_input == "VNX"
    # Don't assert suggestions is a *subset* of {VNM, VNI} — the shared DB
    # may contain other tickers that also fuzzy-match "VNX" (e.g. VN30).
    assert {"VNM", "VNI"} <= set(exc_info.value.suggestions)


class _Listing:
    def lookup_listing(self, ticker):
        return ("Tổng CTCP DIGTEST", "HOSE") if ticker == "DIGTEST" else None


def test_resolve_or_register_ticker_registers_a_listed_non_vn30_ticker(db_conn):
    match = resolve_or_register_ticker(db_conn, _Listing(), " digtest ")
    assert (match.ticker, match.exchange) == ("DIGTEST", "HOSE")
    assert resolve_ticker(db_conn, "DIGTEST").name == "Tổng CTCP DIGTEST"
    assert classify_universe_tier(db_conn, "DIGTEST") == "B"


def test_resolve_or_register_ticker_still_rejects_an_unlisted_ticker(db_conn):
    with pytest.raises(UnknownTickerError):
        resolve_or_register_ticker(db_conn, _Listing(), "NOTLISTED")
    assert db_conn.execute("SELECT 1 FROM tickers WHERE ticker = 'NOTLISTED'").fetchone() is None


def test_classify_universe_tier_a_for_vn30_member(db_conn):
    insert_ticker(db_conn, "VNM")
    db_conn.execute(
        "INSERT INTO index_membership (index_code, ticker, valid_from, valid_to) VALUES ('VN30', 'VNM', %s, NULL)",
        (date(2026, 1, 1),),
    )
    assert classify_universe_tier(db_conn, "VNM") == "A"


def test_classify_universe_tier_b_for_non_member(db_conn):
    insert_ticker(db_conn, "ABC")
    assert classify_universe_tier(db_conn, "ABC") == "B"


def test_gather_coverage_inputs_reads_row_counts(db_conn):
    insert_ticker(db_conn, "VNMTEST")
    for i in range(3):
        db_conn.execute(
            "INSERT INTO prices_daily (ticker, trade_date, open, high, low, close, volume, value, source, fetched_at)"
            " VALUES ('VNMTEST', %s, 1, 1, 1, 1, 1000, 100000, 'TCBS', now())",
            (date(2026, 9, 20 + i),),
        )

    inputs = gather_coverage_inputs(db_conn, "VNMTEST")

    assert inputs.price_days == 3
    assert inputs.avg_liquidity_value_20d == 100000.0
    assert inputs.fundamentals_quarters == 0


def test_coverage_check_sufficient_when_all_components_ok():
    inputs = CoverageInputs(price_days=600, avg_liquidity_value_20d=6.0e9, fundamentals_quarters=5)
    result = coverage_check("A", inputs, DEFAULT_CFG)
    assert result.sufficient is True
    assert result.tier == "A"
    assert result.weight_coverage == 1.0


def test_coverage_check_insufficient_when_all_partial_not_individually_missing():
    # Every component is "partial" (present but under the full threshold) —
    # nothing is flagged outright "missing", yet aggregate weight_coverage
    # must still gate as insufficient (review focus #4).
    inputs = CoverageInputs(price_days=300, avg_liquidity_value_20d=3.0e9, fundamentals_quarters=2)

    result = coverage_check("B", inputs, DEFAULT_CFG)

    assert "missing" not in result.component_status.values()
    assert result.weight_coverage == pytest.approx(0.5)
    assert result.sufficient is False
    assert result.tier == "C"
