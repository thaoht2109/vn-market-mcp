from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from pipeline.news_health import (
    check_and_alert, is_stale, news_sources_status, record_failure, record_ok, source_warnings,
    working_time_between,
)

VN = ZoneInfo("Asia/Ho_Chi_Minh")


def vn(day, hour, minute=0):  # October 2026: 2 = Fri, 3 = Sat, 4 = Sun, 5 = Mon, 6 = Tue, 7 = Wed
    return datetime(2026, 10, day, hour, minute, tzinfo=VN)


def sender(sent):
    return lambda text: sent.append(text) or True


def mine(names):  # the shared *_test DB may hold other rows; look only at ours
    return [n for n in names if n.startswith("test_")]


def test_working_time_counts_only_weekday_office_hours():
    assert working_time_between(vn(2, 17), vn(5, 9)) == timedelta(hours=2)   # Fri 17-18 + Mon 8-9
    assert working_time_between(vn(3, 9), vn(4, 17)) == timedelta(0)         # weekend
    assert working_time_between(vn(6, 18, 30), vn(7, 7, 30)) == timedelta(0)  # overnight


def test_is_stale_uses_first_seen_when_a_source_never_produced_an_item():
    assert is_stale(None, vn(6, 8), vn(6, 15)) is True    # 7 working hours of silence
    assert is_stale(None, vn(6, 8), vn(6, 13)) is False


def test_no_alert_over_a_quiet_weekend(db_conn):
    record_ok(db_conn, "test_weekend", vn(2, 17, 30), vn(2, 17, 30))
    assert mine(check_and_alert(db_conn, vn(4, 12), {"test_weekend"}, sender([]))) == []


def test_silence_alerts_once_and_only_a_new_item_rearms(db_conn):
    now = datetime(2026, 10, 6, 3, 0, tzinfo=timezone.utc)  # Tue 10:00 VN
    record_ok(db_conn, "test_silent", datetime(2026, 10, 1, 3, 0, tzinfo=timezone.utc), now - timedelta(days=5))
    sent = []
    assert mine(check_and_alert(db_conn, now, {"test_silent"}, sender(sent))) == ["test_silent"]
    assert mine(check_and_alert(db_conn, now, {"test_silent"}, sender(sent))) == []
    record_ok(db_conn, "test_silent", None, now)  # call succeeded but still no new item: stay alerted
    assert mine(check_and_alert(db_conn, now, {"test_silent"}, sender(sent))) == []
    record_ok(db_conn, "test_silent", now - timedelta(minutes=5), now)  # a fresh item: real recovery
    assert db_conn.execute("SELECT alerted_at FROM source_health WHERE source = 'test_silent'").fetchone()[0] is None


def test_three_consecutive_failures_alert_even_for_a_non_rss_source(db_conn):
    now = datetime(2026, 10, 6, 3, 0, tzinfo=timezone.utc)
    sent = []
    for _ in range(2):
        record_failure(db_conn, "test_vnstock", "boom", now)
    assert "test_vnstock" not in check_and_alert(db_conn, now, set(), sender(sent))
    record_failure(db_conn, "test_vnstock", "boom", now)
    assert "test_vnstock" in check_and_alert(db_conn, now, set(), sender(sent))
    assert any("test_vnstock" in m and "boom" in m for m in sent)


def test_an_alert_that_could_not_be_sent_is_retried(db_conn):
    now = datetime(2026, 10, 6, 3, 0, tzinfo=timezone.utc)
    for _ in range(3):
        record_failure(db_conn, "test_retry", "boom", now)
    assert "test_retry" not in check_and_alert(db_conn, now, set(), lambda m: False)  # Telegram down
    assert "test_retry" in check_and_alert(db_conn, now, set(), lambda m: True)


def test_news_sources_status_marks_unknown_and_old_sources_stale(db_conn):
    now = datetime(2026, 10, 6, 3, 0, tzinfo=timezone.utc)
    record_ok(db_conn, "test_fresh", now - timedelta(hours=1), now - timedelta(hours=1))
    record_ok(db_conn, "test_old", now - timedelta(days=5), now - timedelta(days=5))
    status = {s["source"]: s for s in news_sources_status(db_conn, now, ["test_fresh", "test_old", "test_never"])}
    assert status["test_fresh"]["stale"] is False
    assert status["test_old"]["stale"] is True
    assert status["test_never"] == {"source": "test_never", "last_item_at": None, "stale": True}
    warnings = source_warnings(list(status.values()))
    assert len(warnings) == 2 and any("test_never" in w for w in warnings)
