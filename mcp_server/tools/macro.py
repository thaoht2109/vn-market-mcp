"""get_macro_context: official numeric indicators (SBV), kept macro headlines per pillar, and how fresh each
news source is. Read-only, no LLM."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from mcp_server.connection import get_ro_conn
from mcp_server.envelope import build_envelope
from pipeline.news import load_sources
from pipeline.news_filter import load_keywords
from pipeline.news_health import news_sources_status, source_warnings

_VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")
PER_PILLAR = 10
INDICATOR_HISTORY = 10   # latest N periods per indicator, enough to read a short trend
STALE_DAILY_AFTER = timedelta(days=4)  # a long weekend + one late day


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
        indicators = _indicators(conn)
    warnings = source_warnings(status) + _indicator_warnings(indicators, now)
    return build_envelope({"indicators": indicators, "pillars": by_pillar, "news_sources": status},
                          sources=["postgres"], as_of=now, warnings=warnings)


def _indicators(conn) -> dict:
    rows = conn.execute(
        "SELECT indicator, period, value, unit, source, source_url FROM ("
        " SELECT *, row_number() OVER (PARTITION BY indicator ORDER BY period DESC) AS n FROM macro_indicators) t"
        " WHERE n <= %s ORDER BY indicator, period DESC",
        (INDICATOR_HISTORY,),
    ).fetchall()
    out: dict = {}
    for indicator, period, value, unit, source, url in rows:
        entry = out.setdefault(indicator, {"unit": unit, "source": source, "source_url": url, "series": []})
        entry["series"].append({"period": period.isoformat(), "value": float(value)})
    return out


def _indicator_warnings(indicators: dict, now: datetime) -> list[str]:
    central = indicators.get("usd_vnd_central")
    if not central:
        return ["Chưa có số liệu chính thức từ NHNN (tỷ giá, lãi suất): chỉ dựa được vào tiêu đề tin"]
    latest = datetime.fromisoformat(central["series"][0]["period"]).replace(tzinfo=_VN_TZ)
    if now - latest > STALE_DAILY_AFTER:
        return [f"Số liệu NHNN chưa cập nhật từ ngày {latest:%d/%m}"]
    return []
