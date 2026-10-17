"""Inputs for Hermes' scheduled digests (08:30 pre-market, Friday weekly). Read-only; Hermes writes the text.

Both return plain facts from Postgres. Nothing here calls an LLM.
"""
from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from mcp_server.connection import get_ro_conn
from mcp_server.envelope import build_envelope
from pipeline.calendar import NoCalendarDataError, is_trading_day
from pipeline.news import load_sources
from pipeline.news_health import news_sources_status, source_warnings

_VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")
# On a non-trading day the digests still carry the latest session's data (a user asking on a weekend gets an answer),
# flagged is_trading_day=false: the scheduled digest prompts turn that flag into [SILENT].
_OFF_DAY = ("Hôm nay không có phiên giao dịch: mọi số liệu là của phiên gần nhất (market.trade_date), "
            "nói rõ điều đó, không gọi là số của hôm nay")

_VN30 = (
    "SELECT ticker FROM index_membership WHERE index_code = 'VN30'"
    " AND (valid_to IS NULL OR valid_to >= CURRENT_DATE)"
)


def _market(conn) -> dict | None:
    row = conn.execute(
        "SELECT trade_date, close, change_pct, ma20, ma50, ma200, rsi14, trend, regime,"
        " computed_at < (trade_date + time '15:00') AT TIME ZONE 'Asia/Ho_Chi_Minh'"
        " FROM market_regime_daily ORDER BY trade_date DESC LIMIT 1"
    ).fetchone()
    if row is None:
        return None
    # provisional: the row was computed mid-session (an intraday run), so close/MA/RSI are not that day's close
    keys = ("trade_date", "close", "change_pct", "ma20", "ma50", "ma200", "rsi14", "trend", "regime", "provisional")
    out = {k: (str(v) if k == "trade_date" else float(v) if isinstance(v, (int, float)) and not isinstance(v, bool)
               or hasattr(v, "quantize") else v)
           for k, v in zip(keys, row)}
    close = out["close"]
    for n in (20, 50, 200):
        ma = out[f"ma{n}"]
        out[f"pct_vs_ma{n}"] = round((close / ma - 1) * 100, 2) if close and ma else None
    ma20 = out["ma20"]
    out["pct_to_ma20"] = round((ma20 / close - 1) * 100, 2) if close and ma20 else None  # gain needed to get back to MA20
    return out


def _moves(conn, sessions: int) -> list[dict]:
    """VN30 % change of the last close vs `sessions` closes earlier, newest data first."""
    rows = conn.execute(
        f"""
        WITH ranked AS (
          SELECT ticker, trade_date, close, row_number() OVER (PARTITION BY ticker ORDER BY trade_date DESC) rn
          FROM prices_daily WHERE ticker IN ({_VN30})
        )
        SELECT a.ticker, a.close, b.close FROM ranked a JOIN ranked b USING (ticker)
        WHERE a.rn = 1 AND b.rn = %s + 1
          -- a ticker without a bar for the latest session (suspended, not yet synced) would compare older days
          AND a.trade_date = (SELECT max(trade_date) FROM prices_daily WHERE ticker IN ({_VN30}))
        """, (sessions,),
    ).fetchall()
    out = [{"ticker": t, "close": float(c), "change_pct": round((float(c) / float(p) - 1) * 100, 2)} for t, c, p in rows]
    return sorted(out, key=lambda r: r["change_pct"], reverse=True)


def _headlines(conn, hours: int, limit: int) -> list[dict]:
    rows = conn.execute(
        f"SELECT published_at, tickers, title, source FROM news_items WHERE published_at >= now() - make_interval(hours => %s)"
        f" AND filter_status IS DISTINCT FROM 'dropped' AND tickers && ARRAY({_VN30}) ORDER BY published_at DESC LIMIT %s",
        (hours, limit),
    ).fetchall()
    return [{"date": p.strftime("%d/%m %H:%M"), "tickers": t, "title": ti, "source": s} for p, t, ti, s in rows]


def _macro_headlines(conn, hours: int, limit: int) -> list[dict]:
    rows = conn.execute(
        "SELECT published_at, title, source FROM news_items WHERE filter_status = 'kept' AND stream = 'A'"
        " AND published_at >= now() - make_interval(hours => %s) ORDER BY published_at DESC LIMIT %s",
        (hours, limit),
    ).fetchall()
    return [{"date": p.astimezone(_VN_TZ).strftime("%d/%m %H:%M"), "title": t, "source": s} for p, t, s in rows]


