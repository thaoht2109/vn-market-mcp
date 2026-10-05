# vn-market-mcp

Đường ống dữ liệu và phân tích cổ phiếu Việt Nam, cung cấp kết quả cho trợ lý chat **Hermes** qua giao thức **MCP**. Hệ thống phân tích VN30 cùng mọi mã niêm yết mà người dùng yêu cầu hoặc theo dõi. Nhiều người dùng có thể dùng chung một hệ thống mà dữ liệu cá nhân (vị thế, danh sách theo dõi, lịch sử chat, kết quả gửi về) không bị chia sẻ cho nhau.

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

Mục 10 có phần [Nhiều người dùng](#nhiều-người-dùng-dữ-liệu-tách-riêng).

---

## 1. Giới thiệu

**Nguyên tắc thiết kế:** Python tính toán, LLM chỉ diễn giải.

- Mọi con số (chỉ báo, điểm, nhãn hành động, kế hoạch rủi ro) do code tính, không có LLM nào chọn nhãn.
- Nhãn hành động: `buy_accumulate` / `watch` / `hold` / `reduce_exit` / `stay_out`.
- Pipeline không gọi LLM (`llm.pipeline_enabled: false`). LLM duy nhất là Hermes, chỉ chạy khi người dùng hỏi. Hermes viết phần "Nhận định", và phần này phải qua bộ kiểm tra tất định trước khi được lưu hoặc gửi đi.
- **Fail-closed:** thiếu dữ liệu, lịch giao dịch hay độ phủ thì trả trạng thái lỗi rõ ràng, không đoán.
- **Dữ liệu chung, góc nhìn riêng:** mỗi mã chỉ được tải và phân tích một lần cho mọi người dùng, và nhãn lưu lại không phụ thuộc vị thế của ai. Phần riêng của từng người (vị thế, danh sách theo dõi, nhãn theo vị thế, kết quả gửi về) được tách theo `user_id`.

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
         MCP server (stdio, mỗi Hermes profile một tiến trình, VNMCP_USER_ID)
                                     │
               Hermes gateway: profile default · profile của từng người
                                     │
     Telegram: mỗi người một bot riêng → profile riêng; bot chung → default (nhóm chung)

 worker ──kết quả on_demand──► bot + chat riêng của người yêu cầu (bảng users)
        ──lỗi vận hành──────► nhóm ops (TELEGRAM_ALERT_CHAT_ID)
```

Các service trong `docker-compose.yml`:

| Service | Lệnh | Vai trò |
|---|---|---|
| `postgres` | Postgres 16, cổng trong `5433`, publish `127.0.0.1:55432` | Cơ sở dữ liệu |
| `worker` (2 bản sao) | `python -m ops.worker` | Lấy job từ hàng đợi, chạy pipeline, chạy close_sync, gửi kết quả về chat riêng của người yêu cầu |
| `scheduler` | `python -m ops.scheduler` | Quyết định khi nào chạy và chạy cho mã nào (VN30 + mã người dùng theo dõi), chỉ enqueue |
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
| `pipeline/action_label.py` | Nhãn hành động, nhãn tạm tính trong phiên, nhãn theo vị thế (`personal_label`) |
| `pipeline/positions.py` | Vị thế theo từng người dùng; `personalize` áp góc nhìn của một người lên kết quả chung |
| `pipeline/risk_plan.py` | Stop-loss theo ATR, R:R, khối lượng theo biên độ sàn |
| `pipeline/coverage.py` | Nhận diện mã (tự đăng ký mã niêm yết chưa có trong DB), kiểm tra độ phủ dữ liệu |
| `pipeline/run_analysis.py` | Điều phối toàn bộ pipeline cho một mã, kèm CLI |
| `pipeline/stock_report.py` | Render báo cáo cổ phiếu bằng code |
| `pipeline/jobs.py`, `pipeline/grading.py` | Hàng đợi job, chấm dự báo |
| `mcp_server/` | MCP server và 16 tool; `identity.py` xác định người gọi (`VNMCP_USER_ID`) |
| `ops/` | worker, scheduler, grading, retention, alerting, backfill/seed, backup, `add_user.sh` / `remove_user.sh` (thêm / xóa người dùng) |
| `db/` | Migrations (`001`–`016`), tạo role, tạo DB test |
| `llm/`, `schemas/` | Các vai trò LLM trong pipeline (đang **tắt**, giữ lại để bật sau) |
| `evals/` | Bộ so sánh mô hình phân loại tin (chạy tay) |
| `.hermes/skills/vn-market/vn-stock-analyze/` | Skill duy nhất cho phân tích cổ phiếu VN: định tuyến câu hỏi sang tool MCP, giọng văn, quy trình báo cáo. Dùng chung, chỉ đọc cho mọi profile |
| `config/` | `vn-rules.yaml` (ngưỡng nghiệp vụ), `models.yaml` (mô hình LLM) |
| `secrets/users.env` | Token bot riêng của từng người cho worker (`ops/add_user.sh` ghi, mount chỉ đọc vào worker). Không commit, không vào image |

## 4. Yêu cầu

- Docker và Docker Compose
- Python 3.11+ (chỉ cần khi chạy test hoặc lệnh trên host)
- API key vnstock (Community tier). Gói `vnstock`/`vnai` cài từ index riêng `https://vnstocks.com/api/simple`, không có trên PyPI công khai.
- Bot Telegram và nhóm ops để nhận cảnh báo (tùy chọn)
- Khi có nhiều người dùng: mỗi người một bot Telegram riêng (tạo qua @BotFather)

## 5. Cài đặt

**5.1. Tạo file môi trường**

```bash
cp .env.example .env && chmod 600 .env
```

Điền các biến `MCP_RO_PASSWORD`, `PIPELINE_RW_PASSWORD`, `RETENTION_JOB_PASSWORD`, `*_DATABASE_URL`, `VNSTOCK_API_KEY`, `TELEGRAM_BOT_TOKEN` và `TELEGRAM_ALERT_CHAT_ID`.

`TELEGRAM_BOT_TOKEN` là bot chung (nhóm chung, cảnh báo vận hành). Token bot riêng của từng người nằm ở `secrets/users.env`, do `ops/add_user.sh` ghi (xem [mục 10](#nhiều-người-dùng-dữ-liệu-tách-riêng)); worker vẫn chạy khi file này trống.

**5.2. Khởi động Postgres, chạy migration, tạo role**

```bash
docker compose up -d postgres
set -a; . ./.env; set +a
python -c "from db.connection import get_conn; from db.migrate import apply_migrations; from pathlib import Path; c=get_conn().__enter__(); print(apply_migrations(c, Path('db/migrations'))); c.commit()"
python -m db.setup_roles
```

Lệnh tạo role sẽ tạo `mcp_ro` (chỉ đọc), `pipeline_rw` (SELECT, INSERT, UPDATE) và `retention_job` (SELECT và DELETE). Quyền được cấp trên các bảng **đang có**, nên sau mỗi migration tạo bảng mới (ví dụ `016` tạo `users`) phải chạy lại `python -m db.setup_roles`.

**5.3. Nạp dữ liệu gốc (chạy một lần, trong venv có vnstock)**

```bash
python ops/seed_market_data.py   # tickers VN30, thành phần VN30, lịch giao dịch
python ops/backfill_prices.py    # lịch sử giá và BCTC (cần ≥500 phiên, ≥4 quý)
```

Chạy lại `seed_market_data.py` khi rổ VN30 thay đổi, và ít nhất mỗi năm một lần để lịch giao dịch phủ năm mới. Mã ngoài VN30 không cần seed hay backfill tay: lần phân tích đầu tiên tự đăng ký và tải lịch sử (xem [mục 6](#6-vận-hành)).

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

1. Nhận diện mã. Mã chưa có trong bảng `tickers` được tra trong danh sách niêm yết của vnstock (`VNStockProvider.lookup_listing`). Nếu đang niêm yết, mã được tự đăng ký (tên, sàn; `HSX` ghi thành `HOSE`). Nếu không, trả `unknown_ticker` kèm gợi ý mã gần giống.
2. Ghi dữ liệu giá, BCTC, khối ngoại và tin tức:
   - Mã có dưới 500 phiên giá trong DB: tải khoảng 3 năm lịch sử (`HISTORY_BACKFILL_DAYS`), đủ cho ngưỡng độ phủ.
   - Mã đã đủ lịch sử: chỉ tải từ phiên cuối đã lưu tới hôm nay, để lấp khoảng trống nếu mã lâu không được phân tích.
   - BCTC chỉ làm mới tối đa mỗi 24 giờ.
3. Kiểm tra chất lượng dữ liệu.
4. Kiểm tra độ phủ dữ liệu.
5. Tính chỉ báo và điểm.
6. Gán nhãn và lập kế hoạch rủi ro.
7. Ghi kết quả vào `runs`/`predictions` và `snapshots/<run_id>.json`.

Tin nhắn Telegram do worker gửi (`ops/worker.py`, `ops/alerting.py`):

| Sự kiện | Gửi tới |
|---|---|
| Kết quả job `on_demand` (`run_analysis`, `watch_ticker`) từ bot riêng của một người dùng, và lỗi của nó | Chat của người đó (`users.chat_id`), qua bot riêng của họ. Nhãn trong tin đã tính theo vị thế của người đó |
| Kết quả `on_demand` từ nhóm chung (profile `default`), hoặc từ người chưa có dòng trong `users` | Nhóm ops (`TELEGRAM_ALERT_CHAT_ID`) |
| Job crash, `data_quality_error` của job theo lịch | Nhóm ops |
| `insufficient_coverage` của job theo lịch | Không gửi: mã nhỏ được theo dõi sẽ báo lỗi này mỗi phiên, đó là bình thường |
| Kết quả job theo lịch | Không gửi, để tránh spam. Xem bằng `/danhsach` hoặc `get_snapshot` |

Người đã đăng ký mà token bot trống hoặc gửi lỗi thì tin bị bỏ (log `alert_skipped`/`alert_send_failed`), **không** chuyển sang nhóm ops, để kết quả của một người không lọt sang chat chung.

Mỗi job `on_demand` ghi người yêu cầu vào `jobs.requested_by`: `user:<id>` (từ `run_analysis` của profile có `VNMCP_USER_ID`), `watch:<id>` (từ `watch_ticker`), `mcp` (không xác định được người) hoặc `cron`.

Áp dụng migration mới cho container đang chạy:

```bash
docker compose exec worker python -c "from db.connection import get_conn; from db.migrate import apply_migrations; from pathlib import Path; c=get_conn().__enter__(); print(apply_migrations(c, Path('db/migrations'))); c.commit()"
```

## 7. Lịch chạy tự động

Scheduler chỉ chạy vào ngày giao dịch. Giờ dưới đây là giờ Việt Nam.

| Giờ | Job | Mô tả |
|---|---|---|
| 08:30 | `macro_premarket` | Bản tin vĩ mô trước phiên. **Chỉ chạy khi** `llm.pipeline_enabled: true` |
| 09:15, 11:00, 13:00 | `scheduled_intraday` | Làm mới số liệu trong phiên (**tạm tính**) cho danh sách theo lịch. Mốc đầu là 09:15 vì phiên ATO chưa có nến khớp |
| 15:05 | `close_sync` | Ghi đè nến giữa phiên bằng giá đóng cửa, chốt khối ngoại, phát hiện vendor điều chỉnh giá. Áp dụng cho mọi mã có nến trong 10 ngày gần nhất |
| 15:20 | `scheduled_post` | **Kết luận chính thức** trong ngày cho danh sách theo lịch, tính trên giá đóng cửa |
| 15:30 (thứ Sáu) | `scheduled_weekly` | Phân tích sâu theo tuần cho VN30 |
| 18:00 | `close_sync_retry` | Chạy lại close_sync cho các mã vnstock bị lỗi lúc 15:05 |

**Danh sách theo lịch** (`ops.scheduler.get_watchlist`) gồm VN30 hợp với mọi mã đang được **ít nhất một** người dùng theo dõi (`watchlist_extra`, `status = 'active'`). Một mã được nhiều người theo dõi vẫn chỉ phân tích một lần mỗi mốc. Bản tuần chỉ chạy cho VN30.

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

Mã ngoài VN30 (nhóm B) phải đạt các ngưỡng sau, và độ tin cậy bị giới hạn ở `tier_b.confidence_cap` (0,6):

- ≥500 phiên giá.
- Thanh khoản trung bình 20 phiên ≥5 tỷ.
- ≥4 quý BCTC.

Vì vậy mã thanh khoản thấp thường nhận `insufficient_coverage` dù dữ liệu đã được tải đủ.

Các ngưỡng nhãn nằm ở `action_labels` trong `vn-rules.yaml`.

**Nhãn chung và nhãn theo vị thế.** Kết quả phân tích dùng chung cho mọi người dùng, nên pipeline luôn gán nhãn như với người **chưa khai báo vị thế** (`holding_state = unknown`); nhãn này được lưu trong `predictions` và snapshot. Khi một người đọc kết quả, `pipeline.positions.personalize` tính nhãn theo vị thế của chính họ (`personal_label`, khớp nhánh "đang giữ" của `action_label`):

| Nhãn chung | Người không giữ mã | Người đang giữ mã |
|---|---|---|
| Không có nhãn (thiếu độ phủ) | Không có nhãn | Không có nhãn |
| Dữ liệu cũ hoặc độ tin cậy dưới `min_confidence_floor` | `stay_out` | `stay_out` |
| Điểm < `reduce_exit.max_score` (40), hoặc nhãn `reduce_exit` | giữ nguyên | `reduce_exit` |
| `stay_out` dù điểm ≥ `watch.min_score` (bị hạ nhãn) | `stay_out` | `reduce_exit` |
| Các trường hợp còn lại (`buy_accumulate`, `watch`, `stay_out` với điểm 40–55) | giữ nguyên | `hold` |

Nhãn theo vị thế được áp dụng ở `get_snapshot`, `get_stock_report`, `list_watchlist` và tin nhắn kết quả của worker. Snapshot lưu thêm `data_stale` để tính được bảng trên; snapshot không có trường điểm (file cũ hoặc đã bị xóa) thì giữ nguyên nhãn chung. `list_predictions` và `get_stats` luôn trả nhãn chung.

## 10. MCP server và Hermes

`python -m mcp_server.server` (stdio) cung cấp 16 tool. Tool chỉ đọc dùng role `mcp_ro`, tool ghi dùng role `pipeline_rw`.

**Người gọi là ai** (`mcp_server/identity.py`): là `VNMCP_USER_ID` của profile đã chạy MCP server, và chỉ là biến đó. Tool không nhận id người dùng làm tham số. Server không có biến này (profile `default`, phục vụ nhóm chung) thì tool phân tích chạy bình thường, còn tool danh mục riêng trả `status="no_personal_scope"` kèm cảnh báo, không ghi gì vào DB.

| Tool | Chức năng |
|---|---|
| `run_analysis` | Đưa yêu cầu phân tích vào hàng đợi (trả `job_id`). Nếu cache còn hiệu lực thì trả kết quả ngay. Mã ngoài VN30 đang niêm yết cũng được phân tích |
| `get_job_status` | Trạng thái job |
| `get_snapshot` | Snapshot theo `ticker` hoặc `run_id`. Có `VNMCP_USER_ID` thì nhãn và `holding_state` tính theo vị thế người gọi |
| `get_stock_report` | Báo cáo do code render (nhãn theo vị thế người gọi), kèm "Nhận định" đã lưu nếu số liệu chưa đổi |
| `save_commentary` | Lưu "Nhận định" của Hermes sau khi qua bộ kiểm tra tất định (`verify_commentary`) |
| `query_history` | Lịch sử `prices` / `fundamentals` / `foreign_flow` |
| `explain_run` | Giải thích một lần chạy (stop-loss, lý do nhãn) |
| `list_predictions`, `get_stats` | Danh sách dự báo, thống kê chấm điểm |
| `set_position`, `clear_position` | Khai báo hoặc xóa trạng thái "đang nắm giữ" của người đang chat (khóa `(ticker, declared_by)` = `VNMCP_USER_ID`). Không làm thay đổi nhãn chung hay nhãn người khác thấy. Nhóm chung: `no_personal_scope` |
| `watch_ticker`, `unwatch_ticker`, `list_watchlist` | Danh sách theo dõi riêng từng người (`watchlist_extra`, khóa `(ticker, added_by)`). `watch_ticker` tự đăng ký mã niêm yết, xếp hàng một lần phân tích ngay, và đưa mã vào danh sách theo lịch. `list_watchlist` trả nhãn mới nhất theo vị thế người gọi, kèm `holding_state` |
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
- Skill `vn-stock-analyze` được nạp qua `skills.external_dirs: [/opt/vn-market-mcp/.hermes/skills]` trong `config.yaml` của **mọi** profile. Xem mục "Skill dùng chung, góc nhìn riêng" bên dưới.

### Nhiều người dùng, dữ liệu tách riêng

| Dùng chung | Riêng từng người |
|---|---|
| Giá, BCTC, khối ngoại, tin tức | Vị thế (`positions`) |
| `runs`, `predictions`, snapshot (nhãn chung) | Danh sách theo dõi (`watchlist_extra`) |
| Hàng đợi job, worker | Nhãn theo vị thế khi đọc kết quả |
| Một tiến trình Hermes gateway | Hermes profile: `config.yaml`, `.env`, `memories/`, `sessions/`, MCP server với `VNMCP_USER_ID` |
| Bot chung (profile `default`) cho nhóm chung | **Bot Telegram riêng**, chat nhận kết quả (`users`) |

**Mỗi người một bot riêng.** Bot Telegram chỉ nhận và gửi tin, không tham gia logic, nhưng nó quyết định tin nhắn vào profile nào: bot của profile `alice` (token trong `~/.hermes/profiles/alice/.env`) chỉ phục vụ profile `alice`, và chỉ trả lời chủ của nó (`TELEGRAM_ALLOWED_USERS` trong cùng file). Bot chung của profile `default` phục vụ các nhóm chung.

| Ai nhắn, ở đâu | Profile | Được làm |
|---|---|---|
| Alice, với bot riêng của Alice (chat riêng hoặc nhóm riêng đã khai báo) | `alice` | Phân tích mã; danh mục riêng (theo dõi, vị thế, `/danhsach`); nhãn theo vị thế của Alice |
| Bất kỳ ai, với bot chung trong nhóm chung | `default` | Phân tích mã, nhãn chung. Tool danh mục riêng trả `status="no_personal_scope"` |
| Người khác nhắn bot riêng của Alice | — | Bot từ chối (không có trong `TELEGRAM_ALLOWED_USERS` của profile) |

Profile `default` không đặt `VNMCP_USER_ID`, nên MCP server của nó không có phạm vi cá nhân nào. Danh tính chỉ đến từ biến môi trường của profile, không bao giờ từ nội dung chat hay tham số do mô hình điền. Hai profile khai báo cùng tên server `vn-market-mcp` nhưng khác `env` thì Hermes không dùng chung kết nối MCP, nên mỗi profile có tiến trình MCP riêng gắn với đúng người.

**Thêm và xóa người dùng không cần restart.** Hermes gateway chạy chế độ nhiều profile: khi một profile được tạo, xóa, hoặc `config.yaml`/`.env` của nó đổi, gateway chỉ bật, tắt hoặc kết nối lại bot của đúng profile đó (lệnh quét `rescan-profiles`, và tự quét mỗi 30 giây), bot của người khác không bị đụng tới. Worker đọc token bot riêng từ `secrets/users.env` mỗi lần gửi tin, nên cũng không cần restart. Chỉ những thay đổi chung (nâng cấp Hermes, sửa `~/.hermes/config.yaml` của `default`, sửa skill chung) mới cần restart gateway.

#### Thêm người dùng

1. Người vận hành tạo một bot cho người đó qua @BotFather (`/newbot`), lấy token. Telegram không cho tạo bot bằng API nên bước này làm tay, khoảng một phút.
2. Chạy trên host, tại thư mục repo:

   ```bash
   ops/add_user.sh <tên> <telegram_user_id> <bot_token> [group_id]
   # ví dụ: ops/add_user.sh lan 123456789 7654321:AAH...
   #        ops/add_user.sh lan 123456789 7654321:AAH... -1001234567890   # kèm nhóm riêng (chỉ lan + bot của lan)
   ```

3. Người dùng nhắn `/start` cho bot của mình (Telegram chỉ cho bot nhắn tới người đã nhắn bot trước), rồi nhắn thử một câu hỏi. Hermes không trả lời riêng `/start`.

Lệnh làm, không restart gì:

| Bước | Việc |
|---|---|
| Kiểm tra token | Gọi `getMe` của Telegram; từ chối token đang được profile khác dùng (hai profile cùng một token sẽ tranh nhau tin nhắn) |
| Profile | Tạo bằng `--clone` nếu chưa có (xóa trắng `memories/USER.md` vì bản clone mang ghi chú về người khác), đặt `VNMCP_USER_ID`, đảm bảo nạp skill chung |
| `.env` của profile | `TELEGRAM_BOT_TOKEN`, `TELEGRAM_ALLOWED_USERS` (chỉ chủ bot), `TELEGRAM_GROUP_ALLOWED_CHATS` (nhóm riêng, nếu có) |
| Nơi nhận kết quả | `TELEGRAM_BOT_TOKEN_<TÊN>` trong `secrets/users.env` (mount chỉ đọc vào worker), dòng `users` trỏ tới biến đó và tới chat riêng hoặc nhóm riêng |
| Dọn cấu hình cũ | Luật `profile_routes` và id trong danh sách cho phép của bot chung từ thời dùng bot chung (nếu có): xóa khỏi file, gateway bỏ ở lần restart tới |
| Bật bot | Gọi `rescan-profiles`, chờ log `✓ telegram connected (profile: <tên>)`; báo lỗi và trả mã khác 0 nếu thấy `✗ telegram failed to connect`. Cuối cùng `mcp test` |

Chạy lại với cùng tham số thì không đổi gì; với tham số mới thì cập nhật. Cấu hình Hermes cũ được sao lưu vào `~/.hermes/backups/add_user-<thời điểm>/`. Biến môi trường tùy chọn: `HERMES_CONTAINER` (mặc định `hermes-gateway`), `COMPOSE_PROJECT_NAME` (mặc định `vn-market-mcp`).

#### Xóa người dùng

```bash
ops/remove_user.sh <tên>          # in danh sách sẽ xóa, hỏi gõ lại tên để xác nhận
ops/remove_user.sh <tên> --yes    # không hỏi (dùng trong script)
```

| Thu hồi | Chi tiết |
|---|---|
| Profile Hermes và bot riêng | Xóa profile (cấu hình, token, bộ nhớ, phiên chat); gateway ngừng bot đó ngay, không restart |
| Token cho worker | Dòng `TELEGRAM_BOT_TOKEN_<TÊN>` trong `secrets/users.env` |
| Cấu hình bot chung cũ (nếu có) | Luật `profile: <tên>` và id trong danh sách cho phép, trừ id còn được luật khác dùng. Đến lần restart tới, luật cũ trỏ vào profile đã xóa nên Hermes từ chối |
| Dữ liệu riêng trong DB | Danh sách theo dõi (`watchlist_extra`), vị thế (`positions`), dòng `users`, trong một transaction |

Trước khi xóa, lệnh sao lưu `config.yaml`, `.env` và bản lưu trữ profile (`hermes profile export`) vào `~/.hermes/backups/remove_user-<thời điểm>/`, và các dòng DB thành CSV trong `backups/remove_user-<tên>-<thời điểm>/`. Dữ liệu dùng chung và lịch sử job không bị đụng tới. Sau khi xóa nên thu hồi token ở @BotFather (`/revoke` hoặc `/deletebot`).

Khôi phục nếu xóa nhầm: `hermes profile import <bản lưu trữ>`, chạy lại `ops/add_user.sh` với cùng tham số, rồi nạp lại các CSV bằng `\copy`.

### Skill dùng chung, góc nhìn riêng

Mỗi người dùng có lập luận và góc nhìn thị trường khác nhau, nhưng luật phân tích phải như nhau cho mọi người. Hai phần này được tách ra:

| Lớp | Nằm ở | Ai sửa | Nội dung |
|---|---|---|---|
| Luật chung | Skill `vn-stock-analyze` trong repo, nạp qua `skills.external_dirs` | Chỉ người vận hành, qua git | Gọi tool nào cho câu hỏi nào; không tự tính số; không nâng nhãn; giọng văn; quy trình báo cáo và "Nhận định"; xử lý `no_personal_scope` |
| Góc nhìn cá nhân | Bộ nhớ của profile (`memories/USER.md`) | Hermes, khi người dùng nêu sở thích hoặc quan điểm | Khung thời gian, khẩu vị rủi ro, ngành quan tâm, quan điểm thị trường, cách trình bày |

- **Skill chung không bị sửa theo từng người.** Hermes coi skill trong `external_dirs` là chỉ đọc: bộ dọn skill hằng tuần (`curator`) và cơ chế tự sửa skill sau mỗi cuộc chat đều bỏ qua nó. Thư mục repo còn được mount chỉ đọc vào container, nên không có cách nào ghi vào. Bản thân skill cũng yêu cầu Hermes không sửa nó và không tạo skill thay thế.
- **Góc nhìn cá nhân chỉ đổi cách trình bày, không đổi kết luận.** Nhãn, con số và ngưỡng cắt lỗ đến từ code; `save_commentary` còn từ chối "Nhận định" lạc quan hơn nhãn. Khi quan điểm của người dùng trái với hệ thống, Hermes phải nói rõ là trái và chỉ ra dữ kiện ủng hộ hoặc bác bỏ.
- **Sửa luật chung:** sửa `SKILL.md` trong repo, merge vào `main` (gateway mount thư mục repo này), rồi `docker restart hermes-gateway`. Mọi profile nhận cùng một bản.
- **Điều Hermes học được về hệ thống** (một lỗi dữ liệu, một hành vi lạ của tool) được ghi vào bộ nhớ của profile phát hiện ra. Người vận hành quyết định có đưa vào skill chung hay không.

Trước đây Hermes dùng hai skill do chính nó viết (`research/vn-market-mcp-analysis`, `research/vn-stock-analysis`), mỗi profile một bản sao và tự sửa riêng. Nội dung hữu ích của chúng đã được gộp vào `vn-stock-analyze`; bản gốc được chuyển vào `~/.hermes/backups/skills-retired-<thời điểm>/`.

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
| `insufficient_coverage` với mã mới | Lần chạy đầu đã tự tải khoảng 3 năm giá. Nếu vẫn báo lỗi thì mã chưa đủ 500 phiên niêm yết, thanh khoản 20 phiên dưới 5 tỷ, hoặc chưa đủ 4 quý BCTC: đúng thiết kế, không phải lỗi |
| `unknown_ticker` | Mã không có trong danh sách niêm yết của vnstock (gõ sai, đã hủy niêm yết). Xem gợi ý trong cảnh báo |
| `data_quality_error` với mã ít giao dịch | Phiên hôm nay mã không có giao dịch nên thiếu nến. Chạy lại ở phiên có giao dịch |
| Người dùng không nhận được kết quả | Kiểm tra dòng của họ trong `users` (`chat_id`, `bot_token_env`), biến tương ứng trong `secrets/users.env`, và họ đã `/start` bot riêng. Log worker: `alert_skipped` (thiếu token) hoặc `alert_send_failed` |
| Kết quả của một người lại về nhóm ops | Người đó chưa có dòng trong `users`, hoặc yêu cầu được gửi từ nhóm chung (profile `default`) |
| Tool trả `no_personal_scope` dù đang chat với bot riêng | Người dùng đang nhắn bot chung chứ không phải bot riêng của họ, hoặc profile thiếu `VNMCP_USER_ID` (chạy lại `ops/add_user.sh`) |
| Bot riêng không trả lời | `grep "(profile: <tên>)" ~/.hermes/logs/gateway.log`: `✗ telegram failed to connect` = token sai/bị thu hồi; không có dòng nào = gateway chưa quét (chờ 30 giây). User id phải có trong `TELEGRAM_ALLOWED_USERS` của `~/.hermes/profiles/<tên>/.env`. Lần đầu người dùng phải nhắn `/start`; Hermes không trả lời riêng lệnh này |
| Tin nhắn mất khi gateway restart | `platforms.telegram.extra.drop_pending_on_cold_boot: true` (mặc định) bỏ tin tới trong lúc gateway tắt. Thêm/xóa người dùng không restart nên không gặp; với restart chủ động thì chọn lúc ít người dùng |
| `hermes profile list` báo Gateway = `running` nhưng bot không chạy | Cột này chỉ cho biết profile được gateway phục vụ, không phải bot đã kết nối. Xem dòng `(profile: <tên>)` trong `gateway.log` |
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
- Kết quả phân tích theo lịch của mã trong danh sách theo dõi không được gửi chủ động cho người theo dõi; họ xem bằng `/danhsach`.
- Danh mục riêng chỉ dùng được trong chat riêng (hoặc nhóm riêng đã có luật). Trong nhóm chung, mọi người chỉ phân tích mã, với nhãn chung.
- Trong nhóm riêng có luật, câu trả lời vẫn hiển thị cho mọi thành viên nhóm; định tuyến chỉ ngăn người khác thao tác danh mục, không ngăn họ đọc tin trong nhóm.
- Mọi bot vẫn chạy trong một tiến trình gateway: gateway lỗi, nâng cấp Hermes hay sửa cấu hình chung thì mọi người dùng cùng gián đoạn.
- Mỗi người dùng mới cần tạo tay một bot ở @BotFather.
- Hermes vẫn có thể tự tạo skill mới (khác tên) từ các cuộc chat. Skill chung yêu cầu không làm vậy cho phân tích cổ phiếu VN, nhưng đó là hướng dẫn, không phải rào chắn. Thỉnh thoảng kiểm tra `hermes -p <tên> skills list` và xóa skill `vn-…` lạ.
- Không giới hạn số mã mỗi người theo dõi. Mỗi mã thêm vào làm tăng số lần gọi vnstock ở mỗi mốc theo lịch (giới hạn 60 lần/phút).
- "Nhận định" đã lưu (`report_commentary`) dùng chung theo mã. Người đang giữ và người không giữ thấy nhãn khác nhau nên dấu vân tay số liệu khác nhau, và nhận định sẽ bị viết lại khi hai nhóm luân phiên hỏi cùng một mã.
- Khi lấp khoảng trống giá cho mã lâu không phân tích, nếu khoảng trống chứa ngày GDKHQ thì phần lịch sử cũ vẫn theo mức giá chưa điều chỉnh cho tới khi close_sync phát hiện và tải lại.
