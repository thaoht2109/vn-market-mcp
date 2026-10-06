"""Shared runner for numeric macro sources (table macro_indicators).

A source is a `fetch(client) -> [(indicator, period, value, unit, source_url)]`. The runner decides whether the
source is due (weekdays only, every N minutes since its last success), upserts the rows and records health in
source_health under the source name. A failing source never raises: it is recorded and the next one runs.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx

from ops.alerting import log_event
from pipeline.news_health import VN, record_failure, record_ok


def _due(conn, source: str, every_minutes: int, now: datetime) -> bool:
    if now.astimezone(VN).weekday() >= 5:
        return False
    row = conn.execute("SELECT last_ok_at FROM source_health WHERE source = %s", (source,)).fetchone()
    return row is None or row[0] is None or now - row[0] >= timedelta(minutes=every_minutes - 5)


def collect(conn, source: str, fetch, *, every_minutes: int, client: httpx.Client | None = None,
            now: datetime | None = None) -> int:
    """Returns the number of rows written (new or updated)."""
    now = now or datetime.now(timezone.utc)
    if not _due(conn, source, every_minutes, now):
        return 0
    own_client = client is None
    client = client or httpx.Client()
    try:
        rows = fetch(client)
        with conn.transaction():
            for indicator, period, value, unit, url in rows:
                conn.execute(
                    "INSERT INTO macro_indicators (indicator, period, value, unit, source, source_url, fetched_at)"
                    " VALUES (%s, %s, %s, %s, %s, %s, %s) ON CONFLICT (indicator, period, source)"
                    " DO UPDATE SET value = EXCLUDED.value, fetched_at = EXCLUDED.fetched_at",
                    (indicator, period, value, unit, source, url, now),
                )
        newest = max(p for _, p, *_ in rows)
        record_ok(conn, source, datetime.combine(newest, datetime.min.time(), VN), now)
        conn.commit()
        return len(rows)
    except Exception as exc:  # one broken source must not take the others (or the RSS collection) down
        conn.rollback()
        record_failure(conn, source, f"{type(exc).__name__}: {exc}", now)
        conn.commit()
        log_event("collect_indicators_failed", source=source, error=str(exc))
        return 0
    finally:
        if own_client:
            client.close()


def run_all(conn, *, now: datetime | None = None) -> dict[str, int]:
    from pipeline.collectors import commodities, nso, sbv

    return {
        "sbv": sbv.run_collect_sbv(conn, now=now),
        "nso": nso.run_collect_nso(conn, now=now),
        "yahoo": commodities.run_collect_futures(conn, now=now),
        "sjc": commodities.run_collect_sjc(conn, now=now),
    }
