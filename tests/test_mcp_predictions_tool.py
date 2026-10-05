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


@pytest.fixture
def seeded_prediction_with_entry_zone():
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO runs (run_id, mode, tickers, style, depth, as_of, snapshot_ref, warnings)
            VALUES ('pred:ZONE:1', 'on_demand', ARRAY['PREDZONE'], 'long', 'quick', now(), '/tmp/x.json', '[]')
            """
        )
        conn.execute(
            """
            INSERT INTO predictions (
              run_id, source, ticker, trigger, action_label, universe_tier, holding_state,
              signal_type, entry_zone, stop_loss, target, horizon_days, confidence, status
            )
            VALUES ('pred:ZONE:1', 'on_demand', 'PREDZONE', 'first', 'buy_accumulate', 'A', 'unknown',
                    'mixed', numrange(10.5, 11.2), 9.8, 13.0, 120, 0.7, 'open')
            """
        )
    yield
    with get_conn() as conn:
        conn.execute("DELETE FROM predictions WHERE run_id = 'pred:ZONE:1'")
        conn.execute("DELETE FROM runs WHERE run_id = 'pred:ZONE:1'")


def test_list_predictions_entry_zone_is_json_serializable(seeded_prediction_with_entry_zone):
    # Regression: psycopg returns a numrange column as a Range object, which
    # the MCP SDK's JSON encoder rejects with "Unable to serialize unknown
    # type" — observed live via a real Hermes chat session calling this tool.
    # (Decimal fields like stop_loss/confidence are a separate, already-OK
    # case: FastMCP serializes via pydantic_core, which natively handles
    # Decimal — only Range needed this explicit conversion.)
    result = list_predictions_tool(ticker="PREDZONE")
    pred = result["data"]["predictions"][0]
    assert pred["entry_zone"] == [10.5, 11.2]
    assert not hasattr(pred["entry_zone"][0], "lower")  # no longer a Range object
