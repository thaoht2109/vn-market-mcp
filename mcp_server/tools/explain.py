from __future__ import annotations

from datetime import datetime, timezone

from mcp_server.connection import get_ro_conn
from mcp_server.envelope import build_envelope


def explain_run_tool(run_id: str) -> dict:
    now = datetime.now(timezone.utc)
    with get_ro_conn() as conn:
        run_row = conn.execute(
            "SELECT run_id, mode, tickers, style, depth, as_of, snapshot_ref, warnings"
            " FROM runs WHERE run_id = %s",
            (run_id,),
        ).fetchone()

        if run_row is None:
            return build_envelope(
                {"status": "not_found", "run_id": run_id},
                sources=["postgres"], as_of=now, warnings=["không tìm thấy run_id"],
            )

        checks = conn.execute(
            "SELECT ticker, check_name, result, detail, created_at FROM data_quality_log"
            " WHERE run_id = %s ORDER BY created_at",
            (run_id,),
        ).fetchall()
        preds = conn.execute(
            "SELECT id, ticker, action_label, status, confidence, created_at FROM predictions"
            " WHERE run_id = %s ORDER BY created_at",
            (run_id,),
        ).fetchall()

    run_cols = ["run_id", "mode", "tickers", "style", "depth", "as_of", "snapshot_ref", "warnings"]
    check_cols = ["ticker", "check_name", "result", "detail", "created_at"]
    pred_cols = ["id", "ticker", "action_label", "status", "confidence", "created_at"]

    return build_envelope(
        {
            "status": "ok",
            "run": dict(zip(run_cols, run_row)),
            "quality_checks": [dict(zip(check_cols, row)) for row in checks],
            "predictions": [dict(zip(pred_cols, row)) for row in preds],
        },
        sources=["postgres"], as_of=now,
    )
