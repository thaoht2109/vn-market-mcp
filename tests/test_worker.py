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


def test_only_ops_problems_reach_telegram(db_conn):
    """Hermes answers on-demand jobs in the user's own chat (it polls get_job_status), so the worker
    sends nothing for them; it only alerts the ops chat about scheduled-run data problems."""
    insert_ticker(db_conn, "WORKERMSG")
    db_conn.execute("DELETE FROM jobs WHERE ticker = 'WORKERMSG'")
    db_conn.commit()
    enqueue(db_conn, "WORKERMSG", job_type="on_demand", requested_by="watch:alice")
    enqueue(db_conn, "WORKERMSG", job_type="on_demand", requested_by="user:alice")
    enqueue(db_conn, "WORKERMSG", job_type="scheduled_post", requested_by="cron")
    results = [
        RunResult(run_id="r1", status="ok", ticker="WORKERMSG", action_label="watch", message=""),
        RunResult(run_id=None, status="data_quality_error", ticker="WORKERMSG", action_label=None, message="gap"),
        RunResult(run_id=None, status="data_quality_error", ticker="WORKERMSG", action_label=None, message="gap"),
    ]
    try:
        with patch("ops.worker.run_analysis", side_effect=results), \
             patch("ops.worker.send_ops_alert") as ops_alert, \
             patch("ops.alerting.send_telegram") as tg:
            for _ in results:
                run_one(db_conn)

        ops_alert.assert_called_once_with("[WORKERMSG] data_quality_error: gap")  # the scheduled one only
        tg.assert_not_called()
    finally:
        db_conn.execute("DELETE FROM jobs WHERE ticker = 'WORKERMSG'")
        db_conn.execute("DELETE FROM tickers WHERE ticker = 'WORKERMSG'")
        db_conn.commit()
