from datetime import date, datetime, timedelta, timezone

import pytest

import mcp_server.tools.macro as macro
from db.connection import get_conn
from pipeline.news import ensure_news_partitions, store_news_item


@pytest.fixture
def seeded(monkeypatch):
    monkeypatch.setattr(macro, "load_sources", lambda: [{"name": "test_fresh"}, {"name": "test_never"}])
    now = datetime.now(timezone.utc)
    with get_conn() as conn:
        ensure_news_partitions(conn, date.today())
        conn.execute("DELETE FROM news_items WHERE source LIKE 'test_%'")
        conn.execute("DELETE FROM source_health WHERE source LIKE 'test_%'")
        put = lambda url, title, status, pillars: store_news_item(
            conn, source="test_fresh", url=url, title=title, summary=None, published_at=now - timedelta(hours=1),
            fetched_at=now, tickers=[], pillars=pillars, stream="A", filter_status=status, filter_reason=None)
        put("http://t/m1", "NHNN giảm lãi suất điều hành", "kept", ["tien_te"])
        put("http://t/m2", "Tin bị loại", "dropped", ["tien_te"])
        conn.execute("INSERT INTO source_health (source, last_item_at, first_seen_at, last_ok_at) VALUES"
                     " ('test_fresh', %s, %s, %s)", (now - timedelta(hours=1),) * 3)
    yield
    with get_conn() as conn:
        conn.execute("DELETE FROM news_items WHERE source LIKE 'test_%'")
        conn.execute("DELETE FROM source_health WHERE source LIKE 'test_%'")


def test_macro_context_returns_kept_headlines_by_pillar_and_source_freshness(seeded):
    out = macro.get_macro_context_tool(days=7)
    pillars = out["data"]["pillars"]
    assert set(pillars) == {"tien_te", "ty_gia_doi_ngoai", "tang_truong", "lam_phat",
                            "tai_khoa_chinh_sach", "thi_truong_von", "toan_cau"}
    assert [h["title"] for h in pillars["tien_te"]] == ["NHNN giảm lãi suất điều hành"]  # dropped item excluded
    assert pillars["lam_phat"] == []  # empty pillar stays visible: "chưa ghi nhận tin", not "không có sự kiện"
    status = {s["source"]: s for s in out["data"]["news_sources"]}
    assert status["test_fresh"]["stale"] is False and status["test_never"]["stale"] is True
    assert any("test_never" in w for w in out["warnings"])


def test_days_is_clamped(seeded):
    assert macro.get_macro_context_tool(days=0)["data"]["pillars"]["tien_te"]
    assert macro.get_macro_context_tool(days=9999)["data"]["pillars"]["tien_te"]
