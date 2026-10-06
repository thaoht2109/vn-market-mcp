from datetime import date, datetime, timezone
from pathlib import Path

import httpx
import pytest

import pipeline.collectors.sbv as sbv

FIX = Path(__file__).parent / "fixtures" / "sbv"
TUE = datetime(2026, 10, 6, 3, 0, tzinfo=timezone.utc)  # Tue 10:00 VN
PAGES = {sbv.RATES_URL: (FIX / "ty-gia.html").read_bytes(), sbv.INTEREST_URL: (FIX / "lai-suat.html").read_bytes()}


def client(pages):
    calls = []

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(200, content=pages[str(request.url)])
    return httpx.Client(transport=httpx.MockTransport(handler)), calls


@pytest.fixture(autouse=True)
def _wipe(db_conn):
    def wipe():
        db_conn.execute("DELETE FROM macro_indicators WHERE source = 'sbv'")
        db_conn.execute("DELETE FROM source_health WHERE source = 'sbv'")
        db_conn.commit()
    wipe()
    yield
    wipe()


def test_parsers_read_the_numbers_off_the_saved_pages():
    rates = {k: (p, v) for k, p, v, _ in sbv.parse_rates(PAGES[sbv.RATES_URL].decode())}
    assert rates == {"usd_vnd_central": (date(2026, 10, 5), 25643), "usd_vnd_ref_buy": (date(2026, 10, 5), 24411),
                     "usd_vnd_ref_sell": (date(2026, 10, 5), 26875)}
    rates = {k: (p, v) for k, p, v, _ in sbv.parse_interest(PAGES[sbv.INTEREST_URL].decode())}
    assert rates["refinancing_rate"] == (date(2023, 3, 19), 4.5)
    assert rates["rediscount_rate"] == (date(2023, 3, 19), 3.0)
    assert rates["interbank_overnight"] == (date(2026, 10, 2), 2.73)
    assert rates["interbank_3m"] == (date(2026, 10, 2), 6.73)
    assert "interbank_6m" not in rates  # '(*)': an older day's value, not 02/10


def test_run_stores_indicators_and_marks_source_ok(db_conn):
    c, _ = client(PAGES)
    assert sbv.run_collect_sbv(db_conn, client=c, now=TUE) == 10
    assert db_conn.execute("SELECT value FROM macro_indicators WHERE indicator = 'usd_vnd_central'"
                           " AND period = '2026-10-05'").fetchone()[0] == 25643
    assert db_conn.execute("SELECT consecutive_failures, last_ok_at FROM source_health WHERE source = 'sbv'"
                           ).fetchone() == (0, TUE)
    c, calls = client(PAGES)
    assert sbv.run_collect_sbv(db_conn, client=c, now=TUE) == 0 and calls == []  # not due again yet


def test_waf_block_page_is_a_failure_not_data(db_conn):
    c, _ = client({**PAGES, sbv.RATES_URL: b"<html><title>Request Rejected</title></html>"})
    assert sbv.run_collect_sbv(db_conn, client=c, now=TUE) == 0
    assert db_conn.execute("SELECT count(*) FROM macro_indicators WHERE source = 'sbv'").fetchone()[0] == 0
    assert db_conn.execute("SELECT consecutive_failures FROM source_health WHERE source = 'sbv'").fetchone()[0] == 1


def test_weekend_is_skipped(db_conn):
    c, calls = client(PAGES)
    assert sbv.run_collect_sbv(db_conn, client=c, now=datetime(2026, 10, 4, 3, tzinfo=timezone.utc)) == 0
    assert calls == []
