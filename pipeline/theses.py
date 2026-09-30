from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone

import psycopg

ALLOWED_INVALIDATION_FIELDS = {
    "roe", "close", "ma50", "ma200", "eps_growth", "revenue_growth",
    "npl_ratio", "nim", "credit_growth", "casa_ratio",
}
ALLOWED_OPS = {"<", "<=", ">", ">=", "=="}


class InvalidThesisRuleError(Exception):
    pass


def _validate_rules(rules: list[dict]) -> None:
    for rule in rules:
        field = rule.get("field")
        op = rule.get("op")
        if field not in ALLOWED_INVALIDATION_FIELDS:
            raise InvalidThesisRuleError(f"unknown field {field!r} — not code-checkable")
        if op not in ALLOWED_OPS:
            raise InvalidThesisRuleError(f"unsupported operator {op!r}")
        if "value" not in rule:
            raise InvalidThesisRuleError(f"rule for {field!r} missing 'value'")


def create_thesis(
    conn: psycopg.Connection,
    ticker: str,
    summary: str,
    pillars: list[dict],
    invalidation_rules: list[dict],
    run_id: str | None,
) -> int:
    _validate_rules(invalidation_rules)

    now = datetime.now(timezone.utc)
    previous = conn.execute(
        "SELECT id, version FROM theses WHERE ticker = %s AND valid_to IS NULL ORDER BY version DESC LIMIT 1",
        (ticker,),
    ).fetchone()

    next_version = 1
    if previous is not None:
        prev_id, prev_version = previous
        next_version = prev_version + 1
        conn.execute("UPDATE theses SET valid_to = %s WHERE id = %s", (now, prev_id))

    row = conn.execute(
        """
        INSERT INTO theses (ticker, version, summary, pillars, invalidation_rules, status, valid_from, created_by_run)
        VALUES (%s, %s, %s, %s, %s, 'active', %s, %s)
        RETURNING id
        """,
        (ticker, next_version, summary, json.dumps(pillars), json.dumps(invalidation_rules), now, run_id),
    ).fetchone()
    return row[0]


_OPS = {
    "<": lambda a, b: a < b,
    "<=": lambda a, b: a <= b,
    ">": lambda a, b: a > b,
    ">=": lambda a, b: a >= b,
    "==": lambda a, b: a == b,
}


@dataclass
class ThesisEvaluation:
    invalidated: bool
    triggered_rules: list[dict]
    unevaluated_fields: list[str]


def evaluate_invalidation(rules: list[dict], snapshot_values: dict[str, float]) -> ThesisEvaluation:
    triggered = []
    unevaluated = []
    for rule in rules:
        field = rule["field"]
        if field not in snapshot_values:
            unevaluated.append(field)
            continue
        if _OPS[rule["op"]](snapshot_values[field], rule["value"]):
            triggered.append(rule)
    return ThesisEvaluation(invalidated=bool(triggered), triggered_rules=triggered, unevaluated_fields=unevaluated)
