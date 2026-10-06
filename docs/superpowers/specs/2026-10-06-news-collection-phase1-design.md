# Thu thập tin tức — Giai đoạn 1: nền tảng thu thập (design)

Ngày: 2026-10-06 · Nguồn yêu cầu: `../trienkhai-tintuc-vimo.md` (plan Luồng A/B), điều chỉnh để khớp kiến trúc `vn-market-mcp`.

## 1. Mục tiêu

Thu tin RSS (vĩ mô + doanh nghiệp VN30) vào Postgres hiện có (SBV: chỉ spike, §9), lọc bằng quy tắc tất định, cho Hermes đọc qua MCP — **không thêm LLM vào pipeline** và **không đổi điểm/nhãn**. Đồng thời sửa ba lỗi làm tin bị sót âm thầm hoặc job chạy trùng.

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

1. **Giai đoạn 1 (spec này):** nền tảng thu thập RSS, spike SBV, lọc tầng 1, tool MCP, sửa lỗi tiên quyết.
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
  source               TEXT PRIMARY KEY,   -- `name` trong config/news_sources.yaml, hoặc 'vnstock_news'
  last_ok_at           TIMESTAMPTZ,        -- lần gọi thành công gần nhất
  last_item_at         TIMESTAMPTZ,        -- published_at mới nhất đã nhận
  last_error           TEXT,
  consecutive_failures INTEGER NOT NULL DEFAULT 0,
  alerted_at           TIMESTAMPTZ,        -- chống gửi cảnh báo lặp; đặt NULL khi nguồn hồi phục
  first_seen_at        TIMESTAMPTZ NOT NULL DEFAULT now()  -- mốc tính "im lặng" khi nguồn chưa từng có tin
);
```

Bảng `macro_indicators` dời sang plan riêng của SBV (§9), tạo khi đã có parser chạy được.

- `news_items.tickers` vẫn `NOT NULL`: tin Luồng A không khớp mã ghi `'{}'`.
- Sau migration chạy lại `python -m db.setup_roles` (quyền chỉ cấp trên bảng đang có).
- Hai nơi đang đọc `news_items` (`mcp_server/tools/digests.py:_headlines`, `mcp_server/tools/stock_report.py`) thêm điều kiện `filter_status IS DISTINCT FROM 'dropped'`. Tin vnstock (`filter_status` NULL) hiển thị như trước.

## 4. Partition tự gia hạn

`pipeline/news.py:ensure_news_partitions(conn, months_ahead=3)` tạo `news_items_YYYY_MM` cho tháng hiện tại và 3 tháng tới nếu chưa có (`CREATE TABLE IF NOT EXISTS … PARTITION OF news_items FOR VALUES FROM … TO …`). Gọi khi `ops.scheduler` khởi động và mỗi lần đổi ngày trong vòng lặp. Không dùng partition DEFAULT (cản việc DETACH khi dọn dữ liệu sau này).

Quyền: tạo partition cần quyền chủ sở hữu bảng cha, `pipeline_rw` không có. Scheduler nhận thêm `ADMIN_DATABASE_URL`, dựng trong `infrastructure/docker-compose.yml` từ `POSTGRES_ADMIN_USER`/`POSTGRES_ADMIN_PASSWORD` sẵn có, chỉ dùng cho hàm này. Tạo partition lỗi → `send_ops_alert`, scheduler vẫn chạy tiếp (hàm chạy lại mỗi ngày, luôn tạo trước 3 tháng, nên còn gần 3 tháng để xử lý trước khi tin bị lỗi ghi).

## 5. Job thu thập

| job_type | Lịch (giờ VN) | Ngày | Nội dung |
|---|---|---|---|
| `collect_rss` | Một lần mỗi giờ 06:00–23:59 (lần poll đầu tiên của giờ đó) | Mọi ngày | Đọc mọi nguồn `enabled` trong `config/news_sources.yaml` có `every_minutes` đến hạn (so với `source_health.last_ok_at`) |

`collect_sbv` dời sang plan riêng sau spike SBV (§9).

- Market-wide (`ticker="MARKET"`), job_key theo ngày + giờ (tham số `slot=` của `enqueue`, như `scheduled_intraday`), nên chạy lại scheduler không tạo trùng.
- Scheduler: thêm `_run_collect_if_due` vào `run_due_jobs`. `collect_rss` không kiểm `is_trading_day`.
- Worker: nhánh market-wide trong `run_one` (đang xử lý `macro_premarket`, `close_sync`) thêm `collect_rss`. Job thu thập không gọi vnstock nên không tốn hạn mức 60 request/phút.
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
- `published_at` lấy từ `published_parsed`/`updated_parsed` của feedparser (UTC). Không có thì dùng `fetched_at`.
- `source` = `name` trong YAML. `summary` = phần tóm tắt RSS đã bỏ thẻ HTML.

## 6. Lọc tầng 1 (tất định, không xóa)

Module `pipeline/news_filter.py`, hàm thuần `classify(title, summary, stream, vn30, aliases, keywords) -> FilterResult(status, reason, pillars, tickers)`.

1. **Loại trừ**: khớp danh sách `exclude` trong `config/macro_keywords.yaml` → `dropped`, `exclude:<cụm>`.
2. **Trụ cột**: khớp từ khóa của 7 trụ cột (không phân biệt hoa thường, chuẩn hóa Unicode NFC, khớp nguyên từ) trên tiêu đề + tóm tắt → `pillars`.
3. **Mã**: khớp mã (3 chữ in hoa giao với VN30 hiện hành) và tên gọi khác → `tickers`.
4. **Giữ hay loại** (giống nhau cho hai luồng, thiên recall): có trụ cột hoặc có mã → `kept`; không có cả hai → `dropped`, lý do `no_keyword` (Luồng A) hoặc `no_ticker` (Luồng B). Ví dụ: tin "khối ngoại bán ròng" trên feed chứng khoán không nêu mã vẫn giữ; tin "FPT lãi kỷ lục" trên feed kinh doanh vẫn giữ và gắn mã.
5. **Trùng tiêu đề**: `difflib.SequenceMatcher(None, a, b).ratio() >= 0.9` với tin `kept` trong 48 giờ → `dropped`, `duplicate_title:<url_hash gốc>`.
6. Mọi tin đều được ghi, kể cả `dropped`. Lệnh `python -m pipeline.news_filter --refilter --days N` chạy lại bộ lọc trên dữ liệu đã lưu khi đổi YAML.

Tên gọi khác: `config/ticker_aliases.yaml` (`tickers.name` là tên pháp lý dài, hiếm khi xuất hiện nguyên văn trong tiêu đề nên không dùng) (vd `TCB: [Techcombank]`, `VCB: [Vietcombank]`), chỉ cho VN30.

**Thiên về recall**: khi phân vân thì giữ tin. Danh sách `exclude` chỉ chứa cụm rõ ràng là quảng cáo/đời sống.

## 7. Giám sát và fail-closed

- Mỗi nguồn, sau mỗi lần gọi, cập nhật `source_health` (thành công: `last_ok_at`, `last_item_at`, `consecutive_failures = 0`; lỗi: tăng `consecutive_failures`, ghi `last_error`). `alerted_at` chỉ về `NULL` khi nguồn hồi phục thật: trước đó đang lỗi, hoặc vừa nhận được tin mới hơn `last_item_at`. Gọi thành công nhưng không có tin mới không làm cảnh báo "im lặng" được báo lại mỗi giờ.
- Một phản hồi HTTP 200 nhưng không phải RSS hợp lệ (vd trang chặn của WAF) tính là **lỗi**, không phải "feed rỗng".
- **Giờ làm việc** = 08:00–18:00 VN, thứ 2–6.
- Cảnh báo ops (`send_ops_alert`, một lần mỗi đợt nhờ `alerted_at`) khi: `consecutive_failures >= 3` (mọi nguồn, kể cả `vnstock_news`), hoặc — chỉ với nguồn RSS `enabled` — `coalesce(last_item_at, first_seen_at)` cũ hơn 6 giờ làm việc. `vnstock_news` chỉ chạy lúc 15:20 nên không xét im lặng. Kiểm tra ở cuối mỗi job `collect_rss`. Ngày lễ chưa được trừ khỏi giờ làm việc (có thể báo nhầm một lần sau kỳ nghỉ).
- `get_macro_context` và `get_market_digest_input` trả khối `news_sources` (§8). Nguồn quá hạn (cùng điều kiện trên) có `stale: true` và một dòng trong `warnings` của envelope.

## 8. MCP cho Hermes

Tool mới `get_macro_context(days: int = 7)` trong `mcp_server/tools/macro.py`, role `mcp_ro`, theo mẫu `digests.py`, trả envelope:

```json
{
  "pillars": {"tien_te": [{"date": "06/10 08:15", "title": "...", "source": "cafef_vi_mo", "url": "..."}]},
  "news_sources": [{"source": "cafef_vi_mo", "last_item_at": "2026-10-06T08:15:00+07:00", "stale": false}]
}
```

(Khóa `news_sources` thay vì `sources` vì envelope đã có trường `sources` ở cấp ngoài. Khối `indicators` thêm khi có SBV.)

- Chỉ tin `kept`, tối đa 10 tin mỗi trụ cột, mới nhất trước. `days` giới hạn 1–30.
- `get_market_digest_input` thêm khối `news_sources` và `macro_headlines` (tin Luồng A `kept` trong 18 giờ, tối đa 15).
- Đăng ký tool trong `mcp_server/server.py`.
- `SKILL.md`:
  - Thêm dòng định tuyến: "tin vĩ mô", "lãi suất", "tỷ giá", "chính sách tiền tệ" → `get_macro_context()`.
  - Thêm luật: có `stale` → nói "tin từ <nguồn> mới cập nhật đến HH:MM"; trụ cột không có tin → "chưa ghi nhận tin", không nói "không có sự kiện".
  - Câu "tin tức và vĩ mô chưa được tính vào điểm" giữ nguyên (điểm chưa đổi ở giai đoạn này).

## 9. SBV — spike trước, collector sau

Kiểm tra ngày 2026-10-06: `https://www.sbv.gov.vn/` với User-Agent đơn giản bị WAF trả trang "Request Rejected" (HTTP 200, 244 byte); với header giống trình duyệt, `/webcenter/portal/vi/menu/trangchu` trả trang đầy đủ (~430 KB) nhưng chỉ có link tới thông báo tỷ giá trung tâm theo tuần và mục "Lãi suất NHNN quy định", không có con số ngay trên trang.

