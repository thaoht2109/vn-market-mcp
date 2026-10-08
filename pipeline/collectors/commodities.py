"""Commodity prices: international futures (Yahoo chart API, daily closes) and SJC gold in Vietnam (vnstock).

Futures are continuous front-month contracts, so a value is "the close of the nearest contract that day".
Each run re-reads the last month of closes: missed days fill in and the series exists from the first run.
Today's bar is the latest traded price until the close; a later run overwrites it with the settled value.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

import httpx

from pipeline.collectors.indicators import collect
from pipeline.collectors.rss import TIMEOUT_S, USER_AGENT, FeedError

YAHOO = "yahoo"
SJC = "sjc"
EVERY_MINUTES = 180
CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{}"
SJC_URL = "https://sjc.com.vn"
# ponytail: Yahoo is unofficial and may block or rename symbols; a dead symbol is skipped, all dead = source failure
FUTURES = {
    "GC=F": ("gold_usd_oz", "USD/oz"),
    "BZ=F": ("brent_usd_bbl", "USD/thùng"),
    "CL=F": ("wti_usd_bbl", "USD/thùng"),
    "HG=F": ("copper_usd_lb", "USD/lb"),
    "TIO=F": ("iron_ore_usd_t", "USD/tấn"),
    "HRC=F": ("hrc_steel_usd_t", "USD/tấn"),
}


def parse_chart(payload: dict) -> list[tuple[date, float]]:
    res = payload["chart"]["result"][0]
    offset = res["meta"].get("gmtoffset", 0)  # exchange-local trading day, not the UTC date of the bar
    closes = res["indicators"]["quote"][0]["close"]
    return [(datetime.fromtimestamp(ts + offset, timezone.utc).date(), c)
            for ts, c in zip(res["timestamp"], closes) if c is not None]


def fetch_futures(client: httpx.Client) -> list[tuple]:
    rows, errors = [], []
    for symbol, (indicator, unit) in FUTURES.items():
        url = CHART_URL.format(symbol)
        try:
            resp = client.get(url, params={"range": "1mo", "interval": "1d"}, headers={"User-Agent": USER_AGENT},
                              timeout=TIMEOUT_S)
            resp.raise_for_status()
            rows += [(indicator, day, round(close, 4), unit, url) for day, close in parse_chart(resp.json())]
        except Exception as exc:
            errors.append(f"{symbol}: {type(exc).__name__}: {exc}")
    if not rows:
        raise FeedError("; ".join(errors) or "no futures data")
    return rows


def fetch_sjc(_client: httpx.Client) -> list[tuple]:
    from vnstock.explorer.misc.gold_price import sjc_gold_price  # heavy import, worker only

    from providers.rate_limit import wait_for_slot

    wait_for_slot()  # vnstock counts this call against the same per-minute limit
    df = sjc_gold_price()
    if df is None or df.empty:
        raise FeedError("SJC: empty price table")
    row = df.iloc[0]  # 'Vàng SJC 1L, 10L, 1KG', Hồ Chí Minh: the reference quote
    day = row["date"] if isinstance(row["date"], date) else date.today()
    return [("sjc_gold_buy", day, float(row["buy_price"]), "VND/lượng", SJC_URL),
            ("sjc_gold_sell", day, float(row["sell_price"]), "VND/lượng", SJC_URL)]


def run_collect_futures(conn, *, client: httpx.Client | None = None, now: datetime | None = None) -> int:
    return collect(conn, YAHOO, fetch_futures, every_minutes=EVERY_MINUTES, client=client, now=now)


def run_collect_sjc(conn, *, now: datetime | None = None) -> int:
    return collect(conn, SJC, fetch_sjc, every_minutes=EVERY_MINUTES, now=now)
