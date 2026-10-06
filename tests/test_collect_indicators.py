import json
from decimal import Decimal
from datetime import date, datetime, timezone
from pathlib import Path

import httpx
import pytest

import pipeline.collectors.commodities as commodities
import pipeline.collectors.nso as nso
from mcp_server.tools.macro import _indicator_warnings

FIX = Path(__file__).parent / "fixtures"
TUE = datetime(2026, 10, 6, 3, 0, tzinfo=timezone.utc)
SEP = (FIX / "nso" / "ktxh-2026-09.html").read_text()
AUG = (FIX / "nso" / "ktxh-2026-08.html").read_text()
GC = json.loads((FIX / "yahoo" / "gc.json").read_text())
R = "https://www.nso.gov.vn/bai-top/2026/{}/bao-cao-tinh-hinh-kinh-te-xa-hoi-{}/"
LISTING = "".join(f'<a href="{R.format(m, s)}">x</a>' for m, s in
                  (("10", "quy-iii-2026"), ("09", "thang-tam-2026"), ("08", "thang-bay-2026"), ("07", "quy-ii-2026")))


def client(routes):
    calls = []

    def handler(request):
        url = str(request.url).split("?")[0]
        calls.append(url)
        r = routes[url]
        return r if isinstance(r, httpx.Response) else httpx.Response(200, **r)
    return httpx.Client(transport=httpx.MockTransport(handler)), calls


@pytest.fixture(autouse=True)
def _wipe(db_conn):
    def wipe():
        db_conn.execute("DELETE FROM macro_indicators WHERE source IN ('nso', 'yahoo')")
        db_conn.execute("DELETE FROM source_health WHERE source IN ('nso', 'yahoo')")
        db_conn.commit()
    wipe()
    yield
    wipe()


def test_nso_report_reads_gdp_cpi_core_inflation_and_fdi():
    got = {k: (p, v) for k, p, v, _ in nso.parse_report(SEP)}
    assert got["gdp_yoy"] == (date(2026, 7, 1), 9.95)  # Q3 -> first day of the quarter
    assert got["cpi_mom"] == (date(2026, 9, 1), 0.62)
    assert got["cpi_yoy"] == (date(2026, 9, 1), 5.08)
    assert got["cpi_vs_dec"] == (date(2026, 9, 1), 4.2)
    assert got["core_inflation_avg_ytd_yoy"] == (date(2026, 9, 1), 4.26)
    assert got["fdi_registered_ytd"] == (date(2026, 9, 1), 50.36)
    assert got["fdi_disbursed_ytd"] == (date(2026, 9, 1), 21.07)
    aug = {k: v for k, _, v, _ in nso.parse_report(AUG)}
    assert "gdp_yoy" not in aug and aug["cpi_yoy"] == 4.89  # GDP only in quarter-end reports


def test_nso_january_wording_and_falls_are_signed():
    t = ("Tổng vốn đầu tư nước ngoài đăng ký vào Việt Nam tính đến ngày 31/1/2027 bao gồm: ... đạt 3,5 tỷ USD, "
         "giảm 10,2% so với cùng kỳ năm trước. Chỉ số giá tiêu dùng (CPI) tháng Một giảm 0,15% so với tháng trước "
         "và tăng 3,1% so với cùng kỳ năm trước.")
    got = {k: (p, v) for k, p, v, _ in nso.parse_report(t)}
    assert got["fdi_registered_ytd_yoy"] == (date(2027, 1, 1), -10.2)
    assert got["cpi_mom"] == (date(2027, 1, 1), -0.15) and got["cpi_yoy"][1] == 3.1
    assert "cpi_vs_dec" not in got


def test_nso_collects_the_newest_reports_and_skips_an_odd_older_one(db_conn):
    c, calls = client({nso.LIST_URL: {"text": LISTING}, R.format("10", "quy-iii-2026"): {"text": SEP},
                       R.format("09", "thang-tam-2026"): {"text": AUG},
                       R.format("08", "thang-bay-2026"): {"text": "<p>trang lạ</p>"}})
    assert nso.run_collect_nso(db_conn, client=c, now=TUE) == 19
    assert len(calls) == 4  # listing + 3 reports, the 4th link is never fetched
    assert db_conn.execute("SELECT count(DISTINCT period) FROM macro_indicators WHERE indicator = 'cpi_yoy'"
                           " AND source = 'nso'").fetchone()[0] == 2


def test_nso_newest_report_unparseable_is_a_source_failure(db_conn):
    c, _ = client({nso.LIST_URL: {"text": LISTING}, R.format("10", "quy-iii-2026"): {"text": "<p>đổi mẫu</p>"}})
    assert nso.run_collect_nso(db_conn, client=c, now=TUE) == 0
    assert db_conn.execute("SELECT consecutive_failures FROM source_health WHERE source = 'nso'").fetchone()[0] == 1


def test_futures_skip_a_dead_symbol_and_fail_only_when_all_are_dead(db_conn, monkeypatch):
    monkeypatch.setattr(commodities, "FUTURES", {"GC=F": ("gold_usd_oz", "USD/oz"), "XX=F": ("dead", "USD")})
    url = commodities.CHART_URL.format
    c, _ = client({url("GC=F"): {"json": GC}, url("XX=F"): httpx.Response(404)})
    assert commodities.run_collect_futures(db_conn, client=c, now=TUE) == 21
    assert db_conn.execute("SELECT value FROM macro_indicators WHERE indicator = 'gold_usd_oz'"
                           " AND period = '2026-10-05'").fetchone()[0] == pytest.approx(Decimal("4156.8"), abs=Decimal("0.01"))
    db_conn.execute("DELETE FROM source_health WHERE source = 'yahoo'")
    c, _ = client({url("GC=F"): httpx.Response(429), url("XX=F"): httpx.Response(404)})
    assert commodities.run_collect_futures(db_conn, client=c, now=TUE) == 0
    assert "429" in db_conn.execute("SELECT last_error FROM source_health WHERE source = 'yahoo'").fetchone()[0]


def test_freshness_warnings_name_missing_and_stale_sources():
    s = lambda p: {"series": [{"period": p, "value": 1}]}
    w = _indicator_warnings({"usd_vnd_central": s("2026-10-06"), "cpi_yoy": s("2026-06-01"),
                             "gold_usd_oz": s("2026-10-05")}, TUE)
    assert w == ["Số liệu NSO (GDP, CPI, FDI) chưa cập nhật từ kỳ 01/06/2026",
                 "Chưa có giá vàng SJC: không tự điền số"]
