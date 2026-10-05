from datetime import date, datetime, timezone

from providers.vnstock_provider import FundamentalRecord
from pipeline.fundamentals import METRIC_SET, fundamental_snapshot


def _record(period, metrics):
    return FundamentalRecord(
        ticker="VCB", period=period, report_type="audited", version=1, metrics=metrics,
        published_date=date(2026, 7, 20), source="TCBS", fetched_at=datetime.now(timezone.utc),
    )


def test_fundamental_snapshot_returns_full_metric_set_regardless_of_industry():
    # vnstock Community tier returns the same fixed ratio set for every
    # ticker (verified 2026-10-01 against real VCI data) — there is no real
    # per-industry schema, so industry_group no longer changes which keys
    # come back, only the metadata on the result.
    records = [_record("2026Q2", {"pb": 1.8, "roe": 0.19, "credit_growth": 0.12, "nim": 0.035, "npl_ratio": 0.011, "casa_ratio": 0.34})]

    snap = fundamental_snapshot("VCB", "bank", records)

    assert set(snap.metrics.keys()) == set(METRIC_SET)
    assert snap.metrics["nim"] == 0.035
    assert snap.quarters_available == 1


def test_fundamental_snapshot_defaults_missing_metric_to_none():
    records = [_record("2026Q2", {"pb": 1.8})]
    snap = fundamental_snapshot("VCB", "bank", records)
    assert snap.metrics["nim"] is None


def test_fundamental_snapshot_keeps_industry_group_as_metadata_only():
    # No metric filtering happens by industry_group anymore — any string
    # (including an unrecognized one) is just carried through on the result.
    records = [_record("2026Q2", {"gross_margin": 0.3, "roe": 0.15})]
    snap = fundamental_snapshot("XYZ", "unknown_group", records)
    assert snap.industry_group == "unknown_group"
    assert set(snap.metrics.keys()) == set(METRIC_SET)


def test_fundamental_snapshot_counts_quarters_from_most_recent():
    records = [_record("2026Q2", {"roe": 0.2}), _record("2026Q1", {"roe": 0.18})]
    snap = fundamental_snapshot("VCB", "bank", records)
    assert snap.quarters_available == 2
    assert snap.metrics["roe"] == 0.2  # most recent quarter wins
