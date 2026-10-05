# Thu thập tin tức — Giai đoạn 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Thu tin RSS vĩ mô + doanh nghiệp VN30 vào Postgres hiện có, lọc bằng quy tắc tất định, theo dõi độ mới từng nguồn, cho Hermes đọc qua tool MCP `get_macro_context`; sửa 3 lỗi tiên quyết (khóa job, partition hết hạn, lỗi tin bị nuốt).

**Architecture:** Mở rộng trong repo: job `collect_rss` đi qua hàng đợi `jobs` + worker hiện có (scheduler chỉ enqueue). Module mới `pipeline/news*.py` + `pipeline/collectors/rss.py`; mọi tin đều được lưu kèm kết luận lọc (`kept`/`dropped` + lý do), không xóa. Không LLM trong pipeline.

**Tech Stack:** Python 3.11, psycopg 3, httpx (đã có), `feedparser` (mới), pyyaml (đã có), pytest, Postgres 16.

**Spec:** `docs/superpowers/specs/2026-10-06-news-collection-phase1-design.md`

## Global Constraints

- `llm.pipeline_enabled` giữ `false`; không thêm lời gọi LLM nào; không đổi điểm, nhãn, grading, `config/vn-rules.yaml`.
- Thư viện mới duy nhất: `feedparser` (không `rapidfuzz`, không `trafilatura`, không SQLAlchemy/Alembic). Khử trùng tiêu đề bằng `difflib.SequenceMatcher`, ngưỡng `>= 0.9`, cửa sổ 48 giờ.
- Migration là file SQL tuần tự `db/migrations/NNN_*.sql` (file mới: `018_news_collection.sql`). Sau migration phải chạy lại `python -m db.setup_roles`.
- Giờ theo múi `Asia/Ho_Chi_Minh`. Giờ làm việc = 08:00–18:00, thứ 2–6. Cảnh báo im lặng sau **6 giờ làm việc**; cảnh báo lỗi sau **3 lần lỗi liên tiếp**.
- Mọi tin được ghi kể cả `dropped`; không bao giờ DELETE tin.
- Gọi HTTP: User-Agent rõ ràng, timeout 15 s, tối thiểu 2 s giữa hai request cùng domain, retry 2 lần khi lỗi mạng/5xx, **không** retry 403/429.
- Test chạy bằng `./run_tests.sh` (DB `vnmcp_test`), không bao giờ chạy pytest trần trên DB thật.
- Log qua `ops.alerting.log_event` (stderr); stdout của MCP server chỉ chứa JSON-RPC.

## Review Focus

Các đầu vào mà spec ngụ ý nhưng dễ bị bỏ sót, theo thứ tự dễ gặp nhất; mỗi dòng có test ở task ghi trong ngoặc.

1. Feed trả HTTP 200 nhưng là trang HTML chặn của WAF ("Request Rejected", đã gặp thật ở SBV): phải tính là **lỗi nguồn**, không phải "feed rỗng im lặng". (Task 6)
2. Mục RSS không có `pubDate`, hoặc `pubDate` ở tương lai: dùng giờ fetch; `last_item_at` không bao giờ vượt `now`, nếu không nguồn chết sẽ không bao giờ bị báo im lặng. (Task 6)
3. Tiêu đề Unicode dạng tổ hợp rời (NFD) vẫn khớp từ khóa; "Fed" không khớp "Federer". (Task 4)
4. Cuối tuần và ban đêm không sinh cảnh báo im lặng; một đợt im lặng chỉ báo một lần; lần gọi thành công nhưng không có tin mới không làm báo lại. (Task 5)
5. Partition qua ranh giới năm (tháng 11/2026 → tháng 2/2027) và ghi tin ngày 2027-01-15 phải thành công; thiếu partition phải **báo lỗi**, không nuốt. (Task 3, Task 7)

---

## Setup (một lần, trước Task 1)

Worktree không có file untracked của repo chính. Từ thư mục worktree:

- [ ] **Step 1: Mang cấu hình và venv vào worktree**

```bash
cp ../../../.env .env
cp ../../../infrastructure/.env infrastructure/.env
ln -s ../../../.venv .venv
.venv/bin/python -m pip install feedparser==6.0.11
```

Expected: `Successfully installed feedparser-6.0.11 sgmllib3k-...` (hoặc "already satisfied").

- [ ] **Step 2: Xác nhận bộ test hiện tại xanh trước khi sửa**

Run: `./run_tests.sh -q`
Expected: toàn bộ PASS. Nếu có test đỏ sẵn, ghi lại tên rồi mới đi tiếp (để không nhầm là lỗi do plan này). Cần Postgres đang chạy (`docker compose up -d postgres` từ repo chính).

---

## File Structure

| File | Trách nhiệm |
|---|---|
| `pipeline/jobs.py` (sửa) | Khóa advisory ổn định; `COLLECT_JOB_TYPES`; ưu tiên job thu thập trong `claim_next` |
| `db/migrations/018_news_collection.sql` (mới) | Cột mới cho `news_items`, bảng `source_health` |
| `pipeline/news.py` (mới) | Đọc `news_sources.yaml`; tạo partition; ghi một tin (khử trùng URL) |
| `pipeline/news_filter.py` (mới) | Lọc tầng 1 thuần (`classify`), khử trùng tiêu đề, `refilter` |
| `pipeline/news_health.py` (mới) | `source_health`: ghi OK/lỗi, giờ làm việc, cảnh báo, trạng thái cho MCP |
| `pipeline/collectors/rss.py` (mới) | Tải feed (ETag, retry, giãn cách), parse, lọc, lưu, cập nhật health |
| `ops/scheduler.py`, `ops/worker.py` (sửa) | Enqueue `collect_rss` mỗi giờ; worker chạy; tạo partition |
| `mcp_server/tools/macro.py` (mới) + `server.py`, `digests.py`, `stock_report.py` (sửa) | Tool `get_macro_context`, lọc `dropped`, khối `news_sources` |
| `config/news_sources.yaml`, `macro_keywords.yaml`, `ticker_aliases.yaml` (mới) | Cấu hình nguồn, từ khóa, tên gọi khác |
| `evals/news_filter_recall.py` (mới) | Xuất mẫu + đo recall (gate) |

---

### Task 1: Khóa advisory ổn định và ưu tiên job thu thập

**Files:**
- Modify: `pipeline/jobs.py` (hàm `_lock_key`, `claim_next`, hằng số mới)
- Test: `tests/test_jobs.py`

**Interfaces:**
- Produces: `COLLECT_RSS_JOB_TYPE = "collect_rss"`, `COLLECT_JOB_TYPES = ("collect_rss",)`, `_lock_key(ticker, job_type) -> int` ổn định giữa các tiến trình.

- [ ] **Step 1: Viết test fail**

Thêm vào cuối `tests/test_jobs.py` (thêm `import subprocess, sys` và `from pathlib import Path` ở đầu file nếu chưa có; import thêm `_lock_key`):

```python
import subprocess
import sys
from pathlib import Path

from pipeline.jobs import _lock_key

_ROOT = Path(__file__).parent.parent


def test_lock_key_is_identical_across_processes():
    """hash() is randomised per process, so two workers used to disagree on the lock for the same job."""
    code = "from pipeline.jobs import _lock_key; print(_lock_key('HPG', 'on_demand'))"
    outs = {
        subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True, cwd=_ROOT).stdout.strip()
        for _ in range(3)
    }
    assert outs == {str(_lock_key("HPG", "on_demand"))}


def test_claim_next_prefers_collect_jobs_over_older_analysis_jobs(db_conn):
    insert_ticker(db_conn, "JOBPRIO")
    db_conn.commit()
    conn = psycopg.connect(DATABASE_URL)
    try:
        conn.execute("DELETE FROM jobs WHERE status = 'queued'")  # dedicated *_test DB (conftest guards it)
        conn.commit()
        enqueue(conn, "JOBPRIO", job_type="on_demand")  # older
        enqueue(conn, "MARKET", job_type="collect_rss", requested_by="cron")  # newer, but must win
        job = claim_next(conn)
        assert job.job_type == "collect_rss"
        release(conn, job)
    finally:
        conn.execute("DELETE FROM jobs WHERE ticker IN ('JOBPRIO', 'MARKET')")
        conn.commit()
        conn.close()
```

- [ ] **Step 2: Chạy test, xác nhận fail**

Run: `./run_tests.sh tests/test_jobs.py -q`
Expected: `test_lock_key_is_identical_across_processes` FAIL (ba số khác nhau), `test_claim_next_prefers_collect_jobs...` FAIL (`on_demand` được claim trước).

- [ ] **Step 3: Sửa `pipeline/jobs.py`**

Thêm `import hashlib` cạnh `import time`. Thay hàm `_lock_key` và khối chú thích phía trên nó:

```python
# Market-wide collection jobs: claimed before analysis jobs so they are not stuck behind the ~30
# tickers queued at 15:05-15:30.
COLLECT_RSS_JOB_TYPE = "collect_rss"
COLLECT_JOB_TYPES = (COLLECT_RSS_JOB_TYPE,)


# Postgres advisory locks take a single bigint key; hash (ticker, job_type) into one so concurrent
# runs of the same ticker+mode serialize (spec §4.4) without a separate lock table. Must be stable
# across processes: the builtin hash() is randomised per process, so two workers would take
# different locks for the same job.
def _lock_key(ticker: str, job_type: str) -> int:
    digest = hashlib.blake2b(f"{ticker}|{job_type}".encode(), digest_size=8).digest()
    return int.from_bytes(digest, "big") & 0x7FFFFFFFFFFFFFFF
```

Trong `claim_next`, đổi câu SELECT:

```python
    row = conn.execute(
        "SELECT id, job_key, job_type, ticker, style, depth, requested_by, attempts"
        " FROM jobs WHERE status = 'queued'"
        " ORDER BY (job_type = ANY(%s::text[])) DESC, created_at LIMIT 20",
        (list(COLLECT_JOB_TYPES),),
    ).fetchall()
```

- [ ] **Step 4: Chạy test, xác nhận pass**

Run: `./run_tests.sh tests/test_jobs.py tests/test_worker.py tests/test_scheduler.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add pipeline/jobs.py tests/test_jobs.py
git commit -m "fix(jobs): process-stable advisory lock key; collect jobs claimed before analysis jobs"
```

---

### Task 2: Migration `018_news_collection.sql`

**Files:**
- Create: `db/migrations/018_news_collection.sql`
- Test: `tests/test_news_collection_schema.py`

**Interfaces:**
- Produces: cột `news_items.stream/filter_status/filter_reason/pillars`; bảng `source_health(source PK, last_ok_at, last_item_at, last_error, consecutive_failures, alerted_at, first_seen_at)`.

- [ ] **Step 1: Viết test fail**

`tests/test_news_collection_schema.py`:

```python
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
```

- [ ] **Step 2: Chạy test, xác nhận fail**

Run: `./run_tests.sh tests/test_news_collection_schema.py -q`
Expected: FAIL (`relation "source_health" does not exist` hoặc thiếu cột).

- [ ] **Step 3: Viết migration**

`db/migrations/018_news_collection.sql`:

```sql
-- Phase 1 of news collection (docs/superpowers/specs/2026-10-06-news-collection-phase1-design.md).
-- Existing rows (vnstock headlines) keep NULL in the new columns and read as "kept".
ALTER TABLE news_items
  ADD COLUMN stream        TEXT,                       -- 'A' macro | 'B' company | NULL (vnstock)
  ADD COLUMN filter_status TEXT,                       -- 'kept' | 'dropped' | NULL (vnstock: treat as kept)
  ADD COLUMN filter_reason TEXT,                       -- 'exclude:<phrase>' | 'no_keyword' | 'no_ticker' | 'duplicate_title:<url_hash>'
  ADD COLUMN pillars       TEXT[] NOT NULL DEFAULT '{}';

-- Freshness per news source. Silence must never read as "no news": readers and alerts use this table.
CREATE TABLE source_health (
  source               TEXT PRIMARY KEY,               -- `name` in config/news_sources.yaml, or 'vnstock_news'
  last_ok_at           TIMESTAMPTZ,
  last_item_at         TIMESTAMPTZ,                    -- newest published_at received (never later than the fetch time)
  last_error           TEXT,
  consecutive_failures INTEGER NOT NULL DEFAULT 0,
  alerted_at           TIMESTAMPTZ,                    -- set when an ops alert was sent; cleared on real recovery
  first_seen_at        TIMESTAMPTZ NOT NULL DEFAULT now()  -- silence clock for a source that never produced an item
);
```

- [ ] **Step 4: Chạy test, xác nhận pass**

Run: `./run_tests.sh tests/test_news_collection_schema.py tests/test_migrate.py tests/test_db_roles.py -q`
Expected: PASS (`create_test_db` áp migration rồi chạy `setup_roles`).

- [ ] **Step 5: Commit**

```bash
git add db/migrations/018_news_collection.sql tests/test_news_collection_schema.py
git commit -m "feat(db): news_items filter columns and source_health (migration 018)"
```

---

### Task 3: `pipeline/news.py` — nguồn, partition, ghi một tin

**Files:**
- Create: `pipeline/news.py`, `config/news_sources.yaml`
- Test: `tests/test_news_store.py`

**Interfaces:**
- Produces:
  - `load_sources(path=SOURCES_PATH) -> list[dict]` (chỉ nguồn `enabled`; mỗi dict có `name, stream, url, every_minutes`)
  - `ensure_news_partitions(conn, today: date, months_ahead: int = 3) -> list[str]` (tên partition mới tạo; có `conn.commit()`)
  - `item_hash(url: str | None, title: str, published_at: datetime) -> str`
  - `store_news_item(conn, *, source, url, title, summary, published_at, fetched_at, tickers, pillars, stream, filter_status, filter_reason) -> bool` (True nếu chèn mới, False nếu chỉ hợp nhất vào dòng cũ; **ném lỗi** nếu thiếu partition, savepoint giữ transaction dùng được)

