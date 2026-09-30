import pytest

from pipeline.risk_plan import RiskPlanConfig, risk_plan

DEFAULT_CFG = RiskPlanConfig(
    atr_stop_multiplier=2.0,
    reward_risk_multiple=2.0,
    entry_zone_atr_fraction=0.5,
    max_pct_of_avg_liquidity=0.05,
    lot_size=100,
    market_bands={"HOSE": 0.07, "HNX": 0.10, "UPCOM": 0.15},
)


def test_risk_plan_computes_stop_and_target_from_atr():
    plan = risk_plan(
        entry_ref_price=100000, atr14=2000, exchange="HOSE",
        avg_liquidity_value_20d=5_000_000_000, cfg=DEFAULT_CFG,
    )

    assert plan.stop_loss == 96000
    assert plan.target == 108000
    assert plan.rr == pytest.approx(2.0)
    assert plan.entry_zone == (99000, 101000)
    assert plan.warnings == []


def test_risk_plan_flags_ket_san_warning_when_stop_exceeds_band():
    cfg = RiskPlanConfig(
        atr_stop_multiplier=3.0, reward_risk_multiple=2.0, entry_zone_atr_fraction=0.5,
        max_pct_of_avg_liquidity=0.05, lot_size=100, market_bands={"HOSE": 0.07},
    )

    plan = risk_plan(
        entry_ref_price=50000, atr14=2000, exchange="HOSE",
        avg_liquidity_value_20d=5_000_000_000, cfg=cfg,
    )

    assert any("kẹt sàn" in w for w in plan.warnings)


def test_risk_plan_suggests_volume_rounded_to_lot_size():
    plan = risk_plan(
        entry_ref_price=100000, atr14=2000, exchange="HOSE",
        avg_liquidity_value_20d=5_000_000_000, cfg=DEFAULT_CFG,
    )
    assert plan.suggested_volume == 2500
    assert plan.suggested_volume % DEFAULT_CFG.lot_size == 0


def test_risk_plan_warns_when_liquidity_too_low_for_one_lot():
    plan = risk_plan(
        entry_ref_price=100000, atr14=2000, exchange="HOSE",
        avg_liquidity_value_20d=1_000_000, cfg=DEFAULT_CFG,
    )
    assert plan.suggested_volume == 0
    assert any("thanh khoản" in w for w in plan.warnings)


def test_risk_plan_unknown_exchange_raises():
    with pytest.raises(ValueError):
        risk_plan(
            entry_ref_price=100000, atr14=2000, exchange="NASDAQ",
            avg_liquidity_value_20d=5_000_000_000, cfg=DEFAULT_CFG,
        )
