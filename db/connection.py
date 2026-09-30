import os
from contextlib import contextmanager
import psycopg


@contextmanager
def get_conn():
    conn = psycopg.connect(os.environ["DATABASE_URL"])
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