- [ ] **Step 1: Kiểm tra các URL nguồn và tạo `config/news_sources.yaml`**

Run:

```bash
for u in https://cafef.vn/vi-mo-dau-tu.rss https://cafef.vn/thi-truong-chung-khoan.rss https://vnexpress.net/rss/kinh-doanh.rss https://cafebiz.vn/rss/vi-mo.rss; do
  printf "%s " "$u"; curl -s -o /dev/null -m 15 -w "%{http_code} %{content_type}\n" -A "vn-market-mcp/1.0 (personal research tool)" "$u"; done
```

(cần `allowed_domains`: cafef.vn, vnexpress.net, cafebiz.vn). Ghi `enabled: true` chỉ cho URL trả `200` + content-type chứa `xml`; nếu UA tùy chỉnh bị chặn (403), đổi `USER_AGENT` ở Task 6 sang chuỗi tương thích trình duyệt có kèm `vn-market-mcp`. Ngày 2026-10-06 đã xác minh 3 URL đầu; `cafebiz` chưa xác minh.

```yaml
# Nguồn tin RSS. enabled=false cho tới khi URL được xác minh trả RSS hợp lệ.
- name: cafef_vi_mo
  stream: A
  url: https://cafef.vn/vi-mo-dau-tu.rss
  every_minutes: 60
  enabled: true
- name: cafef_chung_khoan
  stream: B
  url: https://cafef.vn/thi-truong-chung-khoan.rss
  every_minutes: 60
  enabled: true
- name: vnexpress_kinh_doanh
  stream: A
  url: https://vnexpress.net/rss/kinh-doanh.rss
  every_minutes: 60
  enabled: true
- name: cafebiz_vi_mo
  stream: A
  url: https://cafebiz.vn/rss/vi-mo.rss
  every_minutes: 60
  enabled: false   # đổi thành true nếu curl ở trên trả 200 + xml
```

- [ ] **Step 2: Viết test fail**

`tests/test_news_store.py`:

```python
from datetime import date, datetime, timedelta, timezone

import psycopg
import pytest

from pipeline.news import ensure_news_partitions, item_hash, load_sources, store_news_item


def _kw(**over):
    p = datetime(2026, 10, 6, 1, 0, tzinfo=timezone.utc)
    base = dict(source="test_a", url="http://t/x1", title="t1", summary=None, published_at=p, fetched_at=p,
                tickers=[], pillars=[], stream="A", filter_status="kept", filter_reason=None)
    base.update(over)
    return base


def test_load_sources_returns_only_enabled_sources_with_required_keys():
    sources = load_sources()
    assert sources and all({"name", "stream", "url", "every_minutes"} <= s.keys() for s in sources)
    assert all(s["stream"] in ("A", "B") for s in sources)


def test_ensure_news_partitions_covers_months_across_a_year_end(db_conn):
    ensure_news_partitions(db_conn, date(2026, 11, 20))
    assert ensure_news_partitions(db_conn, date(2026, 11, 20)) == []  # idempotent
    for name in ("news_items_2026_11", "news_items_2026_12", "news_items_2027_01", "news_items_2027_02"):
        assert db_conn.execute("SELECT to_regclass(%s)", (name,)).fetchone()[0] is not None
    jan = datetime(2027, 1, 15, 3, 0, tzinfo=timezone.utc)
    assert store_news_item(db_conn, **_kw(url="http://t/jan", published_at=jan, fetched_at=jan)) is True


def test_store_dedupes_by_url_across_sources_and_merges_tickers_and_pillars(db_conn):
    ensure_news_partitions(db_conn, date(2026, 10, 6))
    first = _kw(pillars=["tien_te"])
    assert store_news_item(db_conn, **first) is True
    later = _kw(source="test_b", tickers=["VCB"], published_at=first["published_at"] + timedelta(hours=2))
    assert store_news_item(db_conn, **later) is False  # same URL, other feed, other pub time
    rows = db_conn.execute("SELECT tickers, pillars FROM news_items WHERE url_hash = %s",
                           (item_hash("http://t/x1", "t1", first["published_at"]),)).fetchall()
    assert rows == [(["VCB"], ["tien_te"])]


def test_store_without_url_falls_back_to_title_and_day(db_conn):
    ensure_news_partitions(db_conn, date(2026, 10, 6))
    assert store_news_item(db_conn, **_kw(url=None, title="no link")) is True
    assert store_news_item(db_conn, **_kw(url=None, title="no link")) is False


def test_missing_partition_raises_instead_of_being_swallowed(db_conn):
    far = datetime(2031, 1, 1, tzinfo=timezone.utc)  # no partition (test_ingest relies on this too)
    with pytest.raises(psycopg.Error):
        store_news_item(db_conn, **_kw(url="http://t/far", published_at=far, fetched_at=far))
    db_conn.execute("SELECT 1")  # savepoint rolled back: the caller's transaction is still usable
```

- [ ] **Step 3: Chạy test, xác nhận fail**

Run: `./run_tests.sh tests/test_news_store.py -q`
Expected: FAIL (`ModuleNotFoundError: pipeline.news`).

- [ ] **Step 4: Viết `pipeline/news.py`**

```python
"""Collected news: source config, monthly partitions, and storing one item.

News is stored raw with its tier-1 verdict; nothing is deleted. A missing partition raises (it used to be
swallowed), so a collector can record it as a source failure instead of silently losing news.
"""
from __future__ import annotations

import hashlib
from datetime import date, datetime, timedelta
from pathlib import Path

import psycopg
import yaml

SOURCES_PATH = Path(__file__).parent.parent / "config" / "news_sources.yaml"
_URL_DEDUP_WINDOW = timedelta(days=7)


def load_sources(path: Path = SOURCES_PATH) -> list[dict]:
    return [s for s in (yaml.safe_load(path.read_text()) or []) if s.get("enabled", True)]


def _month_start(d: date, offset: int) -> date:
    n = d.year * 12 + (d.month - 1) + offset
    return date(n // 12, n % 12 + 1, 1)


def ensure_news_partitions(conn: psycopg.Connection, today: date, months_ahead: int = 3) -> list[str]:
    """Create news_items_YYYY_MM for this month and the next `months_ahead`. Needs the table owner (admin).
    No DEFAULT partition: it would block DETACHing old months during retention."""
    created = []
    for i in range(months_ahead + 1):
        start, end = _month_start(today, i), _month_start(today, i + 1)
        name = f"news_items_{start:%Y_%m}"  # built from dates only, never from input
        if conn.execute("SELECT to_regclass(%s)", (name,)).fetchone()[0] is None:
            conn.execute(f"CREATE TABLE {name} PARTITION OF news_items FOR VALUES FROM ('{start}') TO ('{end}')")
            created.append(name)
    conn.commit()
    return created


def item_hash(url: str | None, title: str, published_at: datetime) -> str:
    """Same key as pipeline.ingest.ingest_news, so a story in both an RSS feed and vnstock is one row."""
    return hashlib.sha256((url or f"{title}|{published_at.date()}").encode()).hexdigest()


def store_news_item(
    conn: psycopg.Connection, *, source: str, url: str | None, title: str, summary: str | None,
    published_at: datetime, fetched_at: datetime, tickers: list[str], pillars: list[str],
    stream: str, filter_status: str, filter_reason: str | None,
) -> bool:
    """Insert one item; True if new. The unique index is (url_hash, published_at), so the same URL seen
    with another pub time (two feeds) is looked up within +-7 days and merged instead of duplicated."""
    url_hash = item_hash(url, title, published_at)
    with conn.transaction():  # savepoint: a failure here must not poison the caller's transaction
        existing = conn.execute(
            "SELECT id, published_at FROM news_items WHERE url_hash = %s AND published_at BETWEEN %s AND %s LIMIT 1",
            (url_hash, published_at - _URL_DEDUP_WINDOW, published_at + _URL_DEDUP_WINDOW),
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE news_items SET"
                " tickers = (SELECT ARRAY(SELECT DISTINCT unnest(tickers || %s::text[]))),"
                " pillars = (SELECT ARRAY(SELECT DISTINCT unnest(pillars || %s::text[])))"
                " WHERE id = %s AND published_at = %s",
                (tickers, pillars, existing[0], existing[1]),
            )
            return False
        conn.execute(
            """
            INSERT INTO news_items (published_at, tickers, source, url, url_hash, title, summary, fetched_at,
                                    stream, filter_status, filter_reason, pillars)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (url_hash, published_at) DO NOTHING
            """,
            (published_at, tickers, source, url, url_hash, title, summary, fetched_at,
             stream, filter_status, filter_reason, pillars),
        )
    return True
```

- [ ] **Step 5: Chạy test, xác nhận pass**

Run: `./run_tests.sh tests/test_news_store.py tests/test_ingest.py -q`
Expected: PASS (test_ingest vẫn xanh: partition năm 2031 chưa được tạo).

- [ ] **Step 6: Commit**

```bash
git add pipeline/news.py config/news_sources.yaml tests/test_news_store.py
git commit -m "feat(news): source config, self-extending partitions, URL-deduplicated storage"
```

---

### Task 4: `pipeline/news_filter.py` — lọc tầng 1

**Files:**
- Create: `pipeline/news_filter.py`, `config/macro_keywords.yaml`, `config/ticker_aliases.yaml`
- Test: `tests/test_news_filter.py`

**Interfaces:**
- Produces:
  - `FilterResult(status: str, reason: str | None, pillars: list[str], tickers: list[str])` (frozen dataclass)
  - `load_keywords(path=...) -> dict[str, list[str]]` (7 trụ cột + `exclude`), `load_aliases(path=...) -> dict[str, list[str]]`
  - `classify(title, summary, stream, vn30: set[str], aliases, keywords) -> FilterResult`
  - `is_duplicate_title(title: str, recent: list[tuple[str, str]]) -> str | None` (`recent` = `[(url_hash, title)]`; trả `url_hash` bản gốc)
  - `get_vn30(conn) -> set[str]`, `refilter(conn, days: int) -> dict`, CLI `python -m pipeline.news_filter --refilter --days N`
  - hằng `DUPLICATE_WINDOW = timedelta(hours=48)`

- [ ] **Step 1: Tạo cấu hình**

`config/macro_keywords.yaml`:

```yaml
# Lọc tầng 1: khớp nguyên từ, không phân biệt hoa thường. Thiên về recall: thêm từ khóa dễ hơn bớt.
tien_te: [lãi suất điều hành, tái cấp vốn, tái chiết khấu, OMO, tín phiếu, liên ngân hàng, lãi suất qua đêm, tín dụng, cung tiền, NHNN, Ngân hàng Nhà nước, dự trữ bắt buộc]
ty_gia_doi_ngoai: [tỷ giá, USD/VND, dự trữ ngoại hối, xuất khẩu, nhập khẩu, FDI, thuế quan, cán cân thương mại, xuất siêu, nhập siêu]
tang_truong: [GDP, PMI, IIP, bán lẻ, đầu tư công, giải ngân, tăng trưởng kinh tế, sản xuất công nghiệp]
lam_phat: [CPI, lạm phát, giá xăng, giá điện, giá thịt heo]
tai_khoa_chinh_sach: [nghị quyết, thông tư, nghị định, thuế, trái phiếu chính phủ, trái phiếu doanh nghiệp, ngân sách, bội chi, Bộ Tài chính]
thi_truong_von: [nâng hạng, khối ngoại, tự doanh, margin, UBCK, KRX, VN-Index]
toan_cau: [Fed, FOMC, lợi suất trái phiếu Mỹ, DXY, giá dầu, Trung Quốc, ECB, OPEC, chiến tranh thương mại]
# Loại trừ có ưu tiên cao hơn trụ cột. Chỉ cụm rõ ràng là quảng cáo/đời sống.
exclude: [doanh nghiệp giới thiệu, khuyến mãi, giá vàng nhẫn hôm nay, giá vàng hôm nay, xổ số, bóng đá, tử vi, thời tiết]
```

`config/ticker_aliases.yaml` (tên gọi khác để khớp tin; chỉ mã có trong VN30 hiện hành mới được dùng — đối chiếu với `SELECT ticker FROM index_membership WHERE index_code='VN30'` và bổ sung/bớt khi rổ đổi):

```yaml
ACB: [Ngân hàng Á Châu]
BCM: [Becamex]
BID: [BIDV]
CTG: [VietinBank]
FPT: [Tập đoàn FPT]
GAS: [PV GAS]
GVR: [Cao su Việt Nam]
HDB: [HDBank]
HPG: [Hòa Phát]
MBB: [MB Bank, Ngân hàng Quân đội]
MSN: [Masan]
MWG: [Thế Giới Di Động]
PLX: [Petrolimex]
SAB: [Sabeco]
SSB: [SeABank]
SSI: [Chứng khoán SSI]
STB: [Sacombank]
TCB: [Techcombank]
TPB: [TPBank]
VCB: [Vietcombank]
VHM: [Vinhomes]
VIC: [Vingroup]
VJC: [Vietjet]
VNM: [Vinamilk]
VPB: [VPBank]
VRE: [Vincom Retail]
```

- [ ] **Step 2: Viết test fail**

`tests/test_news_filter.py`:

