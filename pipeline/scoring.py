from __future__ import annotations

from dataclasses import dataclass


def percentile_score(history: list[float], current: float, invert: bool = False) -> float | None:
    if not history:
        return None
    below_or_equal = sum(1 for v in history if v <= current)
    pct = (below_or_equal / len(history)) * 100
    return 100 - pct if invert else pct


@dataclass
class CompositeResult:
    score: float
    weight_coverage: float


def composite_score(component_scores: dict[str, float | None], weights: dict[str, float]) -> CompositeResult:
    total_weight = sum(weights.values())
    present = {k: v for k, v in component_scores.items() if v is not None and k in weights}
    covered_weight = sum(weights[k] for k in present)
    weight_coverage = covered_weight / total_weight if total_weight else 0.0

    if not present:
        return CompositeResult(score=0.0, weight_coverage=0.0)

    weighted_sum = sum(present[k] * weights[k] for k in present)
    return CompositeResult(score=weighted_sum / covered_weight, weight_coverage=weight_coverage)


def confidence(weight_coverage: float, freshness: float, agreement: float) -> float:
    value = weight_coverage * freshness * agreement
    return max(0.0, min(1.0, value))


def agreement_ratio(component_scores: dict[str, float | None], neutral: float = 50.0) -> tuple[int, float]:
    present = [v for v in component_scores.values() if v is not None]
    if not present:
        return 0, 0.0
    bullish = sum(1 for v in present if v > neutral)
    bearish = sum(1 for v in present if v < neutral)
    agreeing = max(bullish, bearish)
    return agreeing, agreeing / len(present)


def regime_gate(regime: str) -> bool:
    return regime != "risk_off"
