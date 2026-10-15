# Bàn giao — vn-market-mcp, Giai đoạn 6 (Nâng cao)

Cập nhật: 2026-10-14. File `handoff.md` trước đó (2026-10-01) đã **lỗi
thời đáng kể** — rất nhiều việc đã được hoàn thiện sau đó (không rõ bởi
phiên làm việc nào, không phải phiên hiện tại). Bản này viết lại từ việc
đọc trực tiếp code + chạy test + xem log container thật, không dựa trên
giả định từ bản cũ.

## Trạng thái tổng quan

- **Test suite: 388/388 pass.** Chạy bằng `./run_tests.sh -q` (KHÔNG dùng
  `pytest` trực tiếp — script tự chuyển `DATABASE_URL` sang
  `vnmcp_test` và sẽ **từ chối chạy** nếu các biến env trỏ vào DB `vnmcp`
  thật, để tránh test ghi đè dữ liệu sản xuất).
- **Container thật đang chạy ổn định**: `postgres`, `scheduler`, `worker`
  (2 instance song song), `grading`, `retention` — tất cả `Up` liên tục
  5-13 ngày tính đến lúc kiểm tra, không crash-loop.
- **Quyết định kiến trúc lớn đã chốt (không phải việc của phiên này, phát
  hiện khi đọc lại code)**: toàn bộ LLM bên trong pipeline/worker (news
  digest, bull/bear/verifier, synthesis, macro digest) hiện bị **tắt mặc
  định** qua `config/vn-rules.yaml::llm.pipeline_enabled = false`
  (đọc bởi `pipeline/llm_gate.py::llm_pipeline_enabled()`). Thiết kế mới:
  worker/pipeline chỉ fetch dữ liệu vnstock vào Postgres + tính nhãn/chỉ
  báo thuần code (deterministic); **Hermes (chat) là nơi DUY NHẤT dùng
  LLM để suy luận, chỉ khi người dùng hỏi**. Bật lại bằng cách sửa cờ đó
  thành `true` nếu cần quay lại mô hình cron tự sinh report bằng LLM.

## Cron/job đã có (toàn bộ nằm trong `ops/scheduler.py` enqueue +
`ops/worker.py`/`pipeline/run_analysis.py` thực thi)

| job_type | Trigger | Phạm vi | Ghi chú |
|---|---|---|---|
| `scheduled_post` | 08:20 UTC (15:20 VN), các phiên | VN30 + watchlist_extra | Verdict chính thức MỘT lần/ngày trên giá đóng cửa; không còn job "trước phiên" chạy lại giá hôm qua (xoá hẳn `scheduled_pre` cũ) — tin đêm/qua đêm giờ thuộc về macro digest. |
| `scheduled_weekly` | Thứ 6, 08:30 UTC | Chỉ VN30 (không gồm watchlist_extra) | Phân tích sâu theo tuần (spec §4.2) — dùng `role_name="synthesis_full"` khi gọi `run_synthesis_daily()` (effort cao hơn `synthesis_daily`). **Lưu ý: nhánh LLM trong `run_analysis.py` giờ cũng bị cổng bởi `llm_pipeline_enabled()` — xem code trước khi coi job này "chạy đầy đủ".** |
| `scheduled_intraday` | 09:30 / 11:00 / 13:00 VN, ngày giao dịch | watchlist (có cơ chế "busy" tránh chồng job cùng ticker) | Làm mới giá/mức giá trong phiên, CHỈ hạ nhãn (provisional), không nâng. |
| `close_sync` | 08:05 UTC (15:05 VN) | Thị trường (ticker giả `MARKET`) | Ghi đè mọi bar trong phiên bằng OHLCV đóng cửa thật, phát hiện re-base giá, chốt khối ngoại. |
| `close_sync_retry` | 11:00 UTC (18:00 VN) | Thị trường | Retry cho mã `close_sync` lỗi — hiện re-sync lại TẤT CẢ ~30 mã (idempotent), đã đánh dấu `ponytail:` trong code là điểm có thể tối ưu sau. Dùng chung advisory lock với `close_sync` (`_SHARED_LOCK`) để không chạy song song tốn budget vnstock 2 lần. |
| `alert_check` | Mỗi 15 phút, 09:15–11:30 và 13:15–14:45 VN | Thị trường | Kiểm tra cảnh báo giá "touched" (entry_zone/stop_loss/target) trên giá trong phiên. |
| `collect_rss` | Mỗi giờ từ 06:00 VN | Thị trường | Thu thập tin tức RSS theo `config/news_sources.yaml` (mỗi nguồn có `every_minutes` riêng); sau khi fetch xong còn chạy `pipeline.collectors.indicators.run_all()`. |
| `macro_premarket` (hằng số `MACRO_JOB_TYPE` trong `llm/macro.py`) | 01:30 UTC (08:30 VN) | Thị trường | Tóm tắt tin VN30 qua đêm bằng role LLM `macro_daily`. **Bị cổng bởi `llm_pipeline_enabled()`** (xem `ops/scheduler.py::_run_macro_premarket_if_due`) — hiện tại KHÔNG chạy vì cờ đang `false`. |

Thứ tự ưu tiên claim job trong `pipeline/jobs.py`: `collect_rss` +
`alert_check` lên đầu (job thị trường nhanh, không được xếp sau hàng ~30
job ticker lúc 15:05–15:30), rồi `on_demand` (người dùng đang chờ trong
chat), rồi các job theo lịch còn lại.

### Lịch sử: vấn đề "macro job có thể treo scheduler" — ĐÃ SỬA

