# vn-market-mcp

Đường ống dữ liệu và phân tích cổ phiếu Việt Nam (VN30 + danh sách mở rộng), cung cấp kết quả cho trợ lý chat **Hermes** qua giao thức **MCP**.

> **Tuyên bố miễn trừ:** Đây là công cụ hỗ trợ nghiên cứu, **không phải tư vấn đầu tư** và **không tự đặt lệnh**. Mọi rủi ro giao dịch do người dùng tự chịu.

## Mục lục

1. [Giới thiệu](#1-giới-thiệu)
2. [Kiến trúc](#2-kiến-trúc)
3. [Cấu trúc thư mục](#3-cấu-trúc-thư-mục)
4. [Yêu cầu](#4-yêu-cầu)
5. [Cài đặt](#5-cài-đặt)
6. [Vận hành](#6-vận-hành)
7. [Lịch chạy tự động](#7-lịch-chạy-tự-động)
8. [Xử lý dữ liệu theo phiên](#8-xử-lý-dữ-liệu-theo-phiên)
9. [Chấm điểm và nhãn hành động](#9-chấm-điểm-và-nhãn-hành-động)
10. [MCP server và Hermes](#10-mcp-server-và-hermes)
11. [Cấu hình](#11-cấu-hình)
12. [Kiểm thử](#12-kiểm-thử)
13. [Sao lưu và khôi phục](#13-sao-lưu-và-khôi-phục)
14. [Xử lý sự cố](#14-xử-lý-sự-cố)
15. [Hạn chế đã biết](#15-hạn-chế-đã-biết)

---

## 1. Giới thiệu

**Nguyên tắc thiết kế:** Python tính toán, LLM chỉ diễn giải.

- Mọi con số (chỉ báo, điểm, nhãn hành động, kế hoạch rủi ro) do code tính, không có LLM nào chọn nhãn.
- Nhãn hành động: `buy_accumulate` / `watch` / `hold` / `reduce_exit` / `stay_out`.
- Pipeline không gọi LLM (`llm.pipeline_enabled: false`). LLM duy nhất là Hermes, chỉ chạy khi người dùng hỏi. Hermes viết phần "Nhận định", và phần này phải qua bộ kiểm tra tất định trước khi được lưu hoặc gửi đi.
- **Fail-closed:** thiếu dữ liệu, lịch giao dịch hay độ phủ thì trả trạng thái lỗi rõ ràng, không đoán.

Tài liệu thiết kế: `../vn-trading-agent-plan_final.md`. Kế hoạch triển khai: `../docs/superpowers/plans/2026-09-30-vn-trading-agent-phase-0-1.md`.

## 2. Kiến trúc

```
                 vnstock (VCI: giá, BCTC · KBS: khối ngoại · tin tức)
                                     │
 scheduler ──enqueue──► jobs (Postgres) ◄──enqueue── MCP run_analysis
                                     │
                          worker ×2 (claim_next, advisory lock)
                                     │
        ingest → quality checks → indicators → scoring → action label → risk plan
                                     │
                 Postgres (runs, predictions, prices…) + snapshots/*.json
                                     │
                 MCP server (stdio) ──► Hermes (Telegram chat)
```

Các service trong `docker-compose.yml`:

| Service | Lệnh | Vai trò |
|---|---|---|
| `postgres` | Postgres 16, cổng trong `5433`, publish `127.0.0.1:55432` | Cơ sở dữ liệu |
| `worker` (2 bản sao) | `python -m ops.worker` | Lấy job từ hàng đợi, chạy pipeline, chạy close_sync |
| `scheduler` | `python -m ops.scheduler` | Quyết định khi nào chạy và chạy cho mã nào, chỉ enqueue |
| `grading` | `python -m ops.grading_job` | Chấm dự báo tại các mốc 20/60/120 phiên (mỗi giờ) |
| `retention` | `python -m ops.retention_job` | Dọn dữ liệu theo chính sách (mỗi tuần) |

MCP server **không** chạy trong compose này. Hermes gateway khởi chạy nó qua stdio (xem [mục 10](#10-mcp-server-và-hermes)).

## 3. Cấu trúc thư mục

| Đường dẫn | Nội dung |
|---|---|
| `providers/vnstock_provider.py` | Wrapper vnstock, chuẩn hóa đơn vị giá về VND |
| `pipeline/calendar.py` | Lịch giao dịch, xác định phiên mới nhất và phiên tạm tính |
| `pipeline/ingest.py` | Ghi OHLCV, BCTC, khối ngoại, tin tức (idempotent); `sync_recent_prices` |
| `quality/checks.py` | Kiểm tra chất lượng: đủ dữ liệu, schema, đơn vị giá, biến động bất thường, khối lượng, độ mới |
| `pipeline/indicators.py` | MA/EMA/RSI/MACD/Bollinger/ATR |
| `pipeline/fundamentals.py` | Chỉ số cơ bản theo nhóm ngành, định giá so với lịch sử và nhóm ngành |
| `pipeline/scoring.py` | Điểm kỹ thuật, dòng tiền, điểm tổng hợp, độ tin cậy |
| `pipeline/regime.py` | Trạng thái thị trường và độ rộng (breadth) VN30 |
| `pipeline/action_label.py` | Nhãn hành động và nhãn tạm tính trong phiên |
| `pipeline/risk_plan.py` | Stop-loss theo ATR, R:R, khối lượng theo biên độ sàn |
| `pipeline/coverage.py` | Nhận diện mã, kiểm tra độ phủ dữ liệu cho mã ngoài VN30 |
| `pipeline/run_analysis.py` | Điều phối toàn bộ pipeline cho một mã, kèm CLI |
| `pipeline/stock_report.py` | Render báo cáo cổ phiếu bằng code |
| `pipeline/jobs.py`, `pipeline/grading.py` | Hàng đợi job, chấm dự báo |
| `mcp_server/` | MCP server và 16 tool |
| `ops/` | worker, scheduler, grading, retention, alerting, backfill/seed, backup |
| `db/` | Migrations (`001`–`014`), tạo role, tạo DB test |
| `llm/`, `schemas/` | Các vai trò LLM trong pipeline (đang **tắt**, giữ lại để bật sau) |
| `evals/` | Bộ so sánh mô hình phân loại tin (chạy tay) |
| `.hermes/skills/vn-market/vn-stock-analyze/` | Skill định tuyến câu hỏi chat sang các tool MCP |
| `config/` | `vn-rules.yaml` (ngưỡng nghiệp vụ), `models.yaml` (mô hình LLM) |

## 4. Yêu cầu

- Docker và Docker Compose
- Python 3.11+ (chỉ cần khi chạy test hoặc lệnh trên host)
- API key vnstock (Community tier). Gói `vnstock`/`vnai` cài từ index riêng `https://vnstocks.com/api/simple`, không có trên PyPI công khai.
- Bot Telegram và nhóm ops để nhận cảnh báo (tùy chọn)

## 5. Cài đặt

**5.1. Tạo file môi trường**

```bash
cp .env.example .env && chmod 600 .env
```

Điền các biến `MCP_RO_PASSWORD`, `PIPELINE_RW_PASSWORD`, `RETENTION_JOB_PASSWORD`, `*_DATABASE_URL`, `VNSTOCK_API_KEY`, `TELEGRAM_BOT_TOKEN` và `TELEGRAM_ALERT_CHAT_ID`.

**5.2. Khởi động Postgres, chạy migration, tạo role**

```bash
docker compose up -d postgres
set -a; . ./.env; set +a
python -c "from db.connection import get_conn; from db.migrate import apply_migrations; from pathlib import Path; c=get_conn().__enter__(); print(apply_migrations(c, Path('db/migrations'))); c.commit()"
python -m db.setup_roles
```

Lệnh tạo role sẽ tạo `mcp_ro` (chỉ đọc), `pipeline_rw` và `retention_job` (SELECT và DELETE).

**5.3. Nạp dữ liệu gốc (chạy một lần, trong venv có vnstock)**

```bash
python ops/seed_market_data.py   # tickers, thành phần VN30, lịch giao dịch
python ops/backfill_prices.py    # lịch sử giá và BCTC (cần ≥500 phiên, ≥4 quý)
```

Chạy lại `seed_market_data.py` khi rổ VN30 thay đổi, và ít nhất mỗi năm một lần để lịch giao dịch phủ năm mới.

**5.4. Chạy toàn bộ hệ thống**

```bash
docker compose up -d --build
```

## 6. Vận hành

Phân tích một mã theo yêu cầu (nên chạy trong container để dùng địa chỉ mạng nội bộ ổn định):

```bash
docker compose exec worker python -m pipeline.run_analysis VNM --style long --depth quick
```

Các bước pipeline thực hiện:

1. Nhận diện mã.
2. Ghi dữ liệu giá, BCTC, khối ngoại và tin tức.
3. Kiểm tra chất lượng dữ liệu.
4. Kiểm tra độ phủ dữ liệu.
5. Tính chỉ báo và điểm.
6. Gán nhãn và lập kế hoạch rủi ro.
7. Ghi kết quả vào `runs`/`predictions` và `snapshots/<run_id>.json`.

Cảnh báo được gửi tới nhóm ops trên Telegram (`ops/alerting.py`):

- Mọi lỗi job, `data_quality_error` và `insufficient_coverage`.
- Kết quả của job `on_demand`.

Job chạy theo lịch không gửi tin nhắn để tránh spam.

Áp dụng migration mới cho container đang chạy:

```bash
docker compose exec worker python -c "from db.connection import get_conn; from db.migrate import apply_migrations; from pathlib import Path; c=get_conn().__enter__(); print(apply_migrations(c, Path('db/migrations'))); c.commit()"
```

## 7. Lịch chạy tự động

Scheduler chỉ chạy vào ngày giao dịch. Giờ dưới đây là giờ Việt Nam.

| Giờ | Job | Mô tả |
|---|---|---|
| 08:30 | `macro_premarket` | Bản tin vĩ mô trước phiên. **Chỉ chạy khi** `llm.pipeline_enabled: true` |
| 09:15, 11:00, 13:00 | `scheduled_intraday` | Làm mới số liệu trong phiên (**tạm tính**). Mốc đầu là 09:15 vì phiên ATO chưa có nến khớp |
| 15:05 | `close_sync` | Ghi đè nến giữa phiên bằng giá đóng cửa, chốt khối ngoại, phát hiện vendor điều chỉnh giá |
| 15:20 | `scheduled_post` | **Kết luận chính thức** trong ngày, tính trên giá đóng cửa |
| 15:30 (thứ Sáu) | `scheduled_weekly` | Phân tích sâu theo tuần cho VN30 |
| 18:00 | `close_sync_retry` | Chạy lại close_sync cho các mã vnstock bị lỗi lúc 15:05 |

## 8. Xử lý dữ liệu theo phiên

- **Phiên mới nhất** (`latest_trading_day`) được tính theo giờ Việt Nam. Trước 09:15, phiên mới nhất là phiên **trước đó**, nên chạy lúc 08:30 không còn báo thiếu dữ liệu ngày hôm nay.
- **Trong phiên** (`is_provisional_session`): nến hôm nay là ảnh chụp tạm thời. `provisional_label` áp dụng các quy tắc sau:
  - Chỉ **hạ** nhãn về `reduce_exit`/`stay_out` khi giá thủng stop hoặc biến động vượt 2 ATR.
  - **Không bao giờ nâng** lên mua trước giờ đóng cửa.
  - Nếu chưa có nhãn chính thức, `buy` bị giới hạn ở mức `watch`.
  - Snapshot ghi lại `session.live_label` và `session.official_label`, và báo cáo hiển thị cả hai.
- **Sau phiên:** `sync_recent_prices` lấy lại 10 ngày gần nhất. Nếu một nến đã chốt lệch với vendor quá 0,1% (dấu hiệu vnstock điều chỉnh giá do cổ tức hoặc chia tách), toàn bộ lịch sử của mã đó được tải lại.
- **Cache cho chat:**
  - Trong giờ giao dịch, kết quả được tái sử dụng tối đa `snapshot_cache.max_age_minutes` phút.
  - Ngoài giờ, chỉ tái sử dụng kết quả chạy **sau** 15:00, có hiệu lực tới phiên kế tiếp.

## 9. Chấm điểm và nhãn hành động

Điểm tổng hợp là trung bình có trọng số (`weights.long` trong `vn-rules.yaml`) của các thành phần có dữ liệu:

| Thành phần | Cách tính |
|---|---|
| Kỹ thuật | Xu hướng MA20/50/200, MACD, vùng RSI |
| Định giá cơ bản | P/E, P/B so với ≥3 quý lịch sử của chính mã và ≥3 mã cùng ngành, cộng với ROE |
| Dòng tiền | Khối ngoại ròng 5 phiên chia giá trị giao dịch (±10% tương ứng 100/0 điểm, cần ≥3 phiên) |
| Thị trường | Trạng thái thị trường, độ rộng VN30 (số mã tăng/giảm, % mã trên MA50, thanh khoản) |

Thành phần thiếu dữ liệu sẽ bị bỏ qua. Nếu độ phủ trọng số dưới `coverage.min_weight_coverage` thì mã được xử lý fail-closed.

Mã ngoài VN30 phải đạt các ngưỡng sau:

- ≥500 phiên giá.
- Thanh khoản trung bình 20 phiên ≥5 tỷ.
- ≥4 quý BCTC.

Các ngưỡng nhãn nằm ở `action_labels` trong `vn-rules.yaml`.

## 10. MCP server và Hermes

`python -m mcp_server.server` (stdio) cung cấp 16 tool. Tool chỉ đọc dùng role `mcp_ro`, tool ghi dùng role `pipeline_rw`.

| Tool | Chức năng |
|---|---|
| `run_analysis` | Đưa yêu cầu phân tích vào hàng đợi (trả `job_id`). Nếu cache còn hiệu lực thì trả kết quả ngay |
| `get_job_status` | Trạng thái job |
| `get_snapshot` | Snapshot theo `ticker` hoặc `run_id` |
| `get_stock_report` | Báo cáo do code render, kèm "Nhận định" đã lưu nếu số liệu chưa đổi |
| `save_commentary` | Lưu "Nhận định" của Hermes sau khi qua bộ kiểm tra tất định (`verify_commentary`) |
| `query_history` | Lịch sử `prices` / `fundamentals` / `foreign_flow` |
| `explain_run` | Giải thích một lần chạy (stop-loss, lý do nhãn) |
| `list_predictions`, `get_stats` | Danh sách dự báo, thống kê chấm điểm |
| `set_position`, `clear_position` | Khai báo hoặc xóa trạng thái "đang nắm giữ" |
| `watch_ticker`, `unwatch_ticker`, `list_watchlist` | Danh sách theo dõi riêng từng người dùng (`declared_by`). Mã được theo dõi, kể cả ngoài VN30, được phân tích theo lịch cùng VN30 |
| `get_market_digest_input`, `get_weekly_digest_input` | Dữ liệu đầu vào cho bản tin trước phiên và bản tin tuần |

Bộ kiểm tra `verify_commentary` từ chối "Nhận định" trong các trường hợp:

- Có con số không xuất hiện trong báo cáo.
- Lạc quan hơn nhãn hệ thống.
- Dùng thuật ngữ nội bộ.
- Độ dài ngoài khoảng 80–220 từ.

**Kết nối Hermes** (gateway chạy bằng `../hermes_agent/hermes-docker-compose`):

- Gateway mount dự án này ở chế độ chỉ đọc tại `/opt/vn-market-mcp` và tham gia mạng `vn-market-mcp_default`. URL kết nối DB phải dùng `postgres:5433`, **không** dùng `5432` vì cổng này bị một rule iptables trên host chặn.
- Venv riêng nằm ở `/opt/data/vn-market-mcp-venv`, được dựng bởi `ops/setup_venv.sh` (init container `mcp-venv-init`).
- Wrapper `run-vn-market-mcp` phải `cd /opt/vn-market-mcp` trước khi chạy, để tránh trùng tên package `providers` với package nội bộ của Hermes.
- Đăng ký và kiểm tra:

  ```bash
  docker compose exec gateway hermes mcp add vn-market-mcp \
    --command /opt/data/vn-market-mcp-venv/bin/run-vn-market-mcp \
    --env MCP_RO_DATABASE_URL=postgresql://mcp_ro:<pw>@postgres:5433/vnmcp \
          PIPELINE_RW_DATABASE_URL=postgresql://pipeline_rw:<pw>@postgres:5433/vnmcp \
    --connect-timeout 60
  docker compose exec gateway hermes mcp test vn-market-mcp
  ```

- Stdout của MCP stdio chỉ được chứa JSON-RPC. Mọi log đi qua stderr (`ops/alerting.py`).
- Skill `vn-stock-analyze` chỉ được nạp khi thư mục có tổ tiên `.git`. File gitlink `vn-market-mcp/.git` (nội dung `gitdir: ../../.git`) đảm nhiệm việc này. File này **không được Git theo dõi**, nên khi clone mới phải tạo lại bằng tay, và **đừng xóa**. Nạp skill bằng: `hermes skills trust /opt/vn-market-mcp`.

Có thể kiểm thử không cần Hermes bằng MCP Inspector: `npx @modelcontextprotocol/inspector python -m mcp_server.server`.

## 11. Cấu hình

- **`config/vn-rules.yaml`** chứa toàn bộ ngưỡng nghiệp vụ:
  - Độ phủ, nhãn hành động, trọng số.
  - Biên độ sàn HOSE/HNX/UPCoM, lô 100.
  - Giờ giao dịch, cache, các mốc chấm dự báo và phí.
  - Hệ số đơn vị giá theo nguồn, cờ `llm.pipeline_enabled`.

  Chỉ thay đổi file này qua Git có review. Không hardcode ngưỡng trong code.
- **`config/models.yaml`** khai báo mô hình cho các vai trò LLM trong pipeline. Hiện không dùng vì pipeline đang tắt LLM.
- **Bật lại vai trò LLM trong pipeline:**
  1. Đặt `llm.pipeline_enabled: true`.
  2. Thêm khóa provider (`ANTHROPIC_API_KEY`, `DEEPSEEK_API_KEY`, …) vào service `worker` trong `docker-compose.yml`.

## 12. Kiểm thử

```bash
./run_tests.sh                              # toàn bộ
./run_tests.sh tests/test_scheduler.py -q   # một file
```

`run_tests.sh` luôn chạy trên DB riêng `vnmcp_test` (tự tạo), **không bao giờ** chạm vào DB thật `vnmcp`. Test không gọi vnstock hay LLM thật.

## 13. Sao lưu và khôi phục

```bash
./ops/backup.sh         # pg_dump vào ./backups/
./ops/restore_test.sh   # khôi phục bản mới nhất vào DB tạm, so số dòng, rồi xóa
```

Cần client `pg_dump` phiên bản 16. Nếu host khác phiên bản, chạy trong container `postgres`.

## 14. Xử lý sự cố

| Triệu chứng | Nguyên nhân / cách xử lý |
|---|---|
| `data_quality_error: missing_tickers` lúc trước 09:15 | Đã sửa: trước 09:15 hệ thống dùng phiên trước. Nếu vẫn gặp, kiểm tra lịch giao dịch đã được seed tới hôm nay chưa |
| `insufficient_coverage` với mã mới | Chưa đủ 500 phiên hoặc 4 quý. Chạy `ops/backfill_prices.py` |
| Báo cáo ghi "tạm tính trong phiên" | Đúng thiết kế. Kết luận chính thức có sau 15:20 |
| Giá lịch sử có khoảng trống giả quanh ngày GDKHQ | Vendor đã điều chỉnh giá. close_sync tự tải lại; kiểm tra log `close_sync_done` → `rebased` |
| Kết nối DB treo tới timeout | URL đang dùng `postgres:5432`. Đổi sang `postgres:5433` |
| Hermes báo `Failed to parse JSONRPC message` | Có code in ra stdout. Mọi log phải đi qua stderr |
| `worker_job_rate_limited` trong log | Chạm giới hạn vnstock. Worker tự chờ 65 giây. Không nên tăng số bản sao worker |

## 15. Hạn chế đã biết

- Lịch giao dịch được seed theo ngày trong tuần, **chưa loại trừ ngày lễ** Việt Nam.
- `pipeline/theses.py` chưa được nối vào pipeline, nên `thesis_invalidated` luôn là `False`.
- `retention_job` chưa có chính sách xóa nào, mới chỉ dựng sẵn hạ tầng.
- Các vai trò LLM trong `llm/` (news digest, bull/bear, verifier, synthesis, macro) đang tắt và được giữ lại để đánh giá.
- Dữ liệu khối ngoại của KBS chỉ có giá trị "hiện tại", không có lịch sử. Giá trị cuối ngày được chốt tại close_sync.