```python
import unicodedata
from datetime import date, datetime, timedelta, timezone

import pytest

from pipeline.news import ensure_news_partitions, store_news_item
from pipeline.news_filter import (
    classify, get_vn30, is_duplicate_title, load_aliases, load_keywords, refilter,
)

KEYWORDS, ALIASES, VN30 = load_keywords(), load_aliases(), {"FPT", "VCB", "TCB"}


def run(title, summary="", stream="A"):
    return classify(title, summary, stream, VN30, ALIASES, KEYWORDS)


PILLAR_SAMPLES = [
    ("tien_te", "NHNN giảm lãi suất điều hành 0,5 điểm %"),
    ("tien_te", "Thị trường liên ngân hàng: lãi suất qua đêm tăng mạnh"),
    ("tien_te", "Tín dụng tăng 8% từ đầu năm"),
    ("ty_gia_doi_ngoai", "Tỷ giá USD/VND vượt 26.000"),
    ("ty_gia_doi_ngoai", "Dự trữ ngoại hối của Việt Nam đạt mức cao"),
    ("ty_gia_doi_ngoai", "Xuất khẩu tháng 9 tăng 12%"),
    ("tang_truong", "GDP quý III tăng 7,8%"),
    ("tang_truong", "PMI ngành sản xuất vượt ngưỡng 50"),
    ("tang_truong", "Giải ngân vốn đầu tư công đạt 60%"),
    ("lam_phat", "CPI tháng 9 tăng 0,3%"),
    ("lam_phat", "Lạm phát bình quân 9 tháng đạt 3,2%"),
    ("lam_phat", "Giá xăng điều chỉnh giảm từ chiều nay"),
    ("tai_khoa_chinh_sach", "Chính phủ ban hành nghị quyết mới về thuế"),
    ("tai_khoa_chinh_sach", "Bộ Tài chính phát hành trái phiếu chính phủ"),
    ("tai_khoa_chinh_sach", "Thông tư mới siết trái phiếu doanh nghiệp"),
    ("thi_truong_von", "Việt Nam được nâng hạng thị trường mới nổi"),
    ("thi_truong_von", "Khối ngoại bán ròng phiên thứ 5 liên tiếp"),
    ("thi_truong_von", "UBCK đưa ra quy định mới về margin"),
    ("toan_cau", "Fed giữ nguyên lãi suất tại cuộc họp FOMC"),
    ("toan_cau", "Giá dầu giảm sâu"),
    ("toan_cau", "Lợi suất trái phiếu Mỹ lên cao nhất 15 năm"),
]


@pytest.mark.parametrize("pillar,title", PILLAR_SAMPLES)
def test_every_pillar_sample_is_kept_under_its_pillar(pillar, title):
    r = run(title)
    assert r.status == "kept" and pillar in r.pillars


def test_exclude_phrase_beats_a_pillar_match():
    r = run("Giá vàng nhẫn hôm nay tăng, tỷ giá USD/VND đi ngang")
    assert (r.status, r.reason) == ("dropped", "exclude:giá vàng nhẫn hôm nay")


def test_no_keyword_and_no_ticker_reasons_depend_on_stream():
    assert run("Cuối tuần nhiều nơi nắng đẹp", stream="A").reason == "no_keyword"
    assert run("Công ty ABC ký hợp đồng mới", stream="B").reason == "no_ticker"


def test_ticker_and_alias_matching_is_limited_to_vn30():
    assert run("FPT ký hợp đồng mới với đối tác Nhật", stream="B").tickers == ["FPT"]
    assert run("Techcombank báo lãi quý III tăng mạnh", stream="B").tickers == ["TCB"]
    assert run("ABC ký hợp đồng mới", stream="B").status == "dropped"  # not VN30


def test_a_macro_story_without_a_ticker_on_the_stock_feed_is_kept():
    r = run("Khối ngoại bán ròng 500 tỷ đồng", stream="B")
    assert r.status == "kept" and r.tickers == [] and "thi_truong_von" in r.pillars


def test_decomposed_unicode_titles_still_match():
    nfd = unicodedata.normalize("NFD", "Tỷ giá USD/VND tăng mạnh")
    assert nfd != "Tỷ giá USD/VND tăng mạnh"
    assert "ty_gia_doi_ngoai" in run(nfd).pillars


def test_keywords_match_whole_words_only():
    assert run("Federer vô địch giải quần vợt").pillars == []  # "Fed" is not a word in "Federer"


def test_duplicate_title_threshold():
    recent = [("h1", "NHNN giảm lãi suất điều hành, thị trường liên ngân hàng hạ nhiệt")]
    assert is_duplicate_title("NHNN giảm lãi suất điều hành, thị trường liên ngân hàng hạ nhiệt!", recent) == "h1"
    assert is_duplicate_title("Tỷ giá USD/VND vượt 26.000", recent) is None


def test_refilter_reapplies_current_rules_to_stored_rss_items(db_conn):
    ensure_news_partitions(db_conn, date.today())
    now = datetime.now(timezone.utc)

    def put(url, title, hours):
        store_news_item(db_conn, source="test_refilter", url=url, title=title, summary=None,
                        published_at=now - timedelta(hours=hours), fetched_at=now, tickers=[], pillars=[],
                        stream="A", filter_status="dropped", filter_reason="stale-rule")
    put("http://t/r1", "Tỷ giá USD/VND vượt 26.000", 3)
    put("http://t/r2", "Tỷ giá USD/VND vượt 26.000!", 2)   # near-identical to r1 → duplicate
    put("http://t/r3", "Cuối tuần nắng đẹp", 1)
    stats = refilter(db_conn, days=2)
    rows = {u: (s, r) for u, s, r in db_conn.execute(
        "SELECT url, filter_status, filter_reason FROM news_items WHERE source = 'test_refilter'")}
    assert rows["http://t/r1"] == ("kept", None)
    assert rows["http://t/r2"][0] == "dropped" and rows["http://t/r2"][1].startswith("duplicate_title:")
    assert rows["http://t/r3"] == ("dropped", "no_keyword")
    assert stats["rows"] >= 3 and isinstance(get_vn30(db_conn), set)
```

- [ ] **Step 3: Chạy test, xác nhận fail**

Run: `./run_tests.sh tests/test_news_filter.py -q`
Expected: FAIL (`ModuleNotFoundError: pipeline.news_filter`).

- [ ] **Step 4: Viết `pipeline/news_filter.py`**

```python
"""Tier-1 news filter: deterministic keyword / ticker rules, no LLM.

Never deletes: every item is stored with its verdict (`kept`/`dropped` + reason), so a rule change can be
re-applied to stored items with `python -m pipeline.news_filter --refilter --days N`. When in doubt, keep.
"""
from __future__ import annotations

import argparse
import difflib
import re
import unicodedata
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

import yaml

_CONFIG = Path(__file__).parent.parent / "config"
DUPLICATE_RATIO = 0.9
DUPLICATE_WINDOW = timedelta(hours=48)
_TICKER = re.compile(r"(?<![A-Za-z0-9])[A-Z]{3}(?![A-Za-z0-9])")


@dataclass(frozen=True)
class FilterResult:
    status: str            # 'kept' | 'dropped'
    reason: str | None     # why dropped; None when kept
    pillars: list[str]
    tickers: list[str]


def load_keywords(path: Path = _CONFIG / "macro_keywords.yaml") -> dict[str, list[str]]:
    return yaml.safe_load(path.read_text())


def load_aliases(path: Path = _CONFIG / "ticker_aliases.yaml") -> dict[str, list[str]]:
    return yaml.safe_load(path.read_text()) or {}


def _nfc(text: str | None) -> str:
    return unicodedata.normalize("NFC", text or "")


def _has(lower_text: str, phrase: str) -> bool:
    """Whole-word match on NFC-normalised, lower-cased text."""
    return re.search(rf"(?<!\w){re.escape(_nfc(phrase).lower())}(?!\w)", lower_text) is not None


def classify(title: str, summary: str | None, stream: str, vn30: set[str],
             aliases: dict[str, list[str]], keywords: dict[str, list[str]]) -> FilterResult:
    text = _nfc(f"{title}. {summary or ''}")
    lower = text.lower()
    for phrase in keywords.get("exclude", []):
        if _has(lower, phrase):
            return FilterResult("dropped", f"exclude:{phrase}", [], [])
    pillars = [p for p, words in keywords.items() if p != "exclude" and any(_has(lower, w) for w in words)]
    tickers = sorted(
        {t for t in _TICKER.findall(text) if t in vn30}
        | {t for t, names in aliases.items() if t in vn30 and any(_has(lower, n) for n in names)}
    )
    if pillars or tickers:  # same rule for both streams; bias towards recall
        return FilterResult("kept", None, pillars, tickers)
    return FilterResult("dropped", "no_keyword" if stream == "A" else "no_ticker", [], [])


def is_duplicate_title(title: str, recent: list[tuple[str, str]]) -> str | None:
    """url_hash of the first `recent` (url_hash, title) at least 90% similar to `title`, else None."""
    a = _nfc(title).lower()
    for ref, other in recent:
        if difflib.SequenceMatcher(None, a, _nfc(other).lower()).ratio() >= DUPLICATE_RATIO:
            return ref
    return None


def get_vn30(conn) -> set[str]:
    return {r[0] for r in conn.execute(
        "SELECT ticker FROM index_membership WHERE index_code = 'VN30' AND (valid_to IS NULL OR valid_to >= CURRENT_DATE)")}


def refilter(conn, days: int) -> dict:
    """Re-run the current rules over stored RSS items (stream IS NOT NULL) of the last `days` days."""
    keywords, aliases, vn30 = load_keywords(), load_aliases(), get_vn30(conn)
    rows = conn.execute(
        "SELECT id, published_at, url_hash, title, summary, stream FROM news_items"
        " WHERE stream IS NOT NULL AND published_at >= now() - make_interval(days => %s) ORDER BY published_at",
        (days,),
    ).fetchall()
    kept: list[tuple] = []  # (published_at, url_hash, title)
    counts = {"rows": len(rows), "kept": 0, "dropped": 0}
    for id_, published_at, url_hash, title, summary, stream in rows:
        res = classify(title, summary, stream, vn30, aliases, keywords)
        status, reason = res.status, res.reason
        if status == "kept":
            recent = [(h, t) for p, h, t in kept if published_at - p <= DUPLICATE_WINDOW]
            dup = is_duplicate_title(title, recent)
            if dup:
                status, reason = "dropped", f"duplicate_title:{dup}"
            else:
                kept.append((published_at, url_hash, title))
        conn.execute(
            "UPDATE news_items SET filter_status = %s, filter_reason = %s, pillars = %s,"
            " tickers = (SELECT ARRAY(SELECT DISTINCT unnest(tickers || %s::text[])))"
            " WHERE id = %s AND published_at = %s",
            (status, reason, res.pillars, res.tickers, id_, published_at),
        )
        counts[status] += 1
    return counts


def main() -> None:
    from mcp_server.connection import get_rw_conn

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--refilter", action="store_true", required=True)
    ap.add_argument("--days", type=int, required=True)
    args = ap.parse_args()
    with get_rw_conn() as conn:
        print(refilter(conn, args.days))


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Chạy test, xác nhận pass**

Run: `./run_tests.sh tests/test_news_filter.py -q`
Expected: PASS (21 mẫu trụ cột + 8 test còn lại). Nếu một mẫu trụ cột fail, sửa **từ khóa** trong YAML (không sửa mẫu) rồi chạy lại.

- [ ] **Step 6: Commit**

```bash
git add pipeline/news_filter.py config/macro_keywords.yaml config/ticker_aliases.yaml tests/test_news_filter.py
git commit -m "feat(news): deterministic tier-1 filter with keyword pillars, VN30 tickers and title dedup"
```

---

### Task 5: `pipeline/news_health.py` — theo dõi độ mới và cảnh báo

**Files:**
- Create: `pipeline/news_health.py`
- Test: `tests/test_news_health.py`

**Interfaces:**
- Produces (không hàm nào tự `commit`; người gọi commit):
  - `record_ok(conn, source: str, newest_item_at: datetime | None, now: datetime) -> None`
  - `record_failure(conn, source: str, error: str, now: datetime) -> None`
  - `working_time_between(start: datetime, end: datetime) -> timedelta`
  - `is_stale(last_item_at, first_seen_at, now) -> bool`
  - `check_and_alert(conn, now: datetime, rss_sources: set[str], send) -> list[str]` (`send(text) -> bool`; trả tên nguồn đã báo; chỉ đánh dấu `alerted_at` khi `send` trả truthy)
  - `news_sources_status(conn, now, names: list[str]) -> list[dict]` (`{"source","last_item_at"(ISO giờ VN | None),"stale"}`; nguồn chưa có dòng nào → `stale: True`)
  - `source_warnings(status: list[dict]) -> list[str]`
  - hằng `VNSTOCK_NEWS_SOURCE = "vnstock_news"`

- [ ] **Step 1: Viết test fail**

`tests/test_news_health.py`:

```python
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from pipeline.news_health import (
    check_and_alert, is_stale, news_sources_status, record_failure, record_ok, source_warnings,
    working_time_between,
)

VN = ZoneInfo("Asia/Ho_Chi_Minh")


def vn(day, hour, minute=0):  # October 2026: 2 = Fri, 3 = Sat, 4 = Sun, 5 = Mon, 6 = Tue, 7 = Wed
    return datetime(2026, 10, day, hour, minute, tzinfo=VN)


def sender(sent):
    return lambda text: sent.append(text) or True


def mine(names):  # the shared *_test DB may hold other rows; look only at ours
    return [n for n in names if n.startswith("test_")]


def test_working_time_counts_only_weekday_office_hours():
    assert working_time_between(vn(2, 17), vn(5, 9)) == timedelta(hours=2)   # Fri 17-18 + Mon 8-9
    assert working_time_between(vn(3, 9), vn(4, 17)) == timedelta(0)         # weekend
    assert working_time_between(vn(6, 18, 30), vn(7, 7, 30)) == timedelta(0)  # overnight


def test_is_stale_uses_first_seen_when_a_source_never_produced_an_item():
    assert is_stale(None, vn(6, 8), vn(6, 15)) is True    # 7 working hours of silence
    assert is_stale(None, vn(6, 8), vn(6, 13)) is False


def test_no_alert_over_a_quiet_weekend(db_conn):
    record_ok(db_conn, "test_weekend", vn(2, 17, 30), vn(2, 17, 30))
    assert mine(check_and_alert(db_conn, vn(4, 12), {"test_weekend"}, sender([]))) == []


