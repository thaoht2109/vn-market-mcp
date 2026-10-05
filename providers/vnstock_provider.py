from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

_CONFIG_PATH = Path(__file__).parent.parent / "config" / "vn-rules.yaml"


def _load_price_unit_scale() -> dict[str, float]:
    cfg = yaml.safe_load(_CONFIG_PATH.read_text())
    if "price_unit_scale" not in cfg:
        raise ValueError(f"{_CONFIG_PATH} is missing required key 'price_unit_scale'")
    return cfg["price_unit_scale"]


def normalize_price_unit(value: float, source: str, scale_map: dict[str, float]) -> float:
    """Convert a raw vnstock price/value field to full VND.

    ponytail: explicit per-source scale factor from config, not a heuristic
    guesser off the magnitude of the number. Upgrade to auto-detection only
    if a source with an undocumented/variable convention is added.
    """
    scale = scale_map.get(source)
    if scale is None:
        raise ValueError(f"no price_unit_scale configured for source={source!r}")
    return value * scale


# vnstock.api.financial.Finance.ratio()'s item_en values, verified against
# real VCI data for VCB/SSI/BVH (2026-10-01): the Community tier returns the
# SAME fixed set of ~50 ratio rows for every ticker regardless of industry —
# there is no per-industry schema (no brokerage/insurance/real-estate-specific
# fields at all). Map the real labels to the snake_case keys the rest of the
# codebase (pipeline/fundamentals.py's METRIC_SET, run_analysis.py) expects;
# anything not in this map is dropped, so code also sees a snake_case key
# and never silently looks up the wrong label.
_FUNDAMENTAL_METRIC_ALIASES: dict[str, str] = {
    "P/E": "pe",
    "P/B": "pb",
    "ROE (%)": "roe",
    "ROA (%)": "roa",
    "Gross Margin (%)": "gross_margin",
    "Debt/Equity": "debt_to_equity",
    "Loans Growth (%)": "credit_growth",
    "Net Interest Margin": "nim",
    "NPL (%)": "npl_ratio",
    "CASA Ratio": "casa_ratio",
}


_QUARTER_PERIOD = re.compile(r"\d{4}-Q[1-4]")
_ALL_PERIODS = 100_000  # larger than any listing's quarter count


# The VCI ratio report fills these with 0 for non-banks (VNM: nim 0.0, npl 0.0);
# a real bank never has exactly 0, so 0 means "not applicable" — store None so
# a report can't print "nợ xấu 0%" for a dairy company.
_BANK_ONLY_METRICS = {"nim", "npl_ratio", "casa_ratio", "credit_growth"}


def _normalize_fundamental_metrics(raw_metrics: dict[Any, Any]) -> dict[str, Any]:
    out = {}
    for key, value in raw_metrics.items():
        name = _FUNDAMENTAL_METRIC_ALIASES.get(key)
        if name is None:
            continue
        if pd.isna(value) or (name in _BANK_ONLY_METRICS and value == 0):
            value = None
        out[name] = value
    return out


def _to_date(value: Any) -> date:
    # pandas.Timestamp subclasses datetime.date, so an isinstance(value, date)
    # check alone would match it and return the Timestamp unchanged instead
    # of a plain date — check datetime (Timestamp's actual base) first.
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return datetime.fromisoformat(str(value)[:10]).date()


@dataclass
class PriceBar:
    ticker: str
    trade_date: date
    open: float
    high: float
    low: float
    close: float
    volume: int
    value: float | None
    source: str
    fetched_at: datetime


@dataclass
class FundamentalRecord:
    ticker: str
    period: str
    report_type: str
    version: int
    metrics: dict[str, Any]
    published_date: date | None
    source: str
    fetched_at: datetime


@dataclass
class ForeignFlowRecord:
    ticker: str
    trade_date: date
    buy_value: float | None
    sell_value: float | None
    net_value: float | None
    room_left: float | None
    source: str
    fetched_at: datetime


@dataclass
class CorporateEvent:
    ticker: str
    event_type: str
    event_date: date
    payload: dict[str, Any]
    source_url: str | None
    fetched_at: datetime


@dataclass
class NewsItem:
    ticker: str
    published_at: datetime
    source: str
    url: str | None
    title: str
    summary: str | None
    fetched_at: datetime


