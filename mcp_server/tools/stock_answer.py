"""analyze_stock / get_stock_answer: the single-ticker answer flow, run by code.

The SKILL used to walk Hermes through ~8 steps (enqueue, poll the job, read the report, judge news,
read it again, write + retry the commentary, ask the advisor, glue the message together), and one
skipped step broke the answer. Every branch there is deterministic. Only three things need the model:
judging headlines, writing the "Nhận định" and the advisor sub-agent. So the server walks the flow and
returns `next` = the one thing left for the model, and `final` = the exact text to send.

next: judge_news | write_commentary | delegate_advisor (delegate data.advisor_task, then send data.final)
      | send (data.final) | pending (job still running) | failed (data.error) | fallback (no report).
"""
from __future__ import annotations

import time
from datetime import datetime, timezone

from mcp_server.envelope import build_envelope
from mcp_server.identity import writes_shared
from mcp_server.tools.advisor import get_advisor_input_tool
from mcp_server.tools.run_analysis import get_job_status_tool, run_analysis_tool
from mcp_server.tools.stock_report import NEWS_TRIED, REJECTED, get_stock_report_tool
from pipeline.stock_report import COMMENTARY_TITLE, DISCLAIMER

# A run takes ~3 s, ~40 s at worst; a new ticker can take longer. An HTTP host whose clients time out
# sooner lowers these at startup.
WAIT_SECONDS, POLL_SECONDS = 45, 2
COMMENTARY_TRIES = 3  # first draft + 2 fixes, then the report goes out without it
ADVISOR_PENDING = "_Góc nhìn cố vấn đang được chuẩn bị, sẽ gửi ngay sau._"


def analyze_stock_tool(ticker: str, job_id: str | None = None) -> dict:
    """Run (or reuse) the full analysis, wait for it, then the answer. job_id: wait for that job
    instead (the one watch_ticker or an earlier `pending` answer returned)."""
    ticker = ticker.strip().upper()
    if job_id is None:
        started = run_analysis_tool(ticker, style="long", depth="full")["data"]
        if started["status"] == "cache_hit":
            return get_stock_answer_tool(ticker, started["run_id"])
        if started["status"] in ("invalid_ticker", "too_many_pending"):  # refused: no job to wait for
            return _out({"next": "failed", "ticker": ticker, "job_id": None, "error": started["status"]}, started)
        job_id = started["job_id"]
    deadline = time.monotonic() + WAIT_SECONDS
    while True:
        job = get_job_status_tool(job_id)["data"]
        if job["status"] == "done":
            return get_stock_answer_tool(ticker, job["run_id"])
        if job["status"] in ("failed", "not_found"):
            return _out({"next": "failed", "ticker": ticker, "job_id": job_id, "error": job.get("error") or job["status"]})
        if time.monotonic() >= deadline:
            return _out({"next": "pending", "ticker": ticker, "job_id": job_id})
        time.sleep(POLL_SECONDS)


def get_stock_answer_tool(ticker: str, run_id: str) -> dict:
    """Where the answer for run_id stands; call again after judge_news / save_commentary."""
    ticker = ticker.strip().upper()
    got = get_stock_report_tool(ticker, run_id)
    rep = got["data"]
    base = {"ticker": ticker, "run_id": run_id}
    if rep["status"] != "ok":
        return _out({**base, "next": "fallback", "status": rep["status"]}, got)
    shared = writes_shared()  # callers that can't write shared rows skip both steps: their output would reach other users
    if rep["news_to_judge"] and shared:
        NEWS_TRIED.update((ticker, n["id"]) for n in rep["news_to_judge"])  # asked once, never again
        return _out({**base, "next": "judge_news", "news_to_judge": rep["news_to_judge"]}, got)
    rejected, issues = REJECTED.get(run_id, (0, []))
    if rep["needs_commentary"] and shared and rejected < COMMENTARY_TRIES:
        return _out({**base, "next": "write_commentary", "report": rep["report"], "issues": issues}, got)

    body = rep["report"] + (f"\n\n{COMMENTARY_TITLE}\n{rep['commentary']}" if rep["commentary"] else "")
    # Such a caller can't store commentary (shared row), so when none is stored it writes its own into its
    # answer; the flag tells it `final` has no "Nhận định" yet.
    base = {**base, **({"write_own_commentary": True} if rep["needs_commentary"] and not shared else {})}
    adv = get_advisor_input_tool(ticker, run_id)["data"]
    if adv["status"] == "advised":
        return _out({**base, "next": "send", "final": f"{body}\n\n{adv['final']}\n\n{DISCLAIMER}"}, got)
    if adv["status"] == "ok":
        task = adv["advisor_task"]
        return _out({**base, "next": "delegate_advisor", "final": f"{body}\n\n{ADVISOR_PENDING}\n\n{DISCLAIMER}",
                     "advisor_task": {"goal": task["goal"], "output_schema": task["output_schema"],
                                      "context": f"{adv['input']}\nstances: {', '.join(adv['stances'])}"}}, got)
    return _out({**base, "next": "send", "final": f"{body}\n\n{DISCLAIMER}"}, got)


def _out(data: dict, report: dict | None = None) -> dict:
    if report is None:
        return build_envelope(data, sources=["postgres"], as_of=datetime.now(timezone.utc))
    return {**report, "data": data}
