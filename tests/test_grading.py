from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from pipeline.calendar import seed_calendar_from_weekdays, trading_day_offset
from pipeline.grading import GradingConfig, grade_prediction, grade_due_predictions
from tests.conftest import insert_ticker

CFG = GradingConfig(horizons_days=[2], fill_window_days=5, buy_fee_pct=0.0, sell_fee_pct=0.0, sell_tax_pct=0.0)
TICKER = "GRADETEST"


def _seed_prices(conn, ticker, bars):
    """bars: list of (trade_date, open, high, low, close)"""
    for d, o, h, l, c in bars:
        conn.execute(
            """
            INSERT INTO prices_daily (ticker, trade_date, open, high, low, close, volume, source, fetched_at)
            VALUES (%s, %s, %s, %s, %s, %s, 1000, 'test', now())
            ON CONFLICT (ticker, trade_date) DO UPDATE SET open=EXCLUDED.open, high=EXCLUDED.high,
              low=EXCLUDED.low, close=EXCLUDED.close
            """,
            (ticker, d, o, h, l, c),
        )


def _insert_prediction(conn, ticker, action_label, entry_zone=None, stop_loss=None, target=None, created_at=None):
    row = conn.execute(
        """
        INSERT INTO predictions (ticker, source, trigger, action_label, signal_type, entry_zone, stop_loss, target, created_at)
        VALUES (%s, 'on_demand', 'first', %s, 'mixed', %s, %s, %s, %s)
        RETURNING id
        """,
        (ticker, action_label, entry_zone, stop_loss, target, created_at),
    ).fetchone()
    return row[0]


@pytest.fixture
def seeded(db_conn):
    insert_ticker(db_conn, TICKER)
    insert_ticker(db_conn, "VN30")  # prices_daily.ticker has an FK to tickers
    start = date(2021, 1, 4)  # a Monday
    end = date(2021, 1, 15)
    seed_calendar_from_weekdays(db_conn, start, end, holidays=set())
    db_conn.commit()
    yield db_conn
    db_conn.execute("DELETE FROM prediction_outcomes WHERE prediction_id IN (SELECT id FROM predictions WHERE ticker = %s)", (TICKER,))
    db_conn.execute("DELETE FROM predictions WHERE ticker = %s", (TICKER,))
    db_conn.execute("DELETE FROM prices_daily WHERE ticker IN (%s, 'VN30')", (TICKER,))
    db_conn.execute("DELETE FROM trading_calendar WHERE trade_date BETWEEN %s AND %s", (start, end))
    db_conn.execute("DELETE FROM tickers WHERE ticker IN (%s, 'VN30')", (TICKER,))
    db_conn.commit()


def test_trading_day_offset_counts_sessions_forward(seeded):
    # 2021-01-04 Mon; +1 -> 01-05, +2 -> 01-06
    assert trading_day_offset(seeded, date(2021, 1, 4), 1) == date(2021, 1, 5)
    assert trading_day_offset(seeded, date(2021, 1, 4), 2) == date(2021, 1, 6)


def test_trading_day_offset_returns_none_when_not_elapsed(db_conn):
    # Real trading_calendar is seeded years ahead (shared DB), so simulate
    # "not enough sessions yet" against a date right at the seeded horizon
    # instead of relying on calendar_covers ever being False here.
    row = db_conn.execute("SELECT max(trade_date) FROM trading_calendar").fetchone()
    max_date = row[0]
    assert trading_day_offset(db_conn, max_date, 1) is None


def test_grade_prediction_exits_at_target(seeded):
    created = datetime(2021, 1, 4, tzinfo=timezone.utc)
    pred_id = _insert_prediction(
        seeded, TICKER, "buy_accumulate", entry_zone="[95,100]", stop_loss=80, target=110, created_at=created,
    )
    _seed_prices(seeded, TICKER, [
        (date(2021, 1, 5), 98, 99, 97, 98),    # entry session, fills at open=98
        (date(2021, 1, 6), 99, 112, 98, 111),  # hits target 110
    ])
    _seed_prices(seeded, "VN30", [
        (date(2021, 1, 5), 1000, 1000, 1000, 1000),
        (date(2021, 1, 6), 1000, 1000, 1000, 1010),
    ])
    seeded.commit()

    prediction = {"id": pred_id, "ticker": TICKER, "action_label": "buy_accumulate",
                  "thesis_id": None, "entry_zone": seeded.execute(
                      "SELECT entry_zone FROM predictions WHERE id=%s", (pred_id,)).fetchone()[0],
                  "stop_loss": 80, "target": 110, "created_at": created}
    result = grade_prediction(seeded, prediction, 2, CFG)
    assert result.filled is True
    assert result.exit_reason == "target"
    assert result.ret == (Decimal(110) - Decimal(98)) / Decimal(98)


