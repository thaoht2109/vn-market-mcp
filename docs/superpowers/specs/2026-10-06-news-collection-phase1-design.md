# Thu thập tin tức — Giai đoạn 1: nền tảng thu thập (design)

Ngày: 2026-10-06 · Nguồn yêu cầu: `../trienkhai-tintuc-vimo.md` (plan Luồng A/B), điều chỉnh để khớp kiến trúc `vn-market-mcp`.

## 1. Mục tiêu

Thu tin RSS (vĩ mô + doanh nghiệp VN30) và số liệu SBV vào Postgres hiện có, lọc bằng quy tắc tất định, cho Hermes đọc qua MCP — **không thêm LLM vào pipeline** và **không đổi điểm/nhãn**. Đồng thời sửa ba lỗi làm tin bị sót âm thầm hoặc job chạy trùng.

### Quyết định đã chốt

| Quyết định | Lựa chọn |
|---|---|
| Kiến trúc | Phương án A: mở rộng ngay trong repo, dùng hàng đợi `jobs` + worker + `db/migrations` hiện có. Không repo riêng, không systemd, không SQLAlchemy/Alembic, không `postgres_fdw` (giá, BCTC, khối ngoại đã ở cùng DB `vnmcp`) |
| LLM | Không chạy LLM nền. `llm.pipeline_enabled` giữ `false`. Hermes diễn giải khi người dùng hỏi |
| Thiếu dữ liệu | Fail-closed: thiếu tin ≠ không có sự kiện (xem §7) |

### Năm nguyên tắc (áp dụng cho mọi giai đoạn)

1. Thiếu dữ liệu ≠ không có sự kiện: nguồn im lặng thì báo "tin cập nhật đến HH:MM", không coi là "không có tin".
2. Tin chỉ được hạ nhãn, không được nâng (áp dụng từ giai đoạn 4).
3. Red flag đối chiếu hai nguồn độc lập: CBTT HOSE + sự kiện vnstock (giai đoạn 2).
4. Gate đo recall (độ sót), không chỉ precision.
5. Không nuốt lỗi im lặng; partition không được hết hạn.

### Lộ trình các giai đoạn (mỗi giai đoạn một spec + plan riêng)

1. **Giai đoạn 1 (spec này):** nền tảng thu thập RSS + SBV, lọc tầng 1, tool MCP, sửa lỗi tiên quyết.
2. Giai đoạn 2: Luồng B — sự kiện vnstock → `corporate_events`, crawl CBTT HOSE, red flag YAML; Fed/FRED, NSO.
3. Giai đoạn 3: tải bài gốc và trích xuất nội dung có cấu trúc (cách làm không dùng LLM nền sẽ thiết kế khi tới).
4. Giai đoạn 4: đưa kết quả vào `news_events` / `sector_macro` của điểm tổng hợp, sau cờ riêng, theo nguyên tắc 2.

### Ngoài phạm vi giai đoạn 1

Mọi mục của giai đoạn 2–4. Không đổi điểm, nhãn, grading, `config/vn-rules.yaml`.

## 2. Sửa lỗi tiên quyết

| Lỗi | Vị trí | Sửa |
|---|---|---|
| Khóa advisory khác nhau giữa các tiến trình: `hash()` của Python bị ngẫu nhiên hóa theo tiến trình (đã xác minh: hai lần chạy `hash(('HPG','x'))` cho hai số khác nhau), nên 2 worker có thể chạy trùng một (ticker, job_type) | `pipeline/jobs.py:_lock_key` | `int.from_bytes(hashlib.blake2b(f"{ticker}\|{job_type}".encode(), digest_size=8).digest(), "big") & 0x7FFF_FFFF_FFFF_FFFF` |
| `news_items` chỉ có partition đến hết 2026-12; từ 2027-01-01 mọi lệnh ghi tin lỗi và bị nuốt | `db/migrations/009_news_items.sql` | Hàm `ensure_news_partitions` (§4) |
| `ingest_news` nuốt lỗi API, không ai biết | `pipeline/ingest.py:ingest_news` | Ghi lỗi vào `source_health` (nguồn `vnstock_news`); hành vi "không làm hỏng lượt phân tích" giữ nguyên |

