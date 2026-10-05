from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest

import pipeline.collectors.rss as rss
from pipeline.news import ensure_news_partitions

FIX = Path(__file__).parent / "fixtures" / "rss"
NOW = datetime(2026, 10, 6, 3, 0, tzinfo=timezone.utc)  # Tue 10:00 VN
URL_A, URL_B = "http://cafef.test/vi-mo.rss", "http://cafef.test/ck.rss"
HTML_BLOCK = b"<html><head><title>Request Rejected</title></head><body>The requested URL was rejected.</body></html>"


def feed(name):
    return httpx.Response(200, content=(FIX / name).read_bytes(), headers={"content-type": "application/rss+xml"})


def srcs(*specs):
    return [{"name": n, "stream": s, "url": u, "every_minutes": 60, "enabled": True} for n, s, u in specs]


def mock_client(routes):
    calls = []

    def handler(request):
        calls.append(str(request.url))
        r = routes[str(request.url)]
        if callable(r):
            return r(request)
        return httpx.Response(r.status_code, content=r.content, headers=dict(r.headers))  # fresh object per request
    return httpx.Client(transport=httpx.MockTransport(handler)), calls


@pytest.fixture(autouse=True)
def _isolated(db_conn, monkeypatch):
    monkeypatch.setattr(rss, "_cache", {})
    monkeypatch.setattr(rss, "_last_hit", {})
    monkeypatch.setattr(rss, "_sleep", lambda s: None)
    monkeypatch.setattr(rss, "_clock", lambda: 0.0)
    ensure_news_partitions(db_conn, date(2026, 10, 6))

    def wipe():
        db_conn.execute("DELETE FROM news_items WHERE source LIKE 'test_%'")
        db_conn.execute("DELETE FROM source_health WHERE source LIKE 'test_%'")
        db_conn.commit()
    wipe()
    yield
    wipe()


def run(db_conn, monkeypatch, sources, client, now=NOW, alerts=None):
    monkeypatch.setattr(rss, "load_sources", lambda: sources)
    sink = alerts if alerts is not None else []
    return rss.run_collect_rss(db_conn, vn30=["FPT"], send=lambda m: sink.append(m) or True, client=client, now=now)


def rows(db_conn, source):
    return {u: (s, r, p, t) for u, s, r, p, t in db_conn.execute(
        "SELECT url, filter_status, filter_reason, pillars, tickers FROM news_items WHERE source = %s", (source,))}


def test_collects_filters_and_records_health(db_conn, monkeypatch):
    c, _ = mock_client({URL_A: feed("cafef_vi_mo.xml")})
    assert run(db_conn, monkeypatch, srcs(("test_cafef", "A", URL_A)), c) == {"ok": 1, "failed": 0, "stored": 5}
    got = rows(db_conn, "test_cafef")
    assert got["http://cafef.test/a1"] == ("kept", None, ["tien_te"], [])
    assert got["http://cafef.test/a2"][:2] == ("dropped", "exclude:giá vàng nhẫn hôm nay")
    assert got["http://cafef.test/a3"][:2] == ("dropped", "no_keyword")
    assert got["http://cafef.test/a4"][0] == "kept" and got["http://cafef.test/a4"][3] == ["FPT"]
    failures, last_item = db_conn.execute(
        "SELECT consecutive_failures, last_item_at FROM source_health WHERE source = 'test_cafef'").fetchone()
    # a4 has no pubDate (fetch time) and a5 is dated in the future: neither may push last_item_at past `now`
    assert failures == 0 and last_item == NOW


def test_same_url_in_two_feeds_is_one_row_and_a_reposted_title_is_a_duplicate(db_conn, monkeypatch):
    c, _ = mock_client({URL_A: feed("cafef_vi_mo.xml"), URL_B: feed("cafef_chung_khoan.xml")})
    both = srcs(("test_cafef", "A", URL_A), ("test_ck", "B", URL_B))
    assert run(db_conn, monkeypatch, both, c)["stored"] == 7  # 5 + b1 + b2 (a1 merged, not stored)
    assert db_conn.execute("SELECT count(*) FROM news_items WHERE url = 'http://cafef.test/a1'").fetchone()[0] == 1
    b2 = rows(db_conn, "test_ck")["http://cafef.test/b2"]
    assert b2[0] == "dropped" and b2[1].startswith("duplicate_title:")
    assert rows(db_conn, "test_ck")["http://cafef.test/b1"][3] == ["FPT"]
    again = run(db_conn, monkeypatch, both, c, now=NOW + timedelta(minutes=61))
    assert again["stored"] == 0


def test_a_200_html_block_page_is_a_failure_not_an_empty_feed(db_conn, monkeypatch):
    c, _ = mock_client({URL_A: feed("cafef_vi_mo.xml"),
                        URL_B: httpx.Response(200, content=HTML_BLOCK, headers={"content-type": "text/html"})})
    result = run(db_conn, monkeypatch, srcs(("test_cafef", "A", URL_A), ("test_waf", "B", URL_B)), c)
    assert result["ok"] == 1 and result["failed"] == 1  # the other source still ran
    failures, error = db_conn.execute(
        "SELECT consecutive_failures, last_error FROM source_health WHERE source = 'test_waf'").fetchone()
    assert failures == 1 and "not a valid feed" in error


