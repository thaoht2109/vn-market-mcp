from datetime import date, datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from pipeline.calendar import seed_calendar_from_weekdays
from mcp_server.tools.run_analysis import _cache_is_fresh, get_job_status_tool, run_analysis_tool

_RULES = {
    "trading_hours": {
        "morning_start": "09:00", "morning_end": "11:30",
        "afternoon_start": "13:00", "afternoon_end": "15:00",
    },
    "snapshot_cache": {"max_age_minutes": 15, "close_settle_minutes": 20},
}


def test_run_analysis_tool_enqueues_and_returns_job_id():
    # No prior run for this ticker (conn.execute(...).fetchone() -> None) —
    # the §4.4 cache check must fall through cleanly to a fresh enqueue.
    fake_conn = MagicMock()
    fake_conn.execute.return_value.fetchone.return_value = None

    with patch("mcp_server.tools.run_analysis.get_rw_conn") as mock_conn, \
         patch("mcp_server.tools.run_analysis.enqueue", return_value=("on_demand:VNM:123", True)) as mock_enqueue, \
         patch("mcp_server.tools.run_analysis.log_event") as mock_log:
        mock_conn.return_value.__enter__.return_value = fake_conn
        result = run_analysis_tool("VNM", style="long", depth="quick")

    assert result["data"]["job_id"] == "on_demand:VNM:123"
    assert result["data"]["status"] == "queued"
    assert result["data"]["ticker"] == "VNM"
    assert result["sources"] == ["postgres"]
    mock_enqueue.assert_called_once_with(
        fake_conn, "VNM", job_type="on_demand", style="long", depth="quick", requested_by="mcp",
    )
    mock_log.assert_any_call(
        "mcp_run_analysis_enqueued", ticker="VNM", job_key="on_demand:VNM:123", created=True
    )


def test_run_analysis_tool_joins_pending_job_for_same_ticker_style_depth():
    # No fresh run, but another user's job for VNM/long/quick is already running:
    # return that job_id instead of enqueueing a duplicate pipeline run.
    fake_conn = MagicMock()
    fake_conn.execute.return_value.fetchone.side_effect = [None, ("on_demand:VNM:111", "running")]

    with patch("mcp_server.tools.run_analysis.get_rw_conn") as mock_conn, \
         patch("mcp_server.tools.run_analysis.enqueue") as mock_enqueue, \
         patch("mcp_server.tools.run_analysis.log_event"):
        mock_conn.return_value.__enter__.return_value = fake_conn
        result = run_analysis_tool("VNM", style="long", depth="quick")

    assert result["data"]["job_id"] == "on_demand:VNM:111"
    assert result["data"]["status"] == "running"
    mock_enqueue.assert_not_called()
    run_query, run_params = fake_conn.execute.call_args_list[0].args
    assert "style = %s AND depth = %s" in run_query
    assert run_params == ("VNM", "long", "quick")


def test_get_job_status_tool_returns_job_row():
    fake_job = {"status": "done", "run_id": "on_demand:VNM:123", "error": None, "attempts": 1}
    with patch("mcp_server.tools.run_analysis.get_rw_conn") as mock_conn, \
         patch("mcp_server.tools.run_analysis.get_job", return_value=fake_job):
        mock_conn.return_value.__enter__.return_value = MagicMock()
        result = get_job_status_tool("on_demand:VNM:123")

    assert result["data"]["status"] == "done"
    assert result["data"]["run_id"] == "on_demand:VNM:123"
    assert result["warnings"] == []


def test_get_job_status_tool_warns_when_not_found():
    with patch("mcp_server.tools.run_analysis.get_rw_conn") as mock_conn, \
         patch("mcp_server.tools.run_analysis.get_job", return_value=None):
        mock_conn.return_value.__enter__.return_value = MagicMock()
        result = get_job_status_tool("missing:XYZ")

    assert result["data"]["status"] == "not_found"
    assert result["warnings"] == ["job_id không tồn tại"]


def test_cache_is_fresh_within_max_age_during_trading_hours(db_conn):
    seed_calendar_from_weekdays(db_conn, date(2026, 8, 31), date(2026, 9, 4), holidays=set())
    # 2026-09-02 10:00 Asia/Ho_Chi_Minh (UTC+7) == 03:00 UTC, a Wednesday, morning session.
    now = datetime(2026, 9, 2, 3, 0, tzinfo=timezone.utc)
    run_as_of = now - timedelta(minutes=10)
    assert _cache_is_fresh(db_conn, run_as_of, now, _RULES) is True


def test_cache_is_stale_beyond_max_age_during_trading_hours(db_conn):
    seed_calendar_from_weekdays(db_conn, date(2026, 8, 31), date(2026, 9, 4), holidays=set())
    now = datetime(2026, 9, 2, 3, 0, tzinfo=timezone.utc)
    run_as_of = now - timedelta(minutes=20)  # > 15 min max age
    assert _cache_is_fresh(db_conn, run_as_of, now, _RULES) is False


