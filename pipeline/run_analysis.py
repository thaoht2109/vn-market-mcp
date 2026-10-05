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

from llm.bull_bear import BullBearValidationError, run_bear_case, run_bull_case
from llm.config import ModelsConfig
from llm.news_digest import NewsDigestValidationError, run_news_digest
from llm.synthesis import SynthesisValidationError, run_synthesis_daily
from llm.client import LLMCallError
from llm.verifier_logic import VerifierPrecheckError, run_verifier_logic
from ops.alerting import log_event
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
from pipeline.fundamentals import fundamental_snapshot, peer_metrics, valuation_component
from pipeline.indicators import technical_snapshot
from pipeline.ingest import IngestBatchError, assert_batch_ok, ingest_batch, ingest_fundamentals_and_flow, ingest_news
from pipeline.positions import get_holding_state
from pipeline.llm_gate import llm_pipeline_enabled
from pipeline.regime import get_market_context
from pipeline.report import render_synthesis_report
from pipeline.risk_plan import RiskPlanConfig, risk_plan
from pipeline.scoring import (
    agreement_ratio, composite_score, confidence, flow_score, percentile_score, technical_score,
)
from pipeline.snapshot import serialize_snapshot, write_snapshot
from providers.vnstock_provider import FundamentalRecord
from quality.checks import (
    check_abnormal_move,
    check_daily_completeness,
    check_freshness,
    check_fundamentals_freshness,
    check_price_series_units,
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
    report_text: str | None = None


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
    refresh_horizon_days: int,
) -> tuple[int | None, str]:
    if label is None:
        return None, "none"

    # one_open_prediction is unique per (ticker, label, thesis), so an older open
    # row with this same label (e.g. watch -> stay_out -> watch) blocks a new
    # INSERT even though it isn't the newest open row: reuse it.
    same_label = conn.execute(
        "SELECT id FROM predictions WHERE ticker = %s AND action_label = %s"
        " AND status = 'open' AND COALESCE(thesis_id, 0) = 0",
        (ticker, label),
    ).fetchone()
    if same_label is not None:
        return same_label[0], "unchanged"

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
            refresh_horizon_days, confidence_value,
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
    llm_clients: dict | None = None,
) -> RunResult:
    """llm_clients: optional {provider_name: StructuredChatClient} override
    forwarded to run_synthesis_daily — lets tests exercise the
    mode == "scheduled_post" synthesis path without a real network call
    (same injection pattern as llm/client.py's call_role)."""
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
        # The whole series, not just prev-vs-today: a mis-scaled session in
        # the middle of the history poisons ATR14 and every level derived from it.
        unit_check = check_price_series_units(df["trade_date"].tolist(), df["close"].astype(float).tolist())
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
        "SELECT period, metrics FROM fundamentals_quarterly WHERE ticker = %s"
        " AND period ~ '^[0-9]{4}-?Q[1-4]$' ORDER BY period DESC LIMIT 8",
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
    # Fail closed on an implausibly old vintage (e.g. 2018 quarters): same
    # path as stale prices — warning, halved confidence, conservative label.
    fundamentals_check = check_fundamentals_freshness(
        max((r.period for r in fundamentals_records), default=None), now.date(),
    )
    log_check(conn, run_id, ticker, fundamentals_check)
    if fundamentals_check.result == "fail":
        data_stale = True
        warnings.append(
            f"chỉ số tài chính mới nhất là kỳ {fundamentals_check.detail.get('latest_period')}, quá cũ để định giá"
        )
    tech = technical_snapshot(df)

    technical_component = technical_score(today_close, tech)

    fundamental_component = valuation_component(
        fund.metrics, [r.metrics for r in fundamentals_records[1:]], peer_metrics(conn, ticker, industry_group),
    )

    flow_rows = conn.execute(
        "SELECT f.net_value, p.value FROM foreign_flow_daily f"
        " JOIN prices_daily p USING (ticker, trade_date)"
        " WHERE f.ticker = %s ORDER BY f.trade_date DESC LIMIT 5", (ticker,),
    ).fetchall()
    flow_component = flow_score(flow_rows)

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
    if coverage_result.tier == "B":
        # §10.5: nhóm B không chờ đủ mẫu chấm điểm riêng trước khi mở
        # buy_accumulate (plan ước tính ngưỡng đó gần như không đạt được) —
        # thay vào đó dùng thống kê chung của nhóm A nhưng ép trần confidence
        # thấp hơn, để buy_accumulate's min_confidence gate tự nhiên lọc bớt
        # các trường hợp yếu hơn mà không cần đếm mẫu per-ticker.
        conf = min(conf, rules["tier_b"]["confidence_cap"])

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
    market = get_market_context(conn, provider, trading_date, now)
    label_input = ActionLabelInput(
        coverage_insufficient=False,
        gate_blocked=False,
        data_stale=data_stale,
        confidence=conf,
        holding_state=holding_state,
        thesis_invalidated=False,
        score=composite.score,
        # §10.5: buy_accumulate no longer hard-blocked by universe_tier — tier
        # B's confidence was already capped above, so the normal
        # buy_min_confidence gate in action_label() does the filtering.
        buy_allowed=True,
        regime=market["regime"],
        agreeing_sources=agreeing,
        rr=plan.rr,
        valuation_percentile=valuation_percentile,
    )
    label = action_label(label_input, label_cfg)

    snapshot["market"] = market
    snapshot["technical"] = tech
    snapshot["fundamental"] = fund
    snapshot["risk_plan"] = plan
    snapshot["composite_score"] = composite.score
    snapshot["weight_coverage"] = composite.weight_coverage
    snapshot["confidence"] = conf
    snapshot["action_label"] = label

    snapshot_ref = write_snapshot(snapshot_dir, run_id, snapshot)

    # runs must exist before Synthesis runs, since llm_calls.run_id has a FK
    # to runs(run_id) — label/snapshot/warnings below may still change if
    # Synthesis recommends a downgrade, so this row (and the snapshot file)
    # gets overwritten after that block, not re-inserted.
    conn.execute(
        """
        INSERT INTO runs (run_id, mode, tickers, style, depth, as_of, snapshot_ref, warnings)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (run_id, mode, [ticker], style, depth, now, snapshot_ref, json.dumps(warnings)),
    )

    # Twice a day is enough for headlines (pre = overnight, post = the session); weekly runs minutes after post.
    if mode in ("scheduled_pre", "scheduled_post"):
        ingest_news(conn, provider, ticker, trading_date - timedelta(days=7), trading_date)

    # LLM roles (spec §5.7.3) run for the post-session cron report and the
    # weekly deep-dive (spec §4.2, synthesis_full role) — on_demand/
    # scheduled_pre stay fast and free of LLM cost/latency. A
    # failed call for any one role just means the report ships without that
    # extra narrative, never blocks the deterministic pipeline (spec §9
    # "nhãn hành động do code sinh").
    if mode in ("scheduled_post", "scheduled_weekly") and label is not None and llm_pipeline_enabled():
        llm_cfg = ModelsConfig.load()
        # evidence_ref validation walks the snapshot as plain dicts — pass
        # the same serialized shape that ends up in the JSON file, not the
        # raw dict of dataclasses (e.g. snapshot["technical"] is a
        # TechnicalSnapshot instance here), or every evidence_ref into a
        # dataclass-valued section would wrongly fail validation.
        serialized_snapshot = serialize_snapshot(snapshot)

        try:
            news_items = provider.get_news(ticker, trading_date - timedelta(days=7), trading_date)
            news_result = run_news_digest(conn, llm_cfg, news_items, run_id=run_id, clients=llm_clients)
            snapshot["news"] = [
                {
                    "event_type": item.event_type, "sentiment": item.sentiment,
                    "impact_horizon": item.impact_horizon, "thesis_relevance": item.thesis_relevance,
                    "confidence": item.confidence,
                }
                for item in news_result.items
            ]
        except (LLMCallError, NewsDigestValidationError) as exc:
            log_event("news_digest_skipped", ticker=ticker, run_id=run_id, error=str(exc))

        # Bull/Bear + Verifier only run for a ticker that "lọt lưới" — a label
        # worth debating. stay_out carries nothing to argue for/against, so
        # skipping it here is the cost-saving gate spec §5.7.2/§11 Phase 5
        # calls for ("Bull/Bear chỉ cho mã lọt lưới").
        if label != "stay_out":
            try:
                bull = run_bull_case(conn, llm_cfg, serialized_snapshot, run_id, clients=llm_clients)
                bear = run_bear_case(conn, llm_cfg, serialized_snapshot, run_id, clients=llm_clients)
                verifier = run_verifier_logic(conn, llm_cfg, serialized_snapshot, bull, bear, run_id, clients=llm_clients)
                snapshot["debate"] = {
                    "bull": {"arguments": bull.arguments, "key_risk_to_thesis": bull.key_risk_to_thesis},
                    "bear": {"arguments": bear.arguments, "key_risk_to_thesis": bear.key_risk_to_thesis},
                    "verifier": {
                        "bull_logic_valid": verifier.bull_logic_valid,
                        "bear_logic_valid": verifier.bear_logic_valid,
                        "issues": verifier.issues,
                    },
                }
            except (LLMCallError, BullBearValidationError, VerifierPrecheckError) as exc:
                log_event("bull_bear_verifier_skipped", ticker=ticker, run_id=run_id, error=str(exc))

        try:
            synthesis = run_synthesis_daily(
                conn, llm_cfg, serialize_snapshot(snapshot), run_id, clients=llm_clients,
                role_name="synthesis_full" if mode == "scheduled_weekly" else "synthesis_daily",
            )
            snapshot["synthesis"] = {
                "thesis_summary": synthesis.thesis_summary,
                "supporting_points": synthesis.supporting_points,
                "contradictions": synthesis.contradictions,
                "scenarios": synthesis.scenarios,
                "invalidation_rules": synthesis.invalidation_rules,
            }
            if synthesis.downgrade_to is not None:
                warnings.append(f"Synthesis đề nghị hạ nhãn xuống {synthesis.downgrade_to}: {synthesis.downgrade_reason}")
                label = synthesis.downgrade_to
                snapshot["action_label"] = label
        except (LLMCallError, SynthesisValidationError) as exc:
            log_event("synthesis_skipped", ticker=ticker, run_id=run_id, error=str(exc))

        # Persist unconditionally: news/debate may have succeeded even if
        # Synthesis above failed, and must not be silently dropped from the
        # snapshot file just because the last role in the chain errored.
        write_snapshot(snapshot_dir, run_id, snapshot)
        conn.execute("UPDATE runs SET warnings = %s WHERE run_id = %s", (json.dumps(warnings), run_id))

    _prediction_id, trigger = _upsert_prediction(
        conn, run_id, ticker, label, coverage_result.tier, holding_state,
        snapshot["coverage"], plan.entry_zone, plan.stop_loss, plan.target, conf,
        rules["predictions"]["refresh_horizon_days"],
    )

    report_text = render_synthesis_report(snapshot) if mode in ("scheduled_post", "scheduled_weekly") else None
    return RunResult(
        run_id=run_id, status="ok", ticker=ticker, action_label=label, message=f"trigger={trigger}",
        report_text=report_text,
    )


def _run_with_retry(get_conn_fn, provider, ticker: str, snapshot_dir: Path, style: str, depth: str) -> RunResult:
    """Transient connection failures (e.g. the host DB port briefly
    unreachable while a container is being recreated) shouldn't page ops on
    their own — retry a couple times before giving up."""
    for attempt in range(3):
        try:
            with get_conn_fn() as conn:
                return run_analysis(conn, provider, ticker, snapshot_dir, style=style, depth=depth)
        except psycopg.OperationalError:
            if attempt == 2:
                raise
            log_event("run_db_connect_retry", ticker=ticker, attempt=attempt + 1)
            time.sleep(2 * (attempt + 1))


if __name__ == "__main__":
    import argparse

    from db.connection import get_conn
    from ops.alerting import send_ops_alert
    from providers.vnstock_provider import VNStockProvider

    parser = argparse.ArgumentParser(description="Run the Phase 0 pipeline for one ticker (on-demand).")
    parser.add_argument("ticker")
    parser.add_argument("--style", default="long")
    parser.add_argument("--depth", default="quick")
    args = parser.parse_args()

    log_event("run_started", ticker=args.ticker, style=args.style, depth=args.depth)
    try:
        result = _run_with_retry(
            get_conn, VNStockProvider(source="VCI"), args.ticker, Path("snapshots"), args.style, args.depth,
        )
        if result.status == "ok":
            log_event("run_finished", ticker=args.ticker, status=result.status, action_label=result.action_label)
        else:
            log_event("run_finished_with_issue", ticker=args.ticker, status=result.status, message=result.message)
            send_ops_alert(f"[vn-market-mcp] {args.ticker}: {result.status} — {result.message}")
        print(result)
    except Exception as exc:
        log_event("run_failed", ticker=args.ticker, error=str(exc), error_type=type(exc).__name__)
        send_ops_alert(f"[vn-market-mcp] LỖI khi chạy {args.ticker}: {type(exc).__name__}: {exc}")
        raise
