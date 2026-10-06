"""NSO (nso.gov.vn) monthly socio-economic report: GDP (quarter, y/y), CPI, core inflation, FDI.

The report is published around the 6th of the next month as a static HTML article whose sentences keep the
same wording month after month; we read the newest few from the listing page and regex the tag-stripped text.
Period convention: the first day of the month (CPI, FDI year-to-date) or of the quarter (GDP).
"""
from __future__ import annotations

import re
from datetime import date, datetime

import httpx

from pipeline.collectors.indicators import collect
from pipeline.collectors.rss import FeedError
from pipeline.collectors.sbv import _get, _num, _text

SOURCE = "nso"
NSO_EVERY_MINUTES = 720  # monthly data: twice a day is enough to pick a new report up the day it appears
REPORTS = 3  # newest + two before, so the first run already shows a short monthly series
LIST_URL = "https://www.nso.gov.vn/bao-cao-tinh-hinh-kinh-te-xa-hoi-hang-thang/"
_P = r"([\d.]+,\d+|\d+)"  # '9,95' | '21,07' | '3.109,6' | '12'
_QUARTERS = {"I": 1, "II": 2, "III": 3, "IV": 4}


def report_urls(listing: str) -> list[str]:
    """Newest first, as the listing orders them."""
    urls = list(dict.fromkeys(re.findall(
        r'href="(https://www\.nso\.gov\.vn/[^"]+/\d{4}/\d{2}/bao-cao-tinh-hinh-kinh-te-xa-hoi-[^"]+)"', listing)))
    if not urls:
        raise FeedError("NSO listing: no report link found")
    return urls


def _signed(verb: str, num: str) -> float:
    return -_num(num) if verb == "giảm" else _num(num)


def parse_report(page: str) -> list[tuple[str, date, float, str]]:
    t = _text(page)
    m = re.search(r"Tổng vốn đầu tư nước ngoài đăng ký vào Việt Nam.{0,40}?tính đến ngày (\d{1,2})/(\d{1,2})/(\d{4})"
                  rf".{{0,250}}?đạt {_P} tỷ USD, (tăng|giảm) {_P}% so với cùng kỳ", t)
    if not m:  # the reference month comes from this sentence: without it nothing can be dated
        raise FeedError("NSO report: registered FDI sentence not found")
    month = date(int(m[3]), int(m[2]), 1)
    out = [("fdi_registered_ytd", month, _num(m[4]), "tỷ USD"),
           ("fdi_registered_ytd_yoy", month, _signed(m[5], m[6]), "%")]
    m = re.search(rf"Vốn đầu tư trực tiếp nước ngoài thực hiện tại Việt Nam.{{0,40}}?ước đạt {_P} tỷ USD, (tăng|giảm) {_P}%", t)
    if m:
        out += [("fdi_disbursed_ytd", month, _num(m[1]), "tỷ USD"),
                ("fdi_disbursed_ytd_yoy", month, _signed(m[2], m[3]), "%")]
    m = re.search(rf"Chỉ số giá tiêu dùng \(CPI\) tháng \D{{2,15}}? (tăng|giảm) {_P}% so với tháng trước"
                  rf"(?:; (tăng|giảm) {_P}% so với tháng 12/\d{{4}})?(?:;| và) (tăng|giảm) {_P}% so với cùng kỳ", t)
    if m:
        out += [("cpi_mom", month, _signed(m[1], m[2]), "%"), ("cpi_yoy", month, _signed(m[5], m[6]), "%")]
        if m[3]:  # absent in January, where "vs December" is the month-on-month figure
            out.append(("cpi_vs_dec", month, _signed(m[3], m[4]), "%"))
    m = re.search(rf"Bình quân \D{{2,25}}? năm \d{{4}}, CPI (tăng|giảm) {_P}% so với cùng kỳ năm trước;"
                  rf" lạm phát cơ bản (tăng|giảm) {_P}%", t)
    if m:
        out += [("cpi_avg_ytd_yoy", month, _signed(m[1], m[2]), "%"),
                ("core_inflation_avg_ytd_yoy", month, _signed(m[3], m[4]), "%")]
    m = re.search(rf"Tổng sản phẩm trong nước \(GDP\) quý (IV|I{{1,3}})/(\d{{4}}) ước(?: tính)? (tăng|giảm) {_P}%", t)
    if m:  # quarter-end reports only
        q = _QUARTERS[m[1]]
        out.append(("gdp_yoy", date(int(m[2]), 3 * q - 2, 1), _signed(m[3], m[4]), "%"))
    return out


def fetch(client: httpx.Client) -> list[tuple]:
    newest, *older = report_urls(_get(client, LIST_URL))[:REPORTS]
    rows = [(*r, newest) for r in parse_report(_get(client, newest))]  # the newest one must parse
    for url in older:
        try:
            rows += [(*r, url) for r in parse_report(_get(client, url))]
        except FeedError:  # an odd older report only costs history, not today's numbers
            pass
    return rows


def run_collect_nso(conn, *, client: httpx.Client | None = None, now: datetime | None = None) -> int:
    return collect(conn, SOURCE, fetch, every_minutes=NSO_EVERY_MINUTES, client=client, now=now)
