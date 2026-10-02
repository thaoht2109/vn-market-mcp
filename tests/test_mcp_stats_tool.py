import os

import pytest

from db.connection import get_conn
from mcp_server.tools.stats import get_stats_tool


def _is_dedicated_test_database() -> bool:
    return os.environ.get("DATABASE_URL", "").rsplit("/", 1)[-1].split("?")[0].endswith("_test")


@pytest.mark.skipif(
    not _is_dedicated_test_database(),
    reason="wipes ALL runs/predictions — only safe on a dedicated *_test database, never the live one "
    "(running it against the live DB on 2026-10-02 deleted real runs and predictions)",
)
def test_get_stats_on_empty_database_returns_zeroed_counts():
    with get_conn() as conn:
        conn.execute("DELETE FROM predictions")
        conn.execute("DELETE FROM runs")

    result = get_stats_tool()
    assert result["data"]["total_runs"] == 0
    assert result["data"]["predictions_by_action_label"] == {}
    assert result["data"]["predictions_by_status"] == {}
    assert result["warnings"] == []


@pytest.fixture
def seeded_stats_data():
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO runs (run_id, mode, tickers, style, depth, as_of, snapshot_ref, warnings)
            VALUES ('stats:TEST:1', 'on_demand', ARRAY['STATSTEST'], 'long', 'quick', now(), '/tmp/x.json', '[]')
            """
        )
        conn.execute(
            """
            INSERT INTO predictions (
              run_id, source, ticker, trigger, action_label, universe_tier, holding_state,
              signal_type, horizon_days, confidence, status
            )
            VALUES
              ('stats:TEST:1', 'on_demand', 'STATSTEST', 'first', 'watch', 'A', 'unknown', 'mixed', 120, 0.5, 'open'),
              ('stats:TEST:1', 'on_demand', 'STATSTEST', 'first', 'watch', 'A', 'unknown', 'mixed', 120, 0.5, 'closed')
            """
        )
    yield
    with get_conn() as conn:
        conn.execute("DELETE FROM predictions WHERE run_id = 'stats:TEST:1'")
        conn.execute("DELETE FROM runs WHERE run_id = 'stats:TEST:1'")


def test_get_stats_counts_runs_and_breaks_down_predictions(seeded_stats_data):
    result = get_stats_tool()
    assert result["data"]["total_runs"] >= 1
    assert result["data"]["predictions_by_action_label"]["watch"] >= 2
    assert result["data"]["predictions_by_status"]["open"] >= 1
    assert result["data"]["predictions_by_status"]["closed"] >= 1
