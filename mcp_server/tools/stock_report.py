"""get_stock_report / save_commentary.

The data part of the report is rendered by code (pipeline/stock_report.py, free, complete). The AI
"Nhận định" is written once by Hermes and stored; it is replayed while the fingerprint of the numbers
it was based on is unchanged, and rewritten (needs_commentary=True) when anything moves.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import yaml

from mcp_server.connection import get_ro_conn, get_rw_conn
from mcp_server.envelope import build_envelope
from mcp_server.tools.snapshot import get_snapshot_tool
from pipeline.calendar import NoCalendarDataError, is_trading_hours
from pipeline.stock_report import COMMENTARY_TITLE, DISCLAIMER, render_stock_report

# Bump when the report layout / commentary instructions change, so old commentary is never replayed.
REPORT_TEMPLATE_VERSION = "v6"
_RULES = Path(__file__).resolve().parents[2] / "config" / "vn-rules.yaml"
# Deterministic inputs of the report; excludes as_of / run ids / LLM text (synthesis, debate, news).
_SNAPSHOT_KEYS = ("technical", "fundamental", "risk_plan", "composite_score", "weight_coverage",
                  "confidence", "action_label", "warnings", "market")


def fingerprint(snapshot: dict, closes: list, flow: list, news: list = ()) -> str:
    core = {k: snapshot.get(k) for k in _SNAPSHOT_KEYS}
    if core["market"]:
        core["market"] = {k: v for k, v in core["market"].items() if k != "as_of"}
    core["px"] = [[float(c), float(v or 0)] for c, v in closes]
    core["flow"] = [[str(d), round(float(n or 0))] for d, _b, _s, n in flow]
    core["news"] = [[str(d), t] for d, t, _src in news]
    return hashlib.sha256(json.dumps(core, sort_keys=True, default=str).encode()).hexdigest()


def _load(ticker: str, run_id: str):
    """Everything the report is built from, or (None, reason)."""
    got = get_snapshot_tool(run_id=run_id)
    data = got["data"]
    if data.get("status") != "ok":
        return None, data.get("status", "not_found")
    snapshot, run_as_of = data["snapshot"], datetime.fromisoformat(got["as_of"])
    trading_hours = yaml.safe_load(_RULES.read_text())["trading_hours"]
    with get_ro_conn() as conn:
        name = (conn.execute("SELECT name FROM tickers WHERE ticker = %s", (ticker,)).fetchone() or [None])[0]
        px = conn.execute(
            "SELECT close, volume FROM prices_daily WHERE ticker = %s AND trade_date <= %s::date"
            " ORDER BY trade_date DESC LIMIT 2", (ticker, run_as_of.astimezone().date()),
        ).fetchall()
        if not px:
            return None, "no_prices"
        fund = conn.execute(
            "SELECT DISTINCT ON (period) period, metrics FROM fundamentals_quarterly"
            " WHERE ticker = %s AND period ~ '^[0-9]{4}-?Q[1-4]$' ORDER BY period DESC, version DESC LIMIT 8",
            (ticker,),
        ).fetchall()
        flow = conn.execute(
            "SELECT f.trade_date, f.buy_value, f.sell_value, f.net_value FROM foreign_flow_daily f"
            " JOIN prices_daily p USING (ticker, trade_date)"
            " WHERE f.ticker = %s AND abs(f.net_value) <= p.close * p.volume"  # drop implausible rows
            " ORDER BY f.trade_date DESC LIMIT 5", (ticker,),
        ).fetchall()
        news = conn.execute(
            "SELECT published_at, title, source FROM news_items WHERE %s = ANY(tickers)"
            " AND published_at >= now() - interval '7 days' ORDER BY published_at DESC LIMIT 5", (ticker,),
        ).fetchall()
        try:
            in_session = is_trading_hours(conn, run_as_of, trading_hours)
        except NoCalendarDataError:
            in_session = False
    report = render_stock_report(
        snapshot, ticker=ticker, name=name, as_of=run_as_of, close=float(px[0][0]),
        prev_close=float(px[1][0]) if len(px) > 1 else None, volume=float(px[0][1]),
        in_session=in_session, fund_period=fund[0][0] if fund else None, fund_history=fund, flow_rows=flow,
        news_rows=news,
    )
    return (report, fingerprint(snapshot, px, flow, news), run_as_of, snapshot.get("action_label")), None


# --- deterministic verifier for the AI "Nhận định" (replaces the old LLM verifier_logic role) ---
_NUM = re.compile(r"\d+(?:[.,]\d+)*")
_INDICATOR = re.compile(r"\b(?:MA|RSI|ATR)\s?\(?(?:20|50|200|14)\)?", re.IGNORECASE)
_YEAR_OR_QUARTER = re.compile(r"\b(?:19|20)\d{2}(?:-?Q[1-4])?\b|\bQ[1-4]\b")
_SMALL_COUNT_MAX = 12  # "3 phiên", "8 quý", "2 tín hiệu" — counts, not data
_BULLISH = ("nên mua", "mua vào", "giải ngân ngay", "khuyến nghị mua", "mua mạnh", "gom mua", "tích lũy dần",
            "nên giải ngân", "cơ hội mua")
_INTERNAL = ("composite", "weight_coverage", "risk_on", "risk_off", "run_id", "snapshot", "mcp", "tool",
             "percentile", "get_", "action_label", "hệ thống tính", "evidence")
COMMENTARY_WORDS = (80, 220)


def _numbers(text: str) -> set[str]:
    return {m.group(0) for m in _NUM.finditer(text)}


def verify_commentary(commentary: str, report: str, label: str | None) -> list[str]:
    """Issues that make the commentary unsafe to send/cache; [] = OK.

    Rules (all checked against the code-rendered report, the single source of truth):
    every number must appear in the report; never more bullish than the stored label;
    no internal/system vocabulary; length within COMMENTARY_WORDS."""
    issues = []
    text = commentary.replace("−", "-")
    scrubbed = _YEAR_OR_QUARTER.sub("", _INDICATOR.sub("", text))
    known = _numbers(report.replace("−", "-"))
    bad = sorted(n for n in _numbers(scrubbed)
                 if n not in known and not (n.isdigit() and int(n) <= _SMALL_COUNT_MAX))
    if bad:
        issues.append("Số liệu không có trong báo cáo (bịa hoặc tự tính): " + ", ".join(bad)
                      + ". Chỉ dùng đúng con số đã in trong báo cáo, giữ nguyên cách viết.")
    low = text.lower()
    if label != "buy_accumulate":
        hits = [p for p in _BULLISH if p in low]
        if hits:
            issues.append(f"Lạc quan hơn nhãn của hệ thống ({label}): bỏ các cụm " + ", ".join(hits)
                          + ". Chỉ được thận trọng hơn, không được tích cực hơn.")
    leaks = [w for w in _INTERNAL if w in low]
    if leaks:
        issues.append("Dùng từ ngữ nội bộ/kỹ thuật hệ thống: " + ", ".join(leaks) + ". Diễn đạt bằng lời nhà đầu tư.")
    words = len(commentary.split())
    if not COMMENTARY_WORDS[0] <= words <= COMMENTARY_WORDS[1]:
        issues.append(f"Độ dài {words} từ, cần {COMMENTARY_WORDS[0]}–{COMMENTARY_WORDS[1]} từ.")
    return issues


def get_stock_report_tool(ticker: str, run_id: str) -> dict:
    now = datetime.now(timezone.utc)
    loaded, err = _load(ticker, run_id)
    if loaded is None:
        return build_envelope({"status": err}, sources=["postgres"], as_of=now)
    report, fp, run_as_of, _label = loaded
    with get_ro_conn() as conn:
        row = conn.execute(
            "SELECT template_version, fingerprint, commentary FROM report_commentary WHERE ticker = %s", (ticker,),
        ).fetchone()
    cached = row[2] if row and row[0] == REPORT_TEMPLATE_VERSION and row[1] == fp else None
    data = {"status": "ok", "report": report, "commentary": cached, "needs_commentary": cached is None}
    if cached:
        data["final"] = f"{report}\n\n{COMMENTARY_TITLE}\n{cached}\n\n{DISCLAIMER}"
    return build_envelope(data, sources=["postgres", "snapshot_file"], as_of=run_as_of)


def save_commentary_tool(ticker: str, run_id: str, commentary: str) -> dict:
    now = datetime.now(timezone.utc)
    if not commentary.strip():
        raise ValueError("commentary is empty")
    loaded, err = _load(ticker, run_id)  # fingerprint recomputed server-side, never taken from the caller
    if loaded is None:
        return build_envelope({"status": "not_saved", "reason": err}, sources=["postgres"], as_of=now)
    issues = verify_commentary(commentary, loaded[0], loaded[3])
    if issues:  # not cached, not to be sent: Hermes must fix these and call again
        return build_envelope({"status": "rejected", "issues": issues}, sources=["postgres"], as_of=now)
    with get_rw_conn() as conn:
        conn.execute(
            """
            INSERT INTO report_commentary (ticker, template_version, fingerprint, run_id, commentary, created_at)
            VALUES (%s, %s, %s, %s, %s, now())
            ON CONFLICT (ticker) DO UPDATE SET template_version = EXCLUDED.template_version,
              fingerprint = EXCLUDED.fingerprint, run_id = EXCLUDED.run_id,
              commentary = EXCLUDED.commentary, created_at = EXCLUDED.created_at
            """,
            (ticker, REPORT_TEMPLATE_VERSION, loaded[1], run_id, commentary.strip()),
        )
    return build_envelope({"status": "saved"}, sources=["postgres"], as_of=now)
