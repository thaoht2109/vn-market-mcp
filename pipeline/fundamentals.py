from __future__ import annotations

from dataclasses import dataclass

from providers.vnstock_provider import FundamentalRecord

INDUSTRY_METRIC_SETS: dict[str, list[str]] = {
    "bank": ["pb", "roe", "credit_growth", "nim", "npl_ratio", "casa_ratio"],
    "securities": ["roe", "pb", "brokerage_revenue_growth", "margin_lending_growth", "proprietary_trading_pnl_ratio"],
    "insurance": ["roe", "pb", "combined_ratio", "premium_growth", "investment_yield"],
    "real_estate": ["roe", "pe", "pb", "revenue_growth", "net_debt_to_equity", "presales_growth"],
    "other": ["gross_margin", "roe", "revenue_growth", "profit_growth", "debt_to_equity", "pe", "pb"],
}


@dataclass
class FundamentalSnapshot:
    industry_group: str
    metrics: dict[str, float | None]
    quarters_available: int


def fundamental_snapshot(
    ticker: str, industry_group: str, records: list[FundamentalRecord]
) -> FundamentalSnapshot:
    group = industry_group if industry_group in INDUSTRY_METRIC_SETS else "other"
    metric_names = INDUSTRY_METRIC_SETS[group]

    if not records:
        return FundamentalSnapshot(industry_group=group, metrics={m: None for m in metric_names}, quarters_available=0)

    most_recent = sorted(records, key=lambda r: r.period, reverse=True)[0]
    metrics = {name: most_recent.metrics.get(name) for name in metric_names}

    return FundamentalSnapshot(industry_group=group, metrics=metrics, quarters_available=len(records))
