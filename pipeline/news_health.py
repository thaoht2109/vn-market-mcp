"""Per-source freshness for collected news (table source_health).

Silence must not read as "no news": a source with no new item for 6 working hours, or 3 failures in a
row, raises one ops alert and shows up as `stale` in what Hermes reads. Callers commit.
"""
from __future__ import annotations

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

VN = ZoneInfo("Asia/Ho_Chi_Minh")
WORK_START, WORK_END = time(8, 0), time(18, 0)   # Mon-Fri; public holidays are not subtracted
STALE_AFTER = timedelta(hours=6)
FAILURES_BEFORE_ALERT = 3
VNSTOCK_NEWS_SOURCE = "vnstock_news"


def working_time_between(start: datetime, end: datetime) -> timedelta:
    if end <= start:
        return timedelta(0)
    s, e = start.astimezone(VN), end.astimezone(VN)
    total, day = timedelta(0), s.date()
    while day <= e.date():
        if day.weekday() < 5:
            lo, hi = datetime.combine(day, WORK_START, VN), datetime.combine(day, WORK_END, VN)
            total += max(timedelta(0), min(hi, e) - max(lo, s))
        day += timedelta(days=1)
    return total


def is_stale(last_item_at: datetime | None, first_seen_at: datetime, now: datetime) -> bool:
    return working_time_between(last_item_at or first_seen_at, now) > STALE_AFTER


def record_ok(conn, source: str, newest_item_at: datetime | None, now: datetime) -> None:
    """A successful call. `alerted_at` is cleared only on real recovery: the source was failing, or a newer
    item arrived. A call that succeeds but brings nothing new must not re-arm the silence alert."""
    conn.execute(
        """
        INSERT INTO source_health (source, last_ok_at, last_item_at, first_seen_at)
        VALUES (%(s)s, %(now)s, %(item)s, %(now)s)
        ON CONFLICT (source) DO UPDATE SET
          alerted_at = CASE WHEN source_health.consecutive_failures > 0
                              OR (EXCLUDED.last_item_at IS NOT NULL
                                  AND (source_health.last_item_at IS NULL OR EXCLUDED.last_item_at > source_health.last_item_at))
                            THEN NULL ELSE source_health.alerted_at END,
          last_ok_at = EXCLUDED.last_ok_at,
          last_item_at = GREATEST(source_health.last_item_at, EXCLUDED.last_item_at),
          consecutive_failures = 0,
          last_error = NULL
        """,
        {"s": source, "now": now, "item": newest_item_at},
    )


def record_failure(conn, source: str, error: str, now: datetime) -> None:
    conn.execute(
        """
        INSERT INTO source_health (source, last_error, consecutive_failures, first_seen_at)
        VALUES (%(s)s, %(err)s, 1, %(now)s)
        ON CONFLICT (source) DO UPDATE SET
          consecutive_failures = source_health.consecutive_failures + 1, last_error = EXCLUDED.last_error
        """,
        {"s": source, "err": error[:500], "now": now},
    )


def check_and_alert(conn, now: datetime, rss_sources: set[str], send) -> list[str]:
    """One ops alert per bad stretch. `send(text) -> bool`; an unsent alert is retried next time."""
    rows = conn.execute(
        "SELECT source, last_item_at, first_seen_at, consecutive_failures, last_error"
        " FROM source_health WHERE alerted_at IS NULL"
    ).fetchall()
    alerted = []
    for source, last_item, first_seen, failures, error in rows:
        if failures >= FAILURES_BEFORE_ALERT:
            text = f"[tin tức] nguồn {source} lỗi {failures} lần liên tiếp: {error}"
        elif source in rss_sources and is_stale(last_item, first_seen, now):
            since = (last_item or first_seen).astimezone(VN)
            text = f"[tin tức] nguồn {source} không có tin mới từ {since:%H:%M %d/%m} (quá 6 giờ làm việc)"
        else:
            continue
        if send(text):
            conn.execute("UPDATE source_health SET alerted_at = %s WHERE source = %s", (now, source))
            alerted.append(source)
    return alerted


def news_sources_status(conn, now: datetime, names: list[str]) -> list[dict]:
    rows = {r[0]: r[1:] for r in conn.execute(
        "SELECT source, last_item_at, first_seen_at FROM source_health WHERE source = ANY(%s)", (names,))}
    out = []
    for name in names:
        if name not in rows:  # never ran: unknown, not "fine"
            out.append({"source": name, "last_item_at": None, "stale": True})
            continue
        last, first = rows[name]
        out.append({"source": name, "last_item_at": last.astimezone(VN).isoformat() if last else None,
                    "stale": is_stale(last, first, now)})
    return out


def source_warnings(status: list[dict]) -> list[str]:
    out = []
    for s in status:
        if not s["stale"]:
            continue
        if s["last_item_at"]:
            since = datetime.fromisoformat(s["last_item_at"]).astimezone(VN)
            out.append(f"Tin từ nguồn {s['source']} chưa cập nhật từ {since:%H:%M %d/%m}")
        else:
            out.append(f"Chưa có dữ liệu từ nguồn tin {s['source']}")
    return out
