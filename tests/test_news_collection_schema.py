import os

import psycopg


def test_news_items_has_filter_columns_and_source_health_exists(db_conn):
    cols = {r[0] for r in db_conn.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_name = 'news_items'").fetchall()}
    assert {"stream", "filter_status", "filter_reason", "pillars"} <= cols
    db_conn.execute("INSERT INTO source_health (source) VALUES ('test_schema')")
    row = db_conn.execute(
        "SELECT consecutive_failures, first_seen_at IS NOT NULL FROM source_health WHERE source = 'test_schema'"
    ).fetchone()
    assert row == (0, True)


def test_pipeline_rw_can_write_source_health():
    # grants are per existing table: db.setup_roles must have been re-run after the migration
    with psycopg.connect(os.environ["PIPELINE_RW_DATABASE_URL"]) as conn:
        conn.execute("INSERT INTO source_health (source) VALUES ('test_rw') ON CONFLICT DO NOTHING")
        conn.execute("UPDATE source_health SET consecutive_failures = 1 WHERE source = 'test_rw'")
        conn.rollback()
