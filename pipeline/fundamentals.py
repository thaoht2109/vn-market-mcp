from __future__ import annotations

from dataclasses import dataclass

from pipeline.scoring import percentile_score
from providers.vnstock_provider import FundamentalRecord

# vnstock's Community-tier Finance.ratio() returns the SAME fixed ~50-row
# ratio set for every ticker regardless of industry_group (verified
# 2026-10-01 against real VCI data for VCB/SSI/BVH — no brokerage/insurance/
# real-estate-specific fields exist at all; see
# providers.vnstock_provider._FUNDAMENTAL_METRIC_ALIASES for the full set
# of real field names this codebase can actually read). So there is no
# real per-industry metric SET to select — every group gets the same
# metrics that exist; sector-specific coverage requirements belong in
# config/vn-rules.yaml's coverage gate instead, not a fake industry split here.
METRIC_SET = ["pe", "pb", "roe", "roa", "gross_margin", "debt_to_equity", "credit_growth", "nim", "npl_ratio", "casa_ratio"]


@dataclass
class FundamentalSnapshot:
    industry_group: str
    metrics: dict[str, float | None]
    quarters_available: int


def fundamental_snapshot(
    ticker: str, industry_group: str, records: list[FundamentalRecord]
) -> FundamentalSnapshot:
    if not records:
        return FundamentalSnapshot(industry_group=industry_group, metrics={m: None for m in METRIC_SET}, quarters_available=0)

    most_recent = sorted(records, key=lambda r: r.period, reverse=True)[0]
    metrics = {name: most_recent.metrics.get(name) for name in METRIC_SET}

    return FundamentalSnapshot(industry_group=industry_group, metrics=metrics, quarters_available=len(records))


MIN_HISTORY = 3  # quarters of own history before a percentile means anything
MIN_PEERS = 3


def peer_metrics(conn, ticker: str, industry_group: str) -> dict[str, list[float]]:
    """Latest positive P/E and P/B of the other tickers in the same industry group."""
    if industry_group == "other":
        return {"pe": [], "pb": []}
    rows = conn.execute(
        """
        SELECT DISTINCT ON (f.ticker) f.metrics FROM fundamentals_quarterly f
        JOIN tickers t USING (ticker)
        WHERE t.industry_group = %s AND f.ticker <> %s AND f.period ~ '^[0-9]{4}-?Q[1-4]$'
        ORDER BY f.ticker, f.period DESC, f.version DESC
        """,
        (industry_group, ticker),
    ).fetchall()
    return {m: [r[0][m] for r in rows if (r[0].get(m) or 0) > 0] for m in ("pe", "pb")}


def valuation_component(current: dict, history: list[dict], peers: dict[str, list[float]]) -> float | None:
    """0-100, higher = cheaper / better quality. Mean of the parts that have data:
    P/E and P/B cheapness vs the stock's own quarters and vs industry peers, plus ROE vs own
    history. (The old component was ROE alone — quality, not valuation, at weight 0.45.)"""
    parts = []
    for m in ("pe", "pb"):
        cur = current.get(m)
        if cur is None or cur <= 0:
            continue
        own = [h[m] for h in history if (h.get(m) or 0) > 0]
        if len(own) >= MIN_HISTORY:
            parts.append(percentile_score(own, cur, invert=True))
        if len(peers.get(m, [])) >= MIN_PEERS:
            parts.append(percentile_score(peers[m], cur, invert=True))
    roe_hist = [h["roe"] for h in history if h.get("roe") is not None]
    if current.get("roe") is not None and len(roe_hist) >= MIN_HISTORY:
        parts.append(percentile_score(roe_hist, current["roe"]))
    return sum(parts) / len(parts) if parts else None
