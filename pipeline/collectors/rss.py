"""RSS collector for streams A (macro) and B (company): fetch politely, filter, store everything.

Per-source isolation: one failing feed is recorded in source_health and never stops the others. A 200
response that is not a valid feed (e.g. a WAF block page) counts as a failure, not as "no news".
"""
from __future__ import annotations

import html
import re
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import feedparser
import httpx

from ops.alerting import log_event
from pipeline.news import item_hash, load_sources, store_news_item
from pipeline.news_filter import DUPLICATE_WINDOW, classify, is_duplicate_title, load_aliases, load_keywords
from pipeline.news_health import check_and_alert, record_failure, record_ok

USER_AGENT = "vn-market-mcp/1.0 (personal research tool)"
TIMEOUT_S = 15.0
MIN_GAP_S = 2.0          # between two requests to the same domain
RETRIES = 2              # network errors and 5xx only
DUE_SLACK = timedelta(minutes=5)  # an hourly job must still poll a source whose last poll was ~59.5 min ago

_cache: dict[str, dict] = {}      # url -> {"etag", "modified"}; lost on restart (one extra full fetch)
_last_hit: dict[str, float] = {}  # domain -> monotonic time of the last request
_sleep = time.sleep
_clock = time.monotonic


class FeedError(Exception):
    pass


def _get(client: httpx.Client, url: str) -> httpx.Response:
    domain = urlparse(url).netloc
    wait = MIN_GAP_S - (_clock() - _last_hit.get(domain, -1e9))
    if wait > 0:
        _sleep(wait)
    cached = _cache.get(url, {})
    headers = {"User-Agent": USER_AGENT}
    if cached.get("etag"):
        headers["If-None-Match"] = cached["etag"]
    if cached.get("modified"):
        headers["If-Modified-Since"] = cached["modified"]
    last: Exception | None = None
    for attempt in range(RETRIES + 1):
        _last_hit[domain] = _clock()
        try:
            resp = client.get(url, headers=headers, timeout=TIMEOUT_S, follow_redirects=True)
        except httpx.HTTPError as exc:
            last = exc
        else:
            if resp.status_code in (200, 304):
                return resp
            if resp.status_code < 500:  # 403/429/404: retrying only gets us blocked
                raise FeedError(f"HTTP {resp.status_code}")
            last = FeedError(f"HTTP {resp.status_code}")
        if attempt < RETRIES:
            _sleep(2 ** attempt)
    raise FeedError(str(last))


def parse_entries(content: bytes, fetched_at: datetime) -> list[dict]:
    feed = feedparser.parse(content)
    if not feed.entries and not feed.version:  # no feed at all, e.g. an HTML block page served with 200
        raise FeedError(f"not a valid feed: {feed.get('bozo_exception')!r}")
    items = []
    for e in feed.entries:
        title = html.unescape((e.get("title") or "").strip())
        if not title:
            continue
        stamp = e.get("published_parsed") or e.get("updated_parsed")
        summary = html.unescape(re.sub(r"<[^>]+>", " ", e.get("summary") or ""))
        items.append({
            "title": title,
            "url": e.get("link") or None,
            "summary": re.sub(r"\s+", " ", summary).strip() or None,
            # no date, or a date in the future (a typo, e.g. year 2062 has no partition and would sit on top of
            # every pillar list): the fetch time is the best honest answer
            "published_at": min(datetime(*stamp[:6], tzinfo=timezone.utc), fetched_at) if stamp else fetched_at,
        })
    return items


def _recent_kept(conn, published_at: datetime, exclude_hash: str) -> list[tuple[str, str]]:
    return conn.execute(
        "SELECT url_hash, title FROM news_items WHERE filter_status = 'kept'"
        " AND published_at BETWEEN %s AND %s AND url_hash <> %s",
        (published_at - DUPLICATE_WINDOW, published_at + DUPLICATE_WINDOW, exclude_hash),
    ).fetchall()


def collect_source(conn, client, source: dict, *, vn30: set[str], aliases, keywords, now: datetime):
    """Returns (stored, newest_published_at_of_new_items, cache_entry). Raises on any source-level failure.
    The caller remembers `cache_entry` (ETag/Last-Modified) only after the items were committed: a remembered
    ETag whose items failed to store would turn the retry into a 304 and lose them."""
    resp = _get(client, source["url"])
    if resp.status_code == 304:
        return 0, None, None
    entries = parse_entries(resp.content, now)
    cache_entry = {"etag": resp.headers.get("etag"), "modified": resp.headers.get("last-modified")}
    stored, newest = 0, None
    for it in entries:
        h = item_hash(it["url"], it["title"], it["published_at"])
        res = classify(it["title"], it["summary"], source["stream"], vn30, aliases, keywords)
        status, reason = res.status, res.reason
        if status == "kept":
            dup = is_duplicate_title(it["title"], _recent_kept(conn, it["published_at"], h))
            if dup:
                status, reason = "dropped", f"duplicate_title:{dup}"
        inserted = store_news_item(
            conn, source=source["name"], url=it["url"], title=it["title"], summary=it["summary"],
            published_at=it["published_at"], fetched_at=now, tickers=res.tickers, pillars=res.pillars,
            stream=source["stream"], filter_status=status, filter_reason=reason,
        )
        if inserted:  # only NEW items prove the feed is alive: a frozen feed re-lists the same (or undated) ones
            stored += 1
            newest = it["published_at"] if newest is None else max(newest, it["published_at"])
    return stored, newest, cache_entry


def _due(conn, source: dict, now: datetime) -> bool:
    row = conn.execute("SELECT last_ok_at FROM source_health WHERE source = %s", (source["name"],)).fetchone()
    return row is None or row[0] is None or now - row[0] >= timedelta(minutes=source["every_minutes"]) - DUE_SLACK


def run_collect_rss(conn, *, vn30: list[str], send, client: httpx.Client | None = None,
                    now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    keywords, aliases, vn30_set = load_keywords(), load_aliases(), set(vn30)
    all_sources = load_sources()
    own_client = client is None
    client = client or httpx.Client()
    summary = {"ok": 0, "failed": 0, "stored": 0}
    try:
        for source in all_sources:
            if not _due(conn, source, now):
                continue
            try:
                with conn.transaction():
                    stored, newest, cache_entry = collect_source(conn, client, source, vn30=vn30_set,
                                                                 aliases=aliases, keywords=keywords, now=now)
                record_ok(conn, source["name"], newest, now)
                conn.commit()
                if cache_entry:
                    _cache[source["url"]] = cache_entry
                summary["ok"] += 1
                summary["stored"] += stored
            except Exception as exc:  # one bad source must not stop the others
                record_failure(conn, source["name"], f"{type(exc).__name__}: {exc}", now)
                conn.commit()
                summary["failed"] += 1
                log_event("collect_rss_source_failed", source=source["name"], error=str(exc))
    finally:
        if own_client:
            client.close()
    check_and_alert(conn, now, {s["name"] for s in all_sources}, send)
    conn.commit()
    return summary
