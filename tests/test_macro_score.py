from datetime import date, timedelta

import yaml

from pipeline.macro_score import macro_score
from pipeline.scoring import news_score

CFG = yaml.safe_load(open("config/vn-rules.yaml"))["macro_score"]
AS_OF = date(2026, 10, 7)


def _ind(conn, indicator, period, value):
    conn.execute("INSERT INTO macro_indicators (indicator, period, value, unit, source, source_url, fetched_at)"
                 " VALUES (%s, %s, %s, '%%', 'test', 'http://t', now())", (indicator, period, value))


def test_macro_score_averages_fresh_parts_and_skips_stale_ones(db_conn):
    db_conn.execute("DELETE FROM macro_indicators")
    _ind(db_conn, "interbank_overnight", AS_OF - timedelta(days=2), 2.0)   # 50 + (2-4)*-15 = 80
    _ind(db_conn, "cpi_yoy", date(2026, 9, 1), 5.0)                       # 50 + 0.5*-20 = 40
    _ind(db_conn, "gdp_yoy", date(2025, 10, 1), 9.0)                      # 371 days old: dropped
    _ind(db_conn, "usd_vnd_central", AS_OF - timedelta(days=20), 25000)   # +1% → 25
    _ind(db_conn, "usd_vnd_central", AS_OF, 25250)
    _ind(db_conn, "interbank_overnight", AS_OF + timedelta(days=1), 9.0)  # after as_of: not seen
    score, parts = macro_score(db_conn, AS_OF, {"pct_above_ma50": 0.45}, CFG)
    assert set(parts) == {"interbank_overnight", "cpi_yoy", "usd_vnd_change", "breadth_ma50"}
    assert [round(parts[k]["score"], 1) for k in ("interbank_overnight", "cpi_yoy", "usd_vnd_change", "breadth_ma50")] \
        == [80.0, 40.0, 25.0, 45.0]
    assert round(score, 2) == 47.5


def test_macro_score_none_without_data_and_fx_needs_a_week_of_history(db_conn):
    db_conn.execute("DELETE FROM macro_indicators")
    assert macro_score(db_conn, AS_OF, {}, CFG) == (None, {})
    _ind(db_conn, "usd_vnd_central", AS_OF - timedelta(days=1), 25000)
    _ind(db_conn, "usd_vnd_central", AS_OF, 25300)
    assert macro_score(db_conn, AS_OF, {"pct_above_ma50": None}, CFG) == (None, {})


def test_news_score_weighs_recent_news_and_is_pulled_to_neutral():
    assert news_score([], 3.5, 1.0) is None
    assert news_score([(0, -1)], 3.5, 1.0) == 25.0
    assert news_score([(0, 1), (0, 1)], 3.5, 1.0) == 50 + 50 * 2 / 3
    assert news_score([(0, -1), (7, 1)], 3.5, 1.0) < news_score([(7, -1), (0, 1)], 3.5, 1.0)  # newer one wins
    assert news_score([(0, 0)], 3.5, 1.0) == 50.0