def test_grade_prediction_exits_at_stop(seeded):
    created = datetime(2021, 1, 4, tzinfo=timezone.utc)
    pred_id = _insert_prediction(
        seeded, TICKER, "buy_accumulate", entry_zone="[95,100]", stop_loss=90, target=150, created_at=created,
    )
    _seed_prices(seeded, TICKER, [
        (date(2021, 1, 5), 98, 99, 97, 98),
        (date(2021, 1, 6), 95, 96, 85, 90),  # dips through stop=90
    ])
    seeded.commit()
    prediction = {"id": pred_id, "ticker": TICKER, "action_label": "buy_accumulate", "thesis_id": None,
                  "entry_zone": seeded.execute("SELECT entry_zone FROM predictions WHERE id=%s", (pred_id,)).fetchone()[0],
                  "stop_loss": 90, "target": 150, "created_at": created}
    result = grade_prediction(seeded, prediction, 2, CFG)
    assert result.exit_reason == "stop"
    assert result.ret == (Decimal(90) - Decimal(98)) / Decimal(98)


def test_grade_prediction_not_filled_when_zone_never_touched(seeded):
    created = datetime(2021, 1, 4, tzinfo=timezone.utc)
    pred_id = _insert_prediction(
        seeded, TICKER, "buy_accumulate", entry_zone="[10,20]", stop_loss=5, target=30, created_at=created,
    )
    _seed_prices(seeded, TICKER, [
        (date(2021, 1, 5), 98, 99, 97, 98),  # never near [10,20]
        (date(2021, 1, 6), 99, 100, 98, 99),
    ])
    seeded.commit()
    prediction = {"id": pred_id, "ticker": TICKER, "action_label": "buy_accumulate", "thesis_id": None,
                  "entry_zone": seeded.execute("SELECT entry_zone FROM predictions WHERE id=%s", (pred_id,)).fetchone()[0],
                  "stop_loss": 5, "target": 30, "created_at": created}
    result = grade_prediction(seeded, prediction, 2, CFG)
    assert result.filled is False
    assert result.exit_reason == "not_filled"


def test_grade_prediction_watch_label_uses_excess_return(seeded):
    created = datetime(2021, 1, 4, tzinfo=timezone.utc)
    pred_id = _insert_prediction(seeded, TICKER, "watch", created_at=created)
    _seed_prices(seeded, TICKER, [
        (date(2021, 1, 5), 100, 100, 100, 100),
        (date(2021, 1, 6), 100, 100, 100, 105),  # +5%
    ])
    _seed_prices(seeded, "VN30", [
        (date(2021, 1, 5), 1000, 1000, 1000, 1000),
        (date(2021, 1, 6), 1000, 1000, 1000, 1020),  # +2%
    ])
    seeded.commit()
    prediction = {"id": pred_id, "ticker": TICKER, "action_label": "watch", "thesis_id": None,
                  "entry_zone": None, "stop_loss": None, "target": None, "created_at": created}
    result = grade_prediction(seeded, prediction, 2, CFG)
    assert result.filled is True
    assert result.exit_reason == "horizon"
    assert result.excess_vs_vn30 is not None
    assert result.excess_vs_vn30 > 0  # outperformed VN30


def test_grade_due_predictions_writes_outcomes_and_is_idempotent(seeded):
    created = datetime(2021, 1, 4, tzinfo=timezone.utc)
    pred_id = _insert_prediction(
        seeded, TICKER, "buy_accumulate", entry_zone="[95,100]", stop_loss=80, target=110, created_at=created,
    )
    _seed_prices(seeded, TICKER, [
        (date(2021, 1, 5), 98, 99, 97, 98),
        (date(2021, 1, 6), 99, 112, 98, 111),
    ])
    seeded.commit()

    graded = grade_due_predictions(seeded, CFG)
    assert graded == 1
    row = seeded.execute(
        "SELECT exit_reason FROM prediction_outcomes WHERE prediction_id = %s", (pred_id,)
    ).fetchone()
    assert row[0] == "target"

    # Second pass must not re-grade the same (prediction_id, horizon_days).
    graded_again = grade_due_predictions(seeded, CFG)
    assert graded_again == 0