def test_silence_alerts_once_and_only_a_new_item_rearms(db_conn):
    now = datetime(2026, 10, 6, 3, 0, tzinfo=timezone.utc)  # Tue 10:00 VN
    record_ok(db_conn, "test_silent", datetime(2026, 10, 1, 3, 0, tzinfo=timezone.utc), now - timedelta(days=5))
    sent = []
    assert mine(check_and_alert(db_conn, now, {"test_silent"}, sender(sent))) == ["test_silent"]
    assert mine(check_and_alert(db_conn, now, {"test_silent"}, sender(sent))) == []
    record_ok(db_conn, "test_silent", None, now)  # call succeeded but still no new item: stay alerted
    assert mine(check_and_alert(db_conn, now, {"test_silent"}, sender(sent))) == []
    record_ok(db_conn, "test_silent", now - timedelta(minutes=5), now)  # a fresh item: real recovery
    assert db_conn.execute("SELECT alerted_at FROM source_health WHERE source = 'test_silent'").fetchone()[0] is None


def test_three_consecutive_failures_alert_even_for_a_non_rss_source(db_conn):
    now = datetime(2026, 10, 6, 3, 0, tzinfo=timezone.utc)
    sent = []
    for _ in range(2):
        record_failure(db_conn, "test_vnstock", "boom", now)
    assert "test_vnstock" not in check_and_alert(db_conn, now, set(), sender(sent))
    record_failure(db_conn, "test_vnstock", "boom", now)
    assert "test_vnstock" in check_and_alert(db_conn, now, set(), sender(sent))
    assert any("test_vnstock" in m and "boom" in m for m in sent)


def test_an_alert_that_could_not_be_sent_is_retried(db_conn):
    now = datetime(2026, 10, 6, 3, 0, tzinfo=timezone.utc)
    for _ in range(3):
        record_failure(db_conn, "test_retry", "boom", now)
    assert "test_retry" not in check_and_alert(db_conn, now, set(), lambda m: False)  # Telegram down
    assert "test_retry" in check_and_alert(db_conn, now, set(), lambda m: True)


def test_news_sources_status_marks_unknown_and_old_sources_stale(db_conn):
    now = datetime(2026, 10, 6, 3, 0, tzinfo=timezone.utc)
    record_ok(db_conn, "test_fresh", now - timedelta(hours=1), now - timedelta(hours=1))
    record_ok(db_conn, "test_old", now - timedelta(days=5), now - timedelta(days=5))
    status = {s["source"]: s for s in news_sources_status(db_conn, now, ["test_fresh", "test_old", "test_never"])}
    assert status["test_fresh"]["stale"] is False
    assert status["test_old"]["stale"] is True
    assert status["test_never"] == {"source": "test_never", "last_item_at": None, "stale": True}
    warnings = source_warnings(list(status.values()))
    assert len(warnings) == 2 and any("test_never" in w for w in warnings)
```

- [ ] **Step 2: Chạy test, xác nhận fail**

Run: `./run_tests.sh tests/test_news_health.py -q`
Expected: FAIL (`ModuleNotFoundError: pipeline.news_health`).

- [ ] **Step 3: Viết `pipeline/news_health.py`**

```python
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
```

- [ ] **Step 4: Chạy test, xác nhận pass**

Run: `./run_tests.sh tests/test_news_health.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add pipeline/news_health.py tests/test_news_health.py
git commit -m "feat(news): per-source freshness tracking and once-per-stretch ops alerts"
```

---

### Task 6: Bộ thu RSS `pipeline/collectors/rss.py`

**Files:**
- Create: `pipeline/collectors/__init__.py` (rỗng), `pipeline/collectors/rss.py`, `tests/fixtures/rss/cafef_vi_mo.xml`, `tests/fixtures/rss/cafef_chung_khoan.xml`
- Modify: `requirements.txt` (thêm `feedparser==6.0.11`)
- Test: `tests/test_collect_rss.py`

**Interfaces:**
- Consumes: `load_sources`, `store_news_item`, `item_hash`, `DUPLICATE_WINDOW`, `classify`, `is_duplicate_title`, `load_keywords`, `load_aliases`, `record_ok`, `record_failure`, `check_and_alert`.
- Produces: `run_collect_rss(conn, *, vn30: list[str], send, client: httpx.Client | None = None, now: datetime | None = None) -> dict` (`{"ok": int, "failed": int, "stored": int}`; tự commit).

- [ ] **Step 1: Thêm dependency và tạo fixture**

Thêm vào `requirements.txt` (sau dòng `httpx==0.27.2`): `feedparser==6.0.11`.

`tests/fixtures/rss/cafef_vi_mo.xml`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>CafeF Vĩ mô</title><link>http://cafef.test</link><description>fixture</description>
<item><title>NHNN giảm lãi suất điều hành, thị trường liên ngân hàng hạ nhiệt</title><link>http://cafef.test/a1</link>
<description>&lt;p&gt;Lãi suất qua đêm trên thị trường giảm 0,3 điểm phần trăm.&lt;/p&gt;</description><pubDate>Tue, 06 Oct 2026 01:00:00 GMT</pubDate></item>
<item><title>Giá vàng nhẫn hôm nay 6/10: tăng nhẹ</title><link>http://cafef.test/a2</link><description>Giá vàng.</description><pubDate>Tue, 06 Oct 2026 01:10:00 GMT</pubDate></item>
<item><title>Cuối tuần nhiều nơi nắng đẹp</title><link>http://cafef.test/a3</link><description>Dự báo.</description><pubDate>Tue, 06 Oct 2026 01:20:00 GMT</pubDate></item>
<item><title>FPT ký hợp đồng mới với đối tác Nhật</title><link>http://cafef.test/a4</link><description>Hợp đồng.</description></item>
<item><title>Chính sách mới về thuế dự kiến có hiệu lực sớm</title><link>http://cafef.test/a5</link><description>Thuế.</description><pubDate>Fri, 25 Dec 2026 01:00:00 GMT</pubDate></item>
</channel></rss>
```

(`a4` không có `pubDate`; `a5` có `pubDate` ở tương lai.)

`tests/fixtures/rss/cafef_chung_khoan.xml`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>CafeF Chứng khoán</title><link>http://cafef.test</link><description>fixture</description>
<item><title>NHNN giảm lãi suất điều hành, thị trường liên ngân hàng hạ nhiệt</title><link>http://cafef.test/a1</link>
<description>Cùng một bài, nguồn khác.</description><pubDate>Tue, 06 Oct 2026 01:30:00 GMT</pubDate></item>
<item><title>FPT báo lãi quý III tăng 20%</title><link>http://cafef.test/b1</link><description>Kết quả kinh doanh.</description><pubDate>Tue, 06 Oct 2026 02:00:00 GMT</pubDate></item>
<item><title>NHNN giảm lãi suất điều hành, thị trường liên ngân hàng hạ nhiệt!</title><link>http://cafef.test/b2</link>
<description>Bài đăng lại, khác URL.</description><pubDate>Tue, 06 Oct 2026 02:10:00 GMT</pubDate></item>
</channel></rss>
```

- [ ] **Step 2: Viết test fail**

`tests/test_collect_rss.py`:

```python
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest

import pipeline.collectors.rss as rss
from pipeline.news import ensure_news_partitions

FIX = Path(__file__).parent / "fixtures" / "rss"
NOW = datetime(2026, 10, 6, 3, 0, tzinfo=timezone.utc)  # Tue 10:00 VN
URL_A, URL_B = "http://cafef.test/vi-mo.rss", "http://cafef.test/ck.rss"
HTML_BLOCK = b"<html><head><title>Request Rejected</title></head><body>The requested URL was rejected.</body></html>"


def feed(name):
    return httpx.Response(200, content=(FIX / name).read_bytes(), headers={"content-type": "application/rss+xml"})


def srcs(*specs):
    return [{"name": n, "stream": s, "url": u, "every_minutes": 60, "enabled": True} for n, s, u in specs]


def mock_client(routes):
    calls = []

    def handler(request):
        calls.append(str(request.url))
        r = routes[str(request.url)]
        if callable(r):
            return r(request)
        return httpx.Response(r.status_code, content=r.content, headers=dict(r.headers))  # fresh object per request
    return httpx.Client(transport=httpx.MockTransport(handler)), calls


@pytest.fixture(autouse=True)
def _isolated(db_conn, monkeypatch):
    monkeypatch.setattr(rss, "_cache", {})
    monkeypatch.setattr(rss, "_last_hit", {})
    monkeypatch.setattr(rss, "_sleep", lambda s: None)
    monkeypatch.setattr(rss, "_clock", lambda: 0.0)
    ensure_news_partitions(db_conn, date(2026, 10, 6))

    def wipe():
        db_conn.execute("DELETE FROM news_items WHERE source LIKE 'test_%'")
        db_conn.execute("DELETE FROM source_health WHERE source LIKE 'test_%'")
        db_conn.commit()
    wipe()
    yield
    wipe()


def run(db_conn, monkeypatch, sources, client, now=NOW, alerts=None):
    monkeypatch.setattr(rss, "load_sources", lambda: sources)
    sink = alerts if alerts is not None else []
    return rss.run_collect_rss(db_conn, vn30=["FPT"], send=lambda m: sink.append(m) or True, client=client, now=now)


def rows(db_conn, source):
    return {u: (s, r, p, t) for u, s, r, p, t in db_conn.execute(
        "SELECT url, filter_status, filter_reason, pillars, tickers FROM news_items WHERE source = %s", (source,))}


def test_collects_filters_and_records_health(db_conn, monkeypatch):
    c, _ = mock_client({URL_A: feed("cafef_vi_mo.xml")})
    assert run(db_conn, monkeypatch, srcs(("test_cafef", "A", URL_A)), c) == {"ok": 1, "failed": 0, "stored": 5}
    got = rows(db_conn, "test_cafef")
    assert got["http://cafef.test/a1"] == ("kept", None, ["tien_te"], [])
    assert got["http://cafef.test/a2"][:2] == ("dropped", "exclude:giá vàng nhẫn hôm nay")
    assert got["http://cafef.test/a3"][:2] == ("dropped", "no_keyword")
    assert got["http://cafef.test/a4"][0] == "kept" and got["http://cafef.test/a4"][3] == ["FPT"]
    failures, last_item = db_conn.execute(
        "SELECT consecutive_failures, last_item_at FROM source_health WHERE source = 'test_cafef'").fetchone()
    # a4 has no pubDate (fetch time) and a5 is dated in the future: neither may push last_item_at past `now`
    assert failures == 0 and last_item == NOW


def test_same_url_in_two_feeds_is_one_row_and_a_reposted_title_is_a_duplicate(db_conn, monkeypatch):
    c, _ = mock_client({URL_A: feed("cafef_vi_mo.xml"), URL_B: feed("cafef_chung_khoan.xml")})
    both = srcs(("test_cafef", "A", URL_A), ("test_ck", "B", URL_B))
    assert run(db_conn, monkeypatch, both, c)["stored"] == 7  # 5 + b1 + b2 (a1 merged, not stored)
    assert db_conn.execute("SELECT count(*) FROM news_items WHERE url = 'http://cafef.test/a1'").fetchone()[0] == 1
    b2 = rows(db_conn, "test_ck")["http://cafef.test/b2"]
    assert b2[0] == "dropped" and b2[1].startswith("duplicate_title:")
    assert rows(db_conn, "test_ck")["http://cafef.test/b1"][3] == ["FPT"]
    again = run(db_conn, monkeypatch, both, c, now=NOW + timedelta(minutes=61))
    assert again["stored"] == 0


def test_a_200_html_block_page_is_a_failure_not_an_empty_feed(db_conn, monkeypatch):
    c, _ = mock_client({URL_A: feed("cafef_vi_mo.xml"),
                        URL_B: httpx.Response(200, content=HTML_BLOCK, headers={"content-type": "text/html"})})
    result = run(db_conn, monkeypatch, srcs(("test_cafef", "A", URL_A), ("test_waf", "B", URL_B)), c)
    assert result["ok"] == 1 and result["failed"] == 1  # the other source still ran
    failures, error = db_conn.execute(
        "SELECT consecutive_failures, last_error FROM source_health WHERE source = 'test_waf'").fetchone()
    assert failures == 1 and "not a valid feed" in error


def test_429_is_not_retried_but_5xx_is_retried_twice_with_backoff(db_conn, monkeypatch):
    sleeps = []
    monkeypatch.setattr(rss, "_sleep", sleeps.append)
    c, calls = mock_client({URL_A: httpx.Response(429)})
    run(db_conn, monkeypatch, srcs(("test_429", "A", URL_A)), c)
    assert len(calls) == 1 and "429" in db_conn.execute(
        "SELECT last_error FROM source_health WHERE source = 'test_429'").fetchone()[0]
    c, calls = mock_client({URL_B: httpx.Response(503)})
    run(db_conn, monkeypatch, srcs(("test_503", "A", URL_B)), c)
    assert len(calls) == 3 and sleeps == [1, 2]


def test_requests_to_the_same_domain_are_spaced_two_seconds(db_conn, monkeypatch):
    sleeps = []
    monkeypatch.setattr(rss, "_sleep", sleeps.append)
    c, _ = mock_client({URL_A: feed("cafef_vi_mo.xml"), URL_B: feed("cafef_chung_khoan.xml")})
    run(db_conn, monkeypatch, srcs(("test_cafef", "A", URL_A), ("test_ck", "B", URL_B)), c)
    assert sleeps == [2.0]


def test_a_source_polled_recently_is_skipped(db_conn, monkeypatch):
    db_conn.execute("INSERT INTO source_health (source, last_ok_at) VALUES ('test_cafef', %s)",
                    (NOW - timedelta(minutes=10),))
    db_conn.commit()
    c, calls = mock_client({URL_A: feed("cafef_vi_mo.xml")})
    assert run(db_conn, monkeypatch, srcs(("test_cafef", "A", URL_A)), c) == {"ok": 0, "failed": 0, "stored": 0}
    assert calls == []


