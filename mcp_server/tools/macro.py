"""get_macro_context: kept macro headlines per pillar + how fresh each news source is. Read-only, no LLM."""
from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from mcp_server.connection import get_ro_conn
from mcp_server.envelope import build_envelope
from pipeline.news import load_sources
from pipeline.news_filter import load_keywords
from pipeline.news_health import news_sources_status, source_warnings

_VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")
PER_PILLAR = 10


def get_macro_context_tool(days: int = 7) -> dict:
    days = max(1, min(30, days))
    now = datetime.now(timezone.utc)
    pillars = [p for p in load_keywords() if p != "exclude"]
    with get_ro_conn() as conn:
        by_pillar = {}
        for p in pillars:
            rows = conn.execute(
                "SELECT published_at, title, source, url FROM news_items"
                " WHERE filter_status = 'kept' AND %s = ANY(pillars)"
                " AND published_at >= now() - make_interval(days => %s) ORDER BY published_at DESC LIMIT %s",
                (p, days, PER_PILLAR),
            ).fetchall()
            by_pillar[p] = [{"date": at.astimezone(_VN_TZ).strftime("%d/%m %H:%M"), "title": title,
                             "source": source, "url": url} for at, title, source, url in rows]
        status = news_sources_status(conn, now, [s["name"] for s in load_sources()])
    return build_envelope({"pillars": by_pillar, "news_sources": status}, sources=["postgres"], as_of=now,
                          warnings=source_warnings(status))