Bản handoff 2026-10-01 từng cảnh báo: `_run_macro_premarket_if_due()` gọi
trực tiếp `provider.get_news()` tuần tự cho 30 mã VN30 ngay trong vòng lặp
chính của `ops/scheduler.py`, và quan sát log thật cho thấy API
`iq.vietcap.com.vn` timeout liên tiếp, đứng ở cùng 1 mã nhiều lần — rủi ro
treo cả scheduler, trễ `scheduled_post`/`scheduled_weekly`.

**Đã sửa bằng đúng hướng khuyến nghị trong bản cũ**: giờ
`ops/scheduler.py::_run_macro_premarket_if_due()` chỉ `enqueue()` một job
`job_type=MACRO_JOB_TYPE, ticker="MARKET"` (dùng chung `_run_market_job_if_due`
helper với `close_sync`/`close_sync_retry`), và việc fetch tin + gọi LLM
thật sự diễn ra trong `ops/worker.py::_run_macro_premarket()` — một
process/thread khác hẳn vòng lặp enqueue của scheduler. Vẫn giữ nguyên
việc bọc `try/except Exception` quanh từng lệnh `get_news(ticker, ...)`
để một mã lỗi không chặn các mã còn lại. **Coi như đã giải quyết xong**,
không cần làm gì thêm trừ khi có log lỗi mới xuất hiện (không có gì trong
log 48h gần nhất — nhưng vì cờ `llm_pipeline_enabled=false`, job này hiện
không chạy nên đây chưa phải một lần verify "dưới tải thật").

## MCP tools phía Hermes (`mcp_server/tools/`)

Khớp với hướng kiến trúc mới ("Hermes tự suy luận khi hỏi"): đã có thêm
`advisor.py`, `digests.py`, `explain.py`, `macro.py`, `stock_report.py`,
`watchlist.py` bên cạnh các tool cũ (`run_analysis.py`, `snapshot.py`,
`predictions.py`, `positions.py`, `history.py`, `alerts.py`, `stats.py`).
**Chưa đọc kỹ nội dung từng tool này** — chỉ xác nhận sự tồn tại, chưa
verify hành vi.

## Việc CHƯA làm trong Giai đoạn 6 (xác nhận lại, tính đến giờ)

- **Vision chart** (role `chart_vision`) — chưa thấy dấu vết trong
  `config/models.yaml`/code khi khảo sát lần này; coi như vẫn chưa làm,
  cần xác nhận lại nếu muốn chắc chắn 100%.
- **Quét thị trường** (mở rộng phân tích ra ngoài watchlist để tìm mã mới
  đáng chú ý) — chưa thấy module nào tương ứng.
- **Cảnh báo intraday**: PHẦN NÀY THỰC RA ĐÃ CÓ — `scheduled_intraday` +
  `alert_check` (xem bảng trên) đã làm đúng tinh thần "theo dõi giá trong
  phiên và cảnh báo". Nên xác nhận lại với người yêu cầu ban đầu xem đã
  đủ hay còn thiếu ý gì trước khi coi mục này là "chưa làm".

## Việc tiếp theo nên làm, nếu quay lại Giai đoạn 6

1. Đọc kỹ `mcp_server/tools/advisor.py`, `digests.py`, `explain.py`,
   `macro.py`, `watchlist.py` để hiểu Hermes hiện đang trả lời người dùng
   bằng cách nào trong mô hình "pipeline chỉ có dữ liệu, LLM chỉ ở chat".
2. Xác nhận với người dùng có cần bật lại `llm.pipeline_enabled=true`
   (khôi phục macro/synthesis/bull-bear tự động qua cron) hay giữ nguyên
   thiết kế mới (Hermes suy luận on-demand) — đây là quyết định sản phẩm,
   không nên tự ý đổi.
3. Nếu giữ thiết kế mới, kiểm tra xem `scheduled_weekly` (dùng
   `synthesis_full`) còn ý nghĩa hay nên gỡ bỏ, vì nhánh LLM trong nó
   cũng bị cổng bởi cờ tắt.
4. Quyết định có cần vision chart / quét thị trường hay không trước khi
   code — hai mục này cần thêm nguồn dữ liệu/quyết định phạm vi, không
   phải việc "wiring" nhỏ.

## Ghi chú môi trường (giữ nguyên từ bản cũ, vẫn đúng)

- Postgres container publish ở `127.0.0.1:55432` nhưng **sandbox mặc định
  của Claude Code chặn kết nối tới port đó** — mọi lệnh `pytest`/`docker
  compose` cần chạy với sandbox tắt (`dangerouslyDisableSandbox: true`
  trong Bash tool, hoặc chạy ngoài harness).
- `vn-market-mcp/.git` là gitdir pointer trỏ về
  `/home/anm/0_Projects/thaoht/.git` (repo gốc bao trùm nhiều project
  khác của user) — `git diff`/`git status` cần lọc theo path
  `vn-market-mcp/...`, nếu không sẽ lẫn thay đổi của project khác
  (`1.Check_DNS_C2`, `3.Check_DNS_Tunnel_C2`, v.v. — KHÔNG đụng vào).
- **Luôn dùng `./run_tests.sh [pytest args]` để chạy test**, không gọi
  `pytest`/`.venv/bin/python -m pytest` trực tiếp với `.env` — script mới
  đã thêm safeguard tự chối chạy nếu phát hiện biến env trỏ vào DB
  `vnmcp` thật thay vì `vnmcp_test`.
