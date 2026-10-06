"""get_macro_context: numeric indicators (SBV, NSO, commodity prices), kept macro headlines per pillar, and how
fresh each news source is. Read-only, no LLM."""
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
LABELS = {
    "usd_vnd_central": "Tỷ giá trung tâm USD/VND (NHNN, theo ngày)",
    "usd_vnd_ref_buy": "Tỷ giá tham khảo mua USD tại Sở giao dịch NHNN",
    "usd_vnd_ref_sell": "Tỷ giá tham khảo bán USD tại Sở giao dịch NHNN",
    "refinancing_rate": "Lãi suất tái cấp vốn (NHNN; kỳ = ngày bắt đầu áp dụng)",
    "rediscount_rate": "Lãi suất tái chiết khấu (NHNN; kỳ = ngày bắt đầu áp dụng)",
    "interbank_overnight": "Lãi suất bình quân liên ngân hàng qua đêm",
    "interbank_1w": "Lãi suất bình quân liên ngân hàng 1 tuần",
    "interbank_2w": "Lãi suất bình quân liên ngân hàng 2 tuần",
    "interbank_1m": "Lãi suất bình quân liên ngân hàng 1 tháng",
    "interbank_3m": "Lãi suất bình quân liên ngân hàng 3 tháng",
    "gdp_yoy": "Tăng trưởng GDP quý so với cùng kỳ (NSO; kỳ = ngày đầu quý)",
    "cpi_mom": "CPI tháng so với tháng trước (NSO; kỳ = ngày đầu tháng)",
    "cpi_yoy": "CPI tháng so với cùng kỳ năm trước (NSO)",
    "cpi_vs_dec": "CPI tháng so với tháng 12 năm trước (NSO)",
    "cpi_avg_ytd_yoy": "CPI bình quân từ đầu năm so với cùng kỳ (NSO)",
    "core_inflation_avg_ytd_yoy": "Lạm phát cơ bản bình quân từ đầu năm so với cùng kỳ (NSO)",
    "fdi_registered_ytd": "Vốn FDI đăng ký lũy kế từ đầu năm (NSO)",
    "fdi_registered_ytd_yoy": "Vốn FDI đăng ký lũy kế, % so với cùng kỳ (NSO)",
    "fdi_disbursed_ytd": "Vốn FDI thực hiện lũy kế từ đầu năm (NSO)",
    "fdi_disbursed_ytd_yoy": "Vốn FDI thực hiện lũy kế, % so với cùng kỳ (NSO)",
    "gold_usd_oz": "Vàng thế giới, hợp đồng tương lai gần nhất (COMEX)",
    "brent_usd_bbl": "Dầu Brent, hợp đồng tương lai gần nhất",
    "wti_usd_bbl": "Dầu WTI, hợp đồng tương lai gần nhất",
    "copper_usd_lb": "Đồng, hợp đồng tương lai gần nhất (COMEX)",
    "iron_ore_usd_t": "Quặng sắt 62% Fe CFR Trung Quốc, tương lai gần nhất",
    "hrc_steel_usd_t": "Thép cuộn cán nóng HRC (Mỹ), tương lai gần nhất",
    "sjc_gold_buy": "Giá vàng miếng SJC mua vào (TP.HCM)",
    "sjc_gold_sell": "Giá vàng miếng SJC bán ra (TP.HCM)",
}
# (indicator whose latest period proves the source is current, max age, what to call it in a warning)
FRESHNESS = [
    ("usd_vnd_central", timedelta(days=4), "số liệu chính thức từ NHNN (tỷ giá, lãi suất)"),
    ("cpi_yoy", timedelta(days=70), "số liệu NSO (GDP, CPI, FDI)"),  # month M is published ~6th of M+1
    ("gold_usd_oz", timedelta(days=5), "giá hàng hóa thế giới"),
    ("sjc_gold_sell", timedelta(days=5), "giá vàng SJC"),
]


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
        entry = out.setdefault(indicator, {"label": LABELS.get(indicator, indicator), "unit": unit, "source": source,
                                           "source_url": url, "series": []})
        entry["series"].append({"period": period.isoformat(), "value": float(value)})
    return out


def _indicator_warnings(indicators: dict, now: datetime) -> list[str]:
    out = []
    for key, max_age, what in FRESHNESS:
        if key not in indicators:
            out.append(f"Chưa có {what}: không tự điền số")
            continue
        latest = datetime.fromisoformat(indicators[key]["series"][0]["period"]).replace(tzinfo=_VN_TZ)
        if now - latest > max_age:
            out.append(f"{what[0].upper()}{what[1:]} chưa cập nhật từ kỳ {latest:%d/%m/%Y}")
    return out
