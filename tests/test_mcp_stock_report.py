import json
from datetime import date

import pytest

from db.connection import get_conn
from mcp_server.tools.stock_report import get_stock_report_tool, save_commentary_tool, verify_commentary

TICKER, RUN = "RPTCMT", "test:RPTCMT:1"
# ~100 words, no figures: always passes the verifier
GOOD = ("Cổ phiếu nên được theo dõi thêm trước khi giải ngân vì xu hướng kỹ thuật còn đi ngang và động lượng "
        "ngắn hạn đang yếu dần trong khi thị trường chung chưa ủng hộ. Nền tảng sinh lời vẫn tốt nên rủi ro "
        "giảm sâu không lớn, nhưng chưa có tín hiệu xác nhận dòng tiền quay lại. Nhà đầu tư nên chờ giá vượt "
        "vùng kháng cự với khối lượng cải thiện rõ rệt, đồng thời theo dõi diễn biến chung của chỉ số. Nếu giá "
        "đánh mất vùng hỗ trợ gần thì quan điểm thận trọng càng được củng cố và nên tiếp tục đứng ngoài quan sát "
        "thêm một thời gian để tránh rủi ro không cần thiết cho danh mục.")
SNAP = {
    "technical": {"trend": "sideways", "ma20": 100.0, "ma50": 98.0, "ma200": 99.0, "rsi14": 53.0, "macd": 1.0,
                  "macd_signal": 2.0, "bollinger_upper": 104.0, "bollinger_lower": 96.0, "atr14": 2.0,
                  "support": [90.0], "resistance": [110.0], "volume_avg20": 1000.0, "volume_anomaly": False},
    "fundamental": {"industry_group": "Retail", "metrics": {"pe": 10.0, "pb": 2.0, "roe": 0.2}},
    "risk_plan": {"entry_zone": [99.0, 101.0], "stop_loss": 96.0, "target": 108.0, "rr": 2.0, "warnings": []},
    "confidence": 0.7, "weight_coverage": 1.0, "action_label": "watch", "warnings": [],
    "market": {"close": 1700.0, "change_pct": -0.5, "ma20": 1750.0, "ma50": 1740.0, "ma200": 1790.0,
               "rsi14": 40.0, "trend": "down", "regime": "risk_off", "as_of": "2026-10-02"},
}


def _run(path, run_id, snap):
    path.write_text(json.dumps(snap))
    with get_conn() as conn:
        conn.execute("DELETE FROM runs WHERE run_id = %s", (run_id,))
        conn.execute(
            "INSERT INTO runs (run_id, mode, tickers, style, depth, as_of, snapshot_ref, warnings)"
            " VALUES (%s, 'scheduled_pre', ARRAY[%s], 'long', 'quick', now(), %s, '[]')", (run_id, TICKER, str(path)))


@pytest.fixture
def seeded(tmp_path):
    with get_conn() as conn:
        conn.execute("INSERT INTO tickers (ticker, name) VALUES (%s, 'Test Retail') ON CONFLICT DO NOTHING", (TICKER,))
        conn.execute("INSERT INTO prices_daily (ticker, trade_date, open, high, low, close, volume, source, fetched_at)"
                     " VALUES (%s, %s, 100, 101, 99, 100, 900, 't', now()), (%s, %s, 99, 100, 98, 99, 800, 't', now())",
                     (TICKER, date(2026, 10, 2), TICKER, date(2026, 10, 1)))
    _run(tmp_path / "a.json", RUN, SNAP)
    yield tmp_path
    with get_conn() as conn:
        conn.execute("DELETE FROM runs WHERE run_id LIKE 'test:RPTCMT:%'")
        conn.execute("DELETE FROM report_commentary WHERE ticker = %s", (TICKER,))
        conn.execute("DELETE FROM prices_daily WHERE ticker = %s", (TICKER,))
        conn.execute("DELETE FROM tickers WHERE ticker = %s", (TICKER,))


