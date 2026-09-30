from datetime import date, datetime, timezone

import pytest

from pipeline.calendar import (
    NoCalendarDataError,
    is_trading_day,
    latest_trading_day,
    next_trading_day,
    seed_calendar_from_weekdays,
)


def test_seed_marks_weekends_and_holidays_as_non_trading(db_conn):
    holidays = {date(2026, 9, 2)}  # Wednesday, national holiday
    count = seed_calendar_from_weekdays(db_conn, date(2026, 8, 31), date(2026, 9, 6), holidays)

    assert count == 7
    assert is_trading_day(db_conn, date(2026, 8, 31)) is True  # Monday
    assert is_trading_day(db_conn, date(2026, 9, 5)) is False  # Saturday
    assert is_trading_day(db_conn, date(2026, 9, 2)) is False  # holiday


def test_is_trading_day_raises_when_no_data(db_conn):
    with pytest.raises(NoCalendarDataError):
        is_trading_day(db_conn, date(2099, 1, 1))


def test_latest_trading_day_skips_weekend(db_conn):
    seed_calendar_from_weekdays(db_conn, date(2026, 8, 31), date(2026, 9, 6), holidays=set())
    as_of = datetime(2026, 9, 6, 10, 0, tzinfo=timezone.utc)  # Sunday
    assert latest_trading_day(db_conn, as_of) == date(2026, 9, 4)  # Friday


def test_next_trading_day_skips_weekend(db_conn):
    seed_calendar_from_weekdays(db_conn, date(2026, 8, 31), date(2026, 9, 8), holidays=set())
    assert next_trading_day(db_conn, date(2026, 9, 4)) == date(2026, 9, 7)  # Fri -> next Mon
