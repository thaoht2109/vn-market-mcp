from __future__ import annotations

from datetime import datetime, timezone

from mcp_server.connection import get_ro_conn
from mcp_server.envelope import build_envelope

_COLUMNS = [
    "id", "run_id", "ticker", "trigger", "action_label", "universe_tier",
    "holding_state", "signal_type", "entry_zone", "stop_loss", "target",
    "horizon_days", "confidence", "status", "created_at",
]


def list_predictions_tool(
    ticker: str | None = None, status: str | None = None, limit: int = 20
) -> dict:
    now = datetime.now(timezone.utc)
    query = (
        f"SELECT {', '.join(_COLUMNS)} FROM predictions"
        " WHERE ticker = COALESCE(%s, ticker) AND status = COALESCE(%s, status)"
        " ORDER BY created_at DESC LIMIT %s"
    )
    with get_ro_conn() as conn:
        rows = conn.execute(query, (ticker, status, limit)).fetchall()

    predictions = []
    for row in rows:
        pred = dict(zip(_COLUMNS, row))
        # entry_zone is a Postgres numrange — psycopg returns it as a
        # Range object, which the MCP SDK's JSON encoder can't serialize
        # ("Unable to serialize unknown type"). [lower, upper] matches how
        # pipeline/run_analysis.py built it before insert.
        zone = pred["entry_zone"]
        pred["entry_zone"] = None if zone is None else [float(zone.lower), float(zone.upper)]
        predictions.append(pred)
    return build_envelope(
        {"predictions": predictions}, sources=["postgres"], as_of=now,
    )
