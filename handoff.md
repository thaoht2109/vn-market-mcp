# Bàn giao — Giai đoạn 6: Cron trước phiên/tuần (đang dở)

Ngày: 2026-10-01. Phạm vi đang làm: hạng mục đầu tiên của Giai đoạn 6
("Nâng cao" — plan §11, dòng "6. Nâng cao") mà user chọn làm trước:
**cron trước phiên/tuần** (spec §4.2). 3 hạng mục còn lại của Giai đoạn 6
(vision chart, quét thị trường, cảnh báo intraday) **chưa bắt đầu**.

## Đã xong (code-complete, test pass)

### 1. Weekly deep-dive cron (Thứ 6, VN30-only, dùng `synthesis_full`)

- `ops/scheduler.py`: thêm `WEEKLY_JOB_TYPE = "scheduled_weekly"`, trigger
  Thứ 6 08:30 UTC, enqueue qua `pipeline.jobs.enqueue` giống `scheduled_post`
  (tái dùng toàn bộ hạ tầng queue/worker/advisory-lock có sẵn) nhưng chỉ cho
  tickers VN30 (`get_vn30_tickers()`, không gồm `watchlist_extra`).
- `pipeline/run_analysis.py`: nhánh LLM (`News → Bull/Bear/Verifier →
  Synthesis`) giờ chạy cho cả `mode in ("scheduled_post", "scheduled_weekly")`
  thay vì chỉ `scheduled_post`; khi `mode == "scheduled_weekly"` thì gọi
  `run_synthesis_daily(..., role_name="synthesis_full")` — role này đã khai
  báo sẵn trong `config/models.yaml` (effort cao hơn) nhưng trước đây
  **chưa có caller nào** gọi tới.
- `llm/synthesis.py`: thêm tham số `role_name: str = ROLE_NAME` vào
  `run_synthesis_daily()` (giữ hành vi mặc định không đổi — không phá API
  cũ).
- Test mới: `tests/test_scheduler.py::test_run_due_jobs_enqueues_weekly_on_friday_vn30_only`
  (xác nhận Thứ 6 → enqueue đúng VN30, KHÔNG enqueue watchlist_extra; dedup
  theo ngày hoạt động đúng).
- **Đã verify qua test suite** (203 unit test pass, không cần Docker vì
  logic enqueue không gọi LLM/vnstock thật). **Chưa verify end-to-end qua
  Docker/Hermes thật** (chưa chạy thử 1 lần thật để xem report weekly ra
  sao trên Telegram) — nên làm trước khi coi đây là xong hẳn.

### 2. Macro pre-market digest (tóm tắt vĩ mô/tin đêm qua)

Quyết định đã chốt với user: KHÔNG có nguồn tin vĩ mô/quốc tế riêng, nên
dùng tạm `provider.get_news()` gộp cho tất cả mã VN30 trong 24h qua làm
input cho role LLM `macro_daily` (role đã khai báo sẵn trong
`config/models.yaml` + có `schemas/macro.json` nhưng **trước đây hoàn toàn
chưa có schema lẫn caller** — đã tạo mới cả hai).

- `schemas/macro.json` (mới): output gồm `noteworthy` (bool — cho phép
  pipeline bỏ qua gửi report khi không có gì đáng chú ý, đúng tinh thần
  spec §4.2), `summary`, `news_refs` (chống bịa số liệu, giống pattern
  `news_digest`).
- `llm/macro.py` (mới): `run_macro_daily()` — validate digit trong
  `summary` (tái dùng `has_disallowed_digits`) và validate `news_refs` nằm
  trong batch tin đã đưa vào, giống hệt pattern `llm/news_digest.py`.
- `ops/scheduler.py`: `_run_macro_premarket_if_due()` — gọi trực tiếp
  (KHÔNG qua `pipeline.jobs.enqueue`/worker, vì đây là 1 báo cáo tổng thị
  trường chứ không gắn với 1 ticker cụ thể như các job khác) tại cùng giờ
  trigger `scheduled_pre` (01:30 UTC / 08:30 VN).
- Test mới: `tests/test_llm_macro.py` (4 test case, giống pattern
  `test_llm_news_digest.py`) + stub provider trong `test_scheduler.py` để
  tránh gọi `vnstock` thật trong unit test.

## ⚠️ VẤN ĐỀ PHÁT HIỆN KHI VERIFY THẬT — CHƯA SỬA XONG

Rebuild + chạy thử `scheduler` container thật (`docker compose up -d
scheduler`) để verify macro digest. Phát hiện:

**`provider.get_news(ticker, ...)` gọi tuần tự cho cả 30 mã VN30, không có
timeout/circuit-breaker ở tầng gọi.** Khi API `vietcap` (`iq.vietcap.com.vn`)
chập chờn, quan sát được **4 lần timeout liên tiếp, mỗi lần ~30s, vẫn đứng ở
cùng 1 mã** (log: `API request failed: ... Read timed out (read
timeout=30)` lặp lại 10:53:32 → 10:55:10, chưa thấy tiến triển sang mã
tiếp theo khi tôi dừng container để sửa).

Rủi ro thực tế: nếu `vnstock`/`Company.news()` tự retry nội bộ không giới
hạn rõ ràng, macro job có thể **treo cả vòng lặp chính của
`ops/scheduler.py`** hàng chục phút — vì `_run_macro_premarket_if_due()`
chạy đồng bộ ngay trong `run_due_jobs()`, cùng 1 process/1 thread với toàn
bộ logic enqueue `scheduled_pre`/`scheduled_post`/`scheduled_weekly`. Nếu
bị treo đúng lúc sắp tới giờ trigger `scheduled_post` (08:15 UTC) hoặc
weekly (Thứ 6 08:30 UTC), các cron đó sẽ bị trễ hoặc bỏ lỡ hoàn toàn.

