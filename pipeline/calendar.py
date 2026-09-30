from __future__ import annotations

from datetime import date, datetime, timedelta

import psycopg


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


def latest_trading_day(conn: psycopg.Connection, as_of: datetime) -> date:
    row = conn.execute(
        """
        SELECT trade_date FROM trading_calendar
        WHERE trade_date <= %s AND is_trading_day = true
        ORDER BY trade_date DESC LIMIT 1
        """,
        (as_of.date(),),
    ).fetchone()
    if row is None:
        raise NoCalendarDataError(f"no trading day found on or before {as_of.date()}")
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
