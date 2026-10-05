from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from pipeline.calendar import (
    NoCalendarDataError,
    is_trading_day,
    is_trading_hours,
    latest_trading_day,
    next_trading_day,
    seed_calendar_from_weekdays,
)

_TRADING_HOURS = {
    "morning_start": "09:00", "morning_end": "11:30",
    "afternoon_start": "13:00", "afternoon_end": "15:00",
}


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


def test_latest_trading_day_before_session_opens_uses_previous_day(db_conn):
    seed_calendar_from_weekdays(db_conn, date(2026, 10, 1), date(2026, 10, 6), holidays=set())
    vn = ZoneInfo("Asia/Ho_Chi_Minh")
    assert latest_trading_day(db_conn, datetime(2026, 10, 5, 8, 32, tzinfo=vn)) == date(2026, 10, 2)  # Mon pre-open -> Fri
    assert latest_trading_day(db_conn, datetime(2026, 10, 5, 10, 0, tzinfo=vn)) == date(2026, 10, 5)
    # 00:30 VN Tuesday is still Monday in UTC — must resolve in VN time, pre-open -> Monday
    assert latest_trading_day(db_conn, datetime(2026, 10, 5, 17, 30, tzinfo=timezone.utc)) == date(2026, 10, 5)


def test_next_trading_day_skips_weekend(db_conn):
    seed_calendar_from_weekdays(db_conn, date(2026, 8, 31), date(2026, 9, 8), holidays=set())
    assert next_trading_day(db_conn, date(2026, 9, 4)) == date(2026, 9, 7)  # Fri -> next Mon


def test_is_trading_hours_true_during_morning_session(db_conn):
    seed_calendar_from_weekdays(db_conn, date(2026, 8, 31), date(2026, 9, 4), holidays=set())
    # 2026-09-02 10:00 Asia/Ho_Chi_Minh (UTC+7) == 03:00 UTC, a Wednesday.
    now = datetime(2026, 9, 2, 3, 0, tzinfo=timezone.utc)
    assert is_trading_hours(db_conn, now, _TRADING_HOURS) is True


def test_is_trading_hours_false_during_lunch_break(db_conn):
    seed_calendar_from_weekdays(db_conn, date(2026, 8, 31), date(2026, 9, 4), holidays=set())
    # 12:15 Asia/Ho_Chi_Minh == 05:15 UTC — between morning and afternoon sessions.
    now = datetime(2026, 9, 2, 5, 15, tzinfo=timezone.utc)
    assert is_trading_hours(db_conn, now, _TRADING_HOURS) is False


def test_is_trading_hours_false_on_weekend(db_conn):
    seed_calendar_from_weekdays(db_conn, date(2026, 8, 31), date(2026, 9, 6), holidays=set())
    # 2026-09-05 is a Saturday; 10:00 VN == 03:00 UTC.
    now = datetime(2026, 9, 5, 3, 0, tzinfo=timezone.utc)
    assert is_trading_hours(db_conn, now, _TRADING_HOURS) is False


def test_is_provisional_session_only_while_todays_session_is_open():
    from pipeline.calendar import is_provisional_session

    vn = ZoneInfo("Asia/Ho_Chi_Minh")
    mon = date(2026, 10, 5)
    assert is_provisional_session(mon, datetime(2026, 10, 5, 10, 0, tzinfo=vn)) is True
    assert is_provisional_session(mon, datetime(2026, 10, 5, 12, 0, tzinfo=vn)) is True   # lunch break
    assert is_provisional_session(mon, datetime(2026, 10, 5, 15, 5, tzinfo=vn)) is False  # closed
    assert is_provisional_session(date(2026, 10, 2), datetime(2026, 10, 5, 8, 32, tzinfo=vn)) is False  # prev close
