from __future__ import annotations

from pathlib import Path
from typing import Literal

import psycopg
import yaml

from pipeline.action_label import ActionLabelConfig, personal_label

_RULES = Path(__file__).parent.parent / "config" / "vn-rules.yaml"


def set_position(conn: psycopg.Connection, ticker: str, avg_cost: float | None, declared_by: str) -> None:
    conn.execute(
        """
        INSERT INTO positions (ticker, status, avg_cost, declared_by, declared_at)
        VALUES (%s, 'holding', %s, %s, now())
        ON CONFLICT (ticker, declared_by) DO UPDATE SET
          status = 'holding', avg_cost = EXCLUDED.avg_cost, declared_at = now()
        """,
        (ticker, avg_cost, declared_by),
    )


def clear_position(conn: psycopg.Connection, ticker: str, declared_by: str) -> None:
    conn.execute(
        """
        INSERT INTO positions (ticker, status, avg_cost, declared_by, declared_at)
        VALUES (%s, 'none', NULL, %s, now())
        ON CONFLICT (ticker, declared_by) DO UPDATE SET
          status = 'none', avg_cost = NULL, declared_at = now()
        """,
        (ticker, declared_by),
    )


def get_holding_state(conn: psycopg.Connection, ticker: str, user: str) -> Literal["holding", "none", "unknown"]:
    row = conn.execute(
        "SELECT status FROM positions WHERE ticker = %s AND declared_by = %s", (ticker, user)
    ).fetchone()
    if row is None:
        return "unknown"
    return row[0]


def get_avg_cost(conn: psycopg.Connection, ticker: str, user: str) -> float | None:
    row = conn.execute(
        "SELECT avg_cost FROM positions WHERE ticker = %s AND declared_by = %s", (ticker, user)
    ).fetchone()
    return float(row[0]) if row and row[0] is not None else None


def personalize(conn: psycopg.Connection, snapshot: dict, ticker: str, user: str) -> dict:
    """A run is shared by every user, so it is labelled position-neutral; this is `user`'s
    view of it (their holding_state, and the holder's label when they hold the ticker)."""
    state = get_holding_state(conn, ticker, user)
    if "composite_score" not in snapshot:  # snapshot file gone: no inputs to re-label from
        return {**snapshot, "holding_state": state}
    cfg = ActionLabelConfig.from_rules(yaml.safe_load(_RULES.read_text()))
    label = personal_label(
        snapshot.get("action_label"), state, float(snapshot.get("composite_score") or 0),
        float(snapshot.get("confidence") or 0), bool(snapshot.get("data_stale")), cfg,
    )
    return {**snapshot, "holding_state": state, "action_label": label}
