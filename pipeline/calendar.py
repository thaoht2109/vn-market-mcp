from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import psycopg

_VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")


class NoCalendarDataError(Exception):
    """Raised when trading_calendar has no row for a requested date.

    Never guess a session date from the weekday alone — Tet and ad-hoc
    compensatory trading days make that wrong (Nguyên tắc 3, fail closed).
    """


def seed_calendar_from_weekdays(
    conn: psycopg.Connection, start: date, end: date, holidays: set[date]
) -> int:
    rows = []
    d = start
    while d <= end:
        is_weekday = d.weekday() < 5
        is_holiday = d in holidays
        rows.append((d, is_weekday and not is_holiday, "holiday" if is_holiday else None))
        d += timedelta(days=1)

    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO trading_calendar (trade_date, is_trading_day, note)
            VALUES (%s, %s, %s)
            ON CONFLICT (trade_date) DO UPDATE
              SET is_trading_day = EXCLUDED.is_trading_day, note = EXCLUDED.note
            """,
            rows,
        )
    return len(rows)


def is_trading_day(conn: psycopg.Connection, d: date) -> bool:
    row = conn.execute(
        "SELECT is_trading_day FROM trading_calendar WHERE trade_date = %s", (d,)
    ).fetchone()
    if row is None:
        raise NoCalendarDataError(f"no trading_calendar row for {d}")
    return row[0]


# First continuous-matching tick (09:00 ATO + 15 min): before it, today's
# session has no bar yet. Real 2026-10-05 incident: an 08:32 Monday run picked
# today, vnstock returned only Sep 30..Oct 2, and completeness failed.
# ponytail: mirrors vn-rules.yaml trading_hours.morning_start; pass it in if it ever changes.
_FIRST_BAR_TIME = time(9, 15)


def latest_trading_day(conn: psycopg.Connection, as_of: datetime) -> date:
    """Latest trading day that already has a bar as of `as_of` (VN time)."""
    now_vn = as_of.astimezone(_VN_TZ)
    cutoff = now_vn.date() if now_vn.time() >= _FIRST_BAR_TIME else now_vn.date() - timedelta(days=1)
    row = conn.execute(
        """
        SELECT trade_date FROM trading_calendar
        WHERE trade_date <= %s AND is_trading_day = true
        ORDER BY trade_date DESC LIMIT 1
        """,
        (cutoff,),
    ).fetchone()
    if row is None:
        raise NoCalendarDataError(f"no trading day found on or before {cutoff}")
    return row[0]


def calendar_covers(conn: psycopg.Connection, as_of: date) -> bool:
    """Whether trading_calendar has been seeded up to (or past) as_of.

    latest_trading_day(conn, now) always returns a date <= now.date() by
    construction, so comparing that result back against `now` can never
    detect a calendar that simply hasn't been re-seeded for the current
    year (see module docstring re: yearly re-seeding). This checks the
    table's actual max row instead, which is independent of `as_of`.
    """
    row = conn.execute("SELECT max(trade_date) FROM trading_calendar").fetchone()
    max_date = row[0] if row else None
    if max_date is None:
        return False
    return max_date >= as_of


def next_trading_day(conn: psycopg.Connection, d: date) -> date:
    row = conn.execute(
        """
        SELECT trade_date FROM trading_calendar
        WHERE trade_date > %s AND is_trading_day = true
        ORDER BY trade_date ASC LIMIT 1
        """,
        (d,),
    ).fetchone()
    if row is None:
        raise NoCalendarDataError(f"no trading day found after {d}")
    return row[0]


def is_trading_hours(conn: psycopg.Connection, now: datetime, trading_hours_cfg: dict) -> bool:
    """§4.4: whether `now` falls inside continuous-trading session hours on a
    trading day. Used to decide the cache-reuse window for chat requests
    (30 min while trading, else reuse until next session) — not for any
    settlement/grading logic, which only ever cares about calendar days."""
    now_vn = now.astimezone(_VN_TZ)
    if not is_trading_day(conn, now_vn.date()):
        return False
    t = now_vn.time()
    morning = time.fromisoformat(trading_hours_cfg["morning_start"]) <= t <= time.fromisoformat(trading_hours_cfg["morning_end"])
    afternoon = time.fromisoformat(trading_hours_cfg["afternoon_start"]) <= t <= time.fromisoformat(trading_hours_cfg["afternoon_end"])
    return morning or afternoon


def trading_day_offset(conn: psycopg.Connection, d: date, n: int) -> date | None:
    """The n-th trading day strictly after d (n >= 1). None if fewer than n
    trading days have elapsed since d yet (not gradeable now) — raises
    NoCalendarDataError only if the calendar isn't seeded far enough to tell
    the two cases apart (trading_calendar is seeded years ahead in practice,
    see seed_calendar_from_weekdays callers, so this should be rare)."""
    rows = conn.execute(
        """
        SELECT trade_date FROM trading_calendar
        WHERE trade_date > %s AND is_trading_day = true
        ORDER BY trade_date ASC LIMIT %s
        """,
        (d, n),
    ).fetchall()
    if len(rows) == n:
        return rows[-1][0]
    if not calendar_covers(conn, d):
        raise NoCalendarDataError(f"no trading_calendar data after {d}")
    return None