**Đã sửa 1 phần** (chưa đủ): bọc `try/except Exception` quanh từng lệnh gọi
`provider.get_news(ticker, ...)` trong vòng `for` — một mã lỗi cứng
(exception ném ra ngay) sẽ không chặn các mã còn lại. Nhưng việc này
**không giải quyết được trường hợp treo** (API không raise exception, chỉ
chậm/retry nội bộ lâu) — đây mới là cái vừa quan sát thấy trên log thật.

### Việc cần làm tiếp (chưa làm)

Cách sửa đúng, chưa thực hiện — cần quyết định hướng trước khi code tiếp:

1. **Cách ly macro job khỏi scheduler loop** (khuyến nghị) — thay vì gọi
   trực tiếp trong `ops/scheduler.py`, enqueue nó như 1 "job" riêng
   (job_type mới, ví dụ `macro_premarket`, không gắn ticker cụ thể hoặc
   gắn ticker giả `"MARKET"`) và để `ops/worker.py` xử lý — worker đã có
   sẵn cơ chế bắt `SystemExit`/crash/retry-backoff cho đúng loại vấn đề
   này, và treo ở worker không ảnh hưởng tới vòng lặp enqueue của
   scheduler.
2. Hoặc thêm timeout cứng ở tầng gọi (ví dụ chạy trong thread/process con
   với deadline, hoặc giới hạn số mã lấy tin — ví dụ top 10 thanh khoản
   cao nhất thay vì cả 30 mã) để chặn trên tổng thời gian toàn bộ bước
   fetch tin.
3. Dù chọn hướng nào, cần verify lại bằng cách chạy thật qua Docker khi
   API nguồn tin đang chập chờn (hoặc giả lập bằng cách chặn DNS tạm thời
   tới `iq.vietcap.com.vn`) để chắc chắn scheduler không bị treo.

**Trạng thái container lúc bàn giao:** `vn-market-mcp-scheduler-1` đang
**stopped** (tôi chủ động dừng để sửa code, chưa rebuild/khởi động lại).
Cần `docker compose build scheduler && docker compose up -d scheduler`
sau khi chọn và áp dụng hướng sửa ở trên.

## Việc khác chưa làm trong Giai đoạn 6

- Vision chart (role `chart_vision`, hiện `enabled: false` trong
  `config/models.yaml`) — chưa động tới.
- Quét thị trường (mở rộng phân tích ra ngoài watchlist hiện tại) — chưa
  động tới, cần định nghĩa tiêu chí quét + giới hạn chi phí trước.
- Cảnh báo intraday — chưa động tới, khác biệt lớn với kiến trúc
  end-of-day hiện tại (cần nguồn dữ liệu intraday riêng).

## Ghi chú môi trường (để người kế tiếp không mất thời gian dò lại)

- Postgres container publish ở `127.0.0.1:55432` nhưng **sandbox mặc định
  của Claude Code chặn kết nối tới port đó** — mọi lệnh `pytest`/`docker
  compose` cần chạy với sandbox tắt (`dangerouslyDisableSandbox: true`
  trong Bash tool, hoặc chạy ngoài harness).
- `vn-market-mcp/.git` thực ra là gitdir pointer trỏ về
  `/home/anm/0_Projects/thaoht/.git` (repo gốc bao trùm nhiều project
  khác của user, không chỉ `vn-market-mcp`) — `git diff`/`git status` cần
  chạy đã lọc theo path `vn-market-mcp/...` hoặc `cd` đúng chỗ, nếu không
  sẽ thấy lẫn thay đổi của các project không liên quan (`1.Check_DNS_C2`,
  `3.Check_DNS_Tunnel_C2`, v.v. — KHÔNG đụng vào các thư mục đó).
- Test suite cần load `.env` trước khi chạy:
  `set -a; source .env; set +a; .venv/bin/python -m pytest tests/ -q`.
  203/203 pass tính đến thời điểm bàn giao.

## Lịch chạy hiện tại (cập nhật 2026-10-05; các ghi chú `scheduled_pre` ở trên là lịch cũ)

Nhãn chính thức của từng mã chỉ được tạo **một lần/ngày, trên giá đóng cửa đã chốt**.
Giờ VN (UTC+7), các phiên giao dịch:

| Giờ | Job | Ghi chú |
|---|---|---|
| 08:30 | `macro_premarket` | tin tức/vĩ mô qua đêm, không tạo dự đoán từng mã |
| 09:15, 11:00, 13:00 | `scheduled_intraday` | cập nhật giá tạm tính; không lưu dự đoán, chỉ hạ nhãn khi thủng cắt lỗ / biến động > 2 ATR |
| 15:05 | `close_sync` | chốt nến + khối ngoại, phát hiện vnstock điều chỉnh giá (GDKHQ) và tải lại lịch sử |
| 15:20 | `scheduled_post` | đánh giá chính thức, tạo dự đoán |
| 15:30 (T6) | `scheduled_weekly` | phân tích sâu theo tuần |
| 18:00 | `close_sync_retry` | chạy lại close_sync cho phần vnstock lỗi lúc 15:05 |

Quy tắc nhãn trong phiên: `pipeline/action_label.py::provisional_label`. Phân tích on-demand
trong phiên vẫn trả kết quả mới (snapshot có khối `session`), nhưng nâng nhãn chờ giá đóng cửa.