def test_commentary_is_written_once_then_replayed_until_data_changes(seeded):
    first = get_stock_report_tool(TICKER, RUN)["data"]
    assert first["status"] == "ok" and first["needs_commentary"] is True and "final" not in first
    assert "**1. Kỹ thuật**" in first["report"] and "Test Retail" in first["report"]

    assert save_commentary_tool(TICKER, RUN, GOOD)["data"]["status"] == "saved"
    hit = get_stock_report_tool(TICKER, RUN)["data"]
    assert hit["needs_commentary"] is False and GOOD in hit["final"]
    assert hit["final"].startswith(first["report"])

    # new run, same numbers (only as_of / LLM text differ) → still replayed
    _run(seeded / "b.json", "test:RPTCMT:2", {**SNAP, "synthesis": {"x": "other llm text"}})
    assert get_stock_report_tool(TICKER, "test:RPTCMT:2")["data"]["needs_commentary"] is False

    # a number changes → commentary must be rewritten
    _run(seeded / "c.json", "test:RPTCMT:3", {**SNAP, "technical": {**SNAP["technical"], "rsi14": 61.0}})
    assert get_stock_report_tool(TICKER, "test:RPTCMT:3")["data"]["needs_commentary"] is True
    # ... and so does a VN-Index move
    _run(seeded / "d.json", "test:RPTCMT:4", {**SNAP, "market": {**SNAP["market"], "close": 1650.0}})
    assert get_stock_report_tool(TICKER, "test:RPTCMT:4")["data"]["needs_commentary"] is True


def test_old_template_version_commentary_is_not_replayed_and_bad_input_rejected(seeded):
    save_commentary_tool(TICKER, RUN, GOOD)
    with get_conn() as conn:
        conn.execute("UPDATE report_commentary SET template_version = 'v0' WHERE ticker = %s", (TICKER,))
    assert get_stock_report_tool(TICKER, RUN)["data"]["needs_commentary"] is True
    assert get_stock_report_tool(TICKER, "no:such:run")["data"]["status"] == "not_found"
    assert save_commentary_tool(TICKER, "no:such:run", "x")["data"]["status"] == "not_saved"
    with pytest.raises(ValueError):
        save_commentary_tool(TICKER, RUN, "  ")


def test_verifier_rejects_invented_numbers_bullish_tone_internal_terms_and_bad_length(seeded):
    report = get_stock_report_tool(TICKER, RUN)["data"]["report"]
    assert verify_commentary(GOOD, report, "watch") == []
    # a figure that is in the report passes; MA20/RSI(14)/2026-Q2/small counts are not data
    assert verify_commentary(GOOD + " Giá 100 vẫn dưới MA20, RSI(14) trung tính, kỳ 2026-Q2, 3 phiên.", report, "watch") == []

    invented = verify_commentary(GOOD + " Mục tiêu hợp lý là 125 trong quý tới.", report, "watch")
    assert len(invented) == 1 and "125" in invented[0]
    bullish = verify_commentary(GOOD.replace("theo dõi thêm", "mua vào"), report, "watch")
    assert any("Lạc quan hơn nhãn" in i for i in bullish)
    assert verify_commentary(GOOD.replace("theo dõi thêm", "mua vào"), report, "buy_accumulate") == []
    assert any("nội bộ" in i for i in verify_commentary(GOOD + " Theo composite score.", report, "watch"))
    assert any("Độ dài" in i for i in verify_commentary("Quá ngắn.", report, "watch"))


def test_rejected_commentary_is_never_cached(seeded):
    out = save_commentary_tool(TICKER, RUN, GOOD + " Mục tiêu 999.")["data"]
    assert out["status"] == "rejected" and out["issues"]
    assert get_stock_report_tool(TICKER, RUN)["data"]["needs_commentary"] is True


def test_report_news_rows_exclude_dropped_items_but_keep_untagged_vnstock_news():
    from datetime import datetime, timedelta, timezone
    from mcp_server.connection import get_ro_conn
    from mcp_server.tools.stock_report import _news_rows
    from pipeline.news import ensure_news_partitions

    now = datetime.now(timezone.utc)
    with get_conn() as conn:
        ensure_news_partitions(conn, date.today())
        conn.execute("DELETE FROM news_items WHERE source LIKE 'test_%'")
        for url, status in (("http://t/n1", "kept"), ("http://t/n2", "dropped"), ("http://t/n3", None)):
            conn.execute(
                "INSERT INTO news_items (published_at, tickers, source, url, url_hash, title, fetched_at, filter_status)"
                " VALUES (%s, ARRAY['TSTNEWS'], 'test_n', %s, %s, %s, %s, %s)",
                (now - timedelta(hours=1), url, url, f"title {url}", now, status))
    try:
        with get_ro_conn() as conn:
            titles = {r[1] for r in _news_rows(conn, "TSTNEWS")}
        assert titles == {"title http://t/n1", "title http://t/n3"}
    finally:
        with get_conn() as conn:
            conn.execute("DELETE FROM news_items WHERE source LIKE 'test_%'")
