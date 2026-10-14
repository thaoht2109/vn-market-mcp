"""Settled daily foreign trading history from CafeF.

The KBS price board only gives a "now" snapshot of cumulative foreign volume, and out of hours it was
wrong in places: 0/0 before the open, and sell volumes larger than the whole session's traded volume.
CafeF serves per-day matched-order foreign buy/sell for any date range, and its figures agreed with the
KBS snapshot taken inside the session. Matched orders only, put-through deals are not in it.

Unofficial endpoint: a changed response raises ValueError so the caller records an error and retries,
never an empty list that looks like "no foreign activity".
"""
from __future__ import annotations

import time
from datetime import date, datetime, timezone

import httpx

from providers.vnstock_provider import ForeignFlowRecord

_URL = "https://cafef.vn/du-lieu/Ajax/PageNew/DataHistory/GDKhoiNgoai.ashx"
_PAGE_SIZE = 50
_MAX_PAGES = 20
_MIN_GAP_S = 0.3  # one polite request per 0.3 s across callers in this process
_last_call = 0.0


def _get(client: httpx.Client, params: dict) -> dict:
    global _last_call
    last_exc: Exception | None = None
    for attempt in range(3):
        wait = _last_call + _MIN_GAP_S - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _last_call = time.monotonic()
        try:
            resp = client.get(_URL, params=params)
            resp.raise_for_status()
            return resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            last_exc = exc
            time.sleep(1.5 * (attempt + 1))
    raise ValueError(f"CafeF foreign flow request failed for {params.get('Symbol')}: {last_exc!r}")


def _parse_row(ticker: str, row: dict, fetched_at: datetime) -> ForeignFlowRecord:
    try:
        buy, sell, net = float(row["GtMua"]), float(row["GtBan"]), float(row["GTDGRong"])
        room = row.get("RoomConLai")
        return ForeignFlowRecord(
            ticker=ticker, trade_date=datetime.strptime(row["Ngay"], "%d/%m/%Y").date(), buy_value=buy,
            sell_value=sell, net_value=net, room_left=float(room) if room is not None else None,
            source="CAFEF", fetched_at=fetched_at,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"CafeF foreign flow row for {ticker} has an unexpected shape: {row!r}") from exc


def get_foreign_flow_history(ticker: str, start: date, end: date, client: httpx.Client | None = None) -> list[ForeignFlowRecord]:
    """One record per trading day in [start, end], oldest first. Values in full VND, as CafeF reports them."""
    own = client is None
    client = client or httpx.Client(timeout=20.0, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://cafef.vn/"})
    fetched_at = datetime.now(timezone.utc)
    out: dict[date, ForeignFlowRecord] = {}
    try:
        for page in range(1, _MAX_PAGES + 1):
            body = _get(client, {"Symbol": ticker, "StartDate": start.strftime("%m/%d/%Y"),
                                 "EndDate": end.strftime("%m/%d/%Y"), "PageIndex": page, "PageSize": _PAGE_SIZE})
            data = (body or {}).get("Data")
            if not isinstance(data, dict) or not isinstance(data.get("Data"), list):
                raise ValueError(f"CafeF foreign flow response for {ticker} has an unexpected shape: {str(body)[:200]}")
            rows = [r for r in data["Data"] if r.get("Symbol") == ticker]
            for r in rows:
                rec = _parse_row(ticker, r, fetched_at)
                out[rec.trade_date] = rec
            if len(data["Data"]) < _PAGE_SIZE:
                break
    finally:
        if own:
            client.close()
    return [out[d] for d in sorted(out)]
