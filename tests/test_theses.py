import pytest

from pipeline.theses import InvalidThesisRuleError, create_thesis, evaluate_invalidation
from tests.conftest import insert_ticker


def test_create_thesis_rejects_rule_with_unknown_field(db_conn):
    insert_ticker(db_conn, "VNM")
    with pytest.raises(InvalidThesisRuleError):
        create_thesis(
            db_conn, "VNM", "tang truong tot", [{"name": "growth"}],
            [{"field": "made_up_field", "op": "<", "value": 1}], run_id=None,
        )


def test_create_thesis_rejects_rule_with_unsupported_op(db_conn):
    insert_ticker(db_conn, "VNM")
    with pytest.raises(InvalidThesisRuleError):
        create_thesis(
            db_conn, "VNM", "tang truong tot", [{"name": "growth"}],
            [{"field": "roe", "op": "between", "value": 1}], run_id=None,
        )


def test_create_thesis_inserts_version_1_when_none_exists(db_conn):
    insert_ticker(db_conn, "VNM")
    thesis_id = create_thesis(
        db_conn, "VNM", "tang truong tot", [{"name": "growth"}],
        [{"field": "roe", "op": "<", "value": 0.10}], run_id=None,
    )
    row = db_conn.execute("SELECT version, status FROM theses WHERE id = %s", (thesis_id,)).fetchone()
    assert row == (1, "active")


def test_create_thesis_closes_previous_and_increments_version(db_conn):
    insert_ticker(db_conn, "VNM")
    first_id = create_thesis(
        db_conn, "VNM", "v1", [{"name": "growth"}], [{"field": "roe", "op": "<", "value": 0.10}], run_id=None,
    )
    second_id = create_thesis(
        db_conn, "VNM", "v2", [{"name": "growth"}], [{"field": "roe", "op": "<", "value": 0.08}], run_id=None,
    )

    first_row = db_conn.execute("SELECT valid_to FROM theses WHERE id = %s", (first_id,)).fetchone()
    second_row = db_conn.execute("SELECT version FROM theses WHERE id = %s", (second_id,)).fetchone()

    assert first_row[0] is not None  # closed
    assert second_row == (2,)


def test_evaluate_invalidation_triggers_on_matching_condition():
    rules = [{"field": "roe", "op": "<", "value": 0.10}]
    result = evaluate_invalidation(rules, {"roe": 0.05})
    assert result.invalidated is True
    assert result.triggered_rules == rules


def test_evaluate_invalidation_not_triggered_when_condition_not_met():
    rules = [{"field": "roe", "op": "<", "value": 0.10}]
    result = evaluate_invalidation(rules, {"roe": 0.15})
    assert result.invalidated is False
    assert result.triggered_rules == []


def test_evaluate_invalidation_flags_unevaluated_missing_field():
    rules = [{"field": "roe", "op": "<", "value": 0.10}]
    result = evaluate_invalidation(rules, {})
    assert result.invalidated is False
    assert result.unevaluated_fields == ["roe"]
