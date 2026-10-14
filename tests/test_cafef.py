import json
from datetime import date

import httpx
import pytest

import providers.cafef as cafef

# Two real rows of CafeF's GDKhoiNgoai response for MCH (2026-10-13/14), amounts in full VND.
ROWS = [
    {"Symbol": "MCH", "Ngay": "14/10/2026", "KLGDRong": -62000, "GTDGRong": -8718600000, "ThayDoi": "140,90 (-0,21%)",
     "KLMua": 4900, "GtMua": 686460000, "KLBan": 66900, "GtBan": 9405060000, "RoomConLai": 1161540008, "DangSoHuu": 11.16},
    {"Symbol": "MCH", "Ngay": "13/10/2026", "KLGDRong": -93800, "GTDGRong": -13281720000, "ThayDoi": "141,20 (-0,91%)",
     "KLMua": 13900, "GtMua": 1955620000, "KLBan": 107700, "GtBan": 15237340000, "RoomConLai": 1097598310, "DangSoHuu": 16.05},
]


@pytest.fixture(autouse=True)
def no_throttle(monkeypatch):
    monkeypatch.setattr(cafef, "_MIN_GAP_S", 0)
    monkeypatch.setattr(cafef.time, "sleep", lambda s: None)


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def _page(rows):
    return httpx.Response(200, json={"Data": {"TotalCount": len(rows), "Data": rows}})


def test_rows_become_records_oldest_first_in_full_vnd_and_net_is_buy_minus_sell():
    seen = {}

    def handler(req):
        seen.update(dict(req.url.params))
        return _page(ROWS)

    recs = cafef.get_foreign_flow_history("MCH", date(2026, 10, 1), date(2026, 10, 14), client=_client(handler))
    assert seen["Symbol"] == "MCH" and seen["StartDate"] == "10/01/2026" and seen["EndDate"] == "10/14/2026"
    assert [r.trade_date for r in recs] == [date(2026, 10, 13), date(2026, 10, 14)]
    r = recs[1]
    assert (r.buy_value, r.sell_value, r.net_value, r.room_left, r.source) == (
        686460000, 9405060000, -8718600000, 1161540008, "CAFEF")
    assert all(abs(x.buy_value - x.sell_value - x.net_value) < 1 for x in recs)  # the API's own net agrees


def test_pages_until_a_short_page_and_ignores_other_symbols():
    full = [{**ROWS[0], "Ngay": f"{d:02d}/09/2026"} for d in range(1, 29)] + \
           [{**ROWS[0], "Ngay": f"{d:02d}/10/2026"} for d in range(1, 23)]  # 50 rows = a full page
    pages = []

    def handler(req):
        pages.append(int(req.url.params["PageIndex"]))
        return _page(full if pages[-1] == 1 else [{**ROWS[0], "Symbol": "OTHER"}])

    recs = cafef.get_foreign_flow_history("MCH", date(2026, 9, 1), date(2026, 10, 22), client=_client(handler))
    assert pages == [1, 2] and len(recs) == 50


def test_a_changed_response_raises_instead_of_looking_like_no_foreign_activity():
    for body in ({"Data": None}, {"Data": {"Data": "x"}}, {"unexpected": 1}):
        with pytest.raises(ValueError):
            cafef.get_foreign_flow_history("MCH", date(2026, 10, 1), date(2026, 10, 14),
                                           client=_client(lambda req, b=body: httpx.Response(200, json=b)))
    with pytest.raises(ValueError):
        bad_row = {k: v for k, v in ROWS[0].items() if k != "GtBan"}
        cafef.get_foreign_flow_history("MCH", date(2026, 10, 1), date(2026, 10, 14), client=_client(lambda req: _page([bad_row])))


def test_http_errors_are_retried_then_raised():
    calls = []

    def handler(req):
        calls.append(1)
        return httpx.Response(503)

    with pytest.raises(ValueError, match="request failed"):
        cafef.get_foreign_flow_history("MCH", date(2026, 10, 1), date(2026, 10, 14), client=_client(handler))
    assert len(calls) == 3
    calls.clear()

    def flaky(req):
        calls.append(1)
        return httpx.Response(502) if len(calls) == 1 else _page(ROWS)

    assert len(cafef.get_foreign_flow_history("MCH", date(2026, 10, 1), date(2026, 10, 14), client=_client(flaky))) == 2


def test_empty_range_is_an_empty_list():
    assert cafef.get_foreign_flow_history("NEW", date(2026, 10, 1), date(2026, 10, 14), client=_client(lambda req: _page([]))) == []


def test_provider_exposes_the_history(monkeypatch):
    from providers.vnstock_provider import VNStockProvider

    monkeypatch.setattr(cafef, "get_foreign_flow_history", lambda t, s, e, client=None: [t, s, e])
    assert VNStockProvider.get_foreign_flow_history(object(), "MCH", date(2026, 10, 1), date(2026, 10, 2)) == \
        ["MCH", date(2026, 10, 1), date(2026, 10, 2)]
