import pytest

from db.connection import get_conn
from mcp_server.tools.predictions import list_predictions_tool


@pytest.fixture
def seeded_predictions():
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO runs (run_id, mode, tickers, style, depth, as_of, snapshot_ref, warnings)
            VALUES ('pred:TEST:1', 'on_demand', ARRAY['PREDTEST'], 'long', 'quick', now(), '/tmp/x.json', '[]')
            """
        )
        conn.execute(
            """
            INSERT INTO predictions (
              run_id, source, ticker, trigger, action_label, universe_tier, holding_state,
              signal_type, horizon_days, confidence, status
            )
            VALUES ('pred:TEST:1', 'on_demand', 'PREDTEST', 'first', 'watch', 'A', 'unknown',
                    'mixed', 120, 0.5, 'open')
            """
        )
    yield
    with get_conn() as conn:
        conn.execute("DELETE FROM predictions WHERE run_id = 'pred:TEST:1'")
        conn.execute("DELETE FROM runs WHERE run_id = 'pred:TEST:1'")


def test_list_predictions_filters_by_ticker(seeded_predictions):
    result = list_predictions_tool(ticker="PREDTEST")
    assert len(result["data"]["predictions"]) == 1
    assert result["data"]["predictions"][0]["action_label"] == "watch"


def test_list_predictions_filters_by_status(seeded_predictions):
    result = list_predictions_tool(ticker="PREDTEST", status="closed")
    assert result["data"]["predictions"] == []
    assert result["warnings"] == []  # empty result set is not itself a warning


def test_list_predictions_empty_ticker_history_is_not_an_error():
    result = list_predictions_tool(ticker="ZZZNOPRED")
    assert result["data"]["predictions"] == []
