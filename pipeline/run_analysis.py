from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

import pandas as pd
import psycopg
import yaml

from pipeline.action_label import ActionLabelConfig, ActionLabelInput, action_label
from pipeline.calendar import calendar_covers, latest_trading_day
from pipeline.coverage import (
    CoverageConfig,
    UnknownTickerError,
    classify_universe_tier,
    coverage_check,
    gather_coverage_inputs,
    resolve_ticker,
)
from pipeline.fundamentals import fundamental_snapshot
from pipeline.indicators import technical_snapshot
from pipeline.ingest import IngestBatchError, assert_batch_ok, ingest_batch, ingest_fundamentals_and_flow
from pipeline.positions import get_holding_state
from pipeline.risk_plan import RiskPlanConfig, risk_plan
from pipeline.scoring import agreement_ratio, composite_score, confidence, percentile_score
from pipeline.snapshot import write_snapshot
from providers.vnstock_provider import FundamentalRecord
from quality.checks import (
    check_abnormal_move,
    check_daily_completeness,
    check_freshness,
    check_price_unit_consistency,
    check_volume,
    log_check,
)

CONFIG_PATH = Path(__file__).parent.parent / "config" / "vn-rules.yaml"


@dataclass
class RunResult:
    run_id: str | None
    status: Literal["ok", "unknown_ticker", "insufficient_coverage", "data_quality_error"]
    ticker: str
    action_label: str | None
    message: str


def _load_rules() -> dict:
    return yaml.safe_load(CONFIG_PATH.read_text())


def _existing_open_prediction(conn: psycopg.Connection, ticker: str):
    return conn.execute(
        "SELECT id, action_label, thesis_id FROM predictions"
        " WHERE ticker = %s AND status = 'open' ORDER BY created_at DESC LIMIT 1",
        (ticker,),
    ).fetchone()


def _upsert_prediction(
    conn: psycopg.Connection,
    run_id: str,
    ticker: str,
    label: str | None,
    universe_tier: str,
    holding_state: str,
    coverage_detail: dict,
    entry_zone: tuple[float, float],
    stop_loss: float,
    target: float,
    confidence_value: float,
) -> tuple[int | None, str]:
    if label is None:
        return None, "none"

    existing = _existing_open_prediction(conn, ticker)
    if existing is not None:
        existing_id, existing_label, existing_thesis_id = existing
        if existing_label == label and (existing_thesis_id or 0) == 0:
            return existing_id, "unchanged"  # no duplicate open row on an unchanged re-run (review focus #2)
        trigger = "label_change"
    else:
        trigger = "first"

    row = conn.execute(
        """
        INSERT INTO predictions (
          run_id, source, ticker, trigger, thesis_id, action_label, universe_tier, holding_state,
          coverage, signal_type, entry_zone, stop_loss, target, horizon_days, confidence
        )
        VALUES (%s, %s, %s, %s, NULL, %s, %s, %s, %s, %s, numrange(%s::numeric, %s::numeric), %s, %s, %s, %s)
        RETURNING id
        """,
        (
            run_id, "on_demand", ticker, trigger, label, universe_tier, holding_state,
            json.dumps(coverage_detail), "mixed", entry_zone[0], entry_zone[1], stop_loss, target,
            120, confidence_value,
        ),
    ).fetchone()
    return row[0], trigger


