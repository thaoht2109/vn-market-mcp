from __future__ import annotations

from datetime import datetime, timezone

from mcp_server.connection import get_ro_conn
from mcp_server.envelope import build_envelope


def get_stats_tool() -> dict:
    now = datetime.now(timezone.utc)
    with get_ro_conn() as conn:
        total_runs = conn.execute("SELECT count(*) FROM runs").fetchone()[0]
        by_label = conn.execute(
            "SELECT action_label, count(*) FROM predictions GROUP BY action_label"
        ).fetchall()
        by_status = conn.execute(
            "SELECT status, count(*) FROM predictions GROUP BY status"
        ).fetchall()

    return build_envelope(
        {
            "total_runs": total_runs,
            "predictions_by_action_label": {label: count for label, count in by_label},
            "predictions_by_status": {status: count for status, count in by_status},
        },
        sources=["postgres"], as_of=now,
    )
