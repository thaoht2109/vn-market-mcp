from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

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


def _to_date(value: Any) -> date:
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


class VNStockProvider:
    """Thin wrapper around the vnstock library. Accepts an injected client for
    testing; defaults to a real vnstock client when none is given."""

    def __init__(self, source: str = "VCI", client: Any = None, scale_map: dict[str, float] | None = None):
        self.source = source
        self.scale_map = scale_map or _load_price_unit_scale()
        if client is not None:
            self._client = client
        else:
            from vnstock import Vnstock  # lazy import: tests never need vnstock/network

            self._client = Vnstock()

    def get_ohlcv(self, ticker: str, start: date, end: date) -> list[PriceBar]:
        fetched_at = datetime.now(timezone.utc)
        stock = self._client.stock(symbol=ticker, source=self.source)
        raw = stock.quote.history(start=start.isoformat(), end=end.isoformat())
        bars = []
        for row in raw.to_dict("records"):
            bars.append(
                PriceBar(
                    ticker=ticker,
                    trade_date=_to_date(row["time"]),
                    open=normalize_price_unit(row["open"], self.source, self.scale_map),
                    high=normalize_price_unit(row["high"], self.source, self.scale_map),
                    low=normalize_price_unit(row["low"], self.source, self.scale_map),
                    close=normalize_price_unit(row["close"], self.source, self.scale_map),
                    volume=int(row["volume"]),
                    value=normalize_price_unit(row["value"], self.source, self.scale_map) if row.get("value") is not None else None,
                    source=self.source,
                    fetched_at=fetched_at,
                )
            )
        return bars

    def get_fundamentals(self, ticker: str, quarters: int) -> list[FundamentalRecord]:
        fetched_at = datetime.now(timezone.utc)
        stock = self._client.stock(symbol=ticker, source=self.source)
        raw = stock.finance.ratio(period="quarter", lang="en")
        records = []
        for row in raw.to_dict("records")[:quarters]:
            records.append(
                FundamentalRecord(
                    ticker=ticker,
                    period=str(row["period"]),
                    report_type="self_prepared",
                    version=1,
                    metrics={k: v for k, v in row.items() if k != "period"},
                    published_date=_to_date(row["published_date"]) if row.get("published_date") else None,
                    source=self.source,
                    fetched_at=fetched_at,
                )
            )
        return records

    def get_foreign_flow(self, ticker: str, start: date, end: date) -> list[ForeignFlowRecord]:
        fetched_at = datetime.now(timezone.utc)
        stock = self._client.stock(symbol=ticker, source=self.source)
        raw = stock.trading.foreign_trade(start=start.isoformat(), end=end.isoformat())
        records = []
        for row in raw.to_dict("records"):
            records.append(
                ForeignFlowRecord(
                    ticker=ticker,
                    trade_date=_to_date(row["time"]),
                    buy_value=row.get("buy_value"),
                    sell_value=row.get("sell_value"),
                    net_value=row.get("net_value"),
                    room_left=row.get("room_left"),
                    source=self.source,
                    fetched_at=fetched_at,
                )
            )
        return records

    def get_corporate_events(self, ticker: str, start: date, end: date) -> list[CorporateEvent]:
        fetched_at = datetime.now(timezone.utc)
        stock = self._client.stock(symbol=ticker, source=self.source)
        raw = stock.company.events(start=start.isoformat(), end=end.isoformat())
        events = []
        for row in raw.to_dict("records"):
            events.append(
                CorporateEvent(
                    ticker=ticker,
                    event_type=str(row["event_type"]),
                    event_date=_to_date(row["event_date"]),
                    payload=row,
                    source_url=row.get("source_url"),
                    fetched_at=fetched_at,
                )
            )
        return events
