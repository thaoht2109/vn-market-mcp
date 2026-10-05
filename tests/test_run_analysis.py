import json
from contextlib import contextmanager
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import psycopg
import pytest

from llm.providers import ChatResult
from pipeline.calendar import latest_trading_day, seed_calendar_from_weekdays
from pipeline.run_analysis import RunResult, _run_with_retry, run_analysis
from providers.vnstock_provider import ForeignFlowRecord, FundamentalRecord, PriceBar
from tests.conftest import insert_ticker


@pytest.fixture(autouse=True)
def _after_the_close(monkeypatch):
    """Default: run as if the session had closed, whatever the wall clock says (tests run
    mid-session too). In-session behaviour is tested explicitly by overriding this."""
    monkeypatch.setattr("pipeline.run_analysis.is_provisional_session", lambda *a, **k: False)


def _seed_env(
    db_conn, ticker="VNMTEST", exchange="HOSE", industry_group="other", history_days=520,
    calendar_lag_days=0, vn30_member=True,
):
    insert_ticker(db_conn, ticker, industry_group=industry_group, exchange=exchange)
    if vn30_member:
        db_conn.execute(
            "INSERT INTO index_membership (index_code, ticker, valid_from, valid_to) VALUES ('VN30', %s, %s, NULL)",
            (ticker, date(2020, 1, 1)),
        )

    today = datetime.now(timezone.utc).date()
    calendar_end = today - timedelta(days=calendar_lag_days)
    if calendar_lag_days:
        # Simulate a calendar that was never re-seeded past calendar_end: wipe
        # any rows already present up to today (e.g. from a prior real seed).
        db_conn.execute("DELETE FROM trading_calendar WHERE trade_date > %s", (calendar_end,))
    seed_calendar_from_weekdays(db_conn, today - timedelta(days=history_days * 2), calendar_end, holidays=set())

    trading_days = [
        row[0]
        for row in db_conn.execute(
            "SELECT trade_date FROM trading_calendar WHERE is_trading_day = true AND trade_date <= %s"
            " ORDER BY trade_date DESC LIMIT %s",
            (calendar_end, history_days),
        ).fetchall()
    ]
    trading_days.reverse()  # oldest first; last element is the most recent trading day

    closes = np.linspace(80000, 100000, len(trading_days))
    rows = [
        (ticker, d, c, c * 1.01, c * 0.99, c, 2_000_000, c * 2_000_000, "TCBS", datetime.now(timezone.utc))
        for d, c in zip(trading_days[:-1], closes[:-1])  # most recent day comes from the provider below
    ]
    with db_conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO prices_daily (ticker, trade_date, open, high, low, close, volume, value, source, fetched_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (ticker, trade_date) DO NOTHING
            """,
            rows,
        )

    return trading_days[-1], float(closes[-1])


class _StubProvider:
    def __init__(self, latest_day, latest_close):
        self.latest_day = latest_day
        self.latest_close = latest_close
        self.ohlcv_starts = []

    def lookup_listing(self, ticker):
        return None

    def get_ohlcv(self, ticker, start, end):
        self.ohlcv_starts.append(start)
        return [
            PriceBar(
                ticker=ticker, trade_date=self.latest_day, open=self.latest_close,
                high=self.latest_close * 1.01, low=self.latest_close * 0.99, close=self.latest_close,
                volume=2_500_000, value=None, source="TCBS", fetched_at=datetime.now(timezone.utc),
            )
        ]

    def get_fundamentals(self, ticker, quarters):
        return [
            FundamentalRecord(
                ticker=ticker, period=f"2026Q{q}", report_type="audited", version=1,
                metrics={"roe": 0.15 + q * 0.01, "pb": 1.5 + q * 0.05},
                published_date=date(2026, min(3 * q, 12), 20),
                source="TCBS", fetched_at=datetime.now(timezone.utc),
            )
            for q in range(1, 5)
        ]

    def get_foreign_flow(self, ticker, start, end):
        return [
            ForeignFlowRecord(
                ticker=ticker, trade_date=self.latest_day, buy_value=1_000_000, sell_value=800_000,
                net_value=200_000, room_left=500_000, source="TCBS", fetched_at=datetime.now(timezone.utc),
            )
        ]

    def get_news(self, ticker, start, end):
        return []

    def get_market_index(self, symbol, start, end):
        # Empty frame → InsufficientHistoryError inside get_market_regime,
        # which fails open to "risk_on" — these tests don't exercise regime.
        return pd.DataFrame(columns=["trade_date", "open", "high", "low", "close", "volume"])


def test_run_analysis_happy_path_writes_run_and_prediction(db_conn, tmp_path):
    latest_day, latest_close = _seed_env(db_conn, "VNMTEST")
    provider = _StubProvider(latest_day, latest_close)

    result = run_analysis(db_conn, provider, "VNMTEST", tmp_path)

    assert result.status == "ok"
    assert result.action_label is not None

    run_row = db_conn.execute("SELECT snapshot_ref FROM runs WHERE run_id = %s", (result.run_id,)).fetchone()
    assert run_row is not None
    assert Path(run_row[0]).exists()

    prediction_row = db_conn.execute(
        "SELECT action_label, status, horizon_days FROM predictions WHERE ticker = 'VNMTEST'"
    ).fetchone()
    assert prediction_row is not None
    assert prediction_row[0] == result.action_label
    assert prediction_row[1] == "open"
    # §14.6 refresh cycle — read from config/vn-rules.yaml, not hard-coded.
    assert prediction_row[2] == 120


def test_run_analysis_rejects_mis_scaled_bar_mid_history(db_conn, tmp_path):
    # Regression (real 2026-09-28 incident): a worker on a stale image stored
    # one session in thousand-VND while the newer bars around it were
    # correct. Only the last two closes were unit-checked, so the bad bar
    # slipped through and inflated ATR14 ~8x (ACB "dao động bình quân 3,433
    # đồng/phiên" vs 432 real) — and with it every ATR-derived entry/stop/
    # target. Must fail closed instead.
    latest_day, latest_close = _seed_env(db_conn, "VNMTEST")
    bad_day = db_conn.execute(
        "SELECT trade_date FROM prices_daily WHERE ticker = 'VNMTEST' ORDER BY trade_date DESC OFFSET 3 LIMIT 1"
    ).fetchone()[0]
    db_conn.execute(
        "UPDATE prices_daily SET open = open / 1000, high = high / 1000, low = low / 1000, close = close / 1000"
        " WHERE ticker = 'VNMTEST' AND trade_date = %s",
        (bad_day,),
    )

    result = run_analysis(db_conn, _StubProvider(latest_day, latest_close), "VNMTEST", tmp_path)

    assert result.status == "data_quality_error"
    assert str(bad_day) in result.message
    assert db_conn.execute("SELECT count(*) FROM predictions WHERE ticker = 'VNMTEST'").fetchone()[0] == 0


class _Vintage2018Provider(_StubProvider):
    def get_fundamentals(self, ticker, quarters):
        return [replace(r, period=f"2018Q{i}") for i, r in enumerate(super().get_fundamentals(ticker, quarters), 1)]


def test_run_analysis_flags_stale_fundamentals_vintage(db_conn, tmp_path):
    # Regression (real 2026-10-02 incident): fundamentals stuck on 2018
    # quarters. The run must say so and fail closed (data_stale → conservative
    # label, halved confidence) rather than value the stock on a 2018 P/B.
    fresh_day, fresh_close = _seed_env(db_conn, "VNMFRESH")
    fresh = run_analysis(db_conn, _StubProvider(fresh_day, fresh_close), "VNMFRESH", tmp_path)
    stale_day, stale_close = _seed_env(db_conn, "VNMSTALE")
    stale = run_analysis(db_conn, _Vintage2018Provider(stale_day, stale_close), "VNMSTALE", tmp_path)

    def snapshot_of(result):
        ref = db_conn.execute("SELECT snapshot_ref FROM runs WHERE run_id = %s", (result.run_id,)).fetchone()[0]
        return json.loads(Path(ref).read_text())

    assert not any("chỉ số tài chính" in w for w in snapshot_of(fresh)["warnings"])
    assert any("chỉ số tài chính" in w for w in snapshot_of(stale)["warnings"])
    assert snapshot_of(stale)["confidence"] < snapshot_of(fresh)["confidence"]


def test_run_analysis_wires_market_regime_into_action_label(db_conn, tmp_path, monkeypatch):
    # Integration check for the spec §5.7.3/§5.6 regime gate: run_analysis
    # must pass pipeline.regime.get_market_regime's output into
    # ActionLabelInput.regime, not leave it hardcoded to "risk_on". Patches
    # action_label itself to observe the regime it's actually called with,
    # since _StubProvider's fixture ticker never clears every buy_accumulate
    # gate (rr/agreeing_sources/valuation) regardless of regime.
    latest_day, latest_close = _seed_env(db_conn, "VNMTEST")
    seen_regimes = []
    import pipeline.run_analysis as run_analysis_module
    real_action_label = run_analysis_module.action_label

    def _spy_action_label(inp, cfg):
        seen_regimes.append(inp.regime)
        return real_action_label(inp, cfg)

    monkeypatch.setattr(run_analysis_module, "action_label", _spy_action_label)

    class _RiskOffProvider(_StubProvider):
        def get_market_index(self, symbol, start, end):
            dates = [latest_day - timedelta(days=199 - i) for i in range(200)]
            closes = [1000.0] * 199 + [800.0]  # last close far below a flat MA200 -> downtrend
            return pd.DataFrame(
                {
                    "trade_date": dates, "open": closes, "high": closes, "low": closes,
                    "close": closes, "volume": [1_000_000] * 200,
                }
            )

    run_analysis(db_conn, _RiskOffProvider(latest_day, latest_close), "VNMTEST", tmp_path)

    assert seen_regimes == ["risk_off"]


def test_run_analysis_rerun_with_unchanged_label_does_not_duplicate_prediction(db_conn, tmp_path):
    # Review focus #2: re-running for a ticker whose label/thesis hasn't
    # changed must not insert a second open predictions row.
    latest_day, latest_close = _seed_env(db_conn, "VNMTEST")
    provider = _StubProvider(latest_day, latest_close)

    first = run_analysis(db_conn, provider, "VNMTEST", tmp_path)
    second = run_analysis(db_conn, provider, "VNMTEST", tmp_path)

    assert first.action_label == second.action_label
    count = db_conn.execute(
        "SELECT count(*) FROM predictions WHERE ticker = 'VNMTEST' AND status = 'open'"
    ).fetchone()[0]
    assert count == 1


def test_run_analysis_unknown_ticker_is_rejected(db_conn, tmp_path):
    runs_before = db_conn.execute("SELECT count(*) FROM runs").fetchone()[0]

    result = run_analysis(db_conn, _StubProvider(None, None), "NOTATICKER", tmp_path)
    assert result.status == "unknown_ticker"
    assert result.run_id is None

    assert db_conn.execute("SELECT count(*) FROM runs").fetchone()[0] == runs_before


def test_run_analysis_backfills_history_for_a_short_history_ticker(db_conn, tmp_path):
    latest_day, latest_close = _seed_env(db_conn, "SHORTHIST", history_days=30, vn30_member=False)
    provider = _StubProvider(latest_day, latest_close)

    run_analysis(db_conn, provider, "SHORTHIST", tmp_path)

    assert provider.ohlcv_starts[0] <= latest_day - timedelta(days=3 * 365)


def test_run_analysis_fills_gap_from_last_stored_bar(db_conn, tmp_path):
    latest_day, latest_close = _seed_env(db_conn, "GAPHIST", vn30_member=False)
    last_stored = db_conn.execute("SELECT max(trade_date) FROM prices_daily WHERE ticker = 'GAPHIST'").fetchone()[0]
    provider = _StubProvider(latest_day, latest_close)

    run_analysis(db_conn, provider, "GAPHIST", tmp_path)

    assert provider.ohlcv_starts[0] == last_stored


def test_run_analysis_flags_stale_calendar_not_reseeded_for_today(db_conn, tmp_path):
    # Regression: latest_trading_day(conn, now) is always <= now.date() by
    # construction, so feeding it straight back into check_freshness(now, ...)
    # can never fire — it was comparing now against a date derived from now.
    # A trading_calendar that was never re-seeded past a stale cutoff (the
    # real risk: nobody ran the yearly re-seed) must now be caught.
    latest_day, latest_close = _seed_env(db_conn, "VNMTEST", calendar_lag_days=10)
    provider = _StubProvider(latest_day, latest_close)

    result = run_analysis(db_conn, provider, "VNMTEST", tmp_path)

    assert result.status == "ok"
    freshness_row = db_conn.execute(
        "SELECT result FROM data_quality_log WHERE run_id = %s AND check_name = 'freshness'",
        (result.run_id,),
    ).fetchone()
    assert freshness_row is not None
    assert freshness_row[0] == "warn"


_STUB_ADVOCATE_OUTPUT = {"arguments": [], "key_risk_to_thesis": "không có rủi ro đáng kể trong dữ liệu"}
_STUB_VERIFIER_OUTPUT = {"bull_logic_valid": True, "bear_logic_valid": True, "issues": []}
_STUB_NEWS_OUTPUT = {"items": []}


class _FakeSynthesisClient:
    """Routes by role_name (passed through kwargs by call_role) so the same
    injected client can serve every LLM role scheduled_post now triggers —
    only synthesis_daily's output is test-specific, the rest are stubbed."""

    def __init__(self, synthesis_output):
        self._synthesis_output = synthesis_output

    def create(self, **kwargs):
        tool_name = kwargs.get("tool_name", "")
        if tool_name in ("emit_bull_advocate", "emit_bear_advocate"):
            output = _STUB_ADVOCATE_OUTPUT
        elif tool_name == "emit_verifier_logic":
            output = _STUB_VERIFIER_OUTPUT
        elif tool_name == "emit_news_digest":
            output = _STUB_NEWS_OUTPUT
        else:
            output = self._synthesis_output
        return ChatResult(tool_input=output, input_tokens=100, output_tokens=50, cached_tokens=0)


@pytest.fixture
def llm_on(monkeypatch):
    """The pipeline LLM roles are off by default (config llm.pipeline_enabled=false)."""
    monkeypatch.setattr("pipeline.run_analysis.llm_pipeline_enabled", lambda: True)


def test_run_analysis_scheduled_post_runs_no_llm_by_default(db_conn, tmp_path):
    latest_day, latest_close = _seed_env(db_conn, "VNMTEST")
    calls = []

    class _Boom:
        def complete(self, *a, **k):
            calls.append(1)
            raise AssertionError("no LLM may run inside the pipeline")

    result = run_analysis(db_conn, _StubProvider(latest_day, latest_close), "VNMTEST", tmp_path,
                          mode="scheduled_post", llm_clients={"deepseek": _Boom()})

    assert result.status == "ok" and calls == [] and result.report_text is None
    snapshot_ref = db_conn.execute("SELECT snapshot_ref FROM runs WHERE run_id = %s", (result.run_id,)).fetchone()[0]
    snapshot = json.loads(Path(snapshot_ref).read_text())
    assert not {"synthesis", "debate", "news"} & snapshot.keys()
    assert db_conn.execute("SELECT count(*) FROM llm_calls WHERE run_id = %s", (result.run_id,)).fetchone()[0] == 0


def test_run_analysis_scheduled_post_calls_synthesis_and_stores_it(db_conn, tmp_path, llm_on):
    latest_day, latest_close = _seed_env(db_conn, "VNMTEST")
    provider = _StubProvider(latest_day, latest_close)
    synthesis_output = {
        "thesis_summary": "Xu hướng được hỗ trợ bởi dòng tiền tích cực",
        "supporting_points": [{"text": "Chỉ báo động lượng ủng hộ xu hướng", "evidence_ref": "technical"}],
        "contradictions": [],
        "scenarios": [{"name": "base", "description": "Tiếp tục xu hướng hiện tại"}],
        "invalidation_rules": [{"condition": "ROE giảm liên tục", "evidence_ref": "fundamental"}],
        "label_recommendation": {"downgrade_to": None, "reason": ""},
    }

    result = run_analysis(
        db_conn, provider, "VNMTEST", tmp_path, mode="scheduled_post",
        llm_clients={"deepseek": _FakeSynthesisClient(synthesis_output)},
    )

    assert result.status == "ok"
    snapshot_ref = db_conn.execute("SELECT snapshot_ref FROM runs WHERE run_id = %s", (result.run_id,)).fetchone()[0]
    import json
    snapshot = json.loads(Path(snapshot_ref).read_text())
    assert snapshot["synthesis"]["thesis_summary"] == synthesis_output["thesis_summary"]
    assert snapshot["action_label"] == result.action_label  # no downgrade requested
    # label is "watch" here (not stay_out), so Bull/Bear/Verifier must have run too.
    assert snapshot["debate"]["verifier"]["bull_logic_valid"] is True
    assert snapshot["news"] == []


def test_run_analysis_scheduled_post_skips_bull_bear_for_stay_out_label(db_conn, tmp_path, llm_on):
    # Bull/Bear/Verifier cost real LLM calls per spec §5.7.2 ("chỉ cho mã lọt
    # lưới") — a stay_out label (e.g. data_stale gate) has nothing worth
    # debating, so the pipeline must skip them and go straight to Synthesis.
    latest_day, latest_close = _seed_env(db_conn, "VNMTEST", calendar_lag_days=10)
    provider = _StubProvider(latest_day, latest_close)
    synthesis_output = {
        "thesis_summary": "Dữ liệu chưa đủ mới để đánh giá",
        "supporting_points": [], "contradictions": [], "scenarios": [], "invalidation_rules": [],
        "label_recommendation": {"downgrade_to": None, "reason": ""},
    }

    result = run_analysis(
        db_conn, provider, "VNMTEST", tmp_path, mode="scheduled_post",
        llm_clients={"deepseek": _FakeSynthesisClient(synthesis_output)},
    )

    assert result.action_label == "stay_out"
    snapshot_ref = db_conn.execute("SELECT snapshot_ref FROM runs WHERE run_id = %s", (result.run_id,)).fetchone()[0]
    import json
    snapshot = json.loads(Path(snapshot_ref).read_text())
    assert "debate" not in snapshot
    assert snapshot["news"] == []  # news digest still runs regardless of label


def test_run_analysis_scheduled_post_applies_downgrade_recommendation(db_conn, tmp_path, llm_on):
    latest_day, latest_close = _seed_env(db_conn, "VNMTEST")
    provider = _StubProvider(latest_day, latest_close)
    synthesis_output = {
        "thesis_summary": "Rủi ro tăng lên do các yếu tố bất lợi",
        "supporting_points": [],
        "contradictions": [{"text": "Thanh khoản suy yếu gần đây", "evidence_ref": "technical"}],
        "scenarios": [],
        "invalidation_rules": [],
        "label_recommendation": {"downgrade_to": "stay_out", "reason": "Rủi ro thanh khoản"},
    }

    result = run_analysis(
        db_conn, provider, "VNMTEST", tmp_path, mode="scheduled_post",
        llm_clients={"deepseek": _FakeSynthesisClient(synthesis_output)},
    )

    assert result.action_label == "stay_out"
    prediction_row = db_conn.execute(
        "SELECT action_label FROM predictions WHERE ticker = 'VNMTEST' AND status = 'open'"
    ).fetchone()
    assert prediction_row[0] == "stay_out"


def _prediction_rows(db_conn, ticker):
    return db_conn.execute(
        "SELECT action_label FROM predictions WHERE ticker = %s ORDER BY id", (ticker,)
    ).fetchall()


def test_run_analysis_in_session_stores_no_prediction_and_never_shows_a_buy(db_conn, tmp_path, monkeypatch):
    import pipeline.run_analysis as ram
    latest_day, latest_close = _seed_env(db_conn, "VNMTEST")
    monkeypatch.setattr(ram, "is_provisional_session", lambda *a, **k: True)
    monkeypatch.setattr(ram, "action_label", lambda inp, cfg: "buy_accumulate")

    result = run_analysis(db_conn, _StubProvider(latest_day, latest_close), "VNMTEST", tmp_path)

    assert result.status == "ok" and result.action_label == "watch" and result.message == "trigger=provisional"
    assert _prediction_rows(db_conn, "VNMTEST") == []
    snapshot_ref = db_conn.execute("SELECT snapshot_ref FROM runs WHERE run_id = %s", (result.run_id,)).fetchone()[0]
    session = json.loads(Path(snapshot_ref).read_text())["session"]
    assert session == {"provisional": True, "live_label": "buy_accumulate", "official_label": None}


def test_run_analysis_in_session_upgrade_waits_but_stop_breach_downgrades(db_conn, tmp_path, monkeypatch):
    import pipeline.run_analysis as ram
    latest_day, latest_close = _seed_env(db_conn, "VNMTEST")
    provider = _StubProvider(latest_day, latest_close)
    monkeypatch.setattr(ram, "action_label", lambda inp, cfg: "watch")
    run_analysis(db_conn, provider, "VNMTEST", tmp_path)  # after the close: official "watch"
    assert _prediction_rows(db_conn, "VNMTEST") == [("watch",)]

    monkeypatch.setattr(ram, "is_provisional_session", lambda *a, **k: True)
    monkeypatch.setattr(ram, "action_label", lambda inp, cfg: "buy_accumulate")
    up = run_analysis(db_conn, provider, "VNMTEST", tmp_path)
    assert up.action_label == "watch" and _prediction_rows(db_conn, "VNMTEST") == [("watch",)]  # upgrade waits

    monkeypatch.setattr(ram, "action_label", lambda inp, cfg: "stay_out")
    soft = run_analysis(db_conn, provider, "VNMTEST", tmp_path)
    assert soft.action_label == "watch"  # no trigger: noise, official kept

    db_conn.execute("UPDATE predictions SET stop_loss = 1e9 WHERE ticker = 'VNMTEST'")  # price now below the stop
    down = run_analysis(db_conn, provider, "VNMTEST", tmp_path)
    assert down.action_label == "stay_out"
    assert [r[0] for r in _prediction_rows(db_conn, "VNMTEST")] == ["watch", "stay_out"]


def test_run_analysis_on_demand_never_calls_synthesis(db_conn, tmp_path):
    latest_day, latest_close = _seed_env(db_conn, "VNMTEST")
    provider = _StubProvider(latest_day, latest_close)

    # No llm_clients injected and mode defaults to on_demand — if synthesis
    # were accidentally invoked here, it would try a real network call and
    # this test would hang/fail rather than silently pass.
    result = run_analysis(db_conn, provider, "VNMTEST", tmp_path)

    assert result.status == "ok"
    snapshot_ref = db_conn.execute("SELECT snapshot_ref FROM runs WHERE run_id = %s", (result.run_id,)).fetchone()[0]
    import json
    snapshot = json.loads(Path(snapshot_ref).read_text())
    assert "synthesis" not in snapshot


def test_run_analysis_insufficient_coverage_writes_run_but_no_prediction(db_conn, tmp_path):
    insert_ticker(db_conn, "ABC")
    today = datetime.now(timezone.utc).date()
    seed_calendar_from_weekdays(db_conn, today - timedelta(days=10), today, holidays=set())
    trading_day = latest_trading_day(db_conn, datetime.now(timezone.utc))

    class _ThinProvider:
        def get_ohlcv(self, ticker, start, end):
            return [
                PriceBar(
                    ticker=ticker, trade_date=trading_day, open=10000, high=10100, low=9900, close=10000,
                    volume=100, value=None, source="TCBS", fetched_at=datetime.now(timezone.utc),
                )
            ]

        def get_fundamentals(self, ticker, quarters):
            return []

        def get_foreign_flow(self, ticker, start, end):
            return []

    result = run_analysis(db_conn, _ThinProvider(), "ABC", tmp_path)

    assert result.status == "insufficient_coverage"
    assert result.action_label is None

    run_row = db_conn.execute("SELECT run_id FROM runs WHERE run_id = %s", (result.run_id,)).fetchone()
    assert run_row is not None
    prediction_row = db_conn.execute("SELECT id FROM predictions WHERE ticker = 'ABC'").fetchone()
    assert prediction_row is None


def test_run_with_retry_succeeds_after_transient_connection_failure(monkeypatch):
    # Regression: a host-side CLI invocation hit the DB port briefly
    # unreachable while a Docker container was being recreated and paged
    # ops over Telegram for something that resolved itself a moment later.
    monkeypatch.setattr("pipeline.run_analysis.time.sleep", lambda s: None)
    attempts = {"n": 0}

    @contextmanager
    def flaky_get_conn():
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise psycopg.OperationalError("connection failed")
        yield object()

    monkeypatch.setattr(
        "pipeline.run_analysis.run_analysis",
        lambda conn, provider, ticker, snapshot_dir, style, depth: RunResult(
            run_id="r1", status="ok", ticker=ticker, action_label="watch", message="ok",
        ),
    )

    result = _run_with_retry(flaky_get_conn, object(), "FPT", Path("snapshots"), "long", "quick")

    assert result.status == "ok"
    assert attempts["n"] == 3


def test_run_with_retry_raises_after_exhausting_attempts(monkeypatch):
    monkeypatch.setattr("pipeline.run_analysis.time.sleep", lambda s: None)

    @contextmanager
    def always_fails():
        raise psycopg.OperationalError("connection failed")
        yield  # pragma: no cover — unreachable, keeps this a generator

    with pytest.raises(psycopg.OperationalError):
        _run_with_retry(always_fails, object(), "FPT", Path("snapshots"), "long", "quick")


def test_run_analysis_tier_b_confidence_is_capped_not_buy_blocked(db_conn, tmp_path):
    # §10.5: a non-VN30 ticker (tier B) with otherwise strong fundamentals
    # must NOT be hard-blocked from buy_accumulate purely by tier anymore —
    # only its confidence gets capped (config tier_b.confidence_cap), which
    # then has to clear action_labels.buy_accumulate.min_confidence on its
    # own merits, same as tier A.
    latest_day, latest_close = _seed_env(db_conn, "TIERBTEST", vn30_member=False)
    provider = _StubProvider(latest_day, latest_close)

    result = run_analysis(db_conn, provider, "TIERBTEST", tmp_path)

    assert result.status == "ok"
    row = db_conn.execute(
        "SELECT universe_tier, confidence FROM predictions WHERE ticker = 'TIERBTEST'"
    ).fetchone()
    assert row is not None
    universe_tier, confidence_value = row
    assert universe_tier == "B"
    assert float(confidence_value) <= 0.6  # config/vn-rules.yaml tier_b.confidence_cap


def test_upsert_prediction_reuses_older_open_row_with_same_label(db_conn):
    from pipeline.run_analysis import _upsert_prediction

    insert_ticker(db_conn, "VNMFLIP")
    for run_id in ("r1", "r2", "r3"):
        db_conn.execute(
            "INSERT INTO runs (run_id, mode, tickers, style, depth, as_of, snapshot_ref)"
            " VALUES (%s, 'scheduled_pre', ARRAY['VNMFLIP'], 'long', 'quick', now(), 'x')", (run_id,)
        )
    args = dict(universe_tier="vn30", holding_state="none", coverage_detail={}, entry_zone=(1.0, 2.0),
                stop_loss=0.5, target=3.0, confidence_value=0.5, refresh_horizon_days=20)

    watch_id, _ = _upsert_prediction(db_conn, "r1", "VNMFLIP", "watch", **args)
    stay_id, trig = _upsert_prediction(db_conn, "r2", "VNMFLIP", "stay_out", **args)
    assert trig == "label_change" and stay_id != watch_id
    # label flips back to watch while the older watch row is still open: no duplicate-key crash
    again_id, trig = _upsert_prediction(db_conn, "r3", "VNMFLIP", "watch", **args)
    assert (again_id, trig) == (watch_id, "unchanged")