def run_analysis(
    conn: psycopg.Connection,
    provider,
    ticker_raw: str,
    snapshot_dir: Path,
    mode: str = "on_demand",
    style: str = "long",
    depth: str = "quick",
) -> RunResult:
    rules = _load_rules()

    try:
        match = resolve_ticker(conn, ticker_raw)
    except UnknownTickerError as exc:
        return RunResult(run_id=None, status="unknown_ticker", ticker=ticker_raw, action_label=None, message=str(exc))

    ticker = match.ticker
    now = datetime.now(timezone.utc)
    trading_date = latest_trading_day(conn, now)
    run_id = f"{mode}:{ticker}:{int(time.time() * 1000)}"

    outcomes = ingest_batch(conn, provider, [ticker], trading_date)
    try:
        assert_batch_ok(outcomes)
    except IngestBatchError as exc:
        return RunResult(run_id=None, status="data_quality_error", ticker=ticker, action_label=None, message=str(exc))

    ingest_fundamentals_and_flow(conn, provider, ticker)

    completeness = check_daily_completeness(conn, [ticker], trading_date)
    log_check(conn, run_id, ticker, completeness)
    if completeness.result == "fail":
        return RunResult(
            run_id=None, status="data_quality_error", ticker=ticker, action_label=None,
            message=str(completeness.detail),
        )

    price_rows = conn.execute(
        "SELECT trade_date, open, high, low, close, volume FROM prices_daily WHERE ticker = %s ORDER BY trade_date",
        (ticker,),
    ).fetchall()
    df = pd.DataFrame(price_rows, columns=["trade_date", "open", "high", "low", "close", "volume"])
    today_close = float(df.iloc[-1]["close"])

    warnings: list[str] = []
    if len(df) >= 2:
        prev_close = float(df.iloc[-2]["close"])
        unit_check = check_price_unit_consistency(prev_close, today_close)
        log_check(conn, run_id, ticker, unit_check)
        if unit_check.result == "fail":
            return RunResult(
                run_id=None, status="data_quality_error", ticker=ticker, action_label=None,
                message=str(unit_check.detail),
            )

        band_pct = rules["market_bands"].get(match.exchange, 0.07)
        abnormal_check = check_abnormal_move(conn, ticker, trading_date, prev_close, today_close, band_pct)
        log_check(conn, run_id, ticker, abnormal_check)
        if abnormal_check.result == "warn":
            warnings.append(str(abnormal_check.detail))

    volume_check = check_volume(int(df.iloc[-1]["volume"]), is_high_liquidity=True)
    log_check(conn, run_id, ticker, volume_check)
    if volume_check.result == "warn":
        warnings.append(str(volume_check.detail))

    # latest_trading_day(conn, now) is always <= now.date() by construction,
    # so check_freshness(now, trading_date) alone can never fire — it's
    # comparing now against a date derived from now. calendar_covers checks
    # whether trading_calendar was actually seeded through today, which is
    # the real thing that can go stale (needs yearly re-seeding).
    if not calendar_covers(conn, now.date()):
        # Calendar wasn't re-seeded through today: force check_freshness to
        # warn by feeding it a "latest official trading day" one day ahead
        # of as_of, since we have no real one to compare against.
        freshness_check = check_freshness(now, now.date() + timedelta(days=1))
    else:
        freshness_check = check_freshness(now, trading_date)
    log_check(conn, run_id, ticker, freshness_check)
    data_stale = freshness_check.result != "pass"
    if data_stale:
        warnings.append(str(freshness_check.detail))

    tier = classify_universe_tier(conn, ticker)
    coverage_cfg = CoverageConfig.from_rules(rules)
    coverage_inputs = gather_coverage_inputs(conn, ticker)
    coverage_result = coverage_check(tier, coverage_inputs, coverage_cfg)

    snapshot: dict = {
        "ticker": ticker,
        "as_of": now,
        "sources": ["vnstock"],
        "coverage": {
            "sufficient": coverage_result.sufficient,
            "tier": coverage_result.tier,
            "weight_coverage": coverage_result.weight_coverage,
            "component_status": coverage_result.component_status,
        },
        "warnings": warnings,
    }

    if not coverage_result.sufficient:
        snapshot_ref = write_snapshot(snapshot_dir, run_id, snapshot)
        conn.execute(
            """
            INSERT INTO runs (run_id, mode, tickers, style, depth, as_of, snapshot_ref, warnings)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (run_id, mode, [ticker], style, depth, now, snapshot_ref, json.dumps(warnings)),
        )
        return RunResult(
            run_id=run_id, status="insufficient_coverage", ticker=ticker, action_label=None,
            message=f"không đủ dữ liệu để đánh giá (weight_coverage={coverage_result.weight_coverage:.0%})",
        )

    ticker_row = conn.execute("SELECT industry_group FROM tickers WHERE ticker = %s", (ticker,)).fetchone()
    industry_group = ticker_row[0] if ticker_row and ticker_row[0] else "other"

    fundamentals_rows = conn.execute(
        "SELECT period, metrics FROM fundamentals_quarterly WHERE ticker = %s ORDER BY period DESC LIMIT 8",
        (ticker,),
    ).fetchall()
    fundamentals_records = [
        FundamentalRecord(
            ticker=ticker, period=period, report_type="", version=1, metrics=metrics,
            published_date=None, source="db", fetched_at=now,
        )
        for period, metrics in fundamentals_rows
    ]
    fund = fundamental_snapshot(ticker, industry_group, fundamentals_records)
    tech = technical_snapshot(df)

    close_history = df["close"].tolist()[:-1]
    technical_component = percentile_score(close_history, today_close)

    roe_history = [r.metrics.get("roe") for r in fundamentals_records[1:] if r.metrics.get("roe") is not None]
    current_roe = fund.metrics.get("roe")
    fundamental_component = percentile_score(roe_history, current_roe) if current_roe is not None else None

    flow_rows = conn.execute(
        "SELECT net_value FROM foreign_flow_daily WHERE ticker = %s ORDER BY trade_date", (ticker,)
    ).fetchall()
    flow_values = [r[0] for r in flow_rows if r[0] is not None]
    flow_component = percentile_score(flow_values[:-1], flow_values[-1]) if len(flow_values) >= 2 else None

    pb_history = [r.metrics.get("pb") for r in fundamentals_records[1:] if r.metrics.get("pb") is not None]
    current_pb = fund.metrics.get("pb")
    # Fail closed: an unknown valuation must not look cheap — default to the
    # most expensive percentile so it never satisfies max_valuation_percentile.
    valuation_percentile = percentile_score(pb_history, current_pb) if current_pb is not None else 100.0

    component_scores = {
        "technical": technical_component,
        "flow": flow_component,
        "news_events": None,  # LLM role — out of scope for this plan (§3 role table)
        "fundamental_valuation": fundamental_component,
        "sector_macro": None,  # LLM role — out of scope for this plan
    }
    weights = rules["weights"].get(style, rules["weights"]["long"])
    composite = composite_score(component_scores, weights)
    agreeing, agreement = agreement_ratio(component_scores)
    conf = confidence(coverage_result.weight_coverage, 1.0 if not data_stale else 0.5, agreement)

    holding_state = get_holding_state(conn, ticker)

    plan = risk_plan(
        entry_ref_price=today_close,
        atr14=tech.atr14,
        exchange=match.exchange,
        avg_liquidity_value_20d=coverage_inputs.avg_liquidity_value_20d,
        cfg=RiskPlanConfig.from_rules(rules),
    )
    warnings.extend(plan.warnings)

    label_cfg = ActionLabelConfig.from_rules(rules)
    label_input = ActionLabelInput(
        coverage_insufficient=False,
        gate_blocked=False,
        data_stale=data_stale,
        confidence=conf,
        holding_state=holding_state,
        thesis_invalidated=False,
        score=composite.score,
        buy_allowed=(coverage_result.tier == "A"),
        regime="risk_on",  # no macro regime role in this plan — never risk_off by default
        agreeing_sources=agreeing,
        rr=plan.rr,
        valuation_percentile=valuation_percentile,
    )
    label = action_label(label_input, label_cfg)

    snapshot["technical"] = tech
    snapshot["fundamental"] = fund
    snapshot["risk_plan"] = plan
    snapshot["composite_score"] = composite.score
    snapshot["weight_coverage"] = composite.weight_coverage
    snapshot["confidence"] = conf
    snapshot["action_label"] = label

    snapshot_ref = write_snapshot(snapshot_dir, run_id, snapshot)

    conn.execute(
        """
        INSERT INTO runs (run_id, mode, tickers, style, depth, as_of, snapshot_ref, warnings)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (run_id, mode, [ticker], style, depth, now, snapshot_ref, json.dumps(warnings)),
    )

    _prediction_id, trigger = _upsert_prediction(
        conn, run_id, ticker, label, coverage_result.tier, holding_state,
        snapshot["coverage"], plan.entry_zone, plan.stop_loss, plan.target, conf,
    )

    return RunResult(run_id=run_id, status="ok", ticker=ticker, action_label=label, message=f"trigger={trigger}")


if __name__ == "__main__":
    import argparse

    from db.connection import get_conn
    from providers.vnstock_provider import VNStockProvider

    parser = argparse.ArgumentParser(description="Run the Phase 0 pipeline for one ticker (on-demand).")
    parser.add_argument("ticker")
    parser.add_argument("--style", default="long")
    parser.add_argument("--depth", default="quick")
    args = parser.parse_args()

    with get_conn() as conn:
        result = run_analysis(
            conn, VNStockProvider(source="VCI"), args.ticker, Path("snapshots"),
            style=args.style, depth=args.depth,
        )
        print(result)