Giai đoạn 1 chỉ làm **spike**: tìm trang con chứa số liệu, lưu mẫu vào `tests/fixtures/sbv/`, ghi kết luận (parse được bằng HTML tĩnh hay không, URL, tần suất cập nhật) vào `docs/superpowers/specs/2026-10-06-sbv-spike-findings.md`. Nếu khả thi → plan riêng: bảng `macro_indicators`, `pipeline/collectors/sbv.py`, job `collect_sbv` (09:00, 17:00, thứ 2–6), khối `indicators` trong `get_macro_context`. Nếu không khả thi → ghi lý do, giữ ngoài phạm vi; các phần khác của giai đoạn 1 không phụ thuộc vào SBV.

## 10. Kiểm thử

Chạy bằng `./run_tests.sh` (DB `vnmcp_test`).

| Test | Kiểm tra |
|---|---|
| `test_jobs.py` | `_lock_key` cho cùng kết quả ở hai tiến trình con khác nhau; `claim_next` lấy job thu thập trước job phân tích cũ hơn |
| `test_news_partitions.py` | Tạo đúng partition tháng hiện tại + 3 tháng; chạy lại không lỗi; ghi tin ngày 2027-01-15 thành công |
| `test_news_filter.py` | Mỗi trụ cột có ≥ 3 tiêu đề mẫu phải `kept`; cụm exclude → `dropped`; khớp mã và tên gọi khác; trùng tiêu đề ≥ 0,9 → `dropped` |
| `test_collect_rss.py` | Parse RSS mẫu trong `tests/fixtures/rss/`; khử trùng theo URL giữa hai nguồn; một nguồn lỗi không làm hỏng job; `source_health` cập nhật; cảnh báo chỉ gửi một lần mỗi đợt |
| `test_mcp_macro.py` | Envelope đúng; tin `dropped` không lọt; nguồn quá hạn có `stale` + warning |
| `test_mcp_digests.py`, `test_mcp_stock_report.py` | Tin `dropped` không hiển thị; tin vnstock cũ vẫn hiển thị |
| `test_scheduler.py` | `collect_rss` vào hàng đợi đúng giờ, không trùng, không chạy trước 06:00 |

