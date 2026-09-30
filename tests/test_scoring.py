import pytest

from pipeline.scoring import (
    agreement_ratio,
    composite_score,
    confidence,
    percentile_score,
    regime_gate,
)


def test_percentile_score_basic():
    history = [10, 20, 30, 40, 50]
    assert percentile_score(history, 30) == 60.0  # 3 of 5 values <= 30


def test_percentile_score_invert():
    history = [10, 20, 30, 40, 50]
    assert percentile_score(history, 30, invert=True) == 40.0


def test_percentile_score_empty_history_returns_none():
    assert percentile_score([], 30) is None


def test_composite_score_only_uses_available_components_and_reports_coverage():
    weights = {
        "technical": 0.15, "flow": 0.10, "news_events": 0.15,
        "fundamental_valuation": 0.45, "sector_macro": 0.15,
    }
    scores = {
        "technical": 80.0, "fundamental_valuation": 70.0,
        "flow": None, "news_events": None, "sector_macro": None,
    }

    result = composite_score(scores, weights)

    covered = 0.15 + 0.45
    expected_score = (80.0 * 0.15 + 70.0 * 0.45) / covered
    assert result.score == pytest.approx(expected_score)
    assert result.weight_coverage == pytest.approx(covered / sum(weights.values()))


def test_composite_score_all_missing_returns_zero_score_and_zero_coverage():
    result = composite_score({"technical": None}, {"technical": 1.0})
    assert result.score == 0.0
    assert result.weight_coverage == 0.0


def test_confidence_multiplies_and_clamps():
    assert confidence(0.8, 1.0, 0.75) == pytest.approx(0.6)
    assert confidence(2.0, 2.0, 2.0) == 1.0  # clamped to 1.0
    assert confidence(0.0, 1.0, 1.0) == 0.0


def test_agreement_ratio_counts_majority_side():
    scores = {"technical": 80.0, "fundamental_valuation": 70.0, "flow": 20.0, "news_events": None}
    agreeing, ratio = agreement_ratio(scores)
    assert agreeing == 2  # technical + fundamental_valuation both bullish (>50)
    assert ratio == pytest.approx(2 / 3)


def test_regime_gate_blocks_only_risk_off():
    assert regime_gate("risk_on") is True
    assert regime_gate("neutral") is True
    assert regime_gate("risk_off") is False
