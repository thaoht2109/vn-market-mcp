"""Weekly data-retention sweep (spec §7.4, Phase 4).

Runs as `retention_job` role (SELECT+DELETE only, see db/setup_roles.py) —
the only role allowed to delete rows. Each policy runs dry_run first (count
rows, write retention_log, no delete), then the real pass, mirroring the
plan's "dry_run → xuất lưu trữ → kiểm tra → xóa → ghi log" order.

No policy deletes anything yet: predictions/runs/market data are "giữ vĩnh
viễn" per §7.4, and news_items (the only >24-month-TTL table in the plan)
doesn't exist until Phase 5 adds the news role. This job exists now so the
retention_job role, connection, and retention_log plumbing are exercised
end-to-end — add a policy function here when a TTL'd table ships.

Run as a long-lived process:
    python -m ops.retention_job
"""
from __future__ import annotations

import time

from mcp_server.connection import get_retention_conn
from ops.alerting import log_event

POLL_INTERVAL_S = 7 * 24 * 3600


def run_sweep(conn) -> None:
    conn.execute(
        "INSERT INTO retention_log (object_name, action, rows_affected, dry_run)"
        " VALUES (%s, %s, %s, %s)",
        ("none", "noop_sweep", 0, False),
    )
    conn.commit()


def main() -> None:
    log_event("retention_job_started")
    while True:
        with get_retention_conn() as conn:
            run_sweep(conn)
        log_event("retention_job_swept")
        time.sleep(POLL_INTERVAL_S)


if __name__ == "__main__":
    main()