## 3. Dữ liệu — migration `018_news_collection.sql`

```sql
ALTER TABLE news_items
  ADD COLUMN stream        TEXT,      -- 'A' (vĩ mô) | 'B' (doanh nghiệp) | NULL (tin vnstock)
  ADD COLUMN filter_status TEXT,      -- 'kept' | 'dropped' | NULL (tin vnstock: coi như kept)
  ADD COLUMN filter_reason TEXT,      -- vd 'exclude:khuyến mãi', 'no_keyword', 'duplicate_title:<url_hash>'
  ADD COLUMN pillars       TEXT[] NOT NULL DEFAULT '{}';

CREATE TABLE source_health (
  source               TEXT PRIMARY KEY,   -- `name` trong config/news_sources.yaml, hoặc 'vnstock_news', 'sbv'
  last_ok_at           TIMESTAMPTZ,        -- lần gọi thành công gần nhất
  last_item_at         TIMESTAMPTZ,        -- published_at mới nhất đã nhận
  last_error           TEXT,
  consecutive_failures INTEGER NOT NULL DEFAULT 0,
  alerted_at           TIMESTAMPTZ         -- chống gửi cảnh báo lặp; đặt NULL khi nguồn hồi phục
);

CREATE TABLE macro_indicators (
  indicator    TEXT NOT NULL,       -- vd 'sbv_refinancing_rate', 'sbv_central_rate_usd'
  period       DATE NOT NULL,       -- ngày hiệu lực
  value        NUMERIC NOT NULL,
  unit         TEXT NOT NULL,       -- '%', 'VND'
  source       TEXT NOT NULL,       -- 'sbv'
  source_url   TEXT,
  published_at TIMESTAMPTZ,
  fetched_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (indicator, period, source)
);
```

- `news_items.tickers` vẫn `NOT NULL`: tin Luồng A không khớp mã ghi `'{}'`.
- Sau migration chạy lại `python -m db.setup_roles` (quyền chỉ cấp trên bảng đang có).
- Hai nơi đang đọc `news_items` (`mcp_server/tools/digests.py:_headlines`, `mcp_server/tools/stock_report.py`) thêm điều kiện `filter_status IS DISTINCT FROM 'dropped'`. Tin vnstock (`filter_status` NULL) hiển thị như trước.

## 4. Partition tự gia hạn

`pipeline/news.py:ensure_news_partitions(conn, months_ahead=3)` tạo `news_items_YYYY_MM` cho tháng hiện tại và 3 tháng tới nếu chưa có (`CREATE TABLE IF NOT EXISTS … PARTITION OF news_items FOR VALUES FROM … TO …`). Gọi khi `ops.scheduler` khởi động và mỗi lần đổi ngày trong vòng lặp. Không dùng partition DEFAULT (cản việc DETACH khi dọn dữ liệu sau này).

Quyền: tạo partition cần quyền chủ sở hữu bảng cha, `pipeline_rw` không có. Scheduler nhận thêm `ADMIN_DATABASE_URL`, dựng trong `infrastructure/docker-compose.yml` từ `POSTGRES_ADMIN_USER`/`POSTGRES_ADMIN_PASSWORD` sẵn có, chỉ dùng cho hàm này. Tạo partition lỗi → `send_ops_alert`, scheduler vẫn chạy tiếp. Cảnh báo bổ sung: tháng kế tiếp chưa có partition khi còn ≤ 7 ngày → `send_ops_alert`.

## 5. Job thu thập

