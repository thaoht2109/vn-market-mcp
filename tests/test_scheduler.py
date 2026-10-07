from datetime import date, datetime, timezone

from pipeline.calendar import seed_calendar_from_weekdays
from pipeline.jobs import get_job
from ops.scheduler import MACRO_TICKER, get_watchlist, run_due_jobs
from tests.conftest import insert_ticker


def test_run_due_jobs_enqueues_weekly_on_friday_vn30_only(db_conn):
    insert_ticker(db_conn, "WEEKVN30")
    insert_ticker(db_conn, "WEEKEXTRA")
    db_conn.execute(
        "INSERT INTO index_membership (index_code, ticker, valid_from, valid_to) VALUES ('VN30', 'WEEKVN30', %s, NULL)",
        (date(2026, 1, 1),),
    )
    db_conn.execute(
        "INSERT INTO watchlist_extra (ticker, added_by, confirmed_at, status) VALUES ('WEEKEXTRA', 'u1', now(), 'active')"
    )
    friday = date(2020, 1, 10)
    seed_calendar_from_weekdays(db_conn, friday, friday, holidays=set())
    db_conn.commit()

    try:
        now = datetime(2020, 1, 10, 8, 35, tzinfo=timezone.utc)  # past the 08:30 weekly trigger
        fired: set[str] = set()
        run_due_jobs(db_conn, now, fired)

        assert get_job(db_conn, f"scheduled_weekly:WEEKVN30:{friday.isoformat()}")["status"] == "queued"
        assert get_job(db_conn, f"scheduled_weekly:WEEKEXTRA:{friday.isoformat()}") is None
        assert f"scheduled_weekly:{friday.isoformat()}" in fired

        run_due_jobs(db_conn, now, fired)  # second call same day must not duplicate
    finally:
        db_conn.execute("DELETE FROM jobs WHERE job_key LIKE %s", (f"%:{friday.isoformat()}%",))  # trailing % also matches collect_rss slot suffixes
        db_conn.execute("DELETE FROM index_membership WHERE ticker = 'WEEKVN30'")
        db_conn.execute("DELETE FROM watchlist_extra WHERE ticker = 'WEEKEXTRA'")
        db_conn.commit()


def test_run_due_jobs_enqueues_watchlist_once_per_day(db_conn, monkeypatch):
    monkeypatch.setattr("ops.scheduler.llm_pipeline_enabled", lambda: True)  # macro digest is an LLM job
    insert_ticker(db_conn, "SCHEDTEST")
    db_conn.execute(
        "INSERT INTO index_membership (index_code, ticker, valid_from, valid_to) VALUES ('VN30', 'SCHEDTEST', %s, NULL)",
        (date(2026, 1, 1),),
    )
    # A date far in the past so run_due_jobs enqueueing the REAL watchlist
    # (index_membership has real VN30 rows in this shared DB) never collides
    # with today's real scheduled jobs.
    today = date(2020, 1, 7)  # a Tuesday
    seed_calendar_from_weekdays(db_conn, today, today, holidays=set())
    db_conn.commit()

    try:
        assert "SCHEDTEST" in get_watchlist(db_conn)

        now = datetime(2020, 1, 7, 8, 20, tzinfo=timezone.utc)  # past the 08:15 scheduled_post trigger
        fired: set[str] = set()
        run_due_jobs(db_conn, now, fired)

        job_key = f"scheduled_post:SCHEDTEST:{today.isoformat()}"
        assert get_job(db_conn, job_key)["status"] == "queued"
        assert f"scheduled_post:{today.isoformat()}" in fired

        macro_job_key = f"macro_premarket:{MACRO_TICKER}:{today.isoformat()}"
        assert get_job(db_conn, macro_job_key)["status"] == "queued"

        # Second call same day must not enqueue a duplicate job (dedup via `fired`).
        run_due_jobs(db_conn, now, fired)

        run_due_jobs(db_conn, datetime(2020, 1, 7, 8, 50, tzinfo=timezone.utc), fired)  # 15:50 VN
        assert get_job(db_conn, f"close_sync:{MACRO_TICKER}:{today.isoformat()}")["status"] == "queued"
    finally:
        db_conn.execute("DELETE FROM jobs WHERE job_key LIKE %s", (f"%:{today.isoformat()}%",))
        db_conn.execute("DELETE FROM index_membership WHERE ticker = 'SCHEDTEST'")
        db_conn.commit()


def test_intraday_slot_fires_during_the_session_only():
    from datetime import datetime, timezone
    from ops.scheduler import intraday_slot

    def utc(h, m, day=2):  # 2026-10-02 is a Friday
        return datetime(2026, 10, day, h, m, tzinfo=timezone.utc)

    assert [intraday_slot(utc(h, m)) for h, m in ((2, 30), (4, 0), (6, 0))] == ["0930", "1100", "1300"]
    assert intraday_slot(utc(8, 0)) is None          # 15:00 VN: close_sync/post own the close
    assert intraday_slot(utc(2, 0)) is None          # 09:00 VN, ATO — no bar yet
    assert intraday_slot(utc(5, 0)) is None          # 12:00 VN lunch break
    assert intraday_slot(utc(1, 0)) is None          # 08:00 VN
    assert intraday_slot(utc(3, 0, day=3)) is None   # Saturday


def test_macro_digest_is_not_scheduled_while_pipeline_llm_is_off():
    from datetime import datetime, timezone
    from ops.scheduler import _run_macro_premarket_if_due

    class _NoDb:
        def __getattr__(self, name):
            raise AssertionError("must not touch the DB when the pipeline LLM is off")

    fired: set[str] = set()
    _run_macro_premarket_if_due(_NoDb(), datetime(2026, 10, 5, 2, 0, tzinfo=timezone.utc), fired)
    assert fired == set()