def test_cache_is_fresh_off_hours_until_next_session(db_conn):
    seed_calendar_from_weekdays(db_conn, date(2026, 8, 31), date(2026, 9, 4), holidays=set())
    # Run made right after Wednesday's close (2026-09-02); "now" is Wednesday
    # night, still before Thursday's session opens.
    run_as_of = datetime(2026, 9, 2, 8, 25, tzinfo=timezone.utc)  # 15:25 VN, after close settled
    now = datetime(2026, 9, 2, 16, 0, tzinfo=timezone.utc)  # 23:00 VN same day
    assert _cache_is_fresh(db_conn, run_as_of, now, _RULES) is True


def test_cache_is_stale_off_hours_for_run_before_close_settled(db_conn):
    seed_calendar_from_weekdays(db_conn, date(2026, 8, 31), date(2026, 9, 4), holidays=set())
    # 15:10 VN: after the bell but before close_sync settled bars/flow — must not serve all night.
    run_as_of = datetime(2026, 9, 2, 8, 10, tzinfo=timezone.utc)
    now = datetime(2026, 9, 2, 16, 0, tzinfo=timezone.utc)
    assert _cache_is_fresh(db_conn, run_as_of, now, _RULES) is False


def test_cache_is_stale_off_hours_after_next_session_opens(db_conn):
    seed_calendar_from_weekdays(db_conn, date(2026, 8, 31), date(2026, 9, 4), holidays=set())
    run_as_of = datetime(2026, 9, 2, 8, 10, tzinfo=timezone.utc)  # Wed close
    now = datetime(2026, 9, 3, 3, 0, tzinfo=timezone.utc)  # Thu 10:00 VN — next session started
    assert _cache_is_fresh(db_conn, run_as_of, now, _RULES) is False


def test_cache_is_stale_off_hours_for_intraday_run(db_conn):
    seed_calendar_from_weekdays(db_conn, date(2026, 8, 31), date(2026, 9, 4), holidays=set())
    run_as_of = datetime(2026, 9, 2, 3, 0, tzinfo=timezone.utc)  # Wed 10:00 VN — mid-session prices
    now = datetime(2026, 9, 2, 13, 0, tzinfo=timezone.utc)  # Wed 20:00 VN
    assert _cache_is_fresh(db_conn, run_as_of, now, _RULES) is False


def test_cache_reuses_previous_close_run_before_first_bar(db_conn):
    seed_calendar_from_weekdays(db_conn, date(2026, 8, 31), date(2026, 9, 4), holidays=set())
    run_as_of = datetime(2026, 9, 2, 8, 20, tzinfo=timezone.utc)  # Wed 15:20 VN
    now = datetime(2026, 9, 3, 2, 5, tzinfo=timezone.utc)  # Thu 09:05 VN, ATO — no bar yet
    assert _cache_is_fresh(db_conn, run_as_of, now, _RULES) is True


def test_cache_reuses_run_made_during_lunch_break_until_afternoon_opens(db_conn):
    seed_calendar_from_weekdays(db_conn, date(2026, 8, 31), date(2026, 9, 4), holidays=set())
    run_as_of = datetime(2026, 9, 2, 4, 35, tzinfo=timezone.utc)  # 11:35 VN, after morning close
    now = datetime(2026, 9, 2, 5, 50, tzinfo=timezone.utc)  # 12:50 VN, still lunch — no price moved
    assert _cache_is_fresh(db_conn, run_as_of, now, _RULES) is True


def test_cache_is_stale_during_lunch_for_run_made_mid_morning(db_conn):
    seed_calendar_from_weekdays(db_conn, date(2026, 8, 31), date(2026, 9, 4), holidays=set())
    run_as_of = datetime(2026, 9, 2, 4, 0, tzinfo=timezone.utc)  # 11:00 VN, prices moved after it
    now = datetime(2026, 9, 2, 5, 0, tzinfo=timezone.utc)  # 12:00 VN
    assert _cache_is_fresh(db_conn, run_as_of, now, _RULES) is False


def test_cache_is_stale_after_afternoon_opens_for_lunch_run(db_conn):
    seed_calendar_from_weekdays(db_conn, date(2026, 8, 31), date(2026, 9, 4), holidays=set())
    run_as_of = datetime(2026, 9, 2, 4, 35, tzinfo=timezone.utc)  # 11:35 VN
    now = datetime(2026, 9, 2, 6, 30, tzinfo=timezone.utc)  # 13:30 VN, matching again
    assert _cache_is_fresh(db_conn, run_as_of, now, _RULES) is False


def test_cache_reuses_post_close_run_before_close_settled(db_conn):
    seed_calendar_from_weekdays(db_conn, date(2026, 8, 31), date(2026, 9, 4), holidays=set())
    run_as_of = datetime(2026, 9, 2, 8, 5, tzinfo=timezone.utc)  # 15:05 VN, closing price final
    now = datetime(2026, 9, 2, 8, 15, tzinfo=timezone.utc)  # 15:15 VN, inside the settle window
    assert _cache_is_fresh(db_conn, run_as_of, now, _RULES) is True