| job_type | Lịch (giờ VN) | Ngày | Nội dung |
|---|---|---|---|
| `collect_rss` | Mỗi giờ tròn 06:00–23:00 | Mọi ngày | Đọc mọi nguồn `enabled` trong `config/news_sources.yaml` có `every_minutes` đến hạn (so với `source_health.last_ok_at`) |
| `collect_sbv` | 09:00, 17:00 | Thứ 2–6 | Lãi suất điều hành, tỷ giá trung tâm USD |

- Market-wide (`ticker="MARKET"`), job_key theo ngày + slot (tham số `slot=` của `enqueue`, như `scheduled_intraday`), nên chạy lại scheduler không tạo trùng.
- Scheduler: thêm `_run_collect_if_due` vào `run_due_jobs`, theo mẫu `_run_intraday_if_due`. `collect_rss` không kiểm `is_trading_day`; `collect_sbv` chỉ kiểm thứ 2–6.
- Worker: nhánh market-wide trong `run_one` (đang xử lý `macro_premarket`, `close_sync`) thêm `collect_rss`, `collect_sbv`. Job thu thập không gọi vnstock nên không tốn hạn mức 60 request/phút.
- Ưu tiên: `claim_next` đổi thành `ORDER BY (job_type = ANY(%s)) DESC, created_at` với hằng `COLLECT_JOB_TYPES`, để job thu thập không xếp sau 30+ job phân tích lúc 15:05–15:30.
- Lỗi một nguồn không làm hỏng job: mỗi nguồn try/except riêng, ghi `source_health`. Job chỉ `failed` khi lỗi ngoài vòng nguồn (vd mất DB).

### `config/news_sources.yaml`

```yaml
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
```

Danh sách đầy đủ lấy từ plan (CafeF, CafeBiz, VnExpress, VnBusiness, Vietstock). Mỗi URL phải được kiểm tra trả RSS hợp lệ trước khi `enabled: true`; URL chưa xác minh để `enabled: false`. Đã xác minh HTTP 200 + content-type XML ngày 2026-10-06: 3 URL ở trên.

### Gọi HTTP

`httpx` (đã có) với: User-Agent rõ ràng, timeout 15 s, `If-None-Match`/`If-Modified-Since` (ETag/Last-Modified giữ trong bộ nhớ worker — mất khi restart thì chỉ tải lại feed một lần), tối thiểu 2 s giữa hai request cùng domain, retry 2 lần với backoff khi lỗi mạng/5xx, không retry 403/429 (ghi lỗi, để lần chạy sau). Parse RSS bằng `feedparser` — thư viện mới duy nhất, thêm vào `requirements.txt`.

### Chuẩn hóa một mục RSS

- `url_hash = sha256(url)` — cùng công thức với `ingest_news` (`key = it.url`), nên một bài có trong cả RSS và tin vnstock cho cùng hash.
- Khử trùng theo URL: trước khi ghi, tìm `url_hash` trong 7 ngày gần nhất; có rồi thì chỉ hợp nhất `tickers`/`pillars`, không thêm dòng. (Khóa unique hiện là `(url_hash, published_at)`; hai nguồn ghi giờ đăng khác nhau sẽ lọt nếu chỉ dựa vào khóa.)
- `published_at` lấy từ `published_parsed`/`updated_parsed` của feedparser (UTC). Không có thì dùng `fetched_at` và thêm `no_pubdate` vào `filter_reason`.
- `source` = `name` trong YAML. `summary` = phần tóm tắt RSS đã bỏ thẻ HTML.

## 6. Lọc tầng 1 (tất định, không xóa)

Module `pipeline/news_filter.py`, hàm thuần `classify(title, summary, stream, vn30, aliases, keywords) -> FilterResult(status, reason, pillars, tickers)`.