def test_close_sync_runs_before_post_and_is_retried_in_the_evening(db_conn):
    from ops.scheduler import CLOSE_SYNC_RETRY_TRIGGER, CLOSE_SYNC_TRIGGER, SCHEDULE

    day = date(2020, 1, 8)  # Wednesday
    seed_calendar_from_weekdays(db_conn, day, day, holidays=set())
    db_conn.commit()
    post = [t for name, t, _ in SCHEDULE if name == "scheduled_post"][0]
    assert CLOSE_SYNC_TRIGGER < post < CLOSE_SYNC_RETRY_TRIGGER  # 15:05 < 15:20 < 18:00 VN
    assert all(name != "scheduled_pre" for name, _, _ in SCHEDULE)

    try:
        fired: set[str] = set()
        run_due_jobs(db_conn, datetime(2020, 1, 8, 8, 10, tzinfo=timezone.utc), fired)  # 15:10 VN
        assert get_job(db_conn, f"close_sync:{MACRO_TICKER}:{day.isoformat()}")["status"] == "queued"
        assert get_job(db_conn, f"close_sync_retry:{MACRO_TICKER}:{day.isoformat()}") is None
        run_due_jobs(db_conn, datetime(2020, 1, 8, 11, 5, tzinfo=timezone.utc), fired)  # 18:05 VN
        assert get_job(db_conn, f"close_sync_retry:{MACRO_TICKER}:{day.isoformat()}")["status"] == "queued"
    finally:
        db_conn.execute("DELETE FROM jobs WHERE job_key LIKE %s", (f"%:{day.isoformat()}%",))
        db_conn.commit()


def test_collect_rss_is_enqueued_hourly_from_0600_vn_and_not_twice(db_conn):
    from ops.scheduler import _run_collect_if_due

    fired: set[str] = set()
    try:
        _run_collect_if_due(db_conn, datetime(2020, 1, 6, 22, 0, tzinfo=timezone.utc), fired)  # 05:00 VN: too early
        assert get_job(db_conn, f"collect_rss:{MACRO_TICKER}:2020-01-07:05") is None
        _run_collect_if_due(db_conn, datetime(2020, 1, 7, 2, 0, tzinfo=timezone.utc), fired)   # 09:00 VN
        _run_collect_if_due(db_conn, datetime(2020, 1, 7, 2, 1, tzinfo=timezone.utc), fired)   # same hour again
        assert get_job(db_conn, f"collect_rss:{MACRO_TICKER}:2020-01-07:09")["status"] == "queued"
        assert db_conn.execute("SELECT count(*) FROM jobs WHERE job_type = 'collect_rss'"
                               " AND job_key LIKE 'collect_rss:%:2020-01-07:%'").fetchone()[0] == 1
        _run_collect_if_due(db_conn, datetime(2020, 1, 11, 16, 0, tzinfo=timezone.utc), fired)  # Sat 23:00 VN: weekends run too
        assert get_job(db_conn, f"collect_rss:{MACRO_TICKER}:2020-01-11:23")["status"] == "queued"
    finally:
        db_conn.execute("DELETE FROM jobs WHERE job_key LIKE 'collect_rss:%:2020-01-%'")
        db_conn.commit()


def test_partition_creation_failure_alerts_ops_but_never_stops_the_scheduler(monkeypatch):
    import ops.scheduler as scheduler

    alerts = []

    class _Boom:
        def __enter__(self):
            raise RuntimeError("permission denied")

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(scheduler, "get_admin_conn", lambda: _Boom())
    monkeypatch.setattr(scheduler, "send_ops_alert", alerts.append)
    scheduler._ensure_partitions(date(2026, 10, 6))  # must not raise
    assert len(alerts) == 1 and "partition" in alerts[0]


def test_alert_check_runs_every_15_min_while_prices_move_on_trading_days(db_conn):
    from ops.scheduler import _run_alert_check_if_due

    seed_calendar_from_weekdays(db_conn, date(2020, 1, 6), date(2020, 1, 10), holidays=set())
    fired: set[str] = set()
    try:
        _run_alert_check_if_due(db_conn, datetime(2020, 1, 7, 3, 15, tzinfo=timezone.utc), fired)  # 10:15 VN
        _run_alert_check_if_due(db_conn, datetime(2020, 1, 7, 3, 15, tzinfo=timezone.utc), fired)  # same slot again
        _run_alert_check_if_due(db_conn, datetime(2020, 1, 7, 5, 0, tzinfo=timezone.utc), fired)   # 12:00 VN: lunch
        _run_alert_check_if_due(db_conn, datetime(2020, 1, 7, 8, 0, tzinfo=timezone.utc), fired)   # 15:00 VN: closed
        _run_alert_check_if_due(db_conn, datetime(2020, 1, 11, 3, 15, tzinfo=timezone.utc), fired)  # Saturday
        keys = [r[0] for r in db_conn.execute(
            "SELECT job_key FROM jobs WHERE job_type = 'alert_check' AND job_key LIKE 'alert_check:%:2020-01-%'"
        ).fetchall()]
        assert keys == [f"alert_check:{MACRO_TICKER}:2020-01-07:1015"]
    finally:
        db_conn.execute("DELETE FROM jobs WHERE job_key LIKE 'alert_check:%:2020-01-%'")
        db_conn.commit()
