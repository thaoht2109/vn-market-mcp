"""The answer flow (stock_answer) with the DB-backed tools stubbed: only the branching is under test."""
import pytest

from mcp_server.tools import stock_answer as sa
from mcp_server.tools.stock_report import NEWS_TRIED, REJECTED
from pipeline.stock_report import DISCLAIMER

ENV = {"as_of": "2026-10-14T08:00:00+00:00", "sources": ["postgres"], "warnings": ["w"]}


def _stub(monkeypatch, report, advisor, jobs=()):
    monkeypatch.setattr(sa, "get_stock_report_tool", lambda t, r: {**ENV, "data": dict(report)})
    monkeypatch.setattr(sa, "get_advisor_input_tool", lambda t, r: {**ENV, "data": dict(advisor)})
    monkeypatch.setattr(sa, "run_analysis_tool", lambda t, style, depth: {"data": {"status": "queued", "job_id": "j1"}})
    seq = iter(jobs)
    monkeypatch.setattr(sa, "get_job_status_tool", lambda j: {"data": next(seq)})
    monkeypatch.setattr(sa.time, "sleep", lambda s: None)


REPORT = {"status": "ok", "report": "R", "commentary": None, "needs_commentary": True,
          "news_to_judge": [{"id": 7, "title": "t"}]}
ADV_OK = {"status": "ok", "input": "I", "stances": ["watch", "stay_out"],
          "advisor_task": {"goal": "G", "output_schema": {"type": "object"}}}


def test_flow_asks_news_once_then_commentary_until_tries_run_out_then_advisor(monkeypatch):
    NEWS_TRIED.clear(), REJECTED.clear()
    _stub(monkeypatch, REPORT, ADV_OK, jobs=[{"status": "running"}, {"status": "done", "run_id": "r1"}])

    first = sa.analyze_stock_tool(" abc ")
    assert first["data"]["next"] == "judge_news" and first["data"]["run_id"] == "r1" and first["warnings"] == ["w"]
    assert ("ABC", 7) in NEWS_TRIED  # stock_report._load leaves it out from now on: asked once
    _stub(monkeypatch, {**REPORT, "news_to_judge": []}, ADV_OK)
    assert sa.get_stock_answer_tool("ABC", "r1")["data"]["next"] == "write_commentary"

    REJECTED["r1"] = (1, ["Số 999"])
    again = sa.get_stock_answer_tool("ABC", "r1")["data"]
    assert again["next"] == "write_commentary" and again["issues"] == ["Số 999"]

    REJECTED["r1"] = (sa.COMMENTARY_TRIES, ["x"])  # gave up: report goes out without commentary
    out = sa.get_stock_answer_tool("ABC", "r1")["data"]
    assert out["next"] == "delegate_advisor" and out["final"] == f"R\n\n{sa.ADVISOR_PENDING}\n\n{DISCLAIMER}"
    assert out["advisor_task"]["context"] == "I\nstances: watch, stay_out" and out["advisor_task"]["goal"] == "G"


def test_saved_commentary_and_advice_make_one_message_with_one_disclaimer(monkeypatch):
    rep = {**REPORT, "news_to_judge": [], "needs_commentary": False, "commentary": "C"}
    _stub(monkeypatch, rep, {"status": "advised", "final": "A"})
    final = sa.get_stock_answer_tool("ABC", "r1")["data"]["final"]
    assert final == f"R\n\n**Nhận định**\nC\n\nA\n\n{DISCLAIMER}" and final.count(DISCLAIMER) == 1


def test_callers_without_shared_writes_never_get_those_steps(monkeypatch):
    NEWS_TRIED.clear(), REJECTED.clear()
    _stub(monkeypatch, REPORT, ADV_OK)
    monkeypatch.setattr(sa, "writes_shared", lambda: False)
    out = sa.get_stock_answer_tool("ABC", "r1")["data"]
    assert out["next"] == "delegate_advisor" and not NEWS_TRIED


def test_failed_pending_and_no_report(monkeypatch):
    _stub(monkeypatch, {"status": "not_found"}, ADV_OK, jobs=[{"status": "failed", "error": "unknown_ticker"}])
    assert sa.analyze_stock_tool("ZZZ")["data"] == {"next": "failed", "ticker": "ZZZ", "job_id": "j1", "error": "unknown_ticker"}
    assert sa.get_stock_answer_tool("ZZZ", "r9")["data"]["next"] == "fallback"

    monkeypatch.setattr(sa, "WAIT_SECONDS", 0)
    _stub(monkeypatch, REPORT, ADV_OK, jobs=[{"status": "running"}])
    assert sa.analyze_stock_tool("ABC", job_id="j2")["data"]["next"] == "pending"


def test_refused_start_fails_at_once_instead_of_waiting(monkeypatch):
    monkeypatch.setattr(sa, "run_analysis_tool", lambda t, style, depth: {"data": {"status": "invalid_ticker", "job_id": None}})
    monkeypatch.setattr(sa, "get_job_status_tool", lambda j: pytest.fail("no job to poll"))
    out = sa.analyze_stock_tool("bad ticker")["data"]
    assert out == {"next": "failed", "ticker": "BAD TICKER", "job_id": None, "error": "invalid_ticker"}


def test_caller_without_shared_writes_is_told_to_write_its_own_commentary_only_when_none_is_stored(monkeypatch):
    NEWS_TRIED.clear(), REJECTED.clear()
    monkeypatch.setattr(sa, "writes_shared", lambda: False)
    _stub(monkeypatch, REPORT, ADV_OK)  # needs_commentary=True, nothing stored
    out = sa.get_stock_answer_tool("ABC", "r1")["data"]
    assert out["write_own_commentary"] is True and "**Nhận định**" not in out["final"]

    _stub(monkeypatch, {**REPORT, "needs_commentary": False, "commentary": "C"}, ADV_OK)  # stored and current
    assert "write_own_commentary" not in sa.get_stock_answer_tool("ABC", "r1")["data"]

    monkeypatch.setattr(sa, "writes_shared", lambda: True)  # Telegram: the model saves it via write_commentary instead
    _stub(monkeypatch, {**REPORT, "news_to_judge": []}, ADV_OK)
    assert sa.get_stock_answer_tool("ABC", "r1")["data"]["next"] == "write_commentary"


def test_wait_and_poll_come_from_the_module_settings(monkeypatch):
    clock, slept = [0.0], []

    def sleep(s):
        slept.append(s)
        clock[0] += s

    monkeypatch.setattr(sa, "WAIT_SECONDS", 25)  # what an HTTP host sets at startup
    monkeypatch.setattr(sa, "POLL_SECONDS", 3)
    _stub(monkeypatch, REPORT, ADV_OK, jobs=[{"status": "running"}] * 100)
    monkeypatch.setattr(sa.time, "sleep", sleep)
    monkeypatch.setattr(sa.time, "monotonic", lambda: clock[0])
    assert sa.analyze_stock_tool("ABC", job_id="j")["data"]["next"] == "pending"
    assert set(slept) == {3} and 25 <= clock[0] < 28
