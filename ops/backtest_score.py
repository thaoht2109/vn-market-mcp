"""Replay the official score over stored history and check it against what prices did next. Read-only.

Every --step sessions, for each current VN30 member: the technical and valuation components from only
the data that existed that day (a quarter counts from its quarter end + REPORT_LAG_DAYS), the composite
with the live weights, the label (not holding), and the forward return over each horizon minus the
equal-weight VN30 return over the same window (no VN30 index series is stored).
Flow, macro and news have no history yet (collected since 09-10/2026), so this checks technical +
valuation and the label rules on top of them, at the lower confidence that coverage gives.
ponytail: today's VN30 members over the whole history (survivorship bias), overlapping windows (the
t-stat is optimistic); add point-in-time membership if index_membership gets history.

Also scores the advisor's views (advisor_views) against the system label on the same forward excess.

Usage: python -m ops.backtest_score [--step 5]
"""
from __future__ import annotations

import argparse
import math
from datetime import date, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import yaml

from mcp_server.connection import get_ro_conn
from pipeline.action_label import ActionLabelConfig, ActionLabelInput, action_label
from pipeline.fundamentals import valuation_component
from pipeline.indicators import technical_snapshot
from pipeline.regime import _VN30
from pipeline.risk_plan import RiskPlanConfig, risk_plan
from pipeline.scoring import agreement_ratio, composite_score, confidence, percentile_score, technical_score

REPORT_LAG_DAYS = 45  # VN quarterly statements: due 20-45 days after quarter end
HORIZONS = (20, 60)
WARMUP_BARS = 200
DIRECTION = {"buy_accumulate": 1, "hold": 1, "watch": 0, "stay_out": -1, "reduce_exit": -1}
_RULES = Path(__file__).resolve().parents[1] / "config" / "vn-rules.yaml"
_VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")


def quarter_end(period: str) -> date:
    y, q = int(period[:4]), int(period[-1])
    return date(y + (q == 4), 3 * q % 12 + 1, 1) - timedelta(days=1)


def known_quarters(rows: list[tuple[str, dict]], as_of: date) -> list[dict]:
    """Metrics of the quarters published by as_of, newest first (rows: one per period, newest first)."""
    return [m for p, m in rows if quarter_end(p) + timedelta(days=REPORT_LAG_DAYS) <= as_of]


def forward_excess(prices: dict[str, pd.DataFrame], members: list[str], dates: list[date], i: int, h: int,
                   tickers: list[str]) -> dict[str, float]:
    """{ticker: return from the open after dates[i] to the close h sessions later, minus the same for
    the equal-weight VN30 members}; tickers without both bars are left out."""
    if i + h >= len(dates):
        return {}
    entry, exit_ = dates[i + 1], dates[i + h]

    def ret(t):
        df = prices.get(t)
        if df is None or entry not in df.index or exit_ not in df.index:
            return None
        return df.at[exit_, "close"] / df.at[entry, "open"] - 1

    bench = [r for r in map(ret, members) if r is not None]
    if not bench:
        return {}
    b = sum(bench) / len(bench)
    return {t: r - b for t in tickers if (r := ret(t)) is not None}


def load(conn):
    members = [r[0] for r in conn.execute(_VN30)]
    meta = {t: (g or "other", ex or "HOSE") for t, g, ex in conn.execute("SELECT ticker, industry_group, exchange FROM tickers")}
    prices = {}
    for t, d, o, h, l, c, v in conn.execute(
        "SELECT ticker, trade_date, open, high, low, close, volume FROM prices_daily ORDER BY ticker, trade_date"
    ):
        prices.setdefault(t, []).append((d, float(o), float(h), float(l), float(c), float(v)))
    prices = {t: pd.DataFrame(rows, columns=["trade_date", "open", "high", "low", "close", "volume"])
              .set_index("trade_date", drop=False) for t, rows in prices.items()}
    fund = {}
    for t, p, m in conn.execute(
        "SELECT DISTINCT ON (ticker, period) ticker, period, metrics FROM fundamentals_quarterly"
        " WHERE period ~ '^[0-9]{4}-?Q[1-4]$' ORDER BY ticker, period DESC, version DESC"
    ):
        fund.setdefault(t, []).append((p, m))
    return members, meta, prices, fund