class VNStockProvider:
    """Thin wrapper around vnstock.api (the post-2025-08-31 API; the old
    Vnstock().stock(...).quote/finance/trading/company facade is deprecated
    and removed in vnstock>=4). Each vnstock.api class is instantiated
    per-call with source+symbol, so there is no single shared client to
    inject — tests inject a fake per-domain client via the `clients` dict
    instead (see get_ohlcv/get_fundamentals/get_corporate_events)."""

    def __init__(self, source: str = "VCI", clients: dict[str, Any] | None = None, scale_map: dict[str, float] | None = None):
        self.source = source
        self.scale_map = scale_map or _load_price_unit_scale()
        self._clients = clients or {}

    def _quote(self, ticker: str) -> Any:
        if "quote" in self._clients:
            return self._clients["quote"]
        from vnstock.api.quote import Quote  # lazy import: tests never need vnstock/network

        return Quote(symbol=ticker, source=self.source)

    def _finance(self, ticker: str) -> Any:
        if "finance" in self._clients:
            return self._clients["finance"]
        # The VCI explorer class, not vnstock.api.financial.Finance: the public
        # ratio() can't take a limit (see get_fundamentals for why it must).
        from vnstock.explorer.vci.financial import Finance

        return Finance(symbol=ticker, period="quarter")

    def _company(self, ticker: str) -> Any:
        if "company" in self._clients:
            return self._clients["company"]
        from vnstock.api.company import Company

        return Company(source=self.source, symbol=ticker)

    def _trading(self) -> Any:
        if "trading" in self._clients:
            return self._clients["trading"]
        from vnstock.api.trading import Trading

        # Foreign flow fields (foreign_buy_volume/foreign_sell_volume/foreign_room)
        # only exist on the KBS price board, not VCI — independent of self.source.
        return Trading(source="KBS")

    def lookup_listing(self, ticker: str) -> tuple[str, str] | None:
        """(name, exchange) if `ticker` is listed on HOSE/HNX/UPCoM, else None."""
        if "listing" in self._clients:
            listing = self._clients["listing"]
        else:
            from vnstock.api.listing import Listing

            listing = Listing()
        df = listing.symbols_by_exchange()
        rows = df[df["symbol"] == ticker]
        if rows.empty:
            return None
        row = rows.iloc[0]
        exchange = str(row["exchange"]).upper()
        return str(row.get("organ_short_name") or row.get("organ_name") or ticker), {"HSX": "HOSE"}.get(exchange, exchange)

    def get_market_index(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        """OHLCV for a market index (e.g. VNINDEX), for pipeline.regime.

        Returns the raw vnstock frame unscaled — normalize_price_unit's
        per-source VND scale factor (×1000 for VCI) doesn't apply to an
        index's point value, unlike get_ohlcv's per-ticker VND prices.
        """
        raw = self._quote(symbol).history(symbol=symbol, start=start.isoformat(), end=end.isoformat())
        return raw.rename(columns={"time": "trade_date"})

    def get_ohlcv(self, ticker: str, start: date, end: date) -> list[PriceBar]:
        fetched_at = datetime.now(timezone.utc)
        raw = self._quote(ticker).history(symbol=ticker, start=start.isoformat(), end=end.isoformat())
        bars = []
        for row in raw.to_dict("records"):
            close = normalize_price_unit(row["close"], self.source, self.scale_map)
            volume = int(row["volume"])
            # vnstock.api.quote.Quote.history() doesn't return a traded-value
            # column at all (unlike the old API) — approximate it as close*volume,
            # the standard proxy, rather than leaving liquidity checks starved of data.
            if row.get("value") is not None:
                value = normalize_price_unit(row["value"], self.source, self.scale_map)
            else:
                value = close * volume
            bars.append(
                PriceBar(
                    ticker=ticker,
                    trade_date=_to_date(row["time"]),
                    open=normalize_price_unit(row["open"], self.source, self.scale_map),
                    high=normalize_price_unit(row["high"], self.source, self.scale_map),
                    low=normalize_price_unit(row["low"], self.source, self.scale_map),
                    close=close,
                    volume=volume,
                    value=value,
                    source=self.source,
                    fetched_at=fetched_at,
                )
            )
        return bars

    def get_fundamentals(self, ticker: str, quarters: int) -> list[FundamentalRecord]:
        """The VCI ratio report is a wide DataFrame:
        one row per metric ('item'/'item_en'), one column per period
        ('YYYY-Qn' or 'YYYY'). Transpose to the one-record-per-period shape
        FundamentalRecord expects."""
        fetched_at = datetime.now(timezone.utc)
        # VCI's statistics-financial endpoint returns the whole history
        # oldest-first and vnstock's ratio() keeps head(4) of it — i.e. the
        # four OLDEST quarters (2018 for most VN30 tickers). Ask for all rows
        # and pick the newest ourselves.
        # ponytail: private vnstock method; re-check on vnstock upgrades.
        raw = self._finance(ticker)._get_financial_report(
            "ratio", period="quarter", lang="en", limit=_ALL_PERIODS
        )
        # The history interleaves annual columns ("2025"); keep quarters only.
        period_cols = sorted(c for c in raw.columns if _QUARTER_PERIOD.fullmatch(str(c)))
        records = []
        for period in period_cols[-quarters:]:
            metrics = _normalize_fundamental_metrics(dict(zip(raw["item_en"], raw[period])))
            records.append(
                FundamentalRecord(
                    ticker=ticker,
                    period=str(period),
                    report_type="self_prepared",
                    version=1,
                    metrics=metrics,
                    published_date=None,  # not exposed by vnstock.api.financial.Finance.ratio()
                    source=self.source,
                    fetched_at=fetched_at,
                )
            )
        return records

    def get_foreign_flow(self, ticker: str, start: date, end: date) -> list[ForeignFlowRecord]:
        """vnstock>=4 removed the historical foreign-trade endpoint entirely
        (old stock.trading.foreign_trade has no vnstock.api equivalent).
        The only remaining source is the KBS in-session price board, which
        gives one current snapshot — not a date range. start/end are ignored;
        callers get a single record for "now" and must upsert daily to build
        history (ponytail: no backfill possible, only accrues going forward).
        """
        fetched_at = datetime.now(timezone.utc)
        board = self._trading().price_board(symbols_list=[ticker])
        if board.empty:
            return []
        row = board.iloc[0]
        # KBS board prices are already full VND (MWG 72500, verified 2026-10-02);
        # the per-source scale_map is for VCI history and would inflate this ×1000.
        close_price = float(row["close_price"])
        buy_value = float(row["foreign_buy_volume"]) * close_price
        sell_value = float(row["foreign_sell_volume"]) * close_price
        room_left = row.get("foreign_room")
        return [
            ForeignFlowRecord(
                ticker=ticker,
                trade_date=_to_date(datetime.fromtimestamp(int(row["time"]) / 1000, tz=timezone.utc)),
                buy_value=buy_value,
                sell_value=sell_value,
                net_value=buy_value - sell_value,
                room_left=float(room_left) if room_left is not None else None,
                source="KBS",
                fetched_at=fetched_at,
            )
        ]

    def get_corporate_events(self, ticker: str, start: date, end: date) -> list[CorporateEvent]:
        fetched_at = datetime.now(timezone.utc)
        raw = self._company(ticker).events()  # no start/end param in vnstock.api; filter after fetch
        events = []
        for row in raw.to_dict("records"):
            event_date = row.get("public_date") or row.get("display_date1")
            if event_date is None:
                continue
            parsed_date = _to_date(event_date)
            if not (start <= parsed_date <= end):
                continue
            events.append(
                CorporateEvent(
                    ticker=ticker,
                    event_type=str(row.get("event_code") or row.get("category") or "unknown"),
                    event_date=parsed_date,
                    payload=row,
                    source_url=None,  # not exposed by vnstock.api.company.Company.events()
                    fetched_at=fetched_at,
                )
            )
        return events

    def get_news(self, ticker: str, start: date, end: date) -> list[NewsItem]:
        """vnstock.api.company.Company.news() — title/date are populated;
        news_short_content/news_full_content are None on the Community tier
        in practice (verified 2026-10-01 against real GAS data), so summary
        falls back to None rather than the title (never fabricate a summary
        the source didn't provide — spec §5.7.3 "chỉ dùng nội dung trong đầu
        vào; không có thông tin thì trả no_info")."""
        fetched_at = datetime.now(timezone.utc)
        raw = self._company(ticker).news()
        items = []
        for row in raw.to_dict("records"):
            public_date = row.get("public_date")
            if public_date is None:
                continue
            published_at = datetime.fromisoformat(str(public_date)).replace(tzinfo=timezone.utc)
            if not (start <= published_at.date() <= end):
                continue
            title = row.get("news_title")
            if not title:
                continue
            items.append(
                NewsItem(
                    ticker=ticker,
                    published_at=published_at,
                    source=row.get("news_source") or "vnstock",
                    url=row.get("news_source_link"),
                    title=title,
                    summary=row.get("news_short_content") or row.get("news_full_content"),
                    fetched_at=fetched_at,
                )
            )
        return items
