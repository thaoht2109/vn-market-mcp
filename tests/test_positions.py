from pipeline.positions import clear_position, get_avg_cost, get_holding_state, personalize, set_position
from tests.conftest import insert_ticker


def test_get_holding_state_unknown_when_never_declared(db_conn):
    insert_ticker(db_conn, "VNM")
    assert get_holding_state(db_conn, "VNM", "tg:12345") == "unknown"


def test_set_position_marks_holding_with_avg_cost(db_conn):
    insert_ticker(db_conn, "VNM")
    set_position(db_conn, "VNM", avg_cost=85000, declared_by="tg:12345")

    assert get_holding_state(db_conn, "VNM", "tg:12345") == "holding"
    assert get_avg_cost(db_conn, "VNM", "tg:12345") == 85000.0


def test_clear_position_marks_none_not_unknown(db_conn):
    insert_ticker(db_conn, "VNM")
    set_position(db_conn, "VNM", avg_cost=85000, declared_by="tg:12345")

    clear_position(db_conn, "VNM", declared_by="tg:12345")

    assert get_holding_state(db_conn, "VNM", "tg:12345") == "none"  # distinct from "unknown"
    assert get_avg_cost(db_conn, "VNM", "tg:12345") is None


def test_set_position_is_idempotent_upsert(db_conn):
    insert_ticker(db_conn, "VNM")
    set_position(db_conn, "VNM", avg_cost=85000, declared_by="tg:12345")
    set_position(db_conn, "VNM", avg_cost=90000, declared_by="tg:12345")

    row = db_conn.execute("SELECT count(*) FROM positions WHERE ticker = 'VNM'").fetchone()
    assert row[0] == 1
    assert get_avg_cost(db_conn, "VNM", "tg:12345") == 90000.0


def test_positions_are_per_user(db_conn):
    insert_ticker(db_conn, "VNM")
    set_position(db_conn, "VNM", avg_cost=85000, declared_by="alice")
    clear_position(db_conn, "VNM", declared_by="bob")

    assert get_holding_state(db_conn, "VNM", "alice") == "holding"
    assert get_avg_cost(db_conn, "VNM", "alice") == 85000.0
    assert get_holding_state(db_conn, "VNM", "bob") == "none"
    assert get_holding_state(db_conn, "VNM", "carol") == "unknown"


def test_personalize_gives_holder_their_own_label(db_conn):
    insert_ticker(db_conn, "VNM")
    set_position(db_conn, "VNM", avg_cost=85000, declared_by="alice")
    shared = {"action_label": "watch", "composite_score": 60, "confidence": 0.7, "data_stale": False}

    assert personalize(db_conn, shared, "VNM", "alice")["action_label"] == "hold"
    assert personalize(db_conn, shared, "VNM", "bob") == {**shared, "holding_state": "unknown"}
    assert shared["action_label"] == "watch"  # the shared snapshot itself is never changed
