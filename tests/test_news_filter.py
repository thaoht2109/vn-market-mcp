import unicodedata
from datetime import date, datetime, timedelta, timezone

import pytest

from pipeline.news import ensure_news_partitions, store_news_item
from pipeline.news_filter import (
    classify, get_vn30, is_duplicate_title, load_aliases, load_keywords, refilter,
)

KEYWORDS, ALIASES, VN30 = load_keywords(), load_aliases(), {"FPT", "VCB", "TCB"}


def run(title, summary="", stream="A"):
    return classify(title, summary, stream, VN30, ALIASES, KEYWORDS)


PILLAR_SAMPLES = [
    ("tien_te", "NHNN giảm lãi suất điều hành 0,5 điểm %"),
    ("tien_te", "Thị trường liên ngân hàng: lãi suất qua đêm tăng mạnh"),
    ("tien_te", "Tín dụng tăng 8% từ đầu năm"),
    ("ty_gia_doi_ngoai", "Tỷ giá USD/VND vượt 26.000"),
    ("ty_gia_doi_ngoai", "Dự trữ ngoại hối của Việt Nam đạt mức cao"),
    ("ty_gia_doi_ngoai", "Xuất khẩu tháng 9 tăng 12%"),
    ("tang_truong", "GDP quý III tăng 7,8%"),
    ("tang_truong", "PMI ngành sản xuất vượt ngưỡng 50"),
    ("tang_truong", "Giải ngân vốn đầu tư công đạt 60%"),
    ("lam_phat", "CPI tháng 9 tăng 0,3%"),
    ("lam_phat", "Lạm phát bình quân 9 tháng đạt 3,2%"),
    ("lam_phat", "Giá xăng điều chỉnh giảm từ chiều nay"),
    ("tai_khoa_chinh_sach", "Chính phủ ban hành nghị quyết mới về thuế"),
    ("tai_khoa_chinh_sach", "Bộ Tài chính phát hành trái phiếu chính phủ"),
    ("tai_khoa_chinh_sach", "Thông tư mới siết trái phiếu doanh nghiệp"),
    ("thi_truong_von", "Việt Nam được nâng hạng thị trường mới nổi"),
    ("thi_truong_von", "Khối ngoại bán ròng phiên thứ 5 liên tiếp"),
    ("thi_truong_von", "UBCK đưa ra quy định mới về margin"),
    ("toan_cau", "Fed giữ nguyên lãi suất tại cuộc họp FOMC"),
    ("toan_cau", "Giá dầu giảm sâu"),
    ("toan_cau", "Lợi suất trái phiếu Mỹ lên cao nhất 15 năm"),
]


@pytest.mark.parametrize("pillar,title", PILLAR_SAMPLES)
def test_every_pillar_sample_is_kept_under_its_pillar(pillar, title):
    r = run(title)
    assert r.status == "kept" and pillar in r.pillars


def test_exclude_phrase_beats_a_pillar_match():
    r = run("Giá vàng nhẫn hôm nay tăng, tỷ giá USD/VND đi ngang")
    assert (r.status, r.reason) == ("dropped", "exclude:giá vàng nhẫn hôm nay")


def test_no_keyword_and_no_ticker_reasons_depend_on_stream():
    assert run("Cuối tuần nhiều nơi nắng đẹp", stream="A").reason == "no_keyword"
    assert run("Công ty ABC ký hợp đồng mới", stream="B").reason == "no_ticker"


def test_ticker_and_alias_matching_is_limited_to_vn30():
    assert run("FPT ký hợp đồng mới với đối tác Nhật", stream="B").tickers == ["FPT"]
    assert run("Techcombank báo lãi quý III tăng mạnh", stream="B").tickers == ["TCB"]
    assert run("ABC ký hợp đồng mới", stream="B").status == "dropped"  # not VN30


def test_a_macro_story_without_a_ticker_on_the_stock_feed_is_kept():
    r = run("Khối ngoại bán ròng 500 tỷ đồng", stream="B")
    assert r.status == "kept" and r.tickers == [] and "thi_truong_von" in r.pillars


def test_decomposed_unicode_titles_still_match():
    nfd = unicodedata.normalize("NFD", "Tỷ giá USD/VND tăng mạnh")
    assert nfd != "Tỷ giá USD/VND tăng mạnh"
    assert "ty_gia_doi_ngoai" in run(nfd).pillars


def test_keywords_match_whole_words_only():
    assert run("Federer vô địch giải quần vợt").pillars == []  # "Fed" is not a word in "Federer"


def test_duplicate_title_threshold():
    recent = [("h1", "NHNN giảm lãi suất điều hành, thị trường liên ngân hàng hạ nhiệt")]
    assert is_duplicate_title("NHNN giảm lãi suất điều hành, thị trường liên ngân hàng hạ nhiệt!", recent) == "h1"
    assert is_duplicate_title("Tỷ giá USD/VND vượt 26.000", recent) is None


def test_refilter_reapplies_current_rules_to_stored_rss_items(db_conn):
    ensure_news_partitions(db_conn, date.today())
    now = datetime.now(timezone.utc)
    db_conn.execute("DELETE FROM news_items WHERE source = 'test_refilter'")
    db_conn.commit()

    def put(url, title, hours):
        store_news_item(db_conn, source="test_refilter", url=url, title=title, summary=None,
                        published_at=now - timedelta(hours=hours), fetched_at=now, tickers=[], pillars=[],
                        stream="A", filter_status="dropped", filter_reason="stale-rule")
    try:
        put("http://t/r1", "Tỷ giá USD/VND vượt 26.000", 3)
        put("http://t/r2", "Tỷ giá USD/VND vượt 26.000!", 2)   # near-identical to r1 → duplicate
        put("http://t/r3", "Cuối tuần nắng đẹp", 1)
        stats = refilter(db_conn, days=2)
        rows = {u: (s, r) for u, s, r in db_conn.execute(
            "SELECT url, filter_status, filter_reason FROM news_items WHERE source = 'test_refilter'")}
        assert rows["http://t/r1"] == ("kept", None)
        assert rows["http://t/r2"][0] == "dropped" and rows["http://t/r2"][1].startswith("duplicate_title:")
        assert rows["http://t/r3"] == ("dropped", "no_keyword")
        assert stats["rows"] >= 3 and isinstance(get_vn30(db_conn), set)
    finally:
        db_conn.execute("DELETE FROM news_items WHERE source = 'test_refilter'")
        db_conn.commit()
