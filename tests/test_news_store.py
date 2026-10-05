from datetime import date, datetime, timedelta, timezone

import psycopg
import pytest

from pipeline.news import ensure_news_partitions, item_hash, load_sources, store_news_item


@pytest.fixture(autouse=True)
def _wipe(db_conn):
    # store_news_item's transaction() block commits when the connection is idle (it is only a savepoint
    # inside an open transaction), so these rows outlive the test unless wiped.
    def wipe():
        db_conn.execute("DELETE FROM news_items WHERE source LIKE 'test_%'")
        db_conn.commit()
    wipe()
    yield
    wipe()


def _kw(**over):
    p = datetime(2026, 10, 6, 1, 0, tzinfo=timezone.utc)
    base = dict(source="test_a", url="http://t/x1", title="t1", summary=None, published_at=p, fetched_at=p,
                tickers=[], pillars=[], stream="A", filter_status="kept", filter_reason=None)
    base.update(over)
    return base


def test_load_sources_returns_only_enabled_sources_with_required_keys():
    sources = load_sources()
    assert sources and all({"name", "stream", "url", "every_minutes"} <= s.keys() for s in sources)
    assert all(s["stream"] in ("A", "B") for s in sources)


def test_ensure_news_partitions_covers_months_across_a_year_end(db_conn):
    ensure_news_partitions(db_conn, date(2026, 11, 20))
    assert ensure_news_partitions(db_conn, date(2026, 11, 20)) == []  # idempotent
    for name in ("news_items_2026_11", "news_items_2026_12", "news_items_2027_01", "news_items_2027_02"):
        assert db_conn.execute("SELECT to_regclass(%s)", (name,)).fetchone()[0] is not None
    jan = datetime(2027, 1, 15, 3, 0, tzinfo=timezone.utc)
    assert store_news_item(db_conn, **_kw(url="http://t/jan", published_at=jan, fetched_at=jan)) is True


def test_store_dedupes_by_url_across_sources_and_merges_tickers_and_pillars(db_conn):
    ensure_news_partitions(db_conn, date(2026, 10, 6))
    first = _kw(pillars=["tien_te"])
    assert store_news_item(db_conn, **first) is True
    later = _kw(source="test_b", tickers=["VCB"], published_at=first["published_at"] + timedelta(hours=2))
    assert store_news_item(db_conn, **later) is False  # same URL, other feed, other pub time
    rows = db_conn.execute("SELECT tickers, pillars FROM news_items WHERE url_hash = %s",
                           (item_hash("http://t/x1", "t1", first["published_at"]),)).fetchall()
    assert rows == [(["VCB"], ["tien_te"])]


def test_store_without_url_falls_back_to_title_and_day(db_conn):
    ensure_news_partitions(db_conn, date(2026, 10, 6))
    assert store_news_item(db_conn, **_kw(url=None, title="no link")) is True
    assert store_news_item(db_conn, **_kw(url=None, title="no link")) is False


def test_missing_partition_raises_instead_of_being_swallowed(db_conn):
    far = datetime(2031, 1, 1, tzinfo=timezone.utc)  # no partition (test_ingest relies on this too)
    with pytest.raises(psycopg.Error):
        store_news_item(db_conn, **_kw(url="http://t/far", published_at=far, fetched_at=far))
    db_conn.execute("SELECT 1")  # savepoint rolled back: the caller's transaction is still usable
