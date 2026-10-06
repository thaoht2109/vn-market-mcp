"""SBV (sbv.gov.vn) official numbers: USD/VND central + reference rates, policy rates, interbank rates.

Two static HTML pages, parsed with regexes on tag-stripped text (see the SBV spike findings). Runs inside
the hourly collect_rss job, at most every SBV_EVERY_MINUTES on weekdays; health goes to source_health 'sbv'.
"""
from __future__ import annotations

import html
import re
from datetime import date, datetime, timedelta, timezone

import httpx

from ops.alerting import log_event
from pipeline.collectors.rss import TIMEOUT_S, USER_AGENT, FeedError
from pipeline.news_health import VN, record_failure, record_ok

SOURCE = "sbv"
SBV_EVERY_MINUTES = 180  # the central rate is published in the morning; a few polls a day is plenty
RATES_URL = "https://sbv.gov.vn/t%E1%BB%B7-gi%C3%A1"
INTEREST_URL = "https://sbv.gov.vn/l%C3%A3i-su%E1%BA%A5t1"
_D = r"(\d{1,2}/\d{1,2}/\d{4})"
_TERMS = {"Qua đêm": "overnight", "1 Tuần": "1w", "2 Tuần": "2w", "1 Tháng": "1m", "3 Tháng": "3m",
          "6 Tháng": "6m", "9 Tháng": "9m"}


def _text(page: str) -> str:
    page = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", page)
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", page)))


def _num(s: str) -> float:  # '25.643' -> 25643, '4,500' -> 4.5
    return float(s.replace(".", "").replace(",", "."))


def _day(s: str) -> date:
    return datetime.strptime(s, "%d/%m/%Y").date()


def parse_rates(page: str) -> list[tuple[str, date, float, str]]:
    t = _text(page)
    m = re.search(rf"áp dụng cho ngày {_D} như sau:.*?1 Đô la Mỹ = ([\d.]+) VND", t)
    if not m:
        raise FeedError("SBV rates page: central USD/VND rate not found")
    out = [("usd_vnd_central", _day(m[1]), _num(m[2]), "VND")]
    m = re.search(rf"Tỷ giá áp dụng cho ngày {_D}.*?USD Đô la Mỹ ([\d.]+,\d+) ([\d.]+,\d+)", t)
    if m:
        out += [("usd_vnd_ref_buy", _day(m[1]), _num(m[2]), "VND"),
                ("usd_vnd_ref_sell", _day(m[1]), _num(m[3]), "VND")]
    return out


def parse_interest(page: str) -> list[tuple[str, date, float, str]]:
    t = _text(page)
    out = []
    for label, key in (("tái chiết khấu", "rediscount_rate"), ("tái cấp vốn", "refinancing_rate")):
        m = re.search(rf"Lãi suất {label} ([\d,]+)% \S+ ngày {_D} {_D}", t)
        if m:
            out.append((key, _day(m[3]), _num(m[1]), "%/năm"))
    m = re.search(rf"liên ngân hàng Ngày áp dụng: {_D}(.*?)Ghi chú", t)
    if m:
        day = _day(m[1])
        for label, key in _TERMS.items():
            # '(*)'/'(**)' marks a value carried over from an older day: skip it rather than mis-date it
            v = re.search(rf"{label} (\d+,\d+)(?![\d,]| \(\*)", m[2])
            if v:
                out.append((f"interbank_{key}", day, _num(v[1]), "%/năm"))
    if not out:
        raise FeedError("SBV interest page: no rate found")
    return out


def _get(client: httpx.Client, url: str) -> str:
    resp = client.get(url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT_S, follow_redirects=True)
    if resp.status_code != 200:
        raise FeedError(f"HTTP {resp.status_code}")
    if "Request Rejected" in resp.text[:2000]:  # WAF block page served with 200
        raise FeedError("blocked by WAF (Request Rejected)")
    return resp.text


def _due(conn, now: datetime) -> bool:
    if now.astimezone(VN).weekday() >= 5:
        return False
    row = conn.execute("SELECT last_ok_at FROM source_health WHERE source = %s", (SOURCE,)).fetchone()
    return row is None or row[0] is None or now - row[0] >= timedelta(minutes=SBV_EVERY_MINUTES - 5)


def run_collect_sbv(conn, *, client: httpx.Client | None = None, now: datetime | None = None) -> int:
    """Returns the number of rows written (new or updated). Never raises: failures land in source_health."""
    now = now or datetime.now(timezone.utc)
    if not _due(conn, now):
        return 0
    own_client = client is None
    client = client or httpx.Client()
    try:
        rows = [(*r, RATES_URL) for r in parse_rates(_get(client, RATES_URL))]
        rows += [(*r, INTEREST_URL) for r in parse_interest(_get(client, INTEREST_URL))]
        with conn.transaction():
            for indicator, period, value, unit, url in rows:
                conn.execute(
                    "INSERT INTO macro_indicators (indicator, period, value, unit, source, source_url, fetched_at)"
                    " VALUES (%s, %s, %s, %s, %s, %s, %s) ON CONFLICT (indicator, period, source)"
                    " DO UPDATE SET value = EXCLUDED.value, fetched_at = EXCLUDED.fetched_at",
                    (indicator, period, value, unit, SOURCE, url, now),
                )
        # policy rates carry years-old dates; freshness follows the daily series only
        newest = max(p for k, p, *_ in rows if k.startswith(("usd_", "interbank_")))
        record_ok(conn, SOURCE, datetime.combine(newest, datetime.min.time(), VN), now)
        conn.commit()
        return len(rows)
    except Exception as exc:  # a broken SBV page must not take the RSS collection down with it
        conn.rollback()
        record_failure(conn, SOURCE, f"{type(exc).__name__}: {exc}", now)
        conn.commit()
        log_event("collect_sbv_failed", error=str(exc))
        return 0
    finally:
        if own_client:
            client.close()