def session_dates(members, prices) -> list[date]:
    """Days on which at least half the members traded (drops a lone ticker's stray bar)."""
    counts = pd.Series([d for t in members if t in prices for d in prices[t].index]).value_counts()
    return sorted(counts[counts >= len(members) / 2].index)


def replay(members, meta, prices, fund, rules, step: int) -> pd.DataFrame:
    dates = session_dates(members, prices)
    daily = pd.DataFrame({t: prices[t]["close"].pct_change() for t in members if t in prices}).mean(axis=1)
    ew = (1 + daily.fillna(0)).cumprod()
    ew_ma200 = ew.rolling(200).mean()
    weights = rules["weights"]["long"]
    label_cfg, plan_cfg = ActionLabelConfig.from_rules(rules), RiskPlanConfig.from_rules(rules)
    out = []
    for i in range(WARMUP_BARS, len(dates) - 1, step):
        d = dates[i]
        regime = "risk_off" if not math.isnan(ew_ma200.get(d, math.nan)) and ew[d] < 0.99 * ew_ma200[d] else "risk_on"
        known = {t: known_quarters(fund.get(t, []), d) for t in members}
        fwd = {h: forward_excess(prices, members, dates, i, h, members) for h in HORIZONS}
        for t in members:
            df = prices.get(t)
            if df is None or d not in df.index:
                continue
            hist = df.loc[:d]
            if len(hist) < WARMUP_BARS:
                continue
            tech = technical_snapshot(hist.reset_index(drop=True))
            close = float(hist["close"].iloc[-1])
            quarters = known[t][:8]
            cur = quarters[0] if quarters else {}
            group = meta.get(t, ("other", "HOSE"))[0]
            peers = {m: [q[0][m] for p, q in known.items() if p != t and q and meta.get(p, ("other",))[0] == group
                         and group != "other" and (q[0].get(m) or 0) > 0] for m in ("pe", "pb")}
            valuation = valuation_component(cur, quarters[1:], peers) if cur else None
            components = {"technical": technical_score(close, tech), "flow": None, "news_events": None,
                          "fundamental_valuation": valuation, "sector_macro": None}
            comp = composite_score(components, weights)
            agreeing, agreement = agreement_ratio(components)
            pb_hist = [q["pb"] for q in quarters[1:] if q.get("pb") is not None]
            plan = risk_plan(close, tech.atr14, meta.get(t, ("", "HOSE"))[1],
                             float((hist["close"] * hist["volume"]).tail(20).mean()), plan_cfg)
            label = action_label(ActionLabelInput(
                coverage_insufficient=False, gate_blocked=False, data_stale=False,
                confidence=confidence(comp.weight_coverage, 1.0, agreement), holding_state="none",
                thesis_invalidated=False, score=comp.score, buy_allowed=True, regime=regime,
                agreeing_sources=agreeing, rr=plan.rr,
                valuation_percentile=percentile_score(pb_hist, cur["pb"]) if cur.get("pb") is not None and pb_hist else 100.0,
            ), label_cfg)
            out.append({"date": d, "ticker": t, "score": comp.score, "technical": components["technical"],
                        "valuation": valuation, "label": label, "regime": regime,
                        **{f"x{h}": fwd[h].get(t) for h in HORIZONS}})
    return pd.DataFrame(out)


def _ic(df: pd.DataFrame, col: str, target: str) -> tuple[float, float, int] | None:
    """Mean cross-sectional Spearman IC per date, share of dates with IC > 0, number of dates."""
    ics = [g[col].rank().corr(g[target].rank()) for _, g in df.dropna(subset=[col, target]).groupby("date")
           if len(g) >= 10]
    ics = [x for x in ics if not math.isnan(x)]
    if not ics:
        return None
    return sum(ics) / len(ics), sum(x > 0 for x in ics) / len(ics), len(ics)


def _pct(x: float) -> str:
    return f"{x * 100:+.2f}%"