def _flow(conn, days: int) -> dict:
    """Foreign net value over the last `days` stored sessions (plausible rows only), VN30 total + top buy/sell."""
    rows = conn.execute(
        f"""
        SELECT f.ticker, sum(f.net_value) FROM foreign_flow_daily f JOIN prices_daily p USING (ticker, trade_date)
        WHERE f.ticker IN ({_VN30}) AND abs(f.net_value) <= p.close * p.volume
          AND f.trade_date IN (SELECT DISTINCT trade_date FROM foreign_flow_daily ORDER BY trade_date DESC LIMIT %s)
        GROUP BY 1
        """, (days,),
    ).fetchall()
    per = sorted(((t, float(n) / 1e9) for t, n in rows), key=lambda x: x[1])
    stored = conn.execute("SELECT count(DISTINCT trade_date) FROM foreign_flow_daily").fetchone()[0]
    left_out = conn.execute(
        f"""
        SELECT f.ticker, f.trade_date, f.net_value FROM foreign_flow_daily f JOIN prices_daily p USING (ticker, trade_date)
        WHERE f.ticker IN ({_VN30}) AND abs(f.net_value) > p.close * p.volume
          AND f.trade_date IN (SELECT DISTINCT trade_date FROM foreign_flow_daily ORDER BY trade_date DESC LIMIT %s)
        ORDER BY f.trade_date
        """, (days,),
    ).fetchall()
    return {"sessions": min(days, stored),  # DB only holds flow since it started accruing
            "total_bn_vnd": round(sum(n for _, n in per), 1),
            "top_sell": [{"ticker": t, "net_bn_vnd": round(n, 1)} for t, n in per[:3]],
            "top_buy": [{"ticker": t, "net_bn_vnd": round(n, 1)} for t, n in per[::-1][:3]],
            # net beyond the session's matched-order value (put-through block or bad row): not in the sums above
            "left_out": [{"ticker": t, "date": d.strftime("%d/%m"), "net_bn_vnd": round(float(n) / 1e9, 1)}
                         for t, d, n in left_out]}


def _skip_today(conn, now: datetime) -> bool:
    """True on a non-trading day (weekend/holiday). An unknown calendar fails open: send the digest."""
    try:
        return not is_trading_day(conn, now.astimezone(_VN_TZ).date())
    except NoCalendarDataError:
        return False


def _data_warnings(data: dict) -> list[str]:
    out = [] if data.get("is_trading_day", True) else [_OFF_DAY]
    m = data.get("market")
    if m and m.get("provisional"):
        out.append(f"VN-Index phiên {m['trade_date']} là số tạm tính giữa phiên, chưa phải giá đóng cửa: "
                   "ghi rõ 'tạm tính', không gọi là giá chốt")
    if data["foreign_flow"]["left_out"]:
        out.append("Khối ngoại ròng vượt giá trị khớp lệnh cả phiên nên không tính vào tổng và top: "
                   + ", ".join(f"{x['ticker']} {x['date']} ({x['net_bn_vnd']} tỷ)" for x in data["foreign_flow"]["left_out"])
                   + " — có thể là giao dịch thỏa thuận hoặc lỗi dữ liệu; nếu nhắc tới thì nói rõ như vậy")
    return out


def get_market_digest_input_tool() -> dict:
    now = datetime.now(timezone.utc)
    with get_ro_conn() as conn:
        trading = not _skip_today(conn, now)
        moves = _moves(conn, 1)
        status = news_sources_status(conn, now, [s["name"] for s in load_sources()])
        data = {
            "is_trading_day": trading, "market": _market(conn), "vn30_last_session": {"gainers": moves[:3], "losers": moves[::-1][:3],
                                                          "advancing": sum(m["change_pct"] > 0 for m in moves),
                                                          "declining": sum(m["change_pct"] < 0 for m in moves)},
            "foreign_flow": _flow(conn, 1), "overnight_headlines": _headlines(conn, 18, 30),
            "macro_headlines": _macro_headlines(conn, 18, 15), "news_sources": status,
        }
    return build_envelope(data, sources=["postgres"], as_of=now, warnings=source_warnings(status) + _data_warnings(data))


def get_weekly_digest_input_tool() -> dict:
    now = datetime.now(timezone.utc)
    with get_ro_conn() as conn:
        trading = not _skip_today(conn, now)
        moves = _moves(conn, 5)
        labels = dict(conn.execute(
            f"SELECT action_label, count(DISTINCT ticker) FROM predictions WHERE status = 'open' AND ticker IN ({_VN30})"
            " GROUP BY 1").fetchall())
        data = {
            "is_trading_day": trading, "market": _market(conn),
            "vn30_week": {"gainers": moves[:5], "losers": moves[::-1][:5],
                          "advancing": sum(m["change_pct"] > 0 for m in moves),
                          "declining": sum(m["change_pct"] < 0 for m in moves), "stocks": len(moves)},
            "foreign_flow": _flow(conn, 5), "open_label_counts": labels,
            "week_headlines": _headlines(conn, 24 * 7, 40),
        }
    return build_envelope(data, sources=["postgres"], as_of=now, warnings=_data_warnings(data))
