from __future__ import annotations

from typing import Literal

import psycopg


def set_position(conn: psycopg.Connection, ticker: str, avg_cost: float | None, declared_by: str) -> None:
    conn.execute(
        """
        INSERT INTO positions (ticker, status, avg_cost, declared_by, declared_at)
        VALUES (%s, 'holding', %s, %s, now())
        ON CONFLICT (ticker) DO UPDATE SET
          status = 'holding', avg_cost = EXCLUDED.avg_cost, declared_by = EXCLUDED.declared_by, declared_at = now()
        """,
        (ticker, avg_cost, declared_by),
    )


def clear_position(conn: psycopg.Connection, ticker: str, declared_by: str) -> None:
    conn.execute(
        """
        INSERT INTO positions (ticker, status, avg_cost, declared_by, declared_at)
        VALUES (%s, 'none', NULL, %s, now())
        ON CONFLICT (ticker) DO UPDATE SET
          status = 'none', avg_cost = NULL, declared_by = EXCLUDED.declared_by, declared_at = now()
        """,
        (ticker, declared_by),
    )


def get_holding_state(conn: psycopg.Connection, ticker: str) -> Literal["holding", "none", "unknown"]:
    row = conn.execute("SELECT status FROM positions WHERE ticker = %s", (ticker,)).fetchone()
    if row is None:
        return "unknown"
    return row[0]


def get_avg_cost(conn: psycopg.Connection, ticker: str) -> float | None:
    row = conn.execute("SELECT avg_cost FROM positions WHERE ticker = %s", (ticker,)).fetchone()
    return float(row[0]) if row and row[0] is not None else None
