import json
from datetime import date, datetime, timedelta, timezone

from ops.pending_alerts import FOOTER, render, take_pending
from pipeline.price_alerts import CONFIRMED, TOUCHED, evaluate, message, set_alerts, step
from tests.conftest import insert_ticker

REARM = 0.01
SESSION = date(2026, 9, 3)  # Thursday; levels come from Wednesday's 15:20 official run
NOW = datetime(2026, 9, 3, 3, 15, tzinfo=timezone.utc)  # 10:15 VN


def test_step_alerts_on_entering_only_and_rearms_beyond_the_buffer():
    inside, fired = False, []
    for price in [64.0, 61.5, 61.0, 62.1, 61.9, 62.8, 61.8]:  # zone 60–62: left only above 62.62
        inside, fire = step(inside, "entry_zone", price, 60.0, 62.0, REARM)
        fired.append(fire)
    assert fired == [False, True, False, False, False, False, True]


def test_step_stop_loss_and_target_use_the_buffer_on_the_far_side():
    assert step(False, "stop_loss", 57.9, 58.0, 58.0, REARM) == (True, True)
    assert step(True, "stop_loss", 58.4, 58.0, 58.0, REARM) == (True, False)  # within 1%: no re-arm
    assert step(True, "stop_loss", 58.7, 58.0, 58.0, REARM) == (False, False)
    assert step(False, "target", 70.0, 70.0, 70.0, REARM) == (True, True)
    assert step(True, "target", 69.5, 70.0, 70.0, REARM) == (True, False)


def test_message_says_in_session_price_may_still_change():
    touched = message("VNM", "entry_zone", TOUCHED, 61500, 60000, 62000, "watch", NOW)
    assert "vào vùng mua 60.000–62.000" in touched and "10:15" in touched and "có thể còn đổi" in touched
    confirmed = message("VNM", "stop_loss", CONFIRMED, 57900, 58000, 58000, "watch", NOW)
    assert "đóng cửa 57.900" in confirmed and "cắt lỗ 58.000" in confirmed


def test_render_is_empty_without_alerts_so_hermes_sends_nothing():
    assert render([]) == ""
    assert render(["a", "b"]) == f"a\n\nb\n\n{FOOTER}"


def _setup(conn, tmp_path, label="watch"):
    insert_ticker(conn, "ALRT")
    snap = tmp_path / "scheduled_post_ALRT.json"
    snap.write_text(json.dumps({
        "action_label": label,
        "risk_plan": {"entry_zone": [60000, 62000], "stop_loss": 58000, "target": 70000},
    }))
    conn.execute(
        "INSERT INTO prices_daily (ticker, trade_date, open, high, low, close, volume, value, source, fetched_at)"
        " VALUES ('ALRT', %s, 64000, 64000, 64000, 64000, 1000, 0, 'test', now())",
        (SESSION - timedelta(days=1),),
    )
    conn.execute(
        "INSERT INTO runs (run_id, mode, tickers, style, depth, as_of, snapshot_ref, warnings)"
        " VALUES ('scheduled_post:ALRT:1', 'scheduled_post', ARRAY['ALRT'], 'long', 'full', %s, %s, '[]')",
        (datetime(2026, 9, 2, 8, 20, tzinfo=timezone.utc), str(snap)),
    )
    conn.execute(
        "INSERT INTO watchlist_extra (ticker, added_by, confirmed_at, status) VALUES ('ALRT', 'u1', now(), 'active')"
    )
    conn.execute(
        "INSERT INTO positions (ticker, status, declared_by) VALUES ('ALRT', 'holding', 'u2')"
    )


def _alerts(conn):
    return conn.execute(
        "SELECT user_id, condition, kind FROM user_alerts WHERE ticker = 'ALRT' ORDER BY id"
    ).fetchall()


def test_evaluate_alerts_watchers_once_while_price_stays_in_zone(db_conn, tmp_path):
    _setup(db_conn, tmp_path)
    for minutes, price in [(0, 64000), (15, 61500), (30, 61000), (45, 61800)]:
        evaluate(db_conn, {"ALRT": price}, TOUCHED, SESSION, NOW + timedelta(minutes=minutes), REARM)
    assert _alerts(db_conn) == [("u1", "entry_zone", "touched")]  # holder u2 gets no "vào vùng mua"


def test_evaluate_sends_stop_and_target_to_holders_only(db_conn, tmp_path):
    _setup(db_conn, tmp_path)
    evaluate(db_conn, {"ALRT": 57000}, TOUCHED, SESSION, NOW, REARM)
    assert ("u2", "stop_loss", "touched") in _alerts(db_conn)
    assert all(user == "u2" for user, cond, _ in _alerts(db_conn) if cond == "stop_loss")


def test_evaluate_does_not_alert_a_condition_already_met_when_levels_were_set(db_conn, tmp_path):
    _setup(db_conn, tmp_path)
    db_conn.execute("UPDATE prices_daily SET close = 61000 WHERE ticker = 'ALRT'")  # official close already in zone
    evaluate(db_conn, {"ALRT": 61500}, TOUCHED, SESSION, NOW, REARM)
    assert _alerts(db_conn) == []


def test_evaluate_skips_entry_alert_when_verdict_is_defensive(db_conn, tmp_path):
    _setup(db_conn, tmp_path, label="stay_out")
    evaluate(db_conn, {"ALRT": 61500}, TOUCHED, SESSION, NOW, REARM)
    assert _alerts(db_conn) == []


def test_touched_and_confirmed_are_separate_alerts(db_conn, tmp_path):
    _setup(db_conn, tmp_path)
    evaluate(db_conn, {"ALRT": 61500}, TOUCHED, SESSION, NOW, REARM)
    evaluate(db_conn, {"ALRT": 61200}, CONFIRMED, SESSION, NOW + timedelta(hours=5), REARM)
    assert _alerts(db_conn) == [("u1", "entry_zone", "touched"), ("u1", "entry_zone", "confirmed")]


def test_opt_out_per_ticker_and_global(db_conn, tmp_path):
    _setup(db_conn, tmp_path)
    set_alerts(db_conn, "u1", False, "ALRT")
    evaluate(db_conn, {"ALRT": 61500}, TOUCHED, SESSION, NOW, REARM)
    assert _alerts(db_conn) == []

    set_alerts(db_conn, "u1", True)  # "bật lại cảnh báo" for all tickers overrides the per-ticker off
    evaluate(db_conn, {"ALRT": 64000}, TOUCHED, SESSION, NOW + timedelta(minutes=15), REARM)  # leave zone
    evaluate(db_conn, {"ALRT": 61500}, TOUCHED, SESSION, NOW + timedelta(minutes=30), REARM)  # re-enter
    assert _alerts(db_conn) == [("u1", "entry_zone", "touched")]


def test_take_pending_returns_each_alert_once(db_conn, tmp_path):
    _setup(db_conn, tmp_path)
    evaluate(db_conn, {"ALRT": 61500}, TOUCHED, SESSION, NOW, REARM)
    now = datetime.now(timezone.utc)
    first = take_pending(db_conn, "u1", now)
    assert len(first) == 1 and "ALRT vào vùng mua" in first[0]
    assert take_pending(db_conn, "u1", now) == []
    assert take_pending(db_conn, "u2", now) == []  # another user never sees u1's alert
