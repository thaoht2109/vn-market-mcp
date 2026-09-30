from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class RiskPlanConfig:
    atr_stop_multiplier: float
    reward_risk_multiple: float
    entry_zone_atr_fraction: float
    max_pct_of_avg_liquidity: float
    lot_size: int
    market_bands: dict[str, float]

    @classmethod
    def from_rules(cls, rules: dict) -> "RiskPlanConfig":
        return cls(
            atr_stop_multiplier=2.0,
            reward_risk_multiple=2.0,
            entry_zone_atr_fraction=0.5,
            max_pct_of_avg_liquidity=0.05,
            lot_size=rules["lot_size"],
            market_bands=rules["market_bands"],
        )


@dataclass
class RiskPlan:
    entry_zone: tuple[float, float]
    stop_loss: float
    target: float
    rr: float
    suggested_volume: int
    warnings: list[str] = field(default_factory=list)


def risk_plan(
    entry_ref_price: float,
    atr14: float,
    exchange: str,
    avg_liquidity_value_20d: float,
    cfg: RiskPlanConfig,
) -> RiskPlan:
    band_pct = cfg.market_bands.get(exchange)
    if band_pct is None:
        raise ValueError(f"unknown exchange {exchange!r}")

    warnings: list[str] = []

    stop_distance = cfg.atr_stop_multiplier * atr14
    stop_loss = entry_ref_price - stop_distance
    target = entry_ref_price + cfg.reward_risk_multiple * stop_distance
    rr = (target - entry_ref_price) / stop_distance if stop_distance > 0 else 0.0

    entry_zone = (
        entry_ref_price - cfg.entry_zone_atr_fraction * atr14,
        entry_ref_price + cfg.entry_zone_atr_fraction * atr14,
    )

    stop_pct = stop_distance / entry_ref_price
    if stop_pct > band_pct:
        warnings.append(
            f"stop-loss cách {stop_pct:.1%} vượt biên độ {exchange} ({band_pct:.0%}); "
            "có thể kẹt sàn không thoát được trong 1 phiên"
        )

    raw_volume = (avg_liquidity_value_20d * cfg.max_pct_of_avg_liquidity) / entry_ref_price
    suggested_volume = int(raw_volume // cfg.lot_size) * cfg.lot_size
    if suggested_volume <= 0:
        warnings.append("thanh khoản quá thấp để gợi ý khối lượng theo lô chẵn")

    return RiskPlan(
        entry_zone=entry_zone,
        stop_loss=stop_loss,
        target=target,
        rr=rr,
        suggested_volume=suggested_volume,
        warnings=warnings,
    )
