"""Shared vnstock request budget across processes (two workers, each user's MCP server).

vnstock's tier allows 60 requests/min and exits the process (SystemExit) past that; each process
counting on its own never sees the others' calls. Every request instead reserves the next free slot
in api_budget with one row-locked UPDATE and sleeps until it, so slots are SPACING_S apart across
all callers. Fails open (no wait) without a database URL or the api_budget row.
"""
from __future__ import annotations

import os
import time

import psycopg

CALLS_PER_MINUTE = 50  # under vnstock's 60, leaving room for vnstock's own extra requests
SPACING_S = 60 / CALLS_PER_MINUTE


def wait_for_slot(name: str = "vnstock", spacing_s: float = SPACING_S, url: str | None = None) -> float:
    """Block until this caller's reserved slot; returns the seconds waited."""
    url = url or os.environ.get("PIPELINE_RW_DATABASE_URL")
    if not url:
        return 0.0
    with psycopg.connect(url, autocommit=True) as conn:
        row = conn.execute(
            "UPDATE api_budget SET next_slot = greatest(next_slot, clock_timestamp()) + make_interval(secs => %s)"
            " WHERE name = %s RETURNING extract(epoch FROM next_slot - clock_timestamp())::float8 - %s",
            (spacing_s, name, spacing_s),
        ).fetchone()
    wait = max(0.0, row[0]) if row else 0.0
    if wait:
        time.sleep(wait)
    return wait
