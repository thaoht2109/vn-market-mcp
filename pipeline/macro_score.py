"""sector_macro component (0-100, 50 = neutral) from stored numbers only, no LLM.

Sub-scores: interbank overnight rate, CPI y/y, GDP y/y, 30-day USD/VND move (macro_indicators) and
VN30 breadth above MA50 (market_regime_daily). sector_macro = mean of the sub-scores that are fresh.
VN-Index vs MA200 is left out on purpose: it already gates buy_accumulate (regime), counting it here
too would double-count it.
ponytail: one score for every ticker, no per-sector sensitivity (banks vs exporters to rates/FX);
add a sensitivity map per industry_group if the scores need to differ by sector.
"""
from __future__ import annotations

from datetime import date, timedelta

LEVEL_INDICATORS = ("interbank_overnight", "cpi_yoy", "gdp_yoy")


def _clamp(x: float) -> float:
    return max(0.0, min(100.0, x))


def macro_score(conn, as_of: date, market: dict, cfg: dict) -> tuple[float | None, dict]:
    """(score or None when nothing is fresh, {sub-score name: {value, period, score}})."""
    parts: dict = {}
    for name in LEVEL_INDICATORS:
        c = cfg[name]
        row = conn.execute(
            "SELECT period, value FROM macro_indicators WHERE indicator = %s AND period <= %s"
            " ORDER BY period DESC LIMIT 1", (name, as_of),
        ).fetchone()
        if row and (as_of - row[0]).days <= c["max_age_days"]:
            value = float(row[1])
            parts[name] = {"value": value, "period": row[0].isoformat(),
                           "score": _clamp(50 + (value - c["neutral"]) * c["pts_per_unit"])}

    c = cfg["usd_vnd_change"]
    rows = conn.execute(
        "SELECT period, value FROM macro_indicators WHERE indicator = 'usd_vnd_central'"
        " AND period BETWEEN %s AND %s ORDER BY period", (as_of - timedelta(days=c["window_days"]), as_of),
    ).fetchall()
    if rows and (as_of - rows[-1][0]).days <= c["max_age_days"] and (rows[-1][0] - rows[0][0]).days >= c["min_span_days"]:
        pct = (float(rows[-1][1]) / float(rows[0][1]) - 1) * 100
        parts["usd_vnd_change"] = {"value": pct, "since": rows[0][0].isoformat(), "period": rows[-1][0].isoformat(),
                                   "score": _clamp(50 - pct / c["full_scale_pct"] * 50)}

    breadth = market.get("pct_above_ma50")
    if breadth is not None:
        parts["breadth_ma50"] = {"value": float(breadth), "period": market.get("as_of"), "score": _clamp(float(breadth) * 100)}

    if not parts:
        return None, {}
    return sum(p["score"] for p in parts.values()) / len(parts), parts
