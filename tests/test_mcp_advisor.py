from datetime import date

import pandas as pd

from db.connection import get_conn
from mcp_server.tools.advisor import get_advisor_input_tool, playbook, save_advice_tool
from mcp_server.tools.stock_report import get_stock_report_tool
from ops.backtest_score import forward_excess, known_quarters, quarter_end
from tests.test_mcp_stock_report import GOOD, RUN, TICKER, seeded  # noqa: F401  (fixture)


def _clean():
    with get_conn() as conn:
        conn.execute("DELETE FROM advisor_views WHERE ticker = %s", (TICKER,))
        conn.execute("DELETE FROM positions WHERE ticker = %s", (TICKER,))


def test_advisor_sees_no_label_and_its_first_view_is_kept_next_to_the_label(seeded, monkeypatch):
    monkeypatch.setenv("VNMCP_USER_ID", "tadv")
    _clean()
    try:
        data = get_advisor_input_tool(TICKER, RUN)["data"]
        assert data["status"] == "ok" and data["stances"] == ["buy_accumulate", "watch", "stay_out"]
        assert "cố vấn tài chính" in data["advisor_task"]["goal"] and data["advisor_task"]["output_schema"]["required"]
        assert "get_advisor_input" in get_stock_report_tool(TICKER, RUN)["data"]["next_step"]
        assert "Kết luận" not in data["input"] and "theo dõi, chưa giải ngân" not in data["input"]
        assert "**1. Kỹ thuật**" in data["input"] and "Chưa nắm giữ mã này." in data["input"]
        assert "**Nguyên tắc của cố vấn**" in data["input"] and "Ngành chưa có khung riêng" in data["input"]  # no group

        assert save_advice_tool(TICKER, RUN, "hold", GOOD)["data"]["status"] == "rejected"  # not holding
        bad = save_advice_tool(TICKER, RUN, "stay_out", GOOD + " Mục tiêu 999.")["data"]
        assert bad["status"] == "rejected" and "999" in bad["issues"][0]

        saved = save_advice_tool(TICKER, RUN, "stay_out", GOOD)["data"]
        assert saved["status"] == "saved" and "đứng ngoài" in saved["final"]
        assert "Khác nhãn hệ thống (theo dõi, chưa giải ngân)" in saved["final"]
        again = save_advice_tool(TICKER, RUN, "watch", GOOD)["data"]
        assert "Khác nhãn hệ thống" in again["final"]  # first view wins
        assert get_advisor_input_tool(TICKER, RUN)["data"] == {"status": "advised", "final": saved["final"]}
        with get_conn() as conn:
            version = conn.execute("SELECT playbook_version FROM advisor_views WHERE ticker = %s", (TICKER,)).fetchone()[0]
        assert version == playbook(None)[1] and len(version) == 8

        monkeypatch.setenv("VNMCP_USER_ID", "tadv2")  # another user: own position, own view
        with get_conn() as conn:
            conn.execute("INSERT INTO positions (ticker, status, avg_cost, declared_by, declared_at)"
                         " VALUES (%s, 'holding', 95, 'tadv2', now())", (TICKER,))
        held = get_advisor_input_tool(TICKER, RUN)["data"]
        assert held["stances"] == ["hold", "reduce_exit"] and "giá vốn 95 → +5,3% so với giá 100" in held["input"]
    finally:
        _clean()


def test_playbook_gives_the_common_rules_plus_the_ticker_industry():
    banks, version = playbook("Banks")
    assert "Một mã không quá 20% danh mục" in banks and "nợ xấu" in banks and "Ngành chưa có khung riêng" not in banks
    assert "Ngành chưa có khung riêng" in playbook("Không có ngành này")[0] and playbook("Retail")[1] == version


def test_backtest_uses_only_published_quarters_and_excess_over_equal_weight_members():
    assert quarter_end("2025-Q4") == date(2025, 12, 31) and quarter_end("2026Q1") == date(2026, 3, 31)
    rows = [("2026-Q2", {"pe": 2}), ("2026-Q1", {"pe": 1})]
    assert known_quarters(rows, date(2026, 8, 13)) == [{"pe": 1}]  # Q2 counts from 14/08
    assert known_quarters(rows, date(2026, 8, 14)) == [{"pe": 2}, {"pe": 1}]

    days = [date(2026, 1, d) for d in (5, 6, 7)]
    px = lambda o, c: pd.DataFrame({"open": o, "close": c}, index=days)  # noqa: E731
    prices = {"A": px([10, 10, 10], [10, 10, 12]), "B": px([10, 10, 10], [10, 10, 10])}
    x = forward_excess(prices, ["A", "B"], days, 0, 2, ["A", "B"])
    assert round(x["A"], 6) == 0.1 and round(x["B"], 6) == -0.1  # A +20%, B 0%, bench +10%
    assert forward_excess(prices, ["A", "B"], days, 1, 2, ["A"]) == {}  # horizon past the data
