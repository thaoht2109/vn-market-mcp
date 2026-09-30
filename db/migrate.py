from pathlib import Path
import psycopg

_TRACKING_TABLE = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    filename TEXT PRIMARY KEY,
    applied_at TIMESTAMPTZ DEFAULT now()
);
"""


def apply_migrations(conn: psycopg.Connection, migrations_dir: Path) -> list[str]:
    conn.execute(_TRACKING_TABLE)
    conn.commit()

    already = {
        row[0] for row in conn.execute("SELECT filename FROM schema_migrations").fetchall()
    }

    applied = []
    for path in sorted(migrations_dir.glob("*.sql")):
        if path.name in already:
            continue
        sql = path.read_text()
        conn.execute(sql)
        conn.execute(
            "INSERT INTO schema_migrations (filename) VALUES (%s)", (path.name,)
        )
        conn.commit()
        applied.append(path.name)
    return applied