def test_429_is_not_retried_but_5xx_is_retried_twice_with_backoff(db_conn, monkeypatch):
    sleeps = []
    monkeypatch.setattr(rss, "_sleep", sleeps.append)
    c, calls = mock_client({URL_A: httpx.Response(429)})
    run(db_conn, monkeypatch, srcs(("test_429", "A", URL_A)), c)
    assert len(calls) == 1 and "429" in db_conn.execute(
        "SELECT last_error FROM source_health WHERE source = 'test_429'").fetchone()[0]
    rss._last_hit.clear()  # same domain as above: forget it, or the 2 s gap sleep lands in `sleeps` too
    sleeps.clear()
    c, calls = mock_client({URL_B: httpx.Response(503)})
    run(db_conn, monkeypatch, srcs(("test_503", "A", URL_B)), c)
    assert len(calls) == 3 and sleeps == [1, 2]


def test_requests_to_the_same_domain_are_spaced_two_seconds(db_conn, monkeypatch):
    sleeps = []
    monkeypatch.setattr(rss, "_sleep", sleeps.append)
    c, _ = mock_client({URL_A: feed("cafef_vi_mo.xml"), URL_B: feed("cafef_chung_khoan.xml")})
    run(db_conn, monkeypatch, srcs(("test_cafef", "A", URL_A), ("test_ck", "B", URL_B)), c)
    assert sleeps == [2.0]


def test_a_source_polled_recently_is_skipped(db_conn, monkeypatch):
    db_conn.execute("INSERT INTO source_health (source, last_ok_at) VALUES ('test_cafef', %s)",
                    (NOW - timedelta(minutes=10),))
    db_conn.commit()
    c, calls = mock_client({URL_A: feed("cafef_vi_mo.xml")})
    assert run(db_conn, monkeypatch, srcs(("test_cafef", "A", URL_A)), c) == {"ok": 0, "failed": 0, "stored": 0}
    assert calls == []


def test_conditional_get_headers_are_sent_after_the_first_fetch(db_conn, monkeypatch):
    seen = []

    def handler(request):
        seen.append(request.headers.get("if-none-match"))
        return httpx.Response(200, content=(FIX / "cafef_vi_mo.xml").read_bytes(), headers={"etag": '"v1"'})
    c = httpx.Client(transport=httpx.MockTransport(handler))
    run(db_conn, monkeypatch, srcs(("test_cafef", "A", URL_A)), c)
    run(db_conn, monkeypatch, srcs(("test_cafef", "A", URL_A)), c, now=NOW + timedelta(minutes=61))
    assert seen == [None, '"v1"']


def test_a_silent_source_alerts_once_across_runs(db_conn, monkeypatch):
    db_conn.execute("INSERT INTO source_health (source, last_item_at, first_seen_at) VALUES ('test_cafef', %s, %s)",
                    (NOW - timedelta(days=5), NOW - timedelta(days=5)))
    db_conn.commit()
    c, _ = mock_client({URL_A: httpx.Response(503)})
    alerts = []
    run(db_conn, monkeypatch, srcs(("test_cafef", "A", URL_A)), c, alerts=alerts)
    run(db_conn, monkeypatch, srcs(("test_cafef", "A", URL_A)), c, now=NOW + timedelta(minutes=61), alerts=alerts)
    assert len([a for a in alerts if "test_cafef" in a]) == 1


def test_a_frozen_feed_with_undated_items_does_not_look_fresh(db_conn, monkeypatch):
    c, _ = mock_client({URL_A: feed("cafef_vi_mo.xml")})  # a4 has no pubDate: its time is "now" on every poll
    run(db_conn, monkeypatch, srcs(("test_cafef", "A", URL_A)), c)
    run(db_conn, monkeypatch, srcs(("test_cafef", "A", URL_A)), c, now=NOW + timedelta(minutes=61))  # nothing new
    last_item = db_conn.execute("SELECT last_item_at FROM source_health WHERE source = 'test_cafef'").fetchone()[0]
    assert last_item == NOW  # only items that were actually new may advance last_item_at


def test_a_future_pubdate_is_stored_as_fetch_time_and_never_breaks_the_source(db_conn, monkeypatch):
    far = ('<?xml version="1.0"?><rss version="2.0"><channel><title>t</title><link>http://x</link>'
           '<description>d</description><item><title>Chính sách thuế mới</title><link>http://cafef.test/far</link>'
           '<pubDate>Mon, 01 Jan 2062 00:00:00 GMT</pubDate></item></channel></rss>').encode()
    c, _ = mock_client({URL_A: httpx.Response(200, content=far, headers={"content-type": "application/rss+xml"})})
    assert run(db_conn, monkeypatch, srcs(("test_cafef", "A", URL_A)), c) == {"ok": 1, "failed": 0, "stored": 1}
    got = db_conn.execute("SELECT published_at FROM news_items WHERE url = 'http://cafef.test/far'").fetchone()[0]
    assert got == NOW  # not 2062: no partition for that, and it would sit on top of every pillar list


def test_the_etag_is_only_remembered_after_the_items_were_stored(db_conn, monkeypatch):
    c, _ = mock_client({URL_A: feed("cafef_vi_mo.xml")})
    real_store = rss.store_news_item

    def broken(*a, **k):
        raise RuntimeError("db hiccup")
    monkeypatch.setattr(rss, "store_news_item", broken)
    assert run(db_conn, monkeypatch, srcs(("test_cafef", "A", URL_A)), c)["failed"] == 1
    assert URL_A not in rss._cache  # a remembered ETag would turn the retry into a 304 and lose these items
    monkeypatch.setattr(rss, "store_news_item", real_store)
    assert run(db_conn, monkeypatch, srcs(("test_cafef", "A", URL_A)), c, now=NOW + timedelta(minutes=61))["stored"] == 5
