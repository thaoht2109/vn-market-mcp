"""Collected news: source config, monthly partitions, and storing one item.

News is stored raw with its tier-1 verdict; nothing is deleted. A missing partition raises (it used to be
swallowed), so a collector can record it as a source failure instead of silently losing news.
"""
from __future__ import annotations

import hashlib
from datetime import date, datetime, timedelta
from pathlib import Path

import psycopg
import yaml

SOURCES_PATH = Path(__file__).parent.parent / "config" / "news_sources.yaml"
_URL_DEDUP_WINDOW = timedelta(days=7)


def load_sources(path: Path = SOURCES_PATH) -> list[dict]:
    return [s for s in (yaml.safe_load(path.read_text()) or []) if s.get("enabled", True)]


def _month_start(d: date, offset: int) -> date:
    n = d.year * 12 + (d.month - 1) + offset
    return date(n // 12, n % 12 + 1, 1)


def ensure_news_partitions(conn: psycopg.Connection, today: date, months_ahead: int = 3) -> list[str]:
    """Create news_items_YYYY_MM for this month and the next `months_ahead`. Needs the table owner (admin).
    No DEFAULT partition: it would block DETACHing old months during retention."""
    created = []
    for i in range(months_ahead + 1):
        start, end = _month_start(today, i), _month_start(today, i + 1)
        name = f"news_items_{start:%Y_%m}"  # built from dates only, never from input
        if conn.execute("SELECT to_regclass(%s)", (name,)).fetchone()[0] is None:
            conn.execute(f"CREATE TABLE {name} PARTITION OF news_items FOR VALUES FROM ('{start}') TO ('{end}')")
            created.append(name)
    conn.commit()
    return created


def item_hash(url: str | None, title: str, published_at: datetime) -> str:
    """Same key as pipeline.ingest.ingest_news, so a story in both an RSS feed and vnstock is one row."""
    return hashlib.sha256((url or f"{title}|{published_at.date()}").encode()).hexdigest()


def store_news_item(
    conn: psycopg.Connection, *, source: str, url: str | None, title: str, summary: str | None,
    published_at: datetime, fetched_at: datetime, tickers: list[str], pillars: list[str],
    stream: str, filter_status: str, filter_reason: str | None,
) -> bool:
    """Insert one item; True if new. The unique index is (url_hash, published_at), so the same URL seen
    with another pub time (two feeds) is looked up within +-7 days and merged instead of duplicated."""
    url_hash = item_hash(url, title, published_at)
    with conn.transaction():  # savepoint: a failure here must not poison the caller's transaction
        existing = conn.execute(
            "SELECT id, published_at FROM news_items WHERE url_hash = %s AND published_at BETWEEN %s AND %s LIMIT 1",
            (url_hash, published_at - _URL_DEDUP_WINDOW, published_at + _URL_DEDUP_WINDOW),
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE news_items SET"
                " tickers = (SELECT ARRAY(SELECT DISTINCT unnest(tickers || %s::text[]))),"
                " pillars = (SELECT ARRAY(SELECT DISTINCT unnest(pillars || %s::text[])))"
                " WHERE id = %s AND published_at = %s",
                (tickers, pillars, existing[0], existing[1]),
            )
            return False
        conn.execute(
            """
            INSERT INTO news_items (published_at, tickers, source, url, url_hash, title, summary, fetched_at,
                                    stream, filter_status, filter_reason, pillars)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (url_hash, published_at) DO NOTHING
            """,
            (published_at, tickers, source, url, url_hash, title, summary, fetched_at,
             stream, filter_status, filter_reason, pillars),
        )
    return True
