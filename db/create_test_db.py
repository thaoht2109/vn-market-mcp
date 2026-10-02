"""Create (idempotently) the dedicated test database, migrate it and grant the roles.

Tests must never touch the live `vnmcp` database: one test DELETEd every run and
prediction from it (2026-10-02). run_tests.sh points DATABASE_URL and the role
URLs at `vnmcp_test` and then runs this module.

Needs DATABASE_URL (the admin role, already pointing at the *_test database) and
the three role passwords in the environment.
"""
from __future__ import annotations

import os
import sys
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

import psycopg
from psycopg import sql

from db.migrate import apply_migrations
from db.setup_roles import setup_roles
from pipeline.calendar import seed_calendar_from_weekdays

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def main() -> None:
    url = os.environ["DATABASE_URL"]
    name = urlparse(url).path.lstrip("/")
    if not name.endswith("_test"):
        sys.exit(f"refusing: DATABASE_URL points at {name!r}, not a *_test database")

    maintenance = urlparse(url)._replace(path="/postgres").geturl()
    with psycopg.connect(maintenance, autocommit=True) as admin:
        if admin.execute("SELECT 1 FROM pg_database WHERE datname = %s", (name,)).fetchone() is None:
            admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
            print(f"created database {name}")

    with psycopg.connect(url) as conn:
        applied = apply_migrations(conn, MIGRATIONS_DIR)
        setup_roles(conn)  # grants are per database, so re-run after migrations
        # Some tests rely on a calendar seeded years ahead, like the live DB
        # (ops/seed_market_data.py). Weekdays only — no holiday list needed here.
        if conn.execute("SELECT 1 FROM trading_calendar LIMIT 1").fetchone() is None:
            seed_calendar_from_weekdays(conn, date(2020, 1, 1), date(2030, 12, 31), holidays=set())
            conn.commit()
    print(f"{name}: {len(applied)} migration(s) applied, roles granted")


if __name__ == "__main__":
    main()
