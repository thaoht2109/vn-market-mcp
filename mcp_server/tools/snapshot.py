from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from mcp_server.connection import get_ro_conn
from mcp_server.envelope import build_envelope
from mcp_server.identity import pinned_user
from pipeline.positions import personalize


def get_snapshot_tool(ticker: str | None = None, run_id: str | None = None) -> dict:
    if (ticker is None) == (run_id is None):
        raise ValueError("provide exactly one of ticker or run_id")

    now = datetime.now(timezone.utc)
    with get_ro_conn() as conn:
        if run_id is not None:
            row = conn.execute(
                "SELECT run_id, snapshot_ref, as_of FROM runs WHERE run_id = %s", (run_id,)
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT run_id, snapshot_ref, as_of FROM runs WHERE %s = ANY(tickers)"
                " ORDER BY as_of DESC LIMIT 1",
                (ticker,),
            ).fetchone()

    if row is None:
        return build_envelope(
            {"status": "not_found", "run_id": run_id, "ticker": ticker},
            sources=["postgres"], as_of=now, warnings=["không tìm thấy run"],
        )

    found_run_id, snapshot_ref, run_as_of = row
    snapshot_path = Path(snapshot_ref)
    if not snapshot_path.exists():
        return build_envelope(
            {"status": "snapshot_file_missing", "run_id": found_run_id, "ticker": ticker},
            sources=["postgres"], as_of=now, warnings=[f"file snapshot không tồn tại: {snapshot_ref}"],
        )

    snapshot = json.loads(snapshot_path.read_text())
    user = pinned_user()
    if user:
        with get_ro_conn() as conn:
            snapshot = personalize(conn, snapshot, ticker or snapshot.get("ticker"), user)
    return build_envelope(
        {"status": "ok", "run_id": found_run_id, "snapshot": snapshot},
        sources=["postgres", "snapshot_file"], as_of=run_as_of,
    )
