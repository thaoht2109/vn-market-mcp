from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import psycopg


@dataclass
class TickerMatch:
    ticker: str
    name: str
    exchange: str


class UnknownTickerError(Exception):
    def __init__(self, raw_input: str, suggestions: list[str]):
        self.raw_input = raw_input
        self.suggestions = suggestions
        suggestion_text = f" (gần giống: {', '.join(suggestions)})" if suggestions else ""
        super().__init__(f"không tìm thấy mã {raw_input!r}{suggestion_text}")


def resolve_ticker(conn: psycopg.Connection, raw_input: str) -> TickerMatch:
    normalized = raw_input.strip().upper()
    row = conn.execute(
        "SELECT ticker, name, exchange FROM tickers WHERE ticker = %s", (normalized,)
    ).fetchone()
    if row is not None:
        return TickerMatch(ticker=row[0], name=row[1], exchange=row[2])

    candidates = conn.execute(
        "SELECT ticker FROM tickers WHERE ticker LIKE %s ORDER BY ticker LIMIT 5",
        (f"{normalized[:2]}%",),
    ).fetchall()
    raise UnknownTickerError(raw_input, [c[0] for c in candidates])


def resolve_or_register_ticker(conn: psycopg.Connection, provider, raw_input: str) -> TickerMatch:
    """resolve_ticker, but a ticker missing from `tickers` (only VN30 is seeded) that
    vnstock lists is registered on the spot, so any listed ticker can be analysed."""
    try:
        return resolve_ticker(conn, raw_input)
    except UnknownTickerError:
        ticker = raw_input.strip().upper()
        listed = provider.lookup_listing(ticker)
        if listed is None:
            raise
    name, exchange = listed
    conn.execute(
        "INSERT INTO tickers (ticker, name, exchange) VALUES (%s, %s, %s) ON CONFLICT (ticker) DO NOTHING",
        (ticker, name, exchange),
    )
    return TickerMatch(ticker=ticker, name=name, exchange=exchange)


def classify_universe_tier(conn: psycopg.Connection, ticker: str) -> Literal["A", "B"]:
    row = conn.execute(
        """
        SELECT 1 FROM index_membership
        WHERE index_code = 'VN30' AND ticker = %s AND (valid_to IS NULL OR valid_to >= CURRENT_DATE)
        """,
        (ticker,),
    ).fetchone()
    return "A" if row is not None else "B"


@dataclass
class CoverageInputs:
    price_days: int
    avg_liquidity_value_20d: float
    fundamentals_quarters: int


def gather_coverage_inputs(conn: psycopg.Connection, ticker: str) -> CoverageInputs:
    price_days = conn.execute(
        "SELECT count(*) FROM prices_daily WHERE ticker = %s", (ticker,)
    ).fetchone()[0]
    avg_liquidity = conn.execute(
        """
        SELECT avg(value) FROM (
          SELECT value FROM prices_daily WHERE ticker = %s ORDER BY trade_date DESC LIMIT 20
        ) recent
        """,
        (ticker,),
    ).fetchone()[0]
    fundamentals_quarters = conn.execute(
        "SELECT count(DISTINCT period) FROM fundamentals_quarterly WHERE ticker = %s", (ticker,)
    ).fetchone()[0]
    return CoverageInputs(
        price_days=price_days,
        avg_liquidity_value_20d=float(avg_liquidity or 0),
        fundamentals_quarters=fundamentals_quarters,
    )


@dataclass
class CoverageConfig:
    min_price_history_days: int
    min_avg_liquidity_value_20d: float
    min_fundamental_quarters: int
    min_weight_coverage: float

    @classmethod
    def from_rules(cls, rules: dict) -> "CoverageConfig":
        c = rules["coverage"]
        return cls(
            min_price_history_days=c["min_price_history_days"],
            min_avg_liquidity_value_20d=c["min_avg_liquidity_value_20d"],
            min_fundamental_quarters=c["min_fundamental_quarters"],
            min_weight_coverage=c["min_weight_coverage"],
        )


@dataclass
class CoverageResult:
    sufficient: bool
    tier: Literal["A", "B", "C"]
    weight_coverage: float
    component_status: dict[str, str]


_REQUIRED_COMPONENT_WEIGHTS = {"price_history": 0.35, "liquidity": 0.35, "fundamentals": 0.30}
_STATUS_FRACTION = {"ok": 1.0, "partial": 0.5, "missing": 0.0}


def _status(value: float, full_threshold: float, partial_threshold: float) -> str:
    if value >= full_threshold:
        return "ok"
    if value >= partial_threshold:
        return "partial"
    return "missing"


def coverage_check(tier: Literal["A", "B"], inputs: CoverageInputs, cfg: CoverageConfig) -> CoverageResult:
    component_status = {
        "price_history": _status(inputs.price_days, cfg.min_price_history_days, cfg.min_price_history_days * 0.5),
        "liquidity": _status(
            inputs.avg_liquidity_value_20d,
            cfg.min_avg_liquidity_value_20d,
            cfg.min_avg_liquidity_value_20d * 0.5,
        ),
        "fundamentals": _status(inputs.fundamentals_quarters, cfg.min_fundamental_quarters, 1),
    }

    weight_coverage = sum(
        _REQUIRED_COMPONENT_WEIGHTS[k] * _STATUS_FRACTION[status] for k, status in component_status.items()
    )
    sufficient = weight_coverage >= cfg.min_weight_coverage

    return CoverageResult(
        sufficient=sufficient,
        tier=tier if sufficient else "C",
        weight_coverage=weight_coverage,
        component_status=component_status,
    )
