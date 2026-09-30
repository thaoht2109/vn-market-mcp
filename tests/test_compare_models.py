import pytest

from evals.compare_models import (
    SchemaError,
    UnsupportedProviderError,
    _make_client,
    build_prompt,
    parse_response,
    score_prediction,
    summarize,
)


def test_build_prompt_includes_ticker_and_headline():
    item = {"ticker": "VNM", "headline": "VNM cong bo KQKD quy 2", "body_excerpt": "loi nhuan tang 10%"}
    prompt = build_prompt(item)
    assert "VNM" in prompt
    assert "KQKD" in prompt


def test_parse_response_accepts_valid_schema():
    raw = '{"event_type": "earnings", "sentiment": "positive"}'
    parsed = parse_response(raw)
    assert parsed == {"event_type": "earnings", "sentiment": "positive"}


def test_parse_response_rejects_invalid_event_type():
    raw = '{"event_type": "not_a_real_type", "sentiment": "positive"}'
    with pytest.raises(SchemaError):
        parse_response(raw)


def test_parse_response_rejects_malformed_json():
    with pytest.raises(SchemaError):
        parse_response("not json at all")


def test_score_prediction_matches_both_fields():
    expected = {"expected_event_type": "earnings", "expected_sentiment": "positive"}
    assert score_prediction(expected, {"event_type": "earnings", "sentiment": "positive"}) is True
    assert score_prediction(expected, {"event_type": "earnings", "sentiment": "negative"}) is False


def test_summarize_computes_accuracy_and_cost():
    results = [
        {"correct": True, "schema_invalid": False, "cost_usd": 0.01, "latency_ms": 500},
        {"correct": False, "schema_invalid": True, "cost_usd": 0.01, "latency_ms": 700},
    ]
    summary = summarize("claude-sonnet-5-5", results)
    assert summary.n == 2
    assert summary.correct == 1
    assert summary.accuracy == 0.5
    assert summary.schema_invalid == 1
    assert summary.total_cost_usd == pytest.approx(0.02)
    assert summary.avg_latency_ms == 600


def test_make_client_rejects_unsupported_provider():
    with pytest.raises(UnsupportedProviderError):
        _make_client("gemini")


def test_make_client_accepts_deepseek_and_ollama():
    assert _make_client("deepseek") is not None
    assert _make_client("ollama") is not None
