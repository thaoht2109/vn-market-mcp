"""Price alerts on the official verdict's levels: entry zone, stop loss, target.

Levels come from the latest scheduled_post run made before the session opened (the 15:20
official verdict), so they stay fixed for the whole session. Each check compares the new price
with the state stored by the previous check (alert_state) and alerts only on a transition into a
condition, never while the price simply stays there:

    not met -> met        alert (once per session: user_alerts UNIQUE key)
    met -> still met      nothing
    met -> clearly left   re-arm only once the price is rearm_pct beyond the level, so a price
                          wobbling around the edge doesn't alert again on every check

Two kinds: "touched" from in-session prices (every 15 min, ops.scheduler ALERT_CHECK_SLOTS) and
"confirmed" from the closing price after close_sync. No LLM anywhere: alerts are rendered here and
printed verbatim to each user's chat by their Hermes cron (ops/pending_alerts.py).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

import psycopg
import yaml

from pipeline.action_label import DEFENSIVE_LABELS
from pipeline.stock_report import _STANCE, _n

_VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")
_RULES = Path(__file__).parent.parent / "config" / "vn-rules.yaml"
TOUCHED, CONFIRMED = "touched", "confirmed"


def _met(condition: str, price: float, lo: float, hi: float) -> bool:
    if condition == "entry_zone":
        return lo <= price <= hi
    if condition == "stop_loss":
        return price <= lo
    return price >= lo  # target


def _left(condition: str, price: float, lo: float, hi: float, rearm: float) -> bool:
    if condition == "entry_zone":
        return price < lo * (1 - rearm) or price > hi * (1 + rearm)
    if condition == "stop_loss":
        return price > lo * (1 + rearm)
    return price < lo * (1 - rearm)  # target


def step(inside: bool, condition: str, price: float, lo: float, hi: float, rearm: float) -> tuple[bool, bool]:
    """(new inside state, fire an alert) for one condition given the previous state."""
    if not inside:
        return (True, True) if _met(condition, price, lo, hi) else (False, False)
    return (False, False) if _left(condition, price, lo, hi, rearm) else (True, False)


@dataclass
class Levels:
    run_id: str
    label: str | None
    base_close: float  # close the official run was built on: decides the starting state, no alert
    bounds: dict[str, tuple[float, float]]


def official_levels(conn: psycopg.Connection, ticker: str, session_date: date) -> Levels | None:
    opens_at = datetime.combine(session_date, time(9, 0), _VN_TZ)
    row = conn.execute(
        "SELECT run_id, snapshot_ref, as_of FROM runs WHERE mode = 'scheduled_post' AND %s = ANY(tickers)"
        " AND as_of < %s ORDER BY as_of DESC LIMIT 1",
        (ticker, opens_at),
    ).fetchone()
    if row is None:
        return None
    run_id, snapshot_ref, as_of = row
    try:
        snapshot = json.loads(Path(snapshot_ref).read_text())
    except (OSError, ValueError):
        return None
    plan = snapshot.get("risk_plan")
    if not plan:  # insufficient coverage: no levels to watch
        return None
    base = conn.execute(
        "SELECT close FROM prices_daily WHERE ticker = %s AND trade_date <= %s ORDER BY trade_date DESC LIMIT 1",
        (ticker, as_of.astimezone(_VN_TZ).date()),
    ).fetchone()
    if base is None:
        return None
    stop, target = float(plan["stop_loss"]), float(plan["target"])
    return Levels(
        run_id, snapshot.get("action_label"), float(base[0]),
        {
            "entry_zone": (float(plan["entry_zone"][0]), float(plan["entry_zone"][-1])),
            "stop_loss": (stop, stop),
            "target": (target, target),
        },
    )


def alerts_enabled(conn: psycopg.Connection, user: str, ticker: str) -> bool:
    row = conn.execute(
        "SELECT enabled FROM alert_prefs WHERE user_id = %s AND ticker IN (%s, '*') ORDER BY ticker = '*' LIMIT 1",
        (user, ticker),
    ).fetchone()
    return row is None or row[0]


def set_alerts(conn: psycopg.Connection, user: str, enabled: bool, ticker: str | None = None) -> None:
    """ticker=None switches every ticker, overriding earlier per-ticker choices."""
    if ticker is None:
        conn.execute("UPDATE alert_prefs SET enabled = %s, updated_at = now() WHERE user_id = %s", (enabled, user))
    conn.execute(
        "INSERT INTO alert_prefs (user_id, ticker, enabled) VALUES (%s, %s, %s)"
        " ON CONFLICT (user_id, ticker) DO UPDATE SET enabled = EXCLUDED.enabled, updated_at = now()",
        (user, ticker or "*", enabled),
    )


def _recipients(conn: psycopg.Connection, ticker: str, condition: str) -> list[str]:
    """Entry zone: users watching the ticker without holding it. Stop/target: users holding it."""
    if condition == "entry_zone":
        sql = (
            "SELECT w.added_by FROM watchlist_extra w WHERE w.ticker = %s AND w.status = 'active'"
            " AND w.added_by <> 'legacy' AND NOT EXISTS (SELECT 1 FROM positions p"
            " WHERE p.ticker = w.ticker AND p.declared_by = w.added_by AND p.status = 'holding')"
        )
    else:
        sql = "SELECT declared_by FROM positions WHERE ticker = %s AND status = 'holding'"
    users = [r[0] for r in conn.execute(sql, (ticker,)).fetchall()]
    return [u for u in users if alerts_enabled(conn, u, ticker)]


def watched_tickers(conn: psycopg.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT ticker FROM watchlist_extra WHERE status = 'active' AND added_by <> 'legacy'"
        " UNION SELECT ticker FROM positions WHERE status = 'holding' ORDER BY 1"
    ).fetchall()
    return [r[0] for r in rows]


def message(ticker: str, condition: str, kind: str, price: float, lo: float, hi: float,
            label: str | None, now: datetime) -> str:
    if kind == TOUCHED:
        when = f"giá {_n(price)} lúc {now.astimezone(_VN_TZ):%H:%M} (giá trong phiên, có thể còn đổi)"
    else:
        when = f"đóng cửa {_n(price)}"
    if condition == "entry_zone":
        stance = _STANCE.get(label, label)
        return f"🔔 {ticker} vào vùng mua {_n(lo)}–{_n(hi)}: {when}. Nhãn hệ thống: {stance}."
    if condition == "stop_loss":
        return f"⚠️ {ticker} chạm ngưỡng cắt lỗ {_n(lo)}: {when}."
    return f"🎯 {ticker} đạt mục tiêu {_n(lo)}: {when}."


def evaluate(conn: psycopg.Connection, prices: dict[str, float], kind: str, session_date: date,
             now: datetime, rearm: float) -> int:
    """Advance alert_state with `prices`; queue alerts for each condition just entered. Returns
    the number of user alerts queued."""
    queued = 0
    for ticker, price in prices.items():
        levels = official_levels(conn, ticker, session_date)
        if levels is None:
            continue
        for condition, (lo, hi) in levels.bounds.items():
            state = conn.execute(
                "SELECT run_id, inside FROM alert_state WHERE ticker = %s AND condition = %s AND kind = %s",
                (ticker, condition, kind),
            ).fetchone()
            if state is None or state[0] != levels.run_id:
                # New levels: a condition already met when they were set was in the report the user
                # read, so it is the starting state, not news.
                inside = _met(condition, levels.base_close, lo, hi)
            else:
                inside = state[1]
            inside, fire = step(inside, condition, price, lo, hi, rearm)
            conn.execute(
                """
                INSERT INTO alert_state (ticker, condition, kind, run_id, inside, last_price, checked_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (ticker, condition, kind) DO UPDATE SET run_id = EXCLUDED.run_id,
                  inside = EXCLUDED.inside, last_price = EXCLUDED.last_price, checked_at = EXCLUDED.checked_at
                """,
                (ticker, condition, kind, levels.run_id, inside, price, now),
            )
            if not fire or (condition == "entry_zone" and (levels.label is None or levels.label in DEFENSIVE_LABELS)):
                continue  # no "vào vùng mua" while the verdict says stay out / reduce
            text = message(ticker, condition, kind, price, lo, hi, levels.label, now)
            for user in _recipients(conn, ticker, condition):
                cur = conn.execute(
                    """
                    INSERT INTO user_alerts (user_id, ticker, run_id, condition, kind, session_date, price, message)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING
                    """,
                    (user, ticker, levels.run_id, condition, kind, session_date, price, text),
                )
                queued += cur.rowcount
    return queued  # the caller's job commit (mark_done) persists state and alerts together


def _rearm() -> float:
    return float(yaml.safe_load(_RULES.read_text())["price_alerts"]["rearm_pct"])


def check_intraday(conn: psycopg.Connection, provider, now: datetime) -> int:
    tickers = watched_tickers(conn)
    if not tickers:
        return 0
    return evaluate(conn, provider.get_last_prices(tickers), TOUCHED, now.astimezone(_VN_TZ).date(), now, _rearm())


def check_close(conn: psycopg.Connection, tickers: list[str], session_date: date, now: datetime) -> int:
    """`tickers`: those close_sync just settled — a ticker it failed on still has a mid-session bar."""
    watched = set(watched_tickers(conn)) & set(tickers)
    rows = conn.execute(
        "SELECT ticker, close FROM prices_daily WHERE trade_date = %s AND ticker = ANY(%s)",
        (session_date, sorted(watched)),
    ).fetchall()
    return evaluate(conn, {t: float(c) for t, c in rows}, CONFIRMED, session_date, now, _rearm())


if __name__ == "__main__":  # self-check of the edge/re-arm logic, no DB: python -m pipeline.price_alerts
    s, fired = False, []
    for p in [64.0, 61.5, 61.0, 62.1, 61.9, 62.8, 61.8]:  # zone 60–62, re-arm beyond 62.62
        s, fire = step(s, "entry_zone", p, 60.0, 62.0, 0.01)
        fired.append(fire)
    assert fired == [False, True, False, False, False, False, True], fired
    assert step(False, "stop_loss", 57.9, 58.0, 58.0, 0.01) == (True, True)
    assert step(True, "stop_loss", 58.4, 58.0, 58.0, 0.01) == (True, False)  # within 1%: still "inside"
    assert step(True, "stop_loss", 58.7, 58.0, 58.0, 0.01) == (False, False)
    assert step(False, "target", 70.0, 70.0, 70.0, 0.01) == (True, True)
    print("ok")