## 11. Gate qua giai đoạn 1

1. Recall ≥ 95% của lọc tầng 1 trên 200 tiêu đề RSS gán nhãn tay (`evals/news_filter_labels.jsonl`; nhãn: "tin có liên quan vĩ mô / mã VN30 không"). Precision chỉ ghi lại, không chặn.
2. 7 ngày chạy liên tục: mọi nguồn `enabled` có tin mới trong giờ làm việc, hoặc đã có cảnh báo ops tương ứng.
3. Toàn bộ test hiện có và test mới qua.

## 12. Tệp thay đổi

**Mới:** `db/migrations/018_news_collection.sql`, `pipeline/news.py` (partition, ghi tin, `source_health`), `pipeline/news_filter.py`, `pipeline/collectors/rss.py`, `mcp_server/tools/macro.py`, `config/news_sources.yaml`, `config/macro_keywords.yaml`, `config/ticker_aliases.yaml`, `evals/news_filter_recall.py` (xuất mẫu + đo recall; file nhãn `evals/news_filter_labels.jsonl` do người vận hành gán), `docs/superpowers/specs/2026-10-06-sbv-spike-findings.md`, fixtures và test ở §10.

**Sửa:** `pipeline/jobs.py` (`_lock_key`, `claim_next`), `pipeline/ingest.py` (`ingest_news` → `source_health`), `ops/scheduler.py`, `ops/worker.py`, `mcp_server/server.py`, `mcp_server/tools/digests.py`, `mcp_server/tools/stock_report.py`, `.hermes/skills/vn-market/vn-stock-analyze/SKILL.md`, `infrastructure/docker-compose.yml` (scheduler nhận `ADMIN_DATABASE_URL`), `requirements.txt` (`feedparser`), `README.md` (mục 3, 7, 10, 16).

**Không đổi:** `config/vn-rules.yaml` (`llm.pipeline_enabled: false`, `weights`), điểm, nhãn, grading.