1. **Loại trừ**: khớp danh sách `exclude` trong `config/macro_keywords.yaml` → `dropped`, `exclude:<cụm>`.
2. **Luồng A**: khớp từ khóa của 7 trụ cột (không phân biệt hoa thường, chuẩn hóa Unicode NFC) trên tiêu đề + tóm tắt → `pillars`. Có ít nhất một trụ cột → `kept`; không có → `dropped`, `no_keyword`.
3. **Luồng B**: khớp mã (`\b[A-Z]{3}\b` giao với VN30 hiện hành) và tên gọi khác → `tickers`. Có mã → `kept`; không có → `dropped`, `no_ticker`. Tin Luồng B khớp từ khóa vĩ mô thì cũng được gán `pillars`.
4. **Trùng tiêu đề**: `difflib.SequenceMatcher(None, a, b).ratio() >= 0.9` với tin `kept` trong 48 giờ → `dropped`, `duplicate_title:<url_hash gốc>`.
5. Mọi tin đều được ghi, kể cả `dropped`. Lệnh `python -m pipeline.news_filter --refilter --days N` chạy lại bộ lọc trên dữ liệu đã lưu khi đổi YAML.

Tên gọi khác: `tickers.name` + `config/ticker_aliases.yaml` (vd `TCB: [Techcombank]`, `VCB: [Vietcombank]`), chỉ cho VN30.

**Thiên về recall**: khi phân vân thì giữ tin. Danh sách `exclude` chỉ chứa cụm rõ ràng là quảng cáo/đời sống.

## 7. Giám sát và fail-closed

- Mỗi nguồn, sau mỗi lần gọi, cập nhật `source_health` (thành công: `last_ok_at`, `last_item_at`, `consecutive_failures = 0`, `alerted_at = NULL`; lỗi: tăng `consecutive_failures`, ghi `last_error`).
- **Giờ làm việc** = 08:00–18:00 VN, thứ 2–6.
- Cảnh báo ops (`send_ops_alert`, một lần mỗi đợt nhờ `alerted_at`) khi: `consecutive_failures >= 3`, hoặc `last_item_at` cũ hơn 6 giờ làm việc. Kiểm tra ở cuối mỗi job `collect_rss`.
- `get_macro_context` và `get_market_digest_input` trả khối `sources` (§8). Nguồn quá hạn (cùng điều kiện trên) có `stale: true` và một dòng trong `warnings` của envelope.

## 8. MCP cho Hermes

Tool mới `get_macro_context(days: int = 7)` trong `mcp_server/tools/macro.py`, role `mcp_ro`, theo mẫu `digests.py`, trả envelope:

```json
{
  "pillars": {"tien_te": [{"date": "06/10 08:15", "title": "...", "source": "cafef_vi_mo", "url": "..."}]},
  "indicators": [{"indicator": "sbv_refinancing_rate", "period": "2026-10-01", "value": 4.5, "unit": "%"}],
  "sources": [{"source": "cafef_vi_mo", "last_item_at": "2026-10-06T08:15:00+07:00", "stale": false}]
}
```

- Chỉ tin `kept`, tối đa 10 tin mỗi trụ cột, mới nhất trước. `days` giới hạn 1–30.
- `get_market_digest_input` thêm khối `sources` và `macro_headlines` (tin Luồng A `kept` trong 18 giờ, tối đa 15).
- Đăng ký tool trong `mcp_server/server.py`.
- `SKILL.md`:
  - Thêm dòng định tuyến: "tin vĩ mô", "lãi suất", "tỷ giá", "chính sách tiền tệ" → `get_macro_context()`.
  - Thêm luật: có `stale` → nói "tin từ <nguồn> mới cập nhật đến HH:MM"; trụ cột không có tin → "chưa ghi nhận tin", không nói "không có sự kiện".
  - Câu "tin tức và vĩ mô chưa được tính vào điểm" giữ nguyên (điểm chưa đổi ở giai đoạn này).

## 9. SBV collector

`pipeline/collectors/sbv.py`: tải trang SBV, parse lãi suất điều hành (tái cấp vốn, tái chiết khấu) và tỷ giá trung tâm USD/VND, ghi `macro_indicators` (`ON CONFLICT DO UPDATE SET value, fetched_at`).