def test_conditional_get_headers_are_sent_after_the_first_fetch(db_conn, monkeypatch):
    seen = []

    def handler(request):
        seen.append(request.headers.get("if-none-match"))
        return httpx.Response(200, content=(FIX / "cafef_vi_mo.xml").read_bytes(), headers={"etag": '"v1"'})
    c = httpx.Client(transport=httpx.MockTransport(handler))
    run(db_conn, monkeypatch, srcs(("test_cafef", "A", URL_A)), c)
    run(db_conn, monkeypatch, srcs(("test_cafef", "A", URL_A)), c, now=NOW + timedelta(minutes=61))
    assert seen == [None, '"v1"']


def test_a_silent_source_alerts_once_across_runs(db_conn, monkeypatch):
    db_conn.execute("INSERT INTO source_health (source, last_item_at, first_seen_at) VALUES ('test_cafef', %s, %s)",
                    (NOW - timedelta(days=5), NOW - timedelta(days=5)))
    db_conn.commit()
    c, _ = mock_client({URL_A: httpx.Response(503)})
    alerts = []
    run(db_conn, monkeypatch, srcs(("test_cafef", "A", URL_A)), c, alerts=alerts)
    run(db_conn, monkeypatch, srcs(("test_cafef", "A", URL_A)), c, now=NOW + timedelta(minutes=61), alerts=alerts)
    assert len([a for a in alerts if "test_cafef" in a]) == 1
```

- [ ] **Step 3: Chạy test, xác nhận fail**

Run: `./run_tests.sh tests/test_collect_rss.py -q`
Expected: FAIL (`ModuleNotFoundError: pipeline.collectors`).

- [ ] **Step 4: Viết `pipeline/collectors/rss.py`**

Tạo file rỗng `pipeline/collectors/__init__.py`, rồi:

```python
"""RSS collector for streams A (macro) and B (company): fetch politely, filter, store everything.

Per-source isolation: one failing feed is recorded in source_health and never stops the others. A 200
response that is not a valid feed (e.g. a WAF block page) counts as a failure, not as "no news".
"""
from __future__ import annotations

import html
import re
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import feedparser
import httpx

from ops.alerting import log_event
from pipeline.news import item_hash, load_sources, store_news_item
from pipeline.news_filter import DUPLICATE_WINDOW, classify, is_duplicate_title, load_aliases, load_keywords
from pipeline.news_health import check_and_alert, record_failure, record_ok

USER_AGENT = "vn-market-mcp/1.0 (personal research tool)"
TIMEOUT_S = 15.0
MIN_GAP_S = 2.0          # between two requests to the same domain
RETRIES = 2              # network errors and 5xx only
DUE_SLACK = timedelta(minutes=5)  # an hourly job must still poll a source whose last poll was ~59.5 min ago

_cache: dict[str, dict] = {}      # url -> {"etag", "modified"}; lost on restart (one extra full fetch)
_last_hit: dict[str, float] = {}  # domain -> monotonic time of the last request
_sleep = time.sleep
_clock = time.monotonic


class FeedError(Exception):
    pass


def _get(client: httpx.Client, url: str) -> httpx.Response:
    domain = urlparse(url).netloc
    wait = MIN_GAP_S - (_clock() - _last_hit.get(domain, -1e9))
    if wait > 0:
        _sleep(wait)
    cached = _cache.get(url, {})
    headers = {"User-Agent": USER_AGENT}
    if cached.get("etag"):
        headers["If-None-Match"] = cached["etag"]
    if cached.get("modified"):
        headers["If-Modified-Since"] = cached["modified"]
    last: Exception | None = None
    for attempt in range(RETRIES + 1):
        _last_hit[domain] = _clock()
        try:
            resp = client.get(url, headers=headers, timeout=TIMEOUT_S, follow_redirects=True)
        except httpx.HTTPError as exc:
            last = exc
        else:
            if resp.status_code in (200, 304):
                return resp
            if resp.status_code < 500:  # 403/429/404: retrying only gets us blocked
                raise FeedError(f"HTTP {resp.status_code}")
            last = FeedError(f"HTTP {resp.status_code}")
        if attempt < RETRIES:
            _sleep(2 ** attempt)
    raise FeedError(str(last))


def parse_entries(content: bytes, fetched_at: datetime) -> list[dict]:
    feed = feedparser.parse(content)
    if not feed.entries and not feed.version:  # no feed at all, e.g. an HTML block page served with 200
        raise FeedError(f"not a valid feed: {feed.get('bozo_exception')!r}")
    items = []
    for e in feed.entries:
        title = html.unescape((e.get("title") or "").strip())
        if not title:
            continue
        stamp = e.get("published_parsed") or e.get("updated_parsed")
        summary = html.unescape(re.sub(r"<[^>]+>", " ", e.get("summary") or ""))
        items.append({
            "title": title,
            "url": e.get("link") or None,
            "summary": re.sub(r"\s+", " ", summary).strip() or None,
            "published_at": datetime(*stamp[:6], tzinfo=timezone.utc) if stamp else fetched_at,
        })
    return items


def _recent_kept(conn, published_at: datetime, exclude_hash: str) -> list[tuple[str, str]]:
    return conn.execute(
        "SELECT url_hash, title FROM news_items WHERE filter_status = 'kept'"
        " AND published_at BETWEEN %s AND %s AND url_hash <> %s",
        (published_at - DUPLICATE_WINDOW, published_at + DUPLICATE_WINDOW, exclude_hash),
    ).fetchall()


def collect_source(conn, client, source: dict, *, vn30: set[str], aliases, keywords, now: datetime):
    """Returns (stored, newest_published_at). Raises on any source-level failure."""
    resp = _get(client, source["url"])
    if resp.status_code == 304:
        return 0, None
    entries = parse_entries(resp.content, now)
    _cache[source["url"]] = {"etag": resp.headers.get("etag"), "modified": resp.headers.get("last-modified")}
    stored, newest = 0, None
    for it in entries:
        h = item_hash(it["url"], it["title"], it["published_at"])
        res = classify(it["title"], it["summary"], source["stream"], vn30, aliases, keywords)
        status, reason = res.status, res.reason
        if status == "kept":
            dup = is_duplicate_title(it["title"], _recent_kept(conn, it["published_at"], h))
            if dup:
                status, reason = "dropped", f"duplicate_title:{dup}"
        stored += store_news_item(
            conn, source=source["name"], url=it["url"], title=it["title"], summary=it["summary"],
            published_at=it["published_at"], fetched_at=now, tickers=res.tickers, pillars=res.pillars,
            stream=source["stream"], filter_status=status, filter_reason=reason,
        )
        seen = min(it["published_at"], now)  # a future pubDate must not hide a dead feed
        newest = seen if newest is None else max(newest, seen)
    return stored, newest


def _due(conn, source: dict, now: datetime) -> bool:
    row = conn.execute("SELECT last_ok_at FROM source_health WHERE source = %s", (source["name"],)).fetchone()
    return row is None or row[0] is None or now - row[0] >= timedelta(minutes=source["every_minutes"]) - DUE_SLACK


