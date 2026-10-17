"""One-off: load the snapshot files of runs from before runs.snapshot (migration 026) into the column, so readers no
longer need the snapshots/ directory. Safe to re-run: only runs with no stored snapshot are touched.

    python -m ops.backfill_snapshots [--dir /path/to/dir-holding-snapshots/]

snapshot_ref is relative to the process working directory ("snapshots/<run>.json"); --dir is the directory that
holds that `snapshots` folder (default: the current one). Files that are missing or not valid JSON are listed.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from psycopg.types.json import Jsonb

from mcp_server.connection import get_rw_conn
from ops.alerting import log_event
from pipeline.snapshot import serialize_snapshot


def backfill(conn, base: Path) -> tuple[int, list[str]]:
    loaded, skipped = 0, []
    for run_id, ref in conn.execute("SELECT run_id, snapshot_ref FROM runs WHERE snapshot IS NULL").fetchall():
        path = Path(ref) if Path(ref).is_absolute() else base / ref
        try:
            data = serialize_snapshot(json.loads(path.read_text()))
        except (OSError, ValueError):
            skipped.append(run_id)
            continue
        conn.execute("UPDATE runs SET snapshot = %s WHERE run_id = %s AND snapshot IS NULL", (Jsonb(data), run_id))
        loaded += 1
    return loaded, skipped


def main() -> None:
    p = argparse.ArgumentParser(prog="python -m ops.backfill_snapshots")
    p.add_argument("--dir", type=Path, default=Path("."))
    args = p.parse_args()
    with get_rw_conn() as conn:
        loaded, skipped = backfill(conn, args.dir)
    log_event("snapshots_backfilled", loaded=loaded, skipped=len(skipped))
    print(f"đã nạp {loaded} snapshot; bỏ qua {len(skipped)} (file thiếu hoặc hỏng)")
    for run_id in skipped[:20]:
        print("  ", run_id)


if __name__ == "__main__":
    main()