Cấu trúc trang SBV chưa được xác minh (trang chủ trả HTTP 200 ngày 2026-10-06). Bước đầu của phần việc này: lưu trang mẫu vào `tests/fixtures/sbv/` và viết parser theo fixture. Nếu trang chỉ render bằng JavaScript hoặc không parse ổn định → không bật job `collect_sbv`, báo lại người vận hành; các phần khác của giai đoạn 1 vẫn hoàn thành.

## 10. Kiểm thử

Chạy bằng `./run_tests.sh` (DB `vnmcp_test`).

| Test | Kiểm tra |
|---|---|
| `test_jobs.py` | `_lock_key` cho cùng kết quả ở hai tiến trình con khác nhau; `claim_next` lấy job thu thập trước job phân tích cũ hơn |
| `test_news_partitions.py` | Tạo đúng partition tháng hiện tại + 3 tháng; chạy lại không lỗi; ghi tin ngày 2027-01-15 thành công |
| `test_news_filter.py` | Mỗi trụ cột có ≥ 3 tiêu đề mẫu phải `kept`; cụm exclude → `dropped`; khớp mã và tên gọi khác; trùng tiêu đề ≥ 0,9 → `dropped` |
| `test_collect_rss.py` | Parse RSS mẫu trong `tests/fixtures/rss/`; khử trùng theo URL giữa hai nguồn; một nguồn lỗi không làm hỏng job; `source_health` cập nhật; cảnh báo chỉ gửi một lần mỗi đợt |
| `test_sbv.py` | Parse fixture SBV ra đúng giá trị |
| `test_mcp_macro.py` | Envelope đúng; tin `dropped` không lọt; nguồn quá hạn có `stale` + warning |
| `test_mcp_digests.py`, `test_mcp_stock_report.py` | Tin `dropped` không hiển thị; tin vnstock cũ vẫn hiển thị |
| `test_scheduler.py` | `collect_rss` vào hàng đợi đúng giờ, không trùng; `collect_sbv` không chạy thứ 7/CN |

## 11. Gate qua giai đoạn 1

1. Recall ≥ 95% của lọc tầng 1 trên 200 tiêu đề RSS gán nhãn tay (`evals/news_filter_labels.jsonl`; nhãn: "tin có liên quan vĩ mô / mã VN30 không"). Precision chỉ ghi lại, không chặn.
2. 7 ngày chạy liên tục: mọi nguồn `enabled` có tin mới trong giờ làm việc, hoặc đã có cảnh báo ops tương ứng.
3. Toàn bộ test hiện có và test mới qua.

## 12. Tệp thay đổi

**Mới:** `db/migrations/018_news_collection.sql`, `pipeline/news.py` (partition, ghi tin, `source_health`), `pipeline/news_filter.py`, `pipeline/collectors/rss.py`, `pipeline/collectors/sbv.py`, `mcp_server/tools/macro.py`, `config/news_sources.yaml`, `config/macro_keywords.yaml`, `config/ticker_aliases.yaml`, `evals/news_filter_labels.jsonl`, fixtures và test ở §10.

**Sửa:** `pipeline/jobs.py` (`_lock_key`, `claim_next`), `pipeline/ingest.py` (`ingest_news` → `source_health`), `ops/scheduler.py`, `ops/worker.py`, `mcp_server/server.py`, `mcp_server/tools/digests.py`, `mcp_server/tools/stock_report.py`, `.hermes/skills/vn-market/vn-stock-analyze/SKILL.md`, `infrastructure/docker-compose.yml` (scheduler nhận `ADMIN_DATABASE_URL`), `requirements.txt` (`feedparser`), `README.md` (mục 3, 7, 10, 16).

**Không đổi:** `config/vn-rules.yaml` (`llm.pipeline_enabled: false`, `weights`), điểm, nhãn, grading.