def run_collect_rss(conn, *, vn30: list[str], send, client: httpx.Client | None = None,
                    now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    keywords, aliases, vn30_set = load_keywords(), load_aliases(), set(vn30)
    all_sources = load_sources()
    own_client = client is None
    client = client or httpx.Client()
    summary = {"ok": 0, "failed": 0, "stored": 0}
    try:
        for source in all_sources:
            if not _due(conn, source, now):
                continue
            try:
                with conn.transaction():
                    stored, newest = collect_source(conn, client, source, vn30=vn30_set, aliases=aliases,
                                                    keywords=keywords, now=now)
                record_ok(conn, source["name"], newest, now)
                conn.commit()
                summary["ok"] += 1
                summary["stored"] += stored
            except Exception as exc:  # one bad source must not stop the others
                record_failure(conn, source["name"], f"{type(exc).__name__}: {exc}", now)
                conn.commit()
                summary["failed"] += 1
                log_event("collect_rss_source_failed", source=source["name"], error=str(exc))
    finally:
        if own_client:
            client.close()
    check_and_alert(conn, now, {s["name"] for s in all_sources}, send)
    conn.commit()
    return summary
```

- [ ] **Step 5: Chạy test, xác nhận pass**

Run: `./run_tests.sh tests/test_collect_rss.py tests/test_news_filter.py tests/test_news_store.py -q`
Expected: PASS. Nếu `test_collects_filters_and_records_health` báo `stored != 5`, kiểm tra fixture (5 `<item>`).

- [ ] **Step 6: Commit**

```bash
git add pipeline/collectors requirements.txt tests/test_collect_rss.py tests/fixtures/rss
git commit -m "feat(news): RSS collector with polite fetching, tier-1 filtering and per-source health"
```

---

### Task 7: Scheduler — enqueue `collect_rss` và tự tạo partition

**Files:**
- Modify: `ops/scheduler.py`, `mcp_server/connection.py`, `infrastructure/docker-compose.yml`
- Test: `tests/test_scheduler.py`

**Interfaces:**
- Consumes: `COLLECT_RSS_JOB_TYPE` (Task 1), `ensure_news_partitions` (Task 3), `MACRO_TICKER = "MARKET"` (đã có).
- Produces: `get_admin_conn()` (đọc `ADMIN_DATABASE_URL`); `_run_collect_if_due(conn, now, already_fired_today)`; `_ensure_partitions(today: date)`.

- [ ] **Step 1: Viết test fail và sửa cleanup của test cũ**

Test cũ dọn job bằng `LIKE '%:<ngày>'`; job `collect_rss` có hậu tố `:<giờ>` nên sẽ bị bỏ sót và nhiễm vào test worker. Trong `tests/test_scheduler.py`, ở hai test `test_run_due_jobs_enqueues_weekly_on_friday_vn30_only` và `test_run_due_jobs_enqueues_watchlist_once_per_day`, đổi tham số cleanup:

```python
db_conn.execute("DELETE FROM jobs WHERE job_key LIKE %s", (f"%:{friday.isoformat()}%",))
```
```python
db_conn.execute("DELETE FROM jobs WHERE job_key LIKE %s", (f"%:{today.isoformat()}%",))
```

Thêm test mới ở cuối file:

```python
def test_collect_rss_is_enqueued_hourly_from_0600_vn_and_not_twice(db_conn):
    from ops.scheduler import _run_collect_if_due

    fired: set[str] = set()
    try:
        _run_collect_if_due(db_conn, datetime(2020, 1, 6, 22, 0, tzinfo=timezone.utc), fired)  # 05:00 VN: too early
        assert get_job(db_conn, f"collect_rss:{MACRO_TICKER}:2020-01-07:05") is None
        _run_collect_if_due(db_conn, datetime(2020, 1, 7, 2, 0, tzinfo=timezone.utc), fired)   # 09:00 VN
        _run_collect_if_due(db_conn, datetime(2020, 1, 7, 2, 1, tzinfo=timezone.utc), fired)   # same hour again
        assert get_job(db_conn, f"collect_rss:{MACRO_TICKER}:2020-01-07:09")["status"] == "queued"
        assert db_conn.execute("SELECT count(*) FROM jobs WHERE job_type = 'collect_rss'"
                               " AND job_key LIKE 'collect_rss:%:2020-01-07:%'").fetchone()[0] == 1
        _run_collect_if_due(db_conn, datetime(2020, 1, 11, 16, 0, tzinfo=timezone.utc), fired)  # Sat 23:00 VN: weekends run too
        assert get_job(db_conn, f"collect_rss:{MACRO_TICKER}:2020-01-11:23")["status"] == "queued"
    finally:
        db_conn.execute("DELETE FROM jobs WHERE job_key LIKE 'collect_rss:%:2020-01-%'")
        db_conn.commit()


def test_partition_creation_failure_alerts_ops_but_never_stops_the_scheduler(monkeypatch):
    import ops.scheduler as scheduler
    from datetime import date

    alerts = []

    class _Boom:
        def __enter__(self):
            raise RuntimeError("permission denied")

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(scheduler, "get_admin_conn", lambda: _Boom())
    monkeypatch.setattr(scheduler, "send_ops_alert", alerts.append)
    scheduler._ensure_partitions(date(2026, 10, 6))  # must not raise
    assert len(alerts) == 1 and "partition" in alerts[0]
```

- [ ] **Step 2: Chạy test, xác nhận fail**

Run: `./run_tests.sh tests/test_scheduler.py -q`
Expected: 2 test mới FAIL (`ImportError: _run_collect_if_due` / thiếu `get_admin_conn`).

- [ ] **Step 3: Thêm `get_admin_conn`**

Thêm vào cuối `mcp_server/connection.py`:

```python
@contextmanager
def get_admin_conn():
    """Table owner. Used only for DDL the pipeline role cannot do (creating news_items partitions)."""
    conn = psycopg.connect(os.environ["ADMIN_DATABASE_URL"])
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
```

- [ ] **Step 4: Sửa `ops/scheduler.py`**

Sửa import:

```python
from datetime import date, datetime, timezone
...
from mcp_server.connection import get_admin_conn, get_rw_conn
from ops.alerting import log_event, send_ops_alert
from pipeline.calendar import NoCalendarDataError, is_trading_day
from pipeline.jobs import COLLECT_RSS_JOB_TYPE, enqueue
from pipeline.news import ensure_news_partitions
```

Thêm sau khối `CLOSE_SYNC_JOB_TYPES`/`_VN_TZ`:

```python
# RSS news collection: one market-wide job per hour from 06:00 VN, every day (news does not stop at the
# weekend). The job itself decides which sources are due (config/news_sources.yaml `every_minutes`).
COLLECT_FIRST_HOUR_VN = 6


def _run_collect_if_due(conn, now: datetime, already_fired_today: set[str]) -> None:
    vn = now.astimezone(_VN_TZ)
    if vn.hour < COLLECT_FIRST_HOUR_VN:
        return
    slot = f"{vn.hour:02d}"
    fired_key = f"{COLLECT_RSS_JOB_TYPE}:{vn.date().isoformat()}:{slot}"
    if fired_key in already_fired_today:
        return
    already_fired_today.add(fired_key)
    job_key, created = enqueue(
        conn, MACRO_TICKER, job_type=COLLECT_RSS_JOB_TYPE, requested_by="cron", schedule_date=vn.date(), slot=slot,
    )
    if created:
        log_event("scheduler_enqueued", job_key=job_key, ticker=MACRO_TICKER, job_type=COLLECT_RSS_JOB_TYPE)


def _ensure_partitions(today: date) -> None:
    """Keep news_items partitions 3 months ahead; a failure alerts ops but never stops the scheduler
    (it retries daily, so there are ~3 months to react before inserts would start failing)."""
    try:
        with get_admin_conn() as conn:
            created = ensure_news_partitions(conn, today)
        if created:
            log_event("news_partitions_created", names=created)
    except Exception as exc:
        log_event("news_partitions_failed", error=str(exc))
        send_ops_alert(f"[vn-market-mcp] không tạo được partition news_items: {type(exc).__name__}: {exc}")
```

Trong `run_due_jobs`, thêm một dòng cuối hàm (sau hai lời gọi `_run_market_job_if_due`):

```python
    _run_collect_if_due(conn, now, already_fired_today)
```

Trong `main()`, thêm một dòng vào khối đổi ngày:

```python
        if last_date != now.date():
            already_fired_today.clear()
            last_date = now.date()
            _ensure_partitions(now.astimezone(_VN_TZ).date())  # also runs at start-up (last_date is None)
```

- [ ] **Step 5: Cấp biến môi trường cho scheduler trong compose**

Trong `infrastructure/docker-compose.yml`, service `scheduler`, sửa khối `environment` thành:

```yaml
    command: ["python", "-m", "ops.scheduler"]
    environment:
      # Table-owner connection, used only to CREATE news_items partitions (pipeline_rw cannot do DDL).
      ADMIN_DATABASE_URL: postgresql://${POSTGRES_ADMIN_USER:-vnmcp_admin}:${POSTGRES_ADMIN_PASSWORD:-changeme_local_only}@${POSTGRES_HOST:-postgres}:${POSTGRES_PORT:-5433}/${POSTGRES_DB:-vnmcp}
      TELEGRAM_BOT_TOKEN: ${TELEGRAM_BOT_TOKEN:-}
      TELEGRAM_ALERT_CHAT_ID: ${TELEGRAM_ALERT_CHAT_ID:-}
      PIPELINE_RW_DATABASE_URL: postgresql://pipeline_rw:${PIPELINE_RW_PASSWORD:?set PIPELINE_RW_PASSWORD in infrastructure/.env}@${POSTGRES_HOST:-postgres}:${POSTGRES_PORT:-5433}/${POSTGRES_DB:-vnmcp}
```

(Hai biến Telegram để `send_ops_alert` của scheduler thực sự gửi được; trước đây scheduler không có token.)

- [ ] **Step 6: Chạy test, xác nhận pass**

Run: `./run_tests.sh tests/test_scheduler.py tests/test_worker.py -q`
Expected: PASS. Nếu có docker: `docker compose -f infrastructure/docker-compose.yml config -q` (không lỗi cú pháp).

- [ ] **Step 7: Commit**

```bash
git add ops/scheduler.py mcp_server/connection.py infrastructure/docker-compose.yml tests/test_scheduler.py
git commit -m "feat(ops): hourly collect_rss enqueue and self-extending news partitions"
```

---

### Task 8: Worker chạy `collect_rss`; `ingest_news` ghi lỗi vào `source_health`

**Files:**
- Modify: `ops/worker.py`, `pipeline/ingest.py`
- Test: `tests/test_worker.py`, `tests/test_ingest.py`

**Interfaces:**
- Consumes: `run_collect_rss(conn, *, vn30, send, client=None, now=None)` (Task 6), `record_ok/record_failure/VNSTOCK_NEWS_SOURCE` (Task 5), `COLLECT_JOB_TYPES` (Task 1).

- [ ] **Step 1: Viết test fail**

Thêm vào `tests/test_worker.py` (cạnh test macro; thêm import `from pipeline.jobs import COLLECT_RSS_JOB_TYPE`):

```python
def test_run_one_runs_collect_rss_without_run_analysis(db_conn):
    db_conn.execute("DELETE FROM jobs WHERE ticker = %s", (MACRO_TICKER,))
    db_conn.commit()
    job_key, _ = enqueue(db_conn, MACRO_TICKER, job_type=COLLECT_RSS_JOB_TYPE)
    db_conn.commit()
    try:
        with patch("ops.worker.get_vn30_tickers", return_value=["FPT"]), \
             patch("ops.worker.run_collect_rss", return_value={"ok": 1, "failed": 0, "stored": 2}) as collect, \
             patch("ops.worker.run_analysis") as analysis:
            assert run_one(db_conn) is True
        collect.assert_called_once()
        assert collect.call_args.kwargs["vn30"] == ["FPT"]
        analysis.assert_not_called()
        assert get_job(db_conn, job_key)["status"] == "done"
    finally:
        db_conn.execute("DELETE FROM jobs WHERE ticker = %s", (MACRO_TICKER,))
        db_conn.commit()
```

Thêm vào `tests/test_ingest.py`:

```python
def test_ingest_news_failures_are_recorded_in_source_health_not_swallowed(db_conn, monkeypatch):
    import pipeline.ingest as ingest
    from providers.vnstock_provider import NewsItem

    monkeypatch.setattr(ingest, "_news_pause_until", 0.0)

    class _Down:
        def get_news(self, ticker, start, end):
            raise TimeoutError("iq.vietcap.com.vn read timed out")

    assert ingest.ingest_news(db_conn, _Down(), "ACB", date(2026, 10, 1), date(2026, 10, 2)) == 0
    failures, error = db_conn.execute(
        "SELECT consecutive_failures, last_error FROM source_health WHERE source = 'vnstock_news'").fetchone()
    assert failures == 1 and "timed out" in error

    monkeypatch.setattr(ingest, "_news_pause_until", 0.0)
    published = datetime(2031, 1, 1, tzinfo=timezone.utc)  # no partition: the insert fails
    far = NewsItem(ticker="ACB", published_at=published, source="v", url="http://x/h1", title="no partition",
                   summary=None, fetched_at=published)

    class _Far:
        def get_news(self, ticker, start, end):
            return [far]

    assert ingest.ingest_news(db_conn, _Far(), "ACB", date(2030, 12, 30), date(2031, 1, 2)) == 0
    failures, error = db_conn.execute(
        "SELECT consecutive_failures, last_error FROM source_health WHERE source = 'vnstock_news'").fetchone()
    assert failures == 2 and "insert failed" in error

    monkeypatch.setattr(ingest, "_news_pause_until", 0.0)

    class _Empty:
        def get_news(self, ticker, start, end):
            return []

    ingest.ingest_news(db_conn, _Empty(), "ACB", date(2026, 10, 1), date(2026, 10, 2))
    assert db_conn.execute(
        "SELECT consecutive_failures FROM source_health WHERE source = 'vnstock_news'").fetchone()[0] == 0
```

- [ ] **Step 2: Chạy test, xác nhận fail**

Run: `./run_tests.sh tests/test_worker.py tests/test_ingest.py -q`
Expected: 2 test mới FAIL.

- [ ] **Step 3: Sửa `ops/worker.py`**

Sửa import:

```python
from pipeline.collectors.rss import run_collect_rss
from pipeline.jobs import COLLECT_JOB_TYPES, claim_next, mark_done, mark_failed, reclaim_stale_running, release, requeue
```

Thêm hàm sau `_run_close_sync`:

```python
def _run_collect_rss(conn) -> None:
    summary = run_collect_rss(conn, vn30=get_vn30_tickers(conn), send=send_ops_alert)
    log_event("collect_rss_done", **summary)
```

Trong `run_one`, thay khối market-wide:

```python
        if job.job_type == MACRO_JOB_TYPE or job.job_type in CLOSE_SYNC_JOB_TYPES or job.job_type in COLLECT_JOB_TYPES:
            if job.job_type == MACRO_JOB_TYPE:
                _run_macro_premarket(conn)
            elif job.job_type in COLLECT_JOB_TYPES:
                _run_collect_rss(conn)
            else:
                _run_close_sync(conn)
```

- [ ] **Step 4: Sửa `ingest_news` trong `pipeline/ingest.py`**

Đổi import `from datetime import date, timedelta` thành `from datetime import date, datetime, timedelta, timezone`, và thêm:

```python
from pipeline.news_health import VNSTOCK_NEWS_SOURCE, record_failure, record_ok
```

Thay thân hàm `ingest_news` (giữ nguyên chữ ký, docstring và `_news_pause_until`):

```python
def ingest_news(conn: psycopg.Connection, provider, ticker: str, start: date, end: date) -> int:
    """Store raw headlines (no LLM) so the chat model can read them. Best effort: a flaky news
    API, or an item whose month has no partition yet, never fails the analysis run — but it is
    recorded in source_health (source 'vnstock_news'), so a dead feed shows up in ops alerts
    instead of vanishing."""
    import hashlib

    global _news_pause_until
    if time.monotonic() < _news_pause_until:
        return 0
    now = datetime.now(timezone.utc)
    try:
        items = provider.get_news(ticker, start, end)
    except Exception as exc:
        # iq.vietcap.com.vn times out in 30 s x retries when saturated; stop hammering it for a while
        # instead of paying that per ticker for the rest of the batch.
        _news_pause_until = time.monotonic() + NEWS_PAUSE_AFTER_FAILURE_S
        record_failure(conn, VNSTOCK_NEWS_SOURCE, f"{type(exc).__name__}: {exc}", now)
        return 0
    stored, db_error = 0, None
    for it in items:
        key = it.url or f"{it.title}|{it.published_at.date()}"
        try:
            with conn.transaction():  # savepoint: one bad row must not poison the run's transaction
                row = conn.execute(
                    """
                    INSERT INTO news_items (published_at, tickers, source, url, url_hash, title, summary, fetched_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (url_hash, published_at) DO UPDATE
                      SET tickers = (SELECT ARRAY(SELECT DISTINCT unnest(news_items.tickers || EXCLUDED.tickers)))
                    RETURNING 1
                    """,
                    (it.published_at, [ticker], it.source, it.url, hashlib.sha256(key.encode()).hexdigest(),
                     it.title, it.summary, it.fetched_at),
                ).fetchone()
            stored += 1 if row else 0
        except psycopg.Error as exc:
            db_error = exc
    if db_error is not None:
        record_failure(conn, VNSTOCK_NEWS_SOURCE, f"insert failed: {db_error}", now)
    else:
        record_ok(conn, VNSTOCK_NEWS_SOURCE, max((i.published_at for i in items), default=None), now)
    return stored
```

(Phần `record_*` nằm trong transaction của lượt phân tích; nếu lượt đó rollback thì bản ghi health cũng mất — chấp nhận, lần gọi sau ghi lại.)

- [ ] **Step 5: Chạy test, xác nhận pass**

Run: `./run_tests.sh tests/test_worker.py tests/test_ingest.py tests/test_run_analysis.py -q`
Expected: PASS (các test `ingest_news` cũ vẫn xanh).

- [ ] **Step 6: Commit**

```bash
git add ops/worker.py pipeline/ingest.py tests/test_worker.py tests/test_ingest.py
git commit -m "feat(ops): worker runs collect_rss; vnstock news failures recorded in source_health"
```

---

### Task 9: Tool MCP `get_macro_context`, lọc `dropped`, khối `news_sources`

**Files:**
- Create: `mcp_server/tools/macro.py`
- Modify: `mcp_server/server.py`, `mcp_server/tools/digests.py`, `mcp_server/tools/stock_report.py`
- Test: `tests/test_mcp_macro.py` (mới), `tests/test_mcp_digests.py`, `tests/test_mcp_server_integration.py`, `tests/test_mcp_stock_report.py`

**Interfaces:**
- Consumes: `load_sources`, `load_keywords`, `news_sources_status`, `source_warnings`.
- Produces: `get_macro_context_tool(days: int = 7) -> dict` (envelope; `data = {"pillars": {<7 trụ cột>: [{"date","title","source","url"}]}, "news_sources": [...]}`); `stock_report._news_rows(conn, ticker) -> list[tuple]` (tách từ `_load`, loại `dropped`).

- [ ] **Step 1: Viết test fail**

`tests/test_mcp_macro.py`:

```python
from datetime import date, datetime, timedelta, timezone

import pytest

import mcp_server.tools.macro as macro
from db.connection import get_conn
from pipeline.news import ensure_news_partitions, store_news_item


@pytest.fixture
def seeded(monkeypatch):
    monkeypatch.setattr(macro, "load_sources", lambda: [{"name": "test_fresh"}, {"name": "test_never"}])
    now = datetime.now(timezone.utc)
    with get_conn() as conn:
        ensure_news_partitions(conn, date.today())
        conn.execute("DELETE FROM news_items WHERE source LIKE 'test_%'")
        conn.execute("DELETE FROM source_health WHERE source LIKE 'test_%'")
        put = lambda url, title, status, pillars: store_news_item(
            conn, source="test_fresh", url=url, title=title, summary=None, published_at=now - timedelta(hours=1),
            fetched_at=now, tickers=[], pillars=pillars, stream="A", filter_status=status, filter_reason=None)
        put("http://t/m1", "NHNN giảm lãi suất điều hành", "kept", ["tien_te"])
        put("http://t/m2", "Tin bị loại", "dropped", ["tien_te"])
        conn.execute("INSERT INTO source_health (source, last_item_at, first_seen_at, last_ok_at) VALUES"
                     " ('test_fresh', %s, %s, %s)", (now - timedelta(hours=1),) * 3)
    yield
    with get_conn() as conn:
        conn.execute("DELETE FROM news_items WHERE source LIKE 'test_%'")
        conn.execute("DELETE FROM source_health WHERE source LIKE 'test_%'")


def test_macro_context_returns_kept_headlines_by_pillar_and_source_freshness(seeded):
    out = macro.get_macro_context_tool(days=7)
    pillars = out["data"]["pillars"]
    assert set(pillars) == {"tien_te", "ty_gia_doi_ngoai", "tang_truong", "lam_phat",
                            "tai_khoa_chinh_sach", "thi_truong_von", "toan_cau"}
    assert [h["title"] for h in pillars["tien_te"]] == ["NHNN giảm lãi suất điều hành"]  # dropped item excluded
    assert pillars["lam_phat"] == []  # empty pillar stays visible: "chưa ghi nhận tin", not "không có sự kiện"
    status = {s["source"]: s for s in out["data"]["news_sources"]}
    assert status["test_fresh"]["stale"] is False and status["test_never"]["stale"] is True
    assert any("test_never" in w for w in out["warnings"])


def test_days_is_clamped(seeded):
    assert macro.get_macro_context_tool(days=0)["data"]["pillars"]["tien_te"]
    assert macro.get_macro_context_tool(days=9999)["data"]["pillars"]["tien_te"]
```

Thêm vào `tests/test_mcp_digests.py`:

```python
def test_market_digest_has_macro_headlines_and_source_freshness_and_hides_dropped_news(at, calendar, monkeypatch):
    from datetime import date, timedelta
    from pipeline.news import ensure_news_partitions, store_news_item

    monkeypatch.setattr(digests, "load_sources", lambda: [{"name": "test_dig"}])
    at(2026, 10, 5)
    now = datetime.now(timezone.utc)
    with get_conn() as conn:
        ensure_news_partitions(conn, date.today())
        conn.execute("DELETE FROM news_items WHERE source = 'test_dig'")
        conn.execute("INSERT INTO tickers (ticker, name, exchange, sector, industry_group)"
                     " VALUES ('TSTDIG', 'x', 'HOSE', 't', 'other') ON CONFLICT DO NOTHING")
        conn.execute("DELETE FROM index_membership WHERE ticker = 'TSTDIG'")
        conn.execute("INSERT INTO index_membership (index_code, ticker, valid_from) VALUES ('VN30', 'TSTDIG', '2026-01-01')")
        for url, title, status in (("http://t/d1", "Tin giữ lại", "kept"), ("http://t/d2", "Tin bị loại", "dropped")):
            store_news_item(conn, source="test_dig", url=url, title=title, summary=None,
                            published_at=now - timedelta(hours=1), fetched_at=now, tickers=["TSTDIG"],
                            pillars=["tien_te"], stream="A", filter_status=status, filter_reason=None)
    try:
        data = get_market_digest_input_tool()["data"]
        titles = [h["title"] for h in data["overnight_headlines"]]
        assert "Tin giữ lại" in titles and "Tin bị loại" not in titles
        assert [h["title"] for h in data["macro_headlines"]] == ["Tin giữ lại"]
        assert data["news_sources"][0]["source"] == "test_dig"
    finally:
        with get_conn() as conn:
            conn.execute("DELETE FROM news_items WHERE source = 'test_dig'")
            conn.execute("DELETE FROM index_membership WHERE ticker = 'TSTDIG'")
            conn.execute("DELETE FROM tickers WHERE ticker = 'TSTDIG'")
```

Trong `tests/test_mcp_digests.py`, sửa tập khóa kỳ vọng của test đầu tiên:

```python
    assert {"market", "vn30_last_session", "foreign_flow", "overnight_headlines",
            "macro_headlines", "news_sources"} <= market.keys()
```

Trong `tests/test_mcp_server_integration.py`: đổi tên test thành `test_server_lists_all_seventeen_tools` và thêm `"get_macro_context"` vào tập tên.

Thêm vào `tests/test_mcp_stock_report.py`:

```python
def test_report_news_rows_exclude_dropped_items_but_keep_untagged_vnstock_news():
    from datetime import date, timedelta
    from mcp_server.connection import get_ro_conn
    from mcp_server.tools.stock_report import _news_rows
    from pipeline.news import ensure_news_partitions, store_news_item
    from db.connection import get_conn

    now = datetime.now(timezone.utc)
    with get_conn() as conn:
        ensure_news_partitions(conn, date.today())
        conn.execute("INSERT INTO tickers (ticker, name, exchange, sector, industry_group)"
                     " VALUES ('TSTNEWS', 'x', 'HOSE', 't', 'other') ON CONFLICT DO NOTHING")
        conn.execute("DELETE FROM news_items WHERE source LIKE 'test_%'")
        for url, status in (("http://t/n1", "kept"), ("http://t/n2", "dropped"), ("http://t/n3", None)):
            conn.execute(
                "INSERT INTO news_items (published_at, tickers, source, url, url_hash, title, fetched_at, filter_status)"
                " VALUES (%s, ARRAY['TSTNEWS'], 'test_n', %s, %s, %s, %s, %s)",
                (now - timedelta(hours=1), url, url, f"title {url}", now, status))
    try:
        with get_ro_conn() as conn:
            titles = {r[1] for r in _news_rows(conn, "TSTNEWS")}
        assert titles == {"title http://t/n1", "title http://t/n3"}
    finally:
        with get_conn() as conn:
            conn.execute("DELETE FROM news_items WHERE source LIKE 'test_%'")
            conn.execute("DELETE FROM tickers WHERE ticker = 'TSTNEWS'")
```

(Nếu `tests/test_mcp_stock_report.py` chưa import `datetime, timezone`, thêm `from datetime import datetime, timezone` ở đầu file.)

- [ ] **Step 2: Chạy test, xác nhận fail**

Run: `./run_tests.sh tests/test_mcp_macro.py tests/test_mcp_digests.py tests/test_mcp_server_integration.py tests/test_mcp_stock_report.py -q`
Expected: FAIL (thiếu module `macro`, thiếu `_news_rows`, thiếu khóa).

- [ ] **Step 3: Viết `mcp_server/tools/macro.py`**

```python
"""get_macro_context: kept macro headlines per pillar + how fresh each news source is. Read-only, no LLM."""
from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from mcp_server.connection import get_ro_conn
from mcp_server.envelope import build_envelope
from pipeline.news import load_sources
from pipeline.news_filter import load_keywords
from pipeline.news_health import news_sources_status, source_warnings

_VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")
PER_PILLAR = 10


def get_macro_context_tool(days: int = 7) -> dict:
    days = max(1, min(30, days))
    now = datetime.now(timezone.utc)
    pillars = [p for p in load_keywords() if p != "exclude"]
    with get_ro_conn() as conn:
        by_pillar = {}
        for p in pillars:
            rows = conn.execute(
                "SELECT published_at, title, source, url FROM news_items"
                " WHERE filter_status = 'kept' AND %s = ANY(pillars)"
                " AND published_at >= now() - make_interval(days => %s) ORDER BY published_at DESC LIMIT %s",
                (p, days, PER_PILLAR),
            ).fetchall()
            by_pillar[p] = [{"date": at.astimezone(_VN_TZ).strftime("%d/%m %H:%M"), "title": title,
                             "source": source, "url": url} for at, title, source, url in rows]
        status = news_sources_status(conn, now, [s["name"] for s in load_sources()])
    return build_envelope({"pillars": by_pillar, "news_sources": status}, sources=["postgres"], as_of=now,
                          warnings=source_warnings(status))
```

- [ ] **Step 4: Đăng ký tool trong `mcp_server/server.py`**

Thêm import `from mcp_server.tools.macro import get_macro_context_tool` (theo thứ tự chữ cái, sau `history`), và tool trước khối `if __name__`:

```python
@mcp.tool()
def get_macro_context(days: int = 7) -> dict[str, Any]:
    """Tin vĩ mô đã lọc theo 7 trụ cột (tiền tệ, tỷ giá, tăng trưởng, lạm phát, tài khóa, thị trường vốn, toàn cầu)
    trong N ngày (1-30), kèm độ mới của từng nguồn tin. Nguồn quá hạn có cảnh báo: không có tin ≠ không có sự kiện."""
    return get_macro_context_tool(days)
```

- [ ] **Step 5: Sửa `mcp_server/tools/digests.py`**

Thêm import:

```python
from pipeline.news import load_sources
from pipeline.news_health import news_sources_status, source_warnings
```

Trong `_headlines`, thêm điều kiện vào SQL: `... AND filter_status IS DISTINCT FROM 'dropped' AND tickers && ARRAY(...)`. Cụ thể thay câu truy vấn thành:

```python
    rows = conn.execute(
        f"SELECT published_at, tickers, title, source FROM news_items WHERE published_at >= now() - make_interval(hours => %s)"
        f" AND filter_status IS DISTINCT FROM 'dropped' AND tickers && ARRAY({_VN30}) ORDER BY published_at DESC LIMIT %s",
        (hours, limit),
    ).fetchall()
```

Thêm hàm sau `_headlines`:

```python
def _macro_headlines(conn, hours: int, limit: int) -> list[dict]:
    rows = conn.execute(
        "SELECT published_at, title, source FROM news_items WHERE filter_status = 'kept' AND stream = 'A'"
        " AND published_at >= now() - make_interval(hours => %s) ORDER BY published_at DESC LIMIT %s",
        (hours, limit),
    ).fetchall()
    return [{"date": p.astimezone(_VN_TZ).strftime("%d/%m %H:%M"), "title": t, "source": s} for p, t, s in rows]
```

Trong `get_market_digest_input_tool`, thay đoạn từ `moves = _moves(conn, 1)` đến `return build_envelope(...)` (cuối hàm) bằng:

```python
        moves = _moves(conn, 1)
        status = news_sources_status(conn, now, [s["name"] for s in load_sources()])
        data = {
            "is_trading_day": True, "market": _market(conn), "vn30_last_session": {"gainers": moves[:3], "losers": moves[::-1][:3],
                                                          "advancing": sum(m["change_pct"] > 0 for m in moves),
                                                          "declining": sum(m["change_pct"] < 0 for m in moves)},
            "foreign_flow": _flow(conn, 1), "overnight_headlines": _headlines(conn, 18, 30),
            "macro_headlines": _macro_headlines(conn, 18, 15), "news_sources": status,
        }
    return build_envelope(data, sources=["postgres"], as_of=now, warnings=source_warnings(status))
```

(Chỉ thêm `status`, hai khóa `macro_headlines`/`news_sources` và đối số `warnings`; phần còn lại giữ nguyên như hiện có.)

- [ ] **Step 6: Sửa `mcp_server/tools/stock_report.py`**

Thêm hàm trước `_load`:

```python
def _news_rows(conn, ticker: str) -> list[tuple]:
    """Last 7 days of headlines for the ticker, minus those the tier-1 filter dropped (vnstock news has no verdict: kept)."""
    return conn.execute(
        "SELECT published_at, title, source FROM news_items WHERE %s = ANY(tickers)"
        " AND filter_status IS DISTINCT FROM 'dropped'"
        " AND published_at >= now() - interval '7 days' ORDER BY published_at DESC LIMIT 5", (ticker,),
    ).fetchall()
```

Thay đoạn `news = conn.execute(...).fetchall()` trong `_load` bằng `news = _news_rows(conn, ticker)`.

- [ ] **Step 7: Chạy test, xác nhận pass**

Run: `./run_tests.sh tests/test_mcp_macro.py tests/test_mcp_digests.py tests/test_mcp_server_integration.py tests/test_mcp_stock_report.py tests/test_stock_report.py -q`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add mcp_server tests/test_mcp_macro.py tests/test_mcp_digests.py tests/test_mcp_server_integration.py tests/test_mcp_stock_report.py
git commit -m "feat(mcp): get_macro_context, dropped news hidden from readers, per-source freshness warnings"
```

---

### Task 10: Skill Hermes và README

**Files:**
- Modify: `.hermes/skills/vn-market/vn-stock-analyze/SKILL.md`, `README.md`

- [ ] **Step 1: Sửa SKILL.md**

Thêm một dòng vào bảng định tuyến, ngay sau dòng `| "thị trường hôm nay", "bản tin sáng", "VN-Index ra sao" | ... |`:

```
| "tin vĩ mô", "lãi suất", "tỷ giá", "chính sách tiền tệ", "tin kinh tế tuần này" | `get_macro_context(days=7)` |
```

Thêm vào cuối file (sau bullet "Sửa dữ liệu sai ..."), hai bullet:

```
- **Tin vĩ mô** (`get_macro_context`, và khối `macro_headlines` của bản tin sáng): chỉ là tiêu đề đã lọc theo trụ cột, chưa có nội dung bài và chưa được tính vào điểm. Dùng để nêu bối cảnh, không để đổi nhãn.
- **Độ mới của nguồn tin**: nếu `warnings` hoặc `news_sources[].stale` cho biết một nguồn quá hạn, nói rõ "tin từ <nguồn> mới cập nhật đến HH:MM dd/mm". Trụ cột không có tin thì nói "chưa ghi nhận tin" — không bao giờ nói "không có sự kiện" hay "không có tin xấu", vì có thể là nguồn chưa về hoặc tin bị sót.
```

- [ ] **Step 2: Sửa README.md**

Áp dụng sáu sửa đổi sau (mỗi cái là một `Edit`, chuỗi cũ phải khớp nguyên văn):

1. `| \`mcp_server/\` | MCP server và 16 tool;` → `... MCP server và 17 tool;`
2. `cung cấp 16 tool.` → `cung cấp 17 tool.`
3. Sau dòng `| \`get_market_digest_input\`, \`get_weekly_digest_input\` | ... |` thêm dòng: `| \`get_macro_context\` | Tin vĩ mô đã lọc theo 7 trụ cột trong N ngày, kèm độ mới của từng nguồn tin (nguồn quá hạn có cảnh báo) |`
4. Sau dòng `| 18:00 | \`close_sync_retry\` | ... |` thêm: `| Mỗi giờ 06:00–23:00 (cả cuối tuần) | \`collect_rss\` | Thu tin RSS vĩ mô và doanh nghiệp, lọc tầng 1 bằng quy tắc, cập nhật độ mới nguồn, cảnh báo nhóm ops khi nguồn im lặng hoặc lỗi |`
5. Sau dòng `| \`pipeline/ingest.py\` | ... |` thêm: `| \`pipeline/news.py\`, \`news_filter.py\`, \`news_health.py\`, \`collectors/rss.py\` | Thu thập tin RSS: nguồn và partition, lọc tầng 1 (không xóa tin, có \`--refilter\`), độ mới từng nguồn, bộ thu RSS. Cấu hình: \`config/news_sources.yaml\`, \`macro_keywords.yaml\`, \`ticker_aliases.yaml\` |`; và đổi `Migrations (\`001\`–\`017\`)` thành `Migrations (\`001\`–\`018\`)`.
6. Thêm một dòng cuối bảng nhật ký thay đổi (mục 16): `| Thu thập tin RSS vĩ mô và doanh nghiệp, lọc bằng quy tắc, theo dõi độ mới nguồn; tool \`get_macro_context\`; sửa khóa job không ổn định giữa worker, partition \`news_items\` tự gia hạn, lỗi tin vnstock không còn bị nuốt | \`018\` | Thêm \`feedparser\` (cần build lại image) và \`ADMIN_DATABASE_URL\` cho scheduler; sau migration chạy lại \`python -m db.setup_roles\`; restart Hermes gateway để nạp tool mới |`

- [ ] **Step 3: Kiểm tra không còn chỗ nói sai**

Run: `grep -n "16 tool\|001\`–\`017" README.md`
Expected: không có dòng nào.

- [ ] **Step 4: Commit**

```bash
git add .hermes/skills README.md
git commit -m "docs: route macro questions to get_macro_context; document news collection"
```

---

### Task 11: Công cụ đo recall của lọc tầng 1 (gate)

**Files:**
- Create: `evals/news_filter_recall.py`
- Test: `tests/test_news_filter_recall.py`

**Interfaces:**
- Consumes: `classify`, `load_keywords`, `load_aliases`, `get_vn30`.
- Produces: `score(rows: list[dict], vn30: set[str], aliases, keywords) -> {"recall","precision","labelled","missed"}`; CLI `--export N` (in JSONL, `relevant: null`) và `--score FILE` (thoát mã 1 nếu recall < 0,95).

- [ ] **Step 1: Viết test fail**

`tests/test_news_filter_recall.py`:

```python
from evals.news_filter_recall import score
from pipeline.news_filter import load_keywords


def test_score_reports_recall_precision_and_lists_misses():
    rows = [
        {"title": "Tỷ giá USD/VND tăng", "summary": "", "stream": "A", "relevant": True},      # kept: hit
        {"title": "Thời tiết đẹp", "summary": "", "stream": "A", "relevant": True},             # dropped: a miss
        {"title": "Khuyến mãi lớn", "summary": "", "stream": "A", "relevant": False},           # dropped: correct
        {"title": "Chưa gán nhãn", "summary": "", "stream": "A", "relevant": None},             # ignored
    ]
    r = score(rows, set(), {}, load_keywords())
    assert r["labelled"] == 3 and r["recall"] == 0.5 and r["precision"] == 1.0
    assert [m["title"] for m in r["missed"]] == ["Thời tiết đẹp"]


def test_score_refuses_to_report_without_any_relevant_label():
    import pytest
    with pytest.raises(ValueError):
        score([{"title": "x", "summary": "", "stream": "A", "relevant": False}], set(), {}, load_keywords())
```

- [ ] **Step 2: Chạy test, xác nhận fail**

Run: `./run_tests.sh tests/test_news_filter_recall.py -q`
Expected: FAIL (`ModuleNotFoundError: evals.news_filter_recall`).

- [ ] **Step 3: Viết `evals/news_filter_recall.py`**

```python
"""Recall of the tier-1 news filter against hand labels (phase 1 gate: recall >= 95%).

  python -m evals.news_filter_recall --export 200 > evals/news_filter_labels.jsonl
      → then set "relevant": true/false on every line by hand ("relates to macro or a VN30 ticker?")
  python -m evals.news_filter_recall --score evals/news_filter_labels.jsonl
"""
from __future__ import annotations

import argparse
import json
import sys

from pipeline.news_filter import classify, get_vn30, load_aliases, load_keywords

GATE_RECALL = 0.95


def score(rows: list[dict], vn30: set[str], aliases, keywords) -> dict:
    labelled = [r for r in rows if r.get("relevant") is not None]
    tp = fp = fn = 0
    missed = []
    for r in labelled:
        kept = classify(r["title"], r.get("summary"), r.get("stream", "A"), vn30, aliases, keywords).status == "kept"
        if r["relevant"] and kept:
            tp += 1
        elif r["relevant"]:
            fn += 1
            missed.append(r)
        elif kept:
            fp += 1
    if tp + fn == 0:
        raise ValueError("no row labelled relevant=true: recall is undefined")
    return {"labelled": len(labelled), "recall": tp / (tp + fn),
            "precision": tp / (tp + fp) if tp + fp else 1.0, "missed": missed}


def main() -> int:
    from mcp_server.connection import get_rw_conn

    ap = argparse.ArgumentParser(description=__doc__)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--export", type=int, metavar="N")
    g.add_argument("--score", metavar="FILE")
    args = ap.parse_args()
    with get_rw_conn() as conn:
        if args.export:
            for title, summary, stream in conn.execute(
                    "SELECT title, summary, stream FROM news_items WHERE stream IS NOT NULL"
                    " ORDER BY published_at DESC LIMIT %s", (args.export,)):
                print(json.dumps({"title": title, "summary": summary, "stream": stream, "relevant": None},
                                 ensure_ascii=False))
            return 0
        vn30 = get_vn30(conn)
    rows = [json.loads(line) for line in open(args.score, encoding="utf-8") if line.strip()]
    r = score(rows, vn30, load_aliases(), load_keywords())
    print(f"labelled={r['labelled']} recall={r['recall']:.1%} precision={r['precision']:.1%} (gate: recall >= {GATE_RECALL:.0%})")
    for m in r["missed"]:
        print("MISSED:", m["title"])
    return 0 if r["recall"] >= GATE_RECALL else 1


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Chạy test, xác nhận pass**

Run: `./run_tests.sh tests/test_news_filter_recall.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add evals/news_filter_recall.py tests/test_news_filter_recall.py
git commit -m "feat(evals): recall measurement for the tier-1 news filter (phase 1 gate)"
```

---

### Task 12: Spike SBV (chỉ khảo sát, không code collector)

**Files:**
- Create: `tests/fixtures/sbv/*.html`, `docs/superpowers/specs/2026-10-06-sbv-spike-findings.md`

Mục đích: quyết định có làm collector SBV (plan riêng) hay không. Ngày 2026-10-06 đã biết: `https://www.sbv.gov.vn/` với User-Agent đơn giản bị WAF trả "Request Rejected" (HTTP 200, 244 byte); với header giống trình duyệt, `/webcenter/portal/vi/menu/trangchu` trả ~430 KB nhưng trang chủ chỉ có liên kết tới thông báo tỷ giá trung tâm theo tuần và mục "Lãi suất NHNN quy định", không có con số. Truy cập ở tần suất thấp (vài request cho cả spike).

- [ ] **Step 1: Tải trang chủ và tìm liên kết số liệu**

```bash
UA='Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36'
mkdir -p tests/fixtures/sbv
curl -sL -m 30 -A "$UA" -H 'Accept-Language: vi-VN,vi;q=0.9' -o tests/fixtures/sbv/home.html \
  https://www.sbv.gov.vn/webcenter/portal/vi/menu/trangchu
grep -o 'href="[^"]*"' tests/fixtures/sbv/home.html | grep -i -E 'ty-gia|tygia|lai-suat|laisuat|ty_gia' | sort -u | head -30
```

(cần `allowed_domains`: `www.sbv.gov.vn`.)

- [ ] **Step 2: Theo các liên kết hứa hẹn nhất (tối đa 5 request)**

Với mỗi URL tìm được ở Step 1 (ưu tiên trang "Tỷ giá trung tâm" và "Lãi suất NHNN quy định"), tải cùng lệnh `curl` và lưu `tests/fixtures/sbv/<tên-ngắn>.html`, rồi kiểm tra có con số hay không:

```bash
sed 's/<[^>]*>/ /g' tests/fixtures/sbv/<tên-ngắn>.html | tr -s ' \t\n' ' ' | grep -o -i -E '(tỷ giá trung tâm|tái cấp vốn|tái chiết khấu).{0,160}' | head -10
```

- [ ] **Step 3: Ghi kết luận**

Tạo `docs/superpowers/specs/2026-10-06-sbv-spike-findings.md` với đúng bốn mục (nội dung lấy từ những gì Step 1–2 quan sát được, kèm trích đoạn thực):

1. `## Kết luận` — một dòng: **khả thi** nếu một trang HTML tĩnh chứa số của tỷ giá trung tâm USD/VND mới nhất **và** lãi suất tái cấp vốn, mỗi số có nhãn nhận diện được; ngược lại **không khả thi**.
2. `## Cách truy cập` — URL chính xác, header cần để WAF không chặn, có cần cookie/JS không.
3. `## Dữ liệu tìm được` — từng chỉ tiêu: URL, đoạn HTML/regex nhận diện, định dạng ngày, tần suất cập nhật (thông báo theo tuần hay theo ngày).
4. `## Đề xuất cho plan SBV` — nếu khả thi: bảng `macro_indicators`, `pipeline/collectors/sbv.py`, job `collect_sbv`, khối `indicators` trong `get_macro_context`; nếu không: nguồn thay thế đáng thử (vnstock Macro gói trả phí, trang khác) hoặc bỏ.

- [ ] **Step 4: Commit**

```bash
git add tests/fixtures/sbv docs/superpowers/specs/2026-10-06-sbv-spike-findings.md
git commit -m "docs: SBV spike findings and page fixtures"
```

---

### Task 13: Kiểm tra toàn bộ và bàn giao

- [ ] **Step 1: Chạy toàn bộ test**

Run: `./run_tests.sh -q`
Expected: toàn bộ PASS, không test nào bị bỏ qua vì thiếu DB.

- [ ] **Step 2: Rà soát cuối**

Run: `git status --short && git diff main --stat`
Expected: chỉ gồm các file trong bảng "File Structure", không có `.env`/`.venv`/`infrastructure/.env` bị add (đều là file untracked/ignored).

Run: `grep -rn "pipeline_enabled" config/vn-rules.yaml`
Expected: `pipeline_enabled: false` (không đổi).

- [ ] **Step 3: Đẩy nhánh**

```bash
git push -u origin HEAD
```

- [ ] **Step 4: Ghi chú triển khai cho người vận hành (không tự chạy; cần quyền trên máy chạy thật)**

1. `docker compose up -d --build` (image cần `feedparser`).
2. Áp migration: lệnh `apply_migrations` trong README mục 6, rồi `python -m db.setup_roles`.
3. Restart container `hermes-gateway` để MCP server nạp tool `get_macro_context`; kiểm tra: `docker exec hermes-gateway hermes mcp test vn-market-mcp` (phải thấy 17 tool).
4. Theo dõi log worker: `collect_rss_done` mỗi giờ; kiểm tra `SELECT * FROM source_health;`.
5. Gate: sau 1–2 ngày thu tin, chạy `python -m evals.news_filter_recall --export 200 > evals/news_filter_labels.jsonl`, gán nhãn tay `relevant`, rồi `--score`; recall phải ≥ 95%. Chưa đạt → thêm từ khóa vào `config/macro_keywords.yaml` rồi `python -m pipeline.news_filter --refilter --days 7`.
6. Gate thứ hai: 7 ngày chạy liên tục mọi nguồn có tin trong giờ làm việc, hoặc đã có cảnh báo ops tương ứng.

---

## Self-Review (đã chạy)

- **Spec coverage:** §2 sửa lỗi → Task 1 (khóa), Task 3 + 7 (partition), Task 8 (`ingest_news`); §3 migration → Task 2 (`macro_indicators` đã dời sang plan SBV theo spec sửa); §4 partition → Task 3 + 7; §5 job → Task 6 (thu), Task 1 (ưu tiên), Task 7 (scheduler), Task 8 (worker); §6 lọc → Task 4; §7 giám sát → Task 5 + 6; §8 MCP + skill → Task 9 + 10; §9 SBV spike → Task 12; §10 test → mỗi task; §11 gate → Task 11 + 13; §12 file → khớp bảng File Structure (README, compose, requirements đã gồm).
- **Placeholder scan:** không còn "TBD/TODO"; mọi bước code có nội dung; Task 12 là khảo sát nên nội dung kết luận do kết quả quan sát quyết định, nhưng cấu trúc và tiêu chí quyết định đã nêu cụ thể.
- **Type consistency:** `record_ok(conn, source, newest_item_at, now)` / `record_failure(conn, source, error, now)` / `check_and_alert(conn, now, rss_sources, send)` / `news_sources_status(conn, now, names)` dùng thống nhất ở Task 5, 6, 8, 9; `store_news_item(...)` keyword-only khớp giữa Task 3, 4 (test), 6, 9 (test); `run_collect_rss(conn, *, vn30, send, client, now)` khớp Task 6 và 8; `COLLECT_RSS_JOB_TYPE`/`COLLECT_JOB_TYPES` định nghĩa Task 1, dùng Task 7, 8.
- **Review Focus:** 5 mục đều có test ở task ghi trong ngoặc.
