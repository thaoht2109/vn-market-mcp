"""get_advisor_input / save_advice: an independent advisor view next to the system label.

A Hermes sub-agent playing a financial adviser gets the code-rendered report with the conclusion cut
out (label, score, confidence, reference score) plus the asking user's own position, and makes its own
call. It never sees the label first, so it can't just argue for it; the server stores the label that
user saw next to the call, to score both on later prices (ops/backtest_score.py). The official label
and predictions are never touched.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

from mcp_server.connection import get_ro_conn, get_rw_conn
from mcp_server.envelope import build_envelope
from mcp_server.identity import pinned_user
from mcp_server.tools.stock_report import _load, verify_commentary
from pipeline.stock_report import _n, _pct

ADVISOR_TITLE = "**Góc nhìn cố vấn**"
STANCES = {True: ("hold", "reduce_exit"), False: ("buy_accumulate", "watch", "stay_out")}  # by holding
_STANCE_VI = {"buy_accumulate": "tích lũy dần", "watch": "theo dõi, chưa giải ngân", "stay_out": "đứng ngoài",
              "hold": "tiếp tục nắm giữ", "reduce_exit": "giảm tỷ trọng"}
_HIDDEN = ("**Kết luận:**", "_Tính thêm", "_Tín hiệu trong phiên")
_MACRO_SCORE = re.compile(r"Vĩ mô [\d.,]+/100 \([^)]*\): ")


def _advisor_input(conn, ticker: str, user: str | None, report: str, run_as_of: datetime) -> tuple[str, bool]:
    """(report without the system's conclusion + the user's position, whether they hold the ticker)."""
    text = _MACRO_SCORE.sub("Vĩ mô: ", "\n\n".join(p for p in report.split("\n\n") if not p.startswith(_HIDDEN)))
    if user is None:
        return text + "\n\n**Vị thế của người hỏi**\n- Nhóm chung, không có danh mục riêng: xem như chưa nắm giữ.", False
    close = float(conn.execute(
        "SELECT close FROM prices_daily WHERE ticker = %s AND trade_date <= %s::date ORDER BY trade_date DESC LIMIT 1",
        (ticker, run_as_of.astimezone().date()),
    ).fetchone()[0])
    row = conn.execute("SELECT status, avg_cost FROM positions WHERE ticker = %s AND declared_by = %s",
                       (ticker, user)).fetchone()
    holding = bool(row and row[0] == "holding")
    if holding and row[1]:
        lines = [f"Đang nắm giữ, giá vốn {_n(float(row[1]))} → {_pct((close / float(row[1]) - 1) * 100)} so với giá {_n(close)}."]
    else:
        lines = ["Đang nắm giữ, chưa khai giá vốn." if holding else "Chưa nắm giữ mã này."]
    others = conn.execute(
        "SELECT p.ticker, t.industry_group FROM positions p LEFT JOIN tickers t USING (ticker)"
        " WHERE p.declared_by = %s AND p.status = 'holding' AND p.ticker <> %s ORDER BY p.ticker", (user, ticker),
    ).fetchall()
    group = (conn.execute("SELECT industry_group FROM tickers WHERE ticker = %s", (ticker,)).fetchone() or [None])[0]
    same = [t for t, g in others if g and g == group and g != "other"]
    lines.append(("Các mã khác đang giữ: " + ", ".join(t for t, _ in others)
                  + (f"; cùng ngành với {ticker}: {', '.join(same)}" if same else "") + ".") if others
                 else "Không giữ mã nào khác.")
    return text + "\n\n**Vị thế của người hỏi**\n" + "\n".join("- " + l for l in lines), holding


def render_advice(stance: str, code_label: str | None, advice: str) -> str:
    note = (f"_Khác nhãn hệ thống ({_STANCE_VI.get(code_label, code_label)}). Nhãn chính thức không đổi._"
            if code_label and stance != code_label else "_Trùng với nhãn hệ thống._")
    return f"{ADVISOR_TITLE} (ý kiến tham khảo, độc lập với nhãn hệ thống): {_STANCE_VI[stance]}.\n{advice}\n{note}"


def _saved(conn, user: str, run_id: str):
    return conn.execute("SELECT stance, code_label, advice FROM advisor_views WHERE user_id = %s AND run_id = %s",
                        (user, run_id)).fetchone()


def get_advisor_input_tool(ticker: str, run_id: str) -> dict:
    now, ticker, user = datetime.now(timezone.utc), ticker.strip().upper(), pinned_user()
    loaded, err = _load(ticker, run_id)
    if loaded is None:
        return build_envelope({"status": err}, sources=["postgres"], as_of=now)
    with get_ro_conn() as conn:
        saved = _saved(conn, user or "shared", run_id)
        if saved:
            return build_envelope({"status": "advised", "final": render_advice(*saved)}, sources=["postgres"], as_of=now)
        text, holding = _advisor_input(conn, ticker, user, loaded[0], loaded[2])
    return build_envelope({"status": "ok", "input": text, "stances": list(STANCES[holding])},
                          sources=["postgres", "snapshot_file"], as_of=loaded[2])


def save_advice_tool(ticker: str, run_id: str, stance: str, advice: str) -> dict:
    """Same checks as the commentary, against the advisor's input (no invented numbers, no internal
    words, length), with the advisor's own stance as the bar for bullish wording. First view wins."""
    now, ticker, user = datetime.now(timezone.utc), ticker.strip().upper(), pinned_user()
    advice = " ".join(advice.split())
    loaded, err = _load(ticker, run_id)
    if loaded is None:
        return build_envelope({"status": "not_saved", "reason": err}, sources=["postgres"], as_of=now)
    with get_ro_conn() as conn:
        text, holding = _advisor_input(conn, ticker, user, loaded[0], loaded[2])
    issues = [] if stance in STANCES[holding] else [f"stance phải là một trong: {', '.join(STANCES[holding])}"]
    issues += verify_commentary(advice, text, stance)
    if issues:
        return build_envelope({"status": "rejected", "issues": issues}, sources=["postgres"], as_of=now)
    with get_rw_conn() as conn:
        conn.execute(
            "INSERT INTO advisor_views (user_id, run_id, ticker, holding, stance, code_label, advice)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
            (user or "shared", run_id, ticker, holding, stance, loaded[3], advice),
        )
        saved = _saved(conn, user or "shared", run_id)
    return build_envelope({"status": "saved", "final": render_advice(*saved)}, sources=["postgres"], as_of=now)
