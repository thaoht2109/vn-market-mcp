import os
import psycopg
from psycopg import sql

_ROLES = {
    "mcp_ro": os.environ["MCP_RO_PASSWORD"],
    "pipeline_rw": os.environ["PIPELINE_RW_PASSWORD"],
    "retention_job": os.environ["RETENTION_JOB_PASSWORD"],
}

_GRANTS = {
    "mcp_ro": "GRANT SELECT ON ALL TABLES IN SCHEMA public TO mcp_ro;",
    "pipeline_rw": (
        "GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA public TO pipeline_rw;"
        "GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO pipeline_rw;"
    ),
    "retention_job": (
        "GRANT SELECT, DELETE ON ALL TABLES IN SCHEMA public TO retention_job;"
        # retention_job writes its own audit trail into retention_log (spec §7.4 step "ghi retention_log").
        "GRANT INSERT ON retention_log TO retention_job;"
        "GRANT USAGE, SELECT ON SEQUENCE retention_log_id_seq TO retention_job;"
    ),
}


def setup_roles(conn: psycopg.Connection) -> None:
    for role, password in _ROLES.items():
        exists = conn.execute(
            "SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)
        ).fetchone()
        # CREATE/ALTER ROLE ... PASSWORD does not accept a bind parameter in
        # that position (Postgres syntax error); sql.Literal safely quotes
        # the password into the statement instead.
        if not exists:
            conn.execute(
                sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(
                    sql.Identifier(role), sql.Literal(password)
                )
            )
        else:
            conn.execute(
                sql.SQL("ALTER ROLE {} PASSWORD {}").format(
                    sql.Identifier(role), sql.Literal(password)
                )
            )
        conn.execute(_GRANTS[role])
    conn.commit()


if __name__ == "__main__":
    from db.connection import get_conn

    with get_conn() as conn:
        setup_roles(conn)
    print("roles configured")
