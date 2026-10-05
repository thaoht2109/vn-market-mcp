from unittest.mock import patch

from llm.macro import MACRO_JOB_TYPE, MacroDigestResult
from ops.scheduler import MACRO_TICKER
from ops.worker import run_one
from pipeline.jobs import enqueue, get_job
from pipeline.run_analysis import RunResult
from tests.conftest import insert_ticker


def test_run_one_processes_macro_job_without_run_analysis(db_conn):
    """Macro jobs must route to _run_macro_premarket, never pipeline.run_analysis
    (which expects a real ticker row — MACRO_TICKER isn't one)."""
    db_conn.execute("DELETE FROM jobs WHERE ticker = %s", (MACRO_TICKER,))
    db_conn.commit()
    job_key, created = enqueue(db_conn, MACRO_TICKER, job_type=MACRO_JOB_TYPE)
    assert created is True
    db_conn.commit()

    try:
        with patch("ops.worker.get_vn30_tickers", return_value=[]), \
             patch("ops.worker.run_analysis") as mock_run_analysis, \
             patch("ops.worker.run_macro_daily", return_value=MacroDigestResult(noteworthy=False, summary="", news_refs=[])) as mock_macro, \
             patch("ops.worker.send_ops_alert"):
            processed = run_one(db_conn)

        assert processed is True
        mock_run_analysis.assert_not_called()
        mock_macro.assert_called_once()
        assert get_job(db_conn, job_key)["status"] == "done"
    finally:
        db_conn.execute("DELETE FROM jobs WHERE ticker = %s", (MACRO_TICKER,))
        db_conn.commit()


def test_run_one_marks_job_failed_when_run_analysis_does_not_return_ok(db_conn):
    """Regression: run_one used to call mark_done unconditionally, so a
    data_quality_error/insufficient_coverage/unknown_ticker result (returned,
    not raised) was recorded as 'done' instead of 'failed' — invisible to
    anyone checking job status for real failures."""
    insert_ticker(db_conn, "WORKERFAIL")
    db_conn.execute("DELETE FROM jobs WHERE ticker = 'WORKERFAIL'")
    db_conn.commit()
    job_key, created = enqueue(db_conn, "WORKERFAIL", job_type="on_demand")
    assert created is True
    db_conn.commit()

    bad_result = RunResult(
        run_id=None, status="data_quality_error", ticker="WORKERFAIL",
        action_label=None, message="stale data",
    )
    try:
        with patch("ops.worker.run_analysis", return_value=bad_result), \
             patch("ops.worker.send_ops_alert"):
            processed = run_one(db_conn)

        assert processed is True
        job_status = get_job(db_conn, job_key)
        assert job_status["status"] == "failed"
        assert "data_quality_error" in job_status["error"]
    finally:
        db_conn.execute("DELETE FROM jobs WHERE ticker = 'WORKERFAIL'")
        db_conn.execute("DELETE FROM tickers WHERE ticker = 'WORKERFAIL'")
        db_conn.commit()


def test_on_demand_result_goes_only_to_the_requesting_users_chat_with_their_label(db_conn, monkeypatch, tmp_path):
    import json

    from pipeline.positions import set_position

    snap = tmp_path / "s.json"
    snap.write_text(json.dumps({"action_label": "watch", "composite_score": 60, "confidence": 0.7}))

    insert_ticker(db_conn, "WORKERUSR")
    db_conn.execute("DELETE FROM jobs WHERE ticker = 'WORKERUSR'")
    db_conn.execute(
        "INSERT INTO runs (run_id, mode, tickers, style, depth, as_of, snapshot_ref, warnings)"
        " VALUES ('worker-usr-run', 'on_demand', ARRAY['WORKERUSR'], 'long', 'quick', now(), %s, '[]')", (str(snap),),
    )
    db_conn.execute("INSERT INTO users (user_id, chat_id, bot_token_env) VALUES ('alice', '111', 'TELEGRAM_BOT_TOKEN_ALICE')")
    set_position(db_conn, "WORKERUSR", avg_cost=10.0, declared_by="alice")
    db_conn.commit()
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN_ALICE", "alice-token")
    enqueue(db_conn, "WORKERUSR", job_type="on_demand", requested_by="user:alice")
    enqueue(db_conn, "WORKERUSR", job_type="on_demand", requested_by="user:nobody")

    ok = RunResult(run_id="worker-usr-run", status="ok", ticker="WORKERUSR", action_label="watch", message="")
    try:
        with patch("ops.worker.run_analysis", return_value=ok), \
             patch("ops.alerting.send_telegram", return_value=True) as tg, \
             patch("ops.worker.send_ops_alert") as ops_alert:
            run_one(db_conn)
            run_one(db_conn)

        token, chat_id, text = tg.call_args.args
        assert (token, chat_id) == ("alice-token", "111")
        assert "nhãn: hold" in text  # alice holds it: the shared "watch" becomes her "hold"
        ops_alert.assert_called_once()  # unregistered requester: legacy fallback to the ops chat
        assert "nhãn: watch" in ops_alert.call_args.args[0]
    finally:
        db_conn.execute("DELETE FROM jobs WHERE ticker = 'WORKERUSR'")
        db_conn.execute("DELETE FROM positions WHERE ticker = 'WORKERUSR'")
        db_conn.execute("DELETE FROM users WHERE user_id = 'alice'")
        db_conn.execute("DELETE FROM runs WHERE run_id = 'worker-usr-run'")
        db_conn.execute("DELETE FROM tickers WHERE ticker = 'WORKERUSR'")
        db_conn.commit()