def report(df: pd.DataFrame) -> str:
    lines = [f"Mẫu: {len(df)} (mã × ngày), {df['date'].min()} → {df['date'].max()}, "
             f"{df['ticker'].nunique()} mã; có định giá: {df['valuation'].notna().sum()} mẫu."]
    for h in HORIZONS:
        x = f"x{h}"
        sub = df.dropna(subset=[x])
        lines.append(f"\n== Lợi suất vượt trội {h} phiên so với VN30 bình quân ({len(sub)} mẫu) ==")
        lines.append("IC (tương quan thứ hạng theo từng ngày; >0 = điểm cao thì sau đó tốt hơn):")
        for col, name in (("score", "điểm tổng hợp"), ("technical", "kỹ thuật"), ("valuation", "định giá")):
            ic = _ic(sub, col, x)
            lines.append(f"  {name:14s} " + (f"IC {ic[0]:+.3f}, {ic[1]:.0%} số ngày IC>0, {ic[2]} ngày" if ic else "chưa đủ dữ liệu"))
        q = sub.assign(q=sub.groupby("date")["score"].transform(lambda s: (s.rank(pct=True) * 5).clip(upper=4.999) // 1))
        lines.append("Theo nhóm điểm (1 = thấp nhất, 5 = cao nhất trong ngày):")
        for k, g in q.groupby("q"):
            lines.append(f"  nhóm {int(k) + 1}: TB {_pct(g[x].mean())}, {(g[x] > 0).mean():.0%} mẫu thắng VN30, {len(g)} mẫu")
        lines.append(f"Theo nhãn (tỷ lệ nền: {(sub[x] > 0).mean():.0%} mẫu thắng, {(sub[x] < 0).mean():.0%} mẫu thua VN30;"
                     " đúng hướng chỉ có ý nghĩa khi cao hơn tỷ lệ nền):")
        for label, g in sub.groupby("label"):
            right = {1: (g[x] > 0).mean(), -1: (g[x] < 0).mean()}.get(DIRECTION.get(label, 0))
            lines.append(f"  {label:15s} {len(g):5d} mẫu, TB {_pct(g[x].mean())}"
                         + (f", đúng hướng {right:.0%}" if right is not None else ""))
    return "\n".join(lines)


def advisor_report(conn, members, prices) -> str:
    """Advisor stance vs system label on the 20-session forward excess, per saved view."""
    rows = conn.execute("SELECT ticker, stance, code_label, created_at FROM advisor_views ORDER BY created_at").fetchall()
    if not rows:
        return "\n== Cố vấn ==\nChưa có góc nhìn cố vấn nào được lưu."
    dates = session_dates(members, prices)
    h, graded = HORIZONS[0], []
    for ticker, stance, code_label, created in rows:
        day = created.astimezone(_VN_TZ).date()
        i = next((k for k, d in enumerate(dates) if d >= day), None)
        x = forward_excess(prices, members, dates, i, h, [ticker]).get(ticker) if i is not None else None
        if x is not None:
            graded.append((DIRECTION.get(stance, 0), DIRECTION.get(code_label, 0), x))
    lines = [f"\n== Cố vấn ({len(rows)} góc nhìn, {len(graded)} đã đủ {h} phiên để chấm) =="]
    differ = [g for g in graded if g[0] != g[1]]
    if graded:
        lines.append(f"Cùng hướng với nhãn hệ thống: {len(graded) - len(differ)}; khác hướng: {len(differ)}.")
    if differ:
        adv = sum(1 for a, _, x in differ if a * x > 0)
        sys_ = sum(1 for _, s, x in differ if s * x > 0)
        lines.append(f"Khi khác hướng: cố vấn đúng {adv}, hệ thống đúng {sys_} (trung lập không tính).")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--step", type=int, default=5, help="sessions between replayed days")
    args = ap.parse_args()
    rules = yaml.safe_load(_RULES.read_text())
    with get_ro_conn() as conn:
        members, meta, prices, fund = load(conn)
        print(report(replay(members, meta, prices, fund, rules, args.step)))
        print(advisor_report(conn, members, prices))


if __name__ == "__main__":
    main()
