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


def technical_score(close: float, tech) -> float:
    """0-100 trend strength: price vs MA20/50/200, MA20 vs MA50, MACD vs signal and an
    RSI band (overbought/oversold earn nothing). Replaces "where does today's close sit in
    3 years of closes", which scored a stock at its peak 100 and one that just crashed ~0."""
    checks = [(close > tech.ma20, 15), (close > tech.ma50, 20), (tech.ma20 > tech.ma50, 15),
              (tech.macd > tech.macd_signal, 15)]
    if tech.ma200 is not None:  # fewer than 200 bars: drop the term instead of scoring it bearish
        checks.append((close > tech.ma200, 20))
    rsi_pts = 15 if 50 <= tech.rsi14 <= 70 else 8 if 40 <= tech.rsi14 < 50 or 70 < tech.rsi14 <= 80 else 0
    return (sum(w for ok, w in checks if ok) + rsi_pts) / (sum(w for _, w in checks) + 15) * 100


FLOW_MIN_SESSIONS = 3
FLOW_FULL_SCALE = 0.10  # net foreign flow = ±10% of traded value over the window -> 100 / 0


def flow_score(rows: list[tuple[float | None, float | None]]) -> float | None:
    """rows: (net_value, traded_value) for the latest sessions. None until enough sessions
    exist (vnstock has no flow history, it accrues one board snapshot per day)."""
    rows = [(float(n), float(v)) for n, v in rows if n is not None and v]
    if len(rows) < FLOW_MIN_SESSIONS:
        return None
    ratio = sum(n for n, _ in rows) / sum(v for _, v in rows)
    return max(0.0, min(100.0, 50 + ratio / FLOW_FULL_SCALE * 50))
