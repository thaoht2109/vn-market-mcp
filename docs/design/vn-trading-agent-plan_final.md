# Kế hoạch dự án: Hermes Trading Research Agent cho chứng khoán Việt Nam

> Phiên bản: v2.9 · Ngày lập: 29/09/2026 · Trạng thái: đã chốt phong cách, nguồn dữ liệu (vnstock gói Cộng đồng), phạm vi (VN30 + mã bổ sung do người dùng xác nhận), hạ tầng, kênh nhận; đã thêm chat Telegram và vòng cải thiện skill (mục 4.6); mã ngoài VN30 và mã đang nắm giữ (mục 4.7); lựa chọn cơ sở dữ liệu và vòng đời dữ liệu (mục 7); nhãn hành động khuyến nghị (mục 5.6); chiến lược LLM (Claude) và cấu hình model (mục 5.7); rà soát thiết kế (mục 13.1)
>
> **Tuyên bố:** Hệ thống là công cụ hỗ trợ nghiên cứu và ra quyết định, **không phải tư vấn đầu tư** và **không tự đặt lệnh**. Rủi ro giao dịch do người dùng tự chịu.

---

## 1. Mục tiêu

Xây dựng một hệ thống agent, dùng **Hermes Agent (Nous Research)** làm Orchestrator, để:

1. Thu thập thông tin từ nhiều nguồn: giá/khối lượng, biểu đồ kỹ thuật, báo cáo tài chính, tin tức và công bố thông tin, dòng tiền, vĩ mô, ngành.
2. Chia thành các tác vụ riêng lẻ, mỗi tác vụ có đầu ra chuẩn hóa.
3. Tổng hợp thành **một report hỗ trợ giao dịch**: nhận định, kế hoạch vào/cắt lỗ/chốt lời, kịch bản, mức độ tin cậy.
4. Chạy theo hai cách, **dùng chung một luồng xử lý**:
   - **Theo lịch (cron):** báo cáo trước phiên, sau phiên, tổng kết tuần.
   - **Theo yêu cầu (chat):** người dùng nhắn "phân tích HPG" thì chạy cùng pipeline và trả report.
5. Ghi lại mọi dự báo và tự chấm điểm để đo hiệu quả thực tế theo thời gian.

### Ngoài phạm vi (giai đoạn này)

- Tự động đặt lệnh, kết nối API môi giới để giao dịch thật.
- Giao dịch tần suất cao / intraday tick-level.
- Cam kết lợi nhuận hay khuyến nghị mua/bán chắc chắn.

---

## 2. Nguyên tắc thiết kế

| # | Nguyên tắc | Ý nghĩa |
|---|---|---|
| 1 | **Python lo số học, LLM lo phán đoán** | Giá, chỉ báo, tỷ số, sizing, R:R do code tính. LLM chỉ diễn giải, đối chiếu, xử lý tín hiệu mâu thuẫn |
| 2 | **Inject, don't fetch** | Pipeline nạp sẵn dữ liệu gọn vào prompt; tool chỉ để hỏi thêm. Nhanh hơn và chính xác hơn |
| 3 | **Fail closed** | Thiếu/cũ dữ liệu thì rơi về giá trị bảo thủ (đứng ngoài, giảm tỷ trọng), không bao giờ về mặc định dễ dãi |
| 4 | **Một luồng, nhiều cổng vào** | Cron và chat gọi cùng `run_analysis`, tránh lệch logic |
| 5 | **Mọi con số truy được nguồn** | Mỗi số kèm `as_of` và `sources` |
| 6 | **Nêu rõ mâu thuẫn** | Tín hiệu trái chiều được trình bày, không lấy trung bình che đi |
| 7 | **Đo bằng forward test** | Không tin backtest dùng LLM trên dữ liệu trước ngày cắt kiến thức của model |
| 8 | **Không tự học nhận định thị trường** | Hit-rate lấy từ DB, chỉ đọc; skill lõi được version bằng Git |
| 9 | **Pipeline tất định, LLM không điều phối** | Thứ tự các bước, số lần thử lại và các cổng kiểm tra do code quyết định; Hermes chỉ kích hoạt, hỏi đáp và trò chuyện (mục 3) |
| 10 | **Máy kiểm được thì mới dùng làm căn cứ** | Điều kiện vô hiệu hóa luận điểm, độ tin cậy, nhãn hành động đều là quy tắc code kiểm được; LLM không tự chấm độ tin cậy (mục 5.4.1, 5.8) |

---

## 3. Kiến trúc tổng thể (v2)

```
 Cron (giờ UTC) ──┐                                     ┌─► Telegram / kênh chat
                  ├─► run_analysis(mode, tickers, ...) ─┤
 Chat (người dùng)┘                                     └─► DB: runs, predictions

 run_analysis =
   Phase 0  PIPELINE PYTHON (không LLM)
            lấy dữ liệu → chỉ báo, screener, dòng tiền, risk math
            → kiểm tra độ mới → snapshot.json + ảnh chart → log DB
   Phase 1  Chuẩn bị đầu vào gọn cho các vai LLM (snapshot đã nạp sẵn)
   Phase 2  Các vai LLM chạy song song (tool MCP gọi Claude API, mục 5.7):
            Tin tức/CBTT · Vĩ mô/regime · Đọc chart (tùy chọn)
   Phase 3  Bull vs Bear (chỉ 3-5 mã lọt lưới) → Synthesis
   Phase 4  CỔNG PYTHON: thanh khoản, biên độ, kẹt sàn, dữ liệu cũ (fail closed)
            + đối chiếu số trong report với snapshot bằng code
   Phase 5  Gửi report + ghi dự báo khi nhãn/luận điểm thay đổi (mục 10.1)

 Sau phiên: job chấm điểm dự báo → hit-rate theo loại tín hiệu
            → file thống kê chỉ đọc, nạp vào các lần chạy sau
```

**Ai điều phối?** `run_analysis` là pipeline Python tất định: thứ tự các bước, số lần thử lại và các cổng kiểm tra do code quyết định, không do LLM chọn lại mỗi lần chạy. Hermes chỉ (a) kích hoạt pipeline theo lịch hoặc theo yêu cầu, (b) trả lời hỏi đáp trên dữ liệu đã lưu, (c) nhận phản hồi để đề xuất sửa skill. `run_analysis` trả `job_id` ngay và worker chạy nền (mục 4.8); kết quả do worker gửi thẳng qua Telegram Bot API (dùng chung bot token chỉ để gửi tin), nên lệnh gọi MCP không bị timeout khi tổng hợp bằng Opus mất vài phút.

### Phân vai: code hay LLM?

| Thành phần | Bản chất | Ghi chú |
|---|---|---|
| Market Data, Technical, Fundamental, Flow, Sector | **Hàm code** (MCP tool) | Không cần agent LLM |
| Risk & Sizing | **Hàm code** | Đọc quy tắc từ `vn-rules.yaml` |
| Tin tức & sự kiện | **Vai LLM** (tool MCP gọi Claude API) | Đọc nhiều bài, tốn context, cần phán đoán |
| Vĩ mô / regime | **Vai LLM** (nhãn regime do code tính) | Diễn giải bối cảnh |
| Đọc chart | **Vai LLM (vision)** | Tùy chọn, đối chiếu với chỉ báo tính bằng code |
| Bull / Bear advocates | **LLM** | Chỉ cho mã lọt lưới |
| Synthesis | **Vai LLM** (Claude theo `models.yaml`) | Model mạnh, đầu ra theo schema |
| Verifier | **Code** + soát logic bằng LLM | Đối chiếu số tự động |

Các vai LLM được hiện thực bằng tool MCP gọi thẳng Claude API và cấu hình bằng `config/models.yaml` (mục 5.7).

---

## 4. Hai chế độ vận hành

### 4.1. Cổng vào duy nhất

```python
run_analysis(
    mode="scheduled_pre" | "scheduled_post" | "on_demand",
    tickers=["HPG"],            # rỗng = cả watchlist
    style="long",               # mặc định trung/dài hạn; "swing" chỉ để mở rộng sau
    max_age_minutes=None,       # None: dùng ngưỡng theo phiên (mục 4.4)
    depth="quick" | "full",     # quick: không chạy Bull/Bear
) -> {job_id, run_id, status}   # chạy nền; kết quả gửi qua Telegram, đọc lại bằng explain_run/get_snapshot
```

### 4.2. Lịch cron (ví dụ)

| Tác vụ | Giờ VN | Giờ UTC | Nội dung |
|---|---|---|---|
| Trước phiên (tùy chọn) | 08:30 | 01:30 | Tóm tắt ngắn: vĩ mô/quốc tế qua đêm, tin và CBTT mới liên quan VN30. Với trung/dài hạn có thể bỏ hoặc chỉ gửi khi có sự kiện đáng chú ý |
| Sau phiên | 15:15 | 08:15 | Thị trường, dòng tiền, ngành dẫn dắt, cập nhật từng mã, kịch bản phiên sau |
| Chấm điểm | sau phiên | — | Đối chiếu dự báo với kết quả thực tế |
| Phân tích sâu theo tuần | cuối tuần | — | Cập nhật luận điểm từng mã VN30: cơ bản, định giá, xu hướng tuần, dòng tiền; kèm hit-rate và dự báo sai |
| Mùa công bố KQKD | theo sự kiện | — | Khi một mã VN30 công bố BCTC/KQKD: cập nhật luận điểm so với kỳ vọng trước đó |

Lưu ý: kiểm tra phiên bản Hermes đang dùng có cờ múi giờ cho cron hay không; nếu không thì dùng giờ UTC hoặc đặt múi giờ của container.

**Độ tin cậy của lịch chạy**

- **Lịch giao dịch:** mọi phép tính theo phiên ("phiên gần nhất", cờ dữ liệu cũ, mốc chấm điểm) dùng bảng `trading_calendar` (lễ, Tết, nghỉ bù), không tự suy từ thứ trong tuần.
- **Dữ liệu cuối ngày chưa chắc đã chốt lúc 15:15:** giờ đóng cửa cần xác nhận với sàn (nhất là khi KRX thay đổi) và một số dữ liệu như khối ngoại có thể cập nhật muộn hơn giá. Job sau phiên chỉ chạy khi kiểm tra đã đủ dữ liệu của ngày (đủ nến các mã, khối lượng hợp lệ); nếu chưa thì thử lại theo lịch (ví dụ mỗi 10 phút, đến một mốc giờ cố định) rồi mới báo "dữ liệu chưa sẵn sàng".
- **Chạy bù và không gửi trùng:** mỗi job có khóa duy nhất trong bảng `jobs` (ví dụ `scheduled_post:2026-09-29`). Sau khi VPS khởi động lại, hệ thống chạy bù job bị lỡ nếu chưa quá hạn; quá giờ mà chưa có báo cáo thì cảnh báo Telegram thay vì im lặng.
- **Phân biệt lỗi hệ thống với "Đứng ngoài":** nếu kiểm tra chất lượng dữ liệu (mục 4.8) thất bại thì job dừng và báo lỗi, không sinh hàng loạt nhãn "Đứng ngoài" như thể đó là nhận định thị trường.

### 4.3. Định tuyến ý định khi người dùng chat

| Người dùng nhắn | Hành động |
|---|---|
| "phân tích HPG" / `/vn-stock-analyze HPG` | `run_analysis(on_demand, ["HPG"], depth="full")` |
| "HPG hôm nay sao rồi?" | `depth="quick"` (snapshot + tin mới, không Bull/Bear) |
| "báo cáo thị trường hôm nay" | Trả lại report của lần cron gần nhất nếu còn mới |
| "vì sao stop-loss đặt ở đó?" | Trả lời từ `run_id` đã lưu, không chạy lại pipeline |
| "so sánh HPG với HSG" | Chạy hai mã cùng một khung so sánh |
| "MWG có nên mua không?" | Chạy như phân tích MWG, theo các ràng buộc ở mục 4.5 |
| "Tôi đang giữ `<mã>`, nên làm gì?" (mã có thể ngoài VN30) | Chạy flow mục 4.7: xác định mã, phân nhóm, cổng độ phủ dữ liệu, rồi phân tích với `holding_state = holding` (đề nghị lưu vị thế bằng `/dangiu`, mục 5.8) |

### 4.4. Xử lý đồng thời và tái sử dụng

- **Cache snapshot theo `as_of`:** yêu cầu chat trong ngưỡng `max_age_minutes` dùng lại kết quả của cron. Ngưỡng ngắn khi đang giao dịch (mặc định gợi ý 30 phút); ngoài giờ giao dịch dùng lại snapshot cuối ngày cho đến phiên kế tiếp. Report luôn ghi rõ thời điểm tính.
- **Khóa theo `(mã, chế độ)` bằng advisory lock của PostgreSQL** (không dùng khóa trong bộ nhớ tiến trình vì cron, chat và worker là các tiến trình khác nhau): tránh chạy song song cùng một mã.
- **Chạy nền và trả lời hai bước:** `run_analysis` trả `job_id` ngay; worker gửi bản nhanh trước, report đầy đủ khi xong, thẳng qua Telegram Bot API. Lệnh gọi MCP không giữ kết nối chờ kết quả.
- **Trạng thái dùng chung qua DB** (`run_id`), không qua bộ nhớ Hermes.
- **Giới hạn chi phí:** số lần `depth="full"` mỗi giờ, ghi token/chi phí theo `run_id`.
- **Phân quyền tool theo kênh:** cron và chat chỉ có tool đọc dữ liệu, không có quyền đặt lệnh.

### 4.5. Chống thiên kiến từ câu hỏi dẫn dắt

- Câu hỏi của người dùng **không được đổi kết luận**: cùng dữ liệu thì cùng verdict.
- Cổng Python ở Phase 4 áp dụng y hệt cho chat; không có ngoại lệ.
- Thông tin cá nhân (giá vốn, tỷ trọng đang giữ) chỉ đi vào tham số của `risk_plan`, không đổi phần đánh giá thị trường.
- Không trả lời mua/bán "chắc chắn"; luôn có điều kiện vô hiệu hóa và các tín hiệu đi ngược nhận định.
- Dự báo từ chat ghi vào cùng bảng `predictions` với `source='on_demand'` để so sánh với cron.

### 4.6. Chat Telegram: hỏi đáp về dữ liệu và vòng cải thiện skill

Telegram là kênh chat chính thức của Hermes (gateway). Kênh này dùng cùng agent, bộ nhớ và ngữ cảnh với các kênh khác. Có ba nhóm việc:

**A. Hỏi đáp về dữ liệu đã lấy (chỉ đọc)**

Hermes trả lời từ DB/snapshot bằng nhóm tool chỉ đọc (mục 5.1), không gọi lại vnstock:

| Tool | Ví dụ câu hỏi |
|---|---|
| `get_snapshot(ticker, as_of?)` | "Chỉ báo kỹ thuật VCB lúc chốt phiên hôm qua?" |
| `query_history(ticker, metric, days)` | "Khối ngoại FPT 20 phiên gần nhất thế nào?" |
| `explain_run(run_id)` | "Vì sao điểm định giá HPG giảm so với tuần trước?" |
| `list_predictions(ticker?)` / `get_stats()` | "Các dự báo còn mở của VN30?", "Hit-rate tín hiệu breakout?" |

Mọi con số vẫn phải đến từ tool và kèm `as_of`. Muốn dữ liệu mới thì dùng `/chay <mã>` (gọi `run_analysis`).

**B. Điều khiển vận hành**

`/chay <mã>`, `/chitiet <mã>`, `/baocao`, `/watchlist`, `/theodoi <mã>` và `/boqua <mã>` (thêm/bỏ mã ngoài VN30, mục 4.7), `/dangiu <mã> [giá vốn]` và `/khonggiu <mã>` (khai báo vị thế đang nắm giữ, bảng `positions`, mục 5.8), `/trangthai` (lần chạy gần nhất, chi phí token).

**C. Vòng cải thiện skill qua chat (có duyệt)**

Hermes có cơ chế tự tạo và sửa skill; theo một hướng dẫn cộng đồng là công cụ `skill_manage` (create/patch/edit/delete), cần đối chiếu với tài liệu chính thức. Để cron luôn chạy đúng phiên bản đã duyệt, tách hai tầng:

- **Skill lõi** (5 skill, SOUL.md, `vn-rules.yaml`, cổng Phase 4): agent chỉ đọc (mount Docker `:ro` hoặc phân quyền file), chỉ đổi qua Git.
- **Vùng đề xuất** `skills-staging/`: agent được ghi bản đề xuất sửa vào đây.

```
Người dùng nhắn: "report thiếu phần so sánh cùng ngành"   (/gopy hoặc câu tự nhiên)
   ▼
Hermes ghi feedback vào DB (kèm run_id) → soạn bản sửa trong skills-staging/
   ▼
Gửi Telegram: tóm tắt thay đổi + diff ngắn
   ▼
Người dùng: /duyet <id>   (hoặc /tuchoi, /hoantac)
   ▼
Script ngoài agent chạy kiểm thử hồi quy trên snapshot đã lưu:
   report đủ mẫu · số khớp snapshot · cổng Phase 4 vẫn chặn đúng
   ▼
Đạt → commit Git kèm changelog → nạp lại skill
```

Phạm vi cho phép và không cho phép sửa qua chat:

| Được đề xuất qua chat | Chỉ sửa thủ công trong Git |
|---|---|
| Định dạng, độ dài, thêm/bớt mục của report | `vn-rules.yaml` (biên độ, lô, chu kỳ thanh toán, giới hạn rủi ro) |
| Prompt của subagent tin tức, nguồn tham khảo | Ngưỡng của cổng Phase 4 |
| Cách trình bày tóm tắt trên Telegram | Trọng số chấm điểm (mục 5.4), ngưỡng sinh nhãn hành động (mục 5.6), `config/models.yaml` (mục 5.7) |

Ràng buộc bổ sung:

- Không tự động đổi trọng số hay quy tắc dựa trên lãi/lỗ vừa qua; chỉ đổi khi có đủ dữ liệu forward test ở nhiều mốc (mục 10).
- Feedback, đề xuất và thống kê nằm trong DB (`feedback`, `skill_proposals`), không nằm trong bộ nhớ Hermes, để cron và chat cùng thấy.
- Skill do agent tự sinh để riêng khỏi bộ lõi và không cho cron dùng. Cơ chế cụ thể (thư mục skill riêng, tắt `skill_manage` cho cron bằng cấu hình tool theo nền tảng) chưa được xác minh trong tài liệu, cần thử nghiệm ở giai đoạn 2.

**An toàn cho kênh chat**

- **Prompt injection từ tin tức:** subagent tin tức đọc văn bản từ web nên không được có tool ghi skill/file. Chỉ tin nhắn của người dùng (ID trong allowlist) mới được khởi tạo đề xuất sửa skill, không phải nội dung trả về từ tool.
- **Duyệt lệnh:** khi chạy qua gateway, agent chờ phản hồi trong chat mới thực hiện lệnh cần duyệt; **không dùng `/yolo`** vì lệnh này bỏ qua bước duyệt.
- **Giới hạn toolset theo kênh:** kênh chat chỉ có tool đọc dữ liệu và quyền ghi vào `skills-staging/`, không có terminal rộng. Bật đủ ba lớp bảo vệ của gateway: duyệt lệnh, ghép cặp DM, cô lập container.

### 4.7. Mã ngoài VN30 và mã đang nắm giữ

VN30 là phạm vi mặc định, nhưng người dùng có thể hỏi bất kỳ mã nào, kể cả mã đang nắm giữ. `run_analysis` không giới hạn trong watchlist; phần cần quy định là **độ phủ dữ liệu**, mức tin cậy và việc không điền chỗ trống.

**Ba nhóm phạm vi (`universe_tier`)**

| Nhóm | Định nghĩa | Xử lý |
|---|---|---|
| **A** | Thành viên VN30 (theo `index_membership`) | Dữ liệu đã nạp sẵn bởi pipeline, có thống kê forward test |
| **B** | Ngoài VN30, đạt ngưỡng dữ liệu và thanh khoản | Tải bổ sung sau khi người dùng xác nhận, áp dụng ràng buộc riêng bên dưới |
| **C** | Không đạt ngưỡng (quá ít lịch sử, thanh khoản quá thấp, bị hạn chế/kiểm soát giao dịch...) | Từ chối đánh giá, nêu rõ lý do |

**Flow xử lý**

1. **Xác định mã, không đoán:** đối chiếu với danh mục mã toàn thị trường trong `tickers` (chỉ tên, sàn, ngành; cập nhật hằng tuần bằng một lệnh gọi liệt kê mã, không có giá hay báo cáo tài chính). Nếu sai hoặc không tồn tại thì hỏi lại và gợi ý mã gần giống. Report luôn nêu tên công ty và sàn để người dùng xác nhận đúng mã.
2. **Ghi nhận "đang nắm giữ":** khi người dùng nói đang giữ mã, bot đề nghị lưu vào bảng `positions` (`/dangiu <mã> [giá vốn]`) để các lần chạy sau, kể cả cron, đều biết vị thế; không lưu thì chỉ áp dụng cho lần chat này. `holding_state` lấy từ `positions` (thông tin tự khai). Giá vốn, nếu có, chỉ đi vào `risk_plan`, không đổi nhãn (mục 4.5).
3. **Phân nhóm sơ bộ:** mã thuộc VN30 (theo `index_membership`) là nhóm A, chạy ngay bằng dữ liệu đã nạp sẵn. Mã ngoài VN30 chưa được tải dữ liệu chi tiết, chuyển sang bước 4. Nhóm B hay C chỉ xác định được sau khi tải (bước 5).
4. **Xin xác nhận trước khi tải bổ sung:** bot cho biết mã X nằm ngoài VN30, cần tải bổ sung (ước lượng số request và thời gian) và sẽ được thêm vào danh sách theo dõi mở rộng `watchlist_extra` để cập nhật hằng ngày. **Chỉ khi người dùng xác nhận** (nút bấm hoặc trả lời "có") mới tải. Không xác nhận thì không tải và không ghi gì vào DB.
5. **Tải dữ liệu sau khi xác nhận** cho riêng mã này qua `providers/` (giá, thanh khoản, BCTC, khối ngoại, sự kiện), ghi vào DB bằng upsert. Mỗi thành phần trả về `ok`, `partial` hoặc `missing` kèm lý do. Tuân theo giới hạn ở mục "Ngân sách request" bên dưới.
6. **Cổng độ phủ dữ liệu** (bảng dưới): thiếu thành phần bắt buộc thì dừng, trả lời "không đủ dữ liệu để đánh giá" và liệt kê chính xác thiếu gì. Không gọi LLM viết nhận định và không sinh nhãn hành động.
7. **Chạy tiếp Phase 2-4** như cũ, cộng ràng buộc nhóm B.
8. **Report có mục "Độ phủ dữ liệu":** có gì, thiếu gì, phần trăm trọng số có dữ liệu.
9. **Duy trì sau xác nhận:** mã nằm trong `watchlist_extra`, cron cập nhật hằng ngày và bắt đầu tích lũy forward test. `/boqua <mã>` để gỡ. Mã không có tương tác trong một thời gian (mặc định 90 ngày) thì báo trước rồi tạm dừng cập nhật (`paused`); dữ liệu đã tải vẫn được giữ theo chính sách mục 7.4. `/theodoi <mã>` là cách chủ động thêm mã, cũng hiển thị ước lượng và xin xác nhận như bước 4.

**Quyết định phạm vi (29/09/2026):** hệ thống chỉ nạp sẵn VN30. Không nạp toàn thị trường; mã ngoài VN30 chỉ được tải bổ sung khi người dùng quan tâm và xác nhận. Vì vậy độ rộng thị trường lấy từ chỉ số (VN-Index, VN30, HNX) và tính trong nhóm VN30 cùng các mã đã theo dõi, không phải toàn thị trường.

**Ngân sách request (ước lượng thô, cần đo lại)**

| Hạng mục | Ước lượng |
|---|---|
| Nạp ban đầu VN30 | ~360 request (30 mã × ~12), khoảng 6 phút ở hạn mức 60 request/phút |
| Cập nhật giá hằng ngày VN30 | ~30 request, dưới 1 phút |
| Mùa công bố KQKD (VN30) | ~120 request (30 mã × ~4 báo cáo/chỉ số), khoảng 2 phút |
| Mỗi mã bổ sung | ~12 request khi nạp lần đầu (khoảng 12 giây ở 60/phút), ~1 request/ngày sau đó |

Đang dùng **gói Cộng đồng: 60 request/phút** (các gói khác: khách 20, Sponsor 180-600). Với phạm vi VN30 cộng tối đa 20 mã bổ sung, nhu cầu chỉ từ vài chục đến vài trăm request mỗi ngày nên hạn mức này dư dùng. Điểm cần giữ là các đợt tải dồn (nạp ban đầu, mùa KQKD, nhiều mã mới cùng lúc) và việc mọi lệnh gọi từ cùng một nguồn dữ liệu cộng dồn vào một giới hạn. Vì vậy:

- Bộ giới hạn tốc độ nội bộ; dành riêng một phần hạn mức cho câu hỏi on-demand; job nạp lô chạy ngoài giờ giao dịch.
- Giới hạn số mã bổ sung và tốc độ thêm mã mới (bên dưới).
- Không tạo nhiều tài khoản hay né hạn mức: giấy phép vnstock cấm, và tải bất thường có thể khiến nguồn chặn.

```yaml
extra_tickers:                       # vn-rules.yaml, giá trị gợi ý
  require_user_confirmation: true
  max_extra_tickers: 20
  max_new_tickers_per_hour: 5
  pause_after_idle_days: 90
```

**Ngưỡng độ phủ cho phong cách trung/dài hạn (mặc định gợi ý)**

| Thành phần | Yêu cầu tối thiểu | Nếu thiếu |
|---|---|---|
| Giá lịch sử | Từ ~500 phiên trở lên | Dừng |
| Thanh khoản | Bình quân 20 phiên đạt ngưỡng | Dừng (nhóm C) |
| Báo cáo tài chính | Từ 4 quý gần nhất | Dừng |
| Khối ngoại | Không bắt buộc | Ghi "không có dữ liệu", nêu trong mục độ phủ |
| Tin tức/CBTT | Không bắt buộc | Ghi "không tìm thấy tin trong 90 ngày" |
| Ngành/peer | Không bắt buộc | Cảnh báo nếu ít mã so sánh |

Điểm tổng hợp chỉ tính trên các thành phần có dữ liệu và **luôn hiển thị `weight_coverage`** (phần trăm trọng số có dữ liệu). Không âm thầm chia lại trọng số để che phần thiếu. Độ tin cậy bị chặn trên theo `weight_coverage`; dưới ngưỡng tối thiểu (mặc định 70%) thì xử lý như thiếu thành phần bắt buộc.

```yaml
coverage:                        # vn-rules.yaml, giá trị minh họa
  min_price_history_days: 500
  min_avg_liquidity_value_20d: 5.0e9    # VND/phiên
  min_fundamental_quarters: 4
  min_weight_coverage: 0.70
tier_b:
  confidence_cap: 0.6
  allow_buy_label_min_graded_samples: 30
```

**Ràng buộc riêng cho nhóm B**

- Cảnh báo cố định: "mã ngoài VN30, chưa có thống kê forward test".
- Không dùng nhãn "Ưu tiên mua" cho đến khi nhóm B có đủ số mẫu đã chấm điểm (`allow_buy_label_min_graded_samples`). Với mã đang giữ chỉ dùng Nắm giữ / Giảm tỷ trọng / Theo dõi / Đứng ngoài.
- Cổng thanh khoản và biên độ chặt hơn: giới hạn khối lượng gợi ý theo thanh khoản trung bình, tính biên độ HNX ±10% và UPCoM ±15% vào cắt lỗ, lọc nhóm cảnh báo/kiểm soát/hạn chế giao dịch.
- Mã mới chưa có `thesis_status`: đây là đánh giá lần đầu và report ghi rõ như vậy; không dùng nhãn "Nắm giữ" theo kiểu khẳng định luận điểm còn hiệu lực.

**Chống bịa thông tin**

- Số liệu chỉ đến từ tool; cổng Phase 4 đối chiếu từng con số trong report với snapshot bằng code. Số không truy được nguồn thì report bị chặn. Thiếu dữ liệu thì hiển thị "không có dữ liệu", không điền.
- Tin tức: không có tin trong 90 ngày thì ghi đúng như vậy, không tóm tắt từ trí nhớ của model.
- Mô tả công ty/ngành: lấy từ dữ liệu của tool; không có thì bỏ trống, vì model có thể nhớ sai hoặc lỗi thời về các mã nhỏ.
- Phần lý do định tính do LLM viết là rủi ro còn lại vì code không kiểm chứng hết. Giảm rủi ro bằng cách bắt mỗi nhận định gắn với trường dữ liệu làm bằng chứng, Verifier soát logic, và người dùng vẫn nên đối chiếu các nhận định quan trọng với nguồn.

### 4.8. Kiểm tra chất lượng dữ liệu và vận hành job

**Kiểm tra chất lượng dữ liệu (Phase 0, trước mọi phân tích)**

| Kiểm tra | Điều kiện dừng hoặc cảnh báo |
|---|---|
| Đủ dữ liệu ngày | Thiếu nến hôm nay của mã nào, hoặc bảng trả về rỗng: dừng job (lỗi hệ thống), không coi là "Đứng ngoài" |
| Cấu trúc trả về | Thiếu cột, đổi tên cột hoặc đổi kiểu dữ liệu so với schema mong đợi (vnstock hoặc nguồn phía sau có thể đổi): dừng và cảnh báo |
| Đơn vị giá | Giá lệch khoảng 1.000 lần so với phiên trước: dừng; chuẩn hóa đơn vị ở `providers/` và có unit test (mục 8) |
| Biến động bất thường | Giá thay đổi vượt biên độ sàn mà không có sự kiện điều chỉnh (GDKHQ...): cảnh báo, gắn cờ nghi ngờ dữ liệu |
| Khối lượng | Khối lượng bằng 0 hoặc âm ở mã thanh khoản cao: cảnh báo |
| Độ mới | `as_of` cũ hơn phiên gần nhất theo `trading_calendar`: gắn cờ dữ liệu cũ (Phase 4) |

Kết quả ghi vào `data_quality_log`. Khi kiểm tra thất bại, worker gửi cảnh báo Telegram ("lỗi dữ liệu, không có báo cáo"), tách bạch với báo cáo có nhãn "Đứng ngoài".

**Hàng đợi job và worker**

- Bảng `jobs`: mã job, loại, khóa chống trùng (ví dụ `scheduled_post:2026-09-29`), trạng thái (`queued` | `running` | `done` | `failed` | `expired`), số lần thử, thời điểm.
- Worker chạy riêng, không nằm trong tiến trình Hermes: lấy job, chạy pipeline, gửi kết quả qua Telegram Bot API. Hàm gửi tin chia đoạn theo giới hạn 4096 ký tự và dùng chế độ HTML, hoặc escape đầy đủ các ký tự đặc biệt của MarkdownV2 (dấu chấm, gạch ngang, ngoặc xuất hiện khắp nơi trong số liệu); có unit test cho hàm này.
- Chạy bù sau khởi động lại và cảnh báo khi quá hạn (mục 4.2). Batch API chỉ dùng cho việc không gấp (backfill, rà soát tuần); báo cáo sau phiên chạy sync vì Batch API xử lý bất đồng bộ, không đảm bảo xong trong buổi tối (mục 5.7).

**Hermes trong hệ thống này**

- Hermes có bộ nhớ tự động và vòng học kỹ năng; ở giai đoạn 0 cần kiểm tra cấu hình để giới hạn hoặc tắt việc tự ghi bộ nhớ về nhận định thị trường (nguyên tắc 8).
- Chi phí vòng lặp của chính Hermes (lịch sử chat, kết quả tool) chưa nằm trong ước tính mục 5.7.7; cần đo riêng, chọn model phù hợp và đặt hạn mức chi tiêu trong Claude Console.
- Bí mật (Telegram, Anthropic, vnstock) nằm trong `.env` với quyền tệp chặt, không commit vào Git, có quy trình đổi khóa.

---

## 5. Thành phần chi tiết

### 5.1. MCP server `vn-market-mcp` (tự viết)

| Tool | Chức năng |
|---|---|
| `get_ohlcv` | Giá OHLCV đã điều chỉnh, thanh khoản TB20 |
| `technical_snapshot` | MA/EMA, RSI, MACD, Bollinger, ATR, khối lượng bất thường, hỗ trợ/kháng cự; vẽ chart PNG |
| `fundamental_snapshot` | Doanh thu, lợi nhuận, biên, ROE, nợ, P/E, P/B, so ngành và lịch sử |
| `foreign_flow` | Khối ngoại, tự doanh, margin, room ngoại |
| `corporate_events` | Lịch GDKHQ, ĐHCĐ, công bố KQKD, giao dịch nội bộ |
| `market_breadth` | Chỉ số VN-Index/VN30/HNX; độ rộng và sức mạnh tương đối trong nhóm VN30 và các mã đã theo dõi (không nạp toàn thị trường, mục 4.7) |
| `risk_plan` | Stop-loss theo ATR, R:R, khối lượng theo % thanh khoản, cảnh báo kẹt sàn |
| `run_analysis` | Cổng vào duy nhất (mục 4.1) |
| `get_snapshot`, `query_history`, `explain_run`, `list_predictions`, `get_stats` | Nhóm tool **chỉ đọc** phục vụ hỏi đáp qua Telegram (mục 4.6) |
| `analyze_news`, `macro_regime`, `synthesize`, `bull_case`, `bear_case`, `verify_logic` | Các vai LLM: gọi Claude API theo `config/models.yaml`, đầu ra theo schema (mục 5.7) |
| `set_position`, `clear_position` | Khai báo/xóa vị thế đang giữ (tự khai) trong bảng `positions` (mục 5.8) |
| `data_quality_check` | Kiểm tra chất lượng dữ liệu trước phân tích (mục 4.8) |

Mỗi tool trả về cùng một khung dữ liệu (mục 6). Nguồn dữ liệu ở lớp `providers/` để thay được (vnstock, API công ty chứng khoán, nhà cung cấp trả phí).

### 5.2. Skill Hermes (lưu trong Git)

| Skill | Vai trò |
|---|---|
| `vn-stock-analyze` | Quy trình phân tích một mã, định tuyến ý định |
| `vn-market-daily` | Báo cáo trước/sau phiên cho watchlist |
| `vn-news-digest` | Hướng dẫn Hermes gọi tool `analyze_news` và trình bày kết quả tin/CBTT (việc phân tích do tool MCP gọi Claude thực hiện, mục 5.7) |
| `vn-chart-review` | Đọc ảnh chart, đối chiếu với chỉ báo |
| `vn-report-verify` | Soát logic report sau khi code đã đối chiếu số |

### 5.3. SOUL.md (nguyên tắc bất biến)

1. Không đặt lệnh.
2. Mọi con số đến từ tool MCP, kèm `as_of` và nguồn; không tự tính hay dùng số từ trí nhớ.
3. Dữ liệu cũ hơn phiên gần nhất phải bị gắn cờ.
4. Khi tín hiệu mâu thuẫn, nêu rõ mâu thuẫn.
5. Luôn có kịch bản vô hiệu hóa và mức cắt lỗ; không dùng từ "chắc chắn".
6. Cuối report ghi tuyên bố miễn trừ.
7. `config/vn-rules.yaml` chỉ đọc, không tự sửa.
8. Nhãn hành động do hàm code sinh (mục 5.6); chỉ được đề nghị hạ nhãn xuống thận trọng hơn, không được nâng nhãn.

### 5.4. Cách chấm điểm tổng hợp

| Thành phần | Lướt sóng (T+ vài ngày) | Trung/dài hạn |
|---|---|---|
| Kỹ thuật | 35% | 15% |
| Dòng tiền | 25% | 10% |
| Tin tức/sự kiện | 20% | 15% |
| Cơ bản/định giá | 5% | 45% |
| Ngành + vĩ mô | 15% | 15% |

Đã chốt phong cách **trung/dài hạn**, dùng cột thứ hai làm mặc định. Trọng số chỉ là điểm khởi đầu, sẽ điều chỉnh theo dữ liệu forward test.

#### 5.4.1. Cách tính điểm, độ tin cậy và cổng regime

**Chuẩn hóa điểm từng thành phần (0-100), do code tính**

- Mỗi thành phần gồm các chỉ số con, mỗi chỉ số đổi thành **phân vị so với lịch sử của chính mã** (và so với cùng ngành khi đủ mã), rồi lấy trung bình với trọng số cố định trong `vn-rules.yaml`. Ví dụ Cơ bản/định giá: phân vị của ROE, tăng trưởng lợi nhuận và (đảo chiều) P/E, P/B. Kỹ thuật: vị trí so với MA dài hạn, xu hướng tuần, khối lượng.
- **Bộ chỉ số theo nhóm ngành** (`tickers.industry_group`): ngân hàng, chứng khoán, bảo hiểm, bất động sản dùng bộ chỉ số riêng. Ví dụ ngân hàng: P/B, ROE, tăng trưởng tín dụng, NIM, nợ xấu, CASA; không dùng biên lợi nhuận gộp hay nợ vay trên vốn chủ như doanh nghiệp sản xuất. Khoảng một nửa VN30 là ngân hàng và tài chính nên phần này là bắt buộc.
- Thành phần thiếu dữ liệu xử lý theo mục 4.7 (hiển thị `weight_coverage`, không âm thầm chia lại trọng số).

**Độ tin cậy (`confidence`) do code tính, không do LLM tự khai**

`confidence = độ phủ dữ liệu × độ mới dữ liệu × độ đồng thuận`, trong đó độ đồng thuận là tỷ lệ thành phần (kỹ thuật, dòng tiền, tin tức, cơ bản, ngành/vĩ mô) có điểm cùng phía so với ngưỡng trung tính. "Số nguồn đồng thuận" ở mục 5.6 chính là số thành phần cùng phía. Công thức chi tiết đặt trong `vn-rules.yaml` và hiệu chỉnh bằng dữ liệu forward test; LLM chỉ được đề nghị hạ.

**Cổng regime**

Regime vĩ mô không chỉ cộng vào điểm (15%) mà còn là cổng: khi `regime = risk_off` (nhãn do code tính, mục 5.7.3) thì không sinh nhãn "Ưu tiên mua", nhãn bị hạ tối đa về "Theo dõi". Định nghĩa và ngưỡng regime đặt trong `vn-rules.yaml`.

### 5.5. Mẫu report

1. Tóm tắt 3 dòng: nhận định, thiên hướng, mức độ tin cậy.
2. Bối cảnh: vĩ mô, dòng tiền, ngành.
3. Kỹ thuật: xu hướng, hỗ trợ/kháng cự, chart.
4. Cơ bản: điểm nổi bật và rủi ro.
5. Tin tức/sự kiện sắp tới.
6. Kế hoạch giao dịch: nhãn hành động (mục 5.6), vùng vào, cắt lỗ, mục tiêu, R:R, khối lượng gợi ý.
7. Kịch bản: tích cực / cơ sở / tiêu cực và điều kiện vô hiệu hóa.
8. Phụ lục: nguồn, thời điểm dữ liệu, cảnh báo của cổng kiểm tra.

### 5.6. Nhãn hành động (khuyến nghị tham khảo)

Report đưa ra **nhãn hành động** cho từng mã, nhưng nhãn là thiên hướng tham khảo kèm kế hoạch giao dịch, không phải lệnh mua/bán và không phải tư vấn đầu tư.

**Nguyên tắc**

- Nhãn do **code** sinh từ điểm tổng hợp (mục 5.4), độ tin cậy, kết quả cổng Phase 4 và trạng thái danh mục. LLM nhận nhãn làm đầu vào để viết lý do, không tự chọn nhãn.
- LLM (và cổng Phase 4) **chỉ được hạ nhãn xuống thận trọng hơn, không được nâng lên.** Nếu LLM thấy rủi ro chưa được mô hình hóa (ví dụ tin xấu lớn), nó đề nghị hạ nhãn kèm lý do; code chấp nhận nếu nhãn mới thận trọng hơn theo thứ tự trong bảng dưới.
- Ngưỡng đặt trong `vn-rules.yaml`, chỉnh thủ công trong Git, không chỉnh qua chat (mục 4.6). Mọi ngưỡng ban đầu chỉ là điểm khởi đầu chưa kiểm chứng; chỉ forward test mới cho biết khuyến nghị có giá trị hay không.
- Nhãn ghi vào cột `action_label` của bảng `predictions` để chấm điểm về sau.
- Mã ngoài VN30 áp dụng thêm mục 4.7: không đủ dữ liệu thì **không sinh nhãn** (trả lời "không đủ dữ liệu để đánh giá", chỉ ghi vào `runs`, không ghi `predictions`); nhóm B chưa đủ số mẫu chấm điểm thì không được dùng nhãn "Ưu tiên mua".

**Đầu vào của hàm sinh nhãn:** `composite_score` (0-100), `confidence`, số nguồn đồng thuận, phân vị định giá so với lịch sử của chính mã, `rr`, kết quả cổng Phase 4, cờ dữ liệu cũ, `holding_state` (lấy từ bảng `positions`, mục 5.8: `holding` | `none` | `unknown`), `thesis_status`, `regime` (cổng regime, mục 5.4.1).

**Bảng nhãn (trung/dài hạn), theo thứ tự từ tích cực đến thận trọng:**

| Nhãn | Điều kiện gợi ý |
|---|---|
| **Ưu tiên mua/tích lũy** | Điểm cao, đủ số nguồn đồng thuận, độ tin cậy đạt ngưỡng, định giá không đắt so với lịch sử, R:R đạt ngưỡng, qua cổng Phase 4, không có cờ dữ liệu cũ |
| **Theo dõi, chờ vùng giá** | Điểm khá nhưng giá đã xa vùng vào, hoặc các tín hiệu mâu thuẫn |
| **Nắm giữ** | `holding_state = holding` và luận điểm còn hiệu lực |
| **Giảm tỷ trọng/thoát** | `holding_state = holding` và luận điểm bị vô hiệu hóa hoặc điểm rơi dưới ngưỡng |
| **Đứng ngoài** | Điểm thấp, thiếu dữ liệu, dữ liệu cũ, hoặc bị cổng Phase 4 chặn |

**Khi chưa có thông tin danh mục** (`holding_state = unknown`): "Bán" chỉ có nghĩa với mã đang nắm giữ vì bán khống hầu như không khả dụng ở Việt Nam. Khi đó không dùng hai nhãn "Nắm giữ" và "Giảm tỷ trọng/thoát"; thay bằng thông báo "Luận điểm còn hiệu lực" hoặc "Luận điểm suy yếu, cân nhắc giảm nếu đang giữ". Khai báo vị thế bằng `/dangiu` (mục 5.8) thì hai nhãn này khả dụng.

**Cấu hình gợi ý (`vn-rules.yaml`, giá trị chỉ để minh họa):**

```yaml
action_labels:
  buy_accumulate:
    min_score: 70
    min_confidence: 0.6
    min_agreeing_sources: 3
    min_rr: 2.0
    max_valuation_percentile: 70    # so với lịch sử 5 năm của chính mã
  watch:
    min_score: 55
  reduce_exit:
    max_score: 40
    or_thesis_invalidated: true
  # dưới ngưỡng watch, hoặc bị cổng chặn, hoặc dữ liệu cũ -> stay_out
```

**Thứ tự đánh giá của hàm sinh nhãn:**

```python
def action_label(inp, cfg):
    if inp.coverage_insufficient:            # thiếu thành phần bắt buộc hoặc weight_coverage quá thấp (mục 4.7)
        return None                          # không sinh nhãn, trả lời "không đủ dữ liệu để đánh giá"
    if inp.gate_blocked or inp.data_stale or inp.confidence < cfg.min_confidence_floor:
        return "stay_out"
    if inp.holding_state == "holding":
        if inp.thesis_invalidated or inp.score < cfg.reduce_exit.max_score:
            return "reduce_exit"
        return "hold"
    if (inp.buy_allowed                      # nhóm B chưa đủ số mẫu chấm điểm thì False
            and inp.regime != "risk_off"     # cổng regime (mục 5.4.1)
            and inp.score >= cfg.buy.min_score and inp.agree >= cfg.buy.min_sources
            and inp.rr >= cfg.buy.min_rr and inp.valuation_pct <= cfg.buy.max_val_pct):
        return "buy_accumulate"
    if inp.score >= cfg.watch.min_score:
        return "watch"
    return "stay_out"
```

**Hiển thị trên Telegram:** nhãn, một dòng lý do, vùng vào/cắt lỗ/mục tiêu, điều kiện vô hiệu hóa, và tuyên bố "công cụ hỗ trợ, không phải tư vấn đầu tư". Chi tiết xem bằng `/chitiet <mã>`.

**Chấm điểm nhãn:** chấm **tất cả nhãn**, không chỉ nhãn mua. Nhãn "Ưu tiên mua" so lợi suất vượt trội với VN30 ở các mốc 20/60/120 phiên; nhãn "Đứng ngoài" và "Theo dõi" cũng được chấm để biết hệ thống có bỏ lỡ cơ hội hay không. Kết quả nạp lại dưới dạng thống kê chỉ đọc (mục 10).

**Lưu ý pháp lý:** nếu chia sẻ report cho người khác hoặc thu phí, việc đưa khuyến nghị đầu tư có thể thuộc hoạt động tư vấn chứng khoán có điều kiện pháp lý riêng; cần hỏi ý kiến luật sư. Dùng cho cá nhân thì hệ thống chỉ là công cụ nghiên cứu của bạn.

### 5.7. Chiến lược LLM (Claude) và cấu hình model

Claude là nền tảng LLM chính. Các dòng model hiện tại có giá, tham số và vòng đời thay đổi nhanh, nên việc chọn model cho từng tác vụ phải nằm trong một file cấu hình, có thể thay đổi và so sánh mà không sửa code.

#### 5.7.1. Kiến trúc

- Các vai LLM (tin tức, vĩ mô, synthesis, Bull/Bear, Verifier) được hiện thực bằng **tool MCP gọi thẳng Claude API** (`analyze_news`, `macro_regime`, `synthesize`, `bull_case`, `bear_case`, `verify_logic`), điều khiển bởi `config/models.yaml`. Cách này cho phép dùng structured outputs, Batch API, prompt caching, ghi chi phí và kiểm thử ngoài Hermes.
- Hermes giữ vai kích hoạt pipeline, hỏi đáp và trò chuyện Telegram (không quyết định thứ tự các bước, mục 3), với model do bạn chọn khi cấu hình Hermes. Chưa xác minh được việc chọn model riêng cho từng subagent của Hermes, nên thiết kế không phụ thuộc vào tính năng đó.
- **Mỗi lời gọi là một hội thoại riêng**, truyền nhau bằng JSON. Không đổi model giữa chừng vì thinking block gắn với model và không đọc chéo được giữa nhiều dòng (ví dụ Sonnet 5.5 không đọc được block của Opus 5.5).
- File cấu hình nằm trong Git, chỉ đổi qua PR, **không cho chỉnh qua chat** (mục 4.6). Mỗi lần chạy ghi `config_hash` để tái lập kết quả.

#### 5.7.2. Phân bổ model đề xuất

| Vai | Tác vụ | Model | Mức suy luận (`effort`) | Chế độ |
|---|---|---|---|---|
| Hermes (kích hoạt, hỏi đáp, chat) | Định tuyến ý định, hỏi đáp trên dữ liệu đã lưu, trò chuyện; không điều phối pipeline | Sonnet 5.5 | mặc định | sync |
| `news_digest` | Trích xuất/phân loại tin, CBTT; đầu vào không đáng tin | Sonnet 5.5 | thấp | sync (backfill: batch) |
| `macro_daily` | Diễn giải chuỗi số, tin vĩ mô | Sonnet 5.5 | trung bình | sync |
| `macro_weekly` | Rà soát regime hằng tuần | Opus 5.5 | cao | batch |
| `bull_advocate`, `bear_advocate` | Hai lập luận đối nghịch trên cùng dữ liệu | Sonnet 5.5 | cao | sync |
| `synthesis_daily` | Tóm tắt hằng ngày (delta) | Sonnet 5.5 | trung bình | sync |
| `synthesis_full` | Report tuần, mã lọt lưới, on-demand `full` | Opus 5.5 | cao | sync |
| `verifier_logic` | Soát logic (số do code đối chiếu) | Sonnet 5.5 | thấp | sync |
| `skill_patch` | Soạn bản vá skill (mục 4.6) | Opus 5.5 | cao | sync |
| `chart_vision` | Đọc chart (tùy chọn, tắt mặc định) | Sonnet 5.5 | trung bình | sync |

Sonnet 5.5 có giá $2/$10 mỗi triệu token và độ trễ nhanh; Opus 5.5 có giá $4/$20, độ trễ trung bình; cả hai có cửa sổ ngữ cảnh 1M. Với phạm vi VN30, chi phí LLM nhỏ (mục 5.7.7), nên không hạ model ở các vai cần độ chính xác chỉ để tiết kiệm. Fable 5.1 và Mythos không cần thiết cho bài toán này.

#### 5.7.3. Thiết kế từng vai

**News & Events**
- Code lấy tin, sự kiện, giao dịch nội bộ từ vnstock, khử trùng lặp theo `url_hash`, gộp khoảng 10 tin mỗi lệnh gọi.
- Đầu ra theo schema (structured outputs): mã, loại sự kiện, ngày, sentiment, tác động ngắn/trung/dài hạn, độ liên quan đến luận điểm, mã tin làm bằng chứng, độ tin cậy. Phân bậc nguồn: CBTT chính thức, rồi báo chí, rồi mạng xã hội.
- Chỉ dùng nội dung trong đầu vào; không có thông tin thì trả `no_info`. Tin là **dữ liệu, không phải chỉ dẫn**; vai này **không có tool nào**.
- Backfill chạy bằng Batch API (giảm 50% giá). Bản tổng hợp hằng ngày chạy sync vì Batch API xử lý bất đồng bộ, không đảm bảo xong trong buổi tối để kịp báo cáo sau phiên.

**Vĩ mô / regime**
- Nhãn regime (risk-on / trung tính / risk-off...) do **code** tính từ chỉ số (VN-Index so với MA200, thanh khoản, tỷ giá...). LLM chỉ viết diễn giải và liệt kê `unmodeled_risks` kèm bằng chứng, và chỉ được đề nghị **hạ** mức, nhất quán với mục 5.6.
- **Khoảng trống dữ liệu:** cây hàm vnstock chỉ thấy tỷ giá, giá vàng, OHLCV hàng hóa/forex từ MSN; chưa thấy lãi suất liên ngân hàng hay lợi suất trái phiếu. Cần nguồn khác hoặc bỏ phần này khỏi điểm.
- Tin quốc tế: có thể dùng web search của Claude ($10 mỗi 1.000 lượt tìm, cộng chi phí token), giới hạn bằng `max_uses` và `allowed_domains`. Kết quả tìm là nội dung không đáng tin, áp dụng cùng nguyên tắc chống injection như tin tức.

**Synthesis**
- Đầu vào gọn: snapshot JSON, kết quả News và Macro, `risk_plan`, nhãn hành động do code sinh, thống kê hit-rate.
- Đầu ra là JSON theo schema (luận điểm, lập luận hỗ trợ, mâu thuẫn, kịch bản, điều kiện vô hiệu hóa, đề nghị hạ nhãn); **code render ra Markdown**. Số liệu trong văn bản là **placeholder** (ví dụ `{{tech.rsi14}}`) do code điền từ snapshot, nên LLM không có chỗ để bịa số. Mỗi lập luận có `evidence_ref` trỏ tới trường dữ liệu; code kiểm tra ref tồn tại. **Văn bản do LLM viết không được chứa chữ số** (trừ danh sách cho phép như năm, số thứ tự mục); số chỉ xuất hiện qua placeholder. Nhờ vậy việc đối chiếu bằng code không phụ thuộc vào regex đọc số kiểu Việt (dấu chấm hàng nghìn, dấu phẩy thập phân, phần trăm, ngày tháng).

**Bull / Bear và Verifier**
- Bull và Bear là hai lời gọi độc lập, cùng dữ liệu, system prompt đối nghịch; Synthesis là bên phân xử.
- Verifier: code đối chiếu số trước; LLM chỉ kiểm tra kết luận có suy ra được từ bằng chứng không và nhãn có khớp không.

#### 5.7.4. `config/models.yaml`

```yaml
version: 1
provider: anthropic

models:                                   # sổ đăng ký + giá để tính chi phí
  sonnet: {id: claude-sonnet-5-5, usd_per_mtok: {in: 2, out: 10, cache_read: 0.2}, price_checked: 2026-09-29}
  opus:   {id: claude-opus-5-5,   usd_per_mtok: {in: 4, out: 20, cache_read: 0.2}, price_checked: 2026-09-29}
  haiku:  {id: claude-haiku-4-5-20251001, usd_per_mtok: {in: 1, out: 5, cache_read: 0.1},
           price_checked: 2026-09-29, note: "ngừng hỗ trợ không sớm hơn 2026-10-15"}

roles:
  news_digest:
    model: sonnet
    effort: low
    mode: sync                            # sync | batch (batch giảm 50% giá, không đảm bảo thời gian xong)
    backfill_mode: batch
    output_schema: schemas/news_digest.json
    prompt_version: news-v1
    tools: []                             # đầu vào không đáng tin: không cấp tool
    fallback: [haiku]
  macro_daily:      {model: sonnet, effort: medium, mode: sync,  output_schema: schemas/macro.json,  prompt_version: macro-v1, tools: []}
  macro_weekly:     {model: opus,   effort: high,   mode: batch, output_schema: schemas/macro.json,  prompt_version: macro-v1, tools: []}
  synthesis_daily:  {model: sonnet, effort: medium, mode: sync,  output_schema: schemas/report.json, prompt_version: synth-v1, tools: []}
  synthesis_full:   {model: opus,   effort: high,   mode: sync,  output_schema: schemas/report.json, prompt_version: synth-v1, tools: []}
  bull_advocate:    {model: sonnet, effort: high,   mode: sync,  prompt_version: bull-v1,   tools: []}
  bear_advocate:    {model: sonnet, effort: high,   mode: sync,  prompt_version: bear-v1,   tools: []}
  verifier_logic:   {model: sonnet, effort: low,    mode: sync,  prompt_version: verify-v1, tools: []}
  skill_patch:      {model: opus,   effort: high,   mode: sync,  prompt_version: patch-v1}
  chart_vision:     {model: sonnet, effort: medium, enabled: false}    # bật sau khi thử

experiments:
  - id: news-haiku-vs-sonnet
    role: news_digest
    mode: shadow                          # chạy song song, không dùng kết quả
    challenger: {model: haiku, effort: low}
    sample_rate: 0.25
    until: 2026-11-15

limits:
  max_cost_usd_per_run: 1.0
  daily_budget_usd: 10
  alert_at_pct: 80                        # cảnh báo qua Telegram
  request_timeout_s: 120

validation:                               # kiểm tra khi khởi động, sai thì không chạy
  require_roles: [news_digest, synthesis_full, verifier_logic]
  forbid_params: [temperature, top_p, top_k, assistant_prefill, thinking_budget, forced_tool_choice]
```

Lưu ý:
- Giá và model ID lấy từ tài liệu chính thức của Anthropic ngày 29/09/2026. Trường `price_checked` cho biết khi nào cần rà lại.
- Các mức `effort` hợp lệ khác nhau theo model (một số mức cao gây lỗi 400 trong vài cấu hình của Sonnet 5.5), cần đối chiếu tài liệu khi thử.
- Không có `temperature`: Sonnet 5.5 từ chối các tham số lấy mẫu. Tính ổn định đến từ structured outputs, placeholder điền bằng code và kiểm tra lặp lại.
- Ngưỡng nhãn hành động vẫn ở `vn-rules.yaml`, không nằm trong file này, để đổi model không làm đổi quy tắc an toàn.

#### 5.7.5. Ghi nhận lời gọi LLM

```sql
CREATE TABLE llm_calls (
  id BIGSERIAL PRIMARY KEY, run_id TEXT REFERENCES runs(run_id),
  role TEXT NOT NULL, model_id TEXT NOT NULL, config_hash TEXT NOT NULL,
  prompt_version TEXT, experiment_id TEXT, is_shadow BOOLEAN DEFAULT false,
  input_tokens INT, output_tokens INT, cached_tokens INT, cost_usd NUMERIC,
  latency_ms INT, schema_valid BOOLEAN, error TEXT,
  created_at TIMESTAMPTZ DEFAULT now()
);
```

Bảng `runs` và `predictions` có thêm cột `config_hash`. Prompt và phản hồi thô lưu ra tệp và dọn theo chính sách mục 7.4.

#### 5.7.6. Kiểm thử và chọn model

1. **Kiểm thử offline (giai đoạn 0)** trên bộ mẫu vàng: 50-100 tin/CBTT tiếng Việt gán nhãn tay, cộng các report cũ. Ưu tiên chỉ số kiểm tra được bằng code: tỷ lệ đúng schema, độ chính xác từng trường, `evidence_ref` không hợp lệ (dấu hiệu bịa), độ giống nhau giữa 3 lần chạy, chi phí, độ trễ. Chất lượng văn bản chấm bằng checklist và người; dùng LLM làm giám khảo thì cẩn trọng vì có thiên lệch.
2. **Chạy bóng (shadow) trên dữ liệu thật:** model thách đấu chạy song song ở một tỷ lệ mẫu (`experiments`), kết quả chỉ lưu để so sánh, không ảnh hưởng report hay nhãn.
3. **Forward test theo `config_hash`:** so hit-rate theo cấu hình. Vì dự báo trung/dài hạn có mẫu ít và chậm, chỉ dùng để phát hiện suy giảm lớn, không dùng để chọn model.
4. Chỉ chuyển model thách đấu thành mặc định khi không kém baseline ở các chỉ số chính, không tăng `evidence_ref` không hợp lệ, và chi phí/độ trễ chấp nhận được. Mọi thay đổi đi qua PR kèm bảng so sánh.

#### 5.7.7. Chi phí ước tính (giả định thô, cần đo lại)

Giả định: 22 phiên/tháng; 150 tin/ngày, mỗi tin ~1.500 token đầu vào (tiêu đề và đoạn đầu, không toàn văn) và ~250 token đầu ra; khoảng 230 report đầy đủ/tháng (hằng ngày ~5 mã có thay đổi, hằng tuần 30 mã); thinking tính như token đầu ra (chưa xác minh riêng cho adaptive thinking).

| Hạng mục | Ước tính |
|---|---|
| News digest (Sonnet 5.5) | ~$18/tháng (sync); Batch chỉ giảm được phần backfill; Haiku 4.5 chỉ rẻ hơn ~$9/tháng |
| Vĩ mô | ~$3 hằng ngày + ~$2 rà soát tuần bằng Opus |
| Synthesis (230 report) | ~$31 nếu Sonnet, ~$62 nếu Opus 5.5 |
| Bull/Bear + Verifier | ~$7 |
| Hermes (kích hoạt, chat, lịch sử, kết quả tool) | Chưa tính; cần đo riêng ở giai đoạn 0 |
| **Tổng nền** | **khoảng $60-130/tháng** |
| Chat on-demand | ~$0,05/lần `quick`; ~$0,13-0,27/lần `full` |

Prompt caching giảm thêm chi phí phần system prompt ổn định (đọc cache $0,20/triệu token, so với $2 đầu vào của Sonnet 5.5). Giới hạn chi phí theo lần chạy và theo ngày đặt trong `limits`.

#### 5.7.8. Ràng buộc kỹ thuật của dòng Claude 5.5 (đã kiểm chứng từ tài liệu chính thức)

- Thinking thích ứng, không tắt được trên Opus 5.5; điều khiển bằng `output_config.effort`.
- Không ép gọi tool (`tool_choice` kiểu `any` hoặc chỉ định tool): Opus 5.5 và Sonnet 5.5 trả lỗi 400. Muốn JSON đúng schema thì dùng structured outputs hoặc strict tool use với `tool_choice: auto`.
- Sonnet 5.5 từ chối tham số lấy mẫu, prefill của assistant và ngân sách thinking.
- Kiến thức của model dừng ở tháng 6/2026; sự kiện sau đó phải đến từ dữ liệu của pipeline.
- **Vòng đời:** Haiku 4.5 ở trạng thái Active nhưng ngày ngừng hỗ trợ "không sớm hơn 15/10/2026"; Anthropic đã thông báo Haiku 5.5 sẽ ra trong vài tuần tới, chưa có ngày/giá/mã. Không hard-code tên model trong code.

| Khẳng định | Kết luận |
|---|---|
| Opus 5.5 $4/$20, 1M ngữ cảnh, tối đa 128K đầu ra | ✅ Đúng (tài liệu chính thức) |
| Sonnet 5.5 $2/$10, 1M, 128K, thinking thích ứng | ✅ Đúng (tài liệu chính thức) |
| Batch giảm 50%; đọc cache Opus 5.5 $0,20 | ✅ Đúng (trang giá, trang "What's new") |
| Ép dùng tool bị từ chối trên Opus 5.5 và Sonnet 5.5 | ✅ Đúng (migration guide) |
| Sonnet 5.5 từ chối `temperature` và tham số lấy mẫu | ✅ Đúng cho Sonnet 5.5; Opus 5.5 chưa thấy nêu rõ |
| Web search $10 mỗi 1.000 lượt | ✅ Đúng (tài liệu web search) |
| Haiku 4.5 bị ngừng vào 15/10/2026 | 🔶 Sai lệch: đó là mốc "không sớm hơn", chưa có ngày ngừng chính thức |
| Fable 5.1 giá $10/$50 | ⚠️ Chỉ có nguồn thứ ba |
| Sonnet 5.5 hỗ trợ đọc ảnh chart | ❓ Chưa xác minh (chỉ xác nhận cho Haiku 4.5), cần thử |
| Claude xử lý tin tài chính tiếng Việt đủ tốt | ❓ Không thể xác minh bằng tài liệu; phải đo bằng bộ mẫu vàng |
| Thinking tính như token đầu ra | ❓ Chưa xác minh cho adaptive thinking; theo dõi trường `usage` |
| Hermes Agent tương thích các thay đổi của dòng 5.5 | ❓ Chưa xác minh; dòng 5.5 mới ra vài ngày, cần thử ở giai đoạn 0 |

#### 5.7.9. An toàn vận hành

- Model bị ngừng hoặc lỗi thì chỉ chuyển sang `fallback` đã khai báo; không có fallback thì bỏ vai đó và ghi "không đủ dữ liệu", không tự hạ xuống model yếu hơn (fail closed).
- Trần chi phí theo lần chạy và theo ngày, cảnh báo qua Telegram.
- Vai xử lý nội dung không đáng tin (tin tức, kết quả web search) không có tool và không có quyền ghi.
- `validation.forbid_params` chặn tham số không tương thích ngay khi khởi động, thay vì lỗi lúc chạy.

### 5.8. Vị thế đang giữ và luận điểm

**Vị thế (`positions`)**

- Khai báo bằng `/dangiu <mã> [giá vốn]` và `/khonggiu <mã>` (tự khai, không kết nối tài khoản môi giới); lưu bền trong DB để cron và các phiên chat sau đều dùng chung. Đây là nguồn duy nhất của `holding_state`.
- Chỉ dùng để chọn nhãn (Nắm giữ, Giảm tỷ trọng/thoát) và làm tham số cho `risk_plan`; không đổi phần đánh giá thị trường (mục 4.5).
- Mã chưa khai báo thì `holding_state = unknown`; mã đã gỡ bằng `/khonggiu` thì `none` (mục 5.6).

**Luận điểm (`theses`)**

- Mỗi mã có một luận điểm đang hiệu lực: tóm tắt ngắn, các trụ cột (ví dụ tăng trưởng lợi nhuận, định giá, xu hướng) và **điều kiện vô hiệu hóa đo được bằng code** (ví dụ "ROE dưới X trong 4 quý liên tiếp", "đóng cửa tuần dưới vùng hỗ trợ Y", "lợi nhuận quý thấp hơn Z% so với kỳ vọng đã ghi").
- Vai Synthesis đề xuất luận điểm và điều kiện vô hiệu hóa theo schema; code kiểm tra điều kiện có cú pháp hợp lệ và tham chiếu trường dữ liệu có thật. Điều kiện nào code không kiểm được thì bị từ chối hoặc gắn cờ "cần soát thủ công".
- Hằng ngày cron chỉ **đánh giá lại các điều kiện** (rẻ, không gọi LLM); `thesis_invalidated` do code xác định. Luận điểm mới đóng luận điểm cũ (`valid_to`), giữ lại lịch sử.
- Trạng thái `thesis_status`: `active` | `weakened` (một phần điều kiện xấu đi) | `invalidated`.

---

## 6. Hợp đồng dữ liệu

```json
{
  "agent": "technical",
  "ticker": "FPT",
  "as_of": "2026-09-29T15:05:00+07:00",
  "sources": ["vnstock", "hose.vn"],
  "data": { "trend": "up", "rsi14": 61.2, "support": [128.5, 124.0], "resistance": [136.0] },
  "signal": { "bias": "bullish", "strength": 0.7 },
  "confidence": 0.8,
  "warnings": ["thanh khoản thấp hơn TB20"]
}
```

`as_of` và `sources` là bắt buộc. Cổng kiểm tra dựa vào đó để phát hiện dữ liệu cũ.

## 7. Cơ sở dữ liệu: lựa chọn, lược đồ và vòng đời dữ liệu

### 7.1. Lựa chọn loại cơ sở dữ liệu

Quy mô thực tế rất nhỏ. Với VN30 theo ngày, mỗi bảng chuỗi thời gian chỉ tăng cỡ vài nghìn dòng mỗi năm (30 mã × ~250 phiên ≈ 7.500 dòng/năm cho giá). Phần tăng nhanh nhất là snapshot JSON, report và tin tức; ước lượng thô là vài trăm MB đến khoảng 1-2 GB mỗi năm nếu không có chính sách dọn dẹp.

| Phương án | Đánh giá |
|---|---|
| **PostgreSQL (đề xuất)** | Một VPS với nhiều tiến trình ghi đồng thời (cron, chat, job chấm điểm, job dọn dữ liệu); có ràng buộc khóa, giao dịch, JSONB, phân vùng theo thời gian, phân quyền theo role. Khớp với lược đồ bên dưới (`NUMRANGE`, `TEXT[]`, `JSONB`) |
| SQLite | Dùng được cho MVP (một file, không cần dịch vụ), nhưng kém khi nhiều tiến trình ghi và khó phân quyền chỉ-đọc theo bảng. Nếu bắt đầu bằng SQLite, giữ SQL tương thích để chuyển sang PostgreSQL |
| TimescaleDB / InfluxDB / ClickHouse | Chưa cần: dữ liệu quá nhỏ. Xem lại nếu mở rộng cả thị trường hoặc dữ liệu intraday/tick |
| DuckDB + Parquet | Tùy chọn để phân tích lịch sử và làm kho lưu trữ lạnh, không thay DB chính |

Nguyên tắc dữ liệu:

- **Append-only và idempotent:** chuỗi thời gian ghi bằng upsert theo khóa tự nhiên (`ticker`, `trade_date`), chạy lại pipeline không tạo bản ghi trùng.
- **Point-in-time:** mỗi bản ghi có `fetched_at`/`as_of` và `source`; báo cáo tài chính có `published_date` để không dùng số liệu chưa công bố tại thời điểm cần xét; lịch sử thành viên VN30 có `valid_from`/`valid_to`.
- **Lưu giá thô và sự kiện điều chỉnh:** giá điều chỉnh tính khi đọc, vì cổ tức/tách cổ phiếu làm đổi giá lịch sử.
- **Phân quyền theo role:** `mcp_ro` (chỉ SELECT, cho tool hỏi đáp qua Telegram), `pipeline_rw` (ghi dữ liệu và kết quả chạy), `retention_job` (duy nhất có quyền xóa/lưu trữ).
- **Sao lưu:** `pg_dump` hằng ngày, sao chép ra ngoài VPS, thử khôi phục định kỳ. Dự báo và kết quả chấm điểm là dữ liệu **không thể tạo lại** nên ưu tiên sao lưu.

### 7.2. Lược đồ chạy và dự báo

```sql
CREATE TABLE runs (
  run_id       TEXT PRIMARY KEY,
  mode         TEXT NOT NULL,          -- scheduled_pre | scheduled_post | on_demand
  tickers      TEXT[] NOT NULL,
  style        TEXT NOT NULL,
  depth        TEXT NOT NULL,
  as_of        TIMESTAMPTZ NOT NULL,
  snapshot_ref TEXT NOT NULL,
  report_md    TEXT,
  warnings     JSONB,
  cost_tokens  INTEGER,
  config_hash  TEXT,                   -- băm của config/models.yaml lúc chạy (mục 5.7)
  created_at   TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE predictions (
  id           BIGSERIAL PRIMARY KEY,
  run_id       TEXT REFERENCES runs(run_id),
  source       TEXT NOT NULL,          -- cron | on_demand
  ticker       TEXT NOT NULL,
  trigger      TEXT NOT NULL,          -- first | label_change | thesis_change | horizon_refresh (mục 10.1)
  thesis_id    BIGINT REFERENCES theses(id),
  action_label TEXT NOT NULL,          -- buy_accumulate | watch | hold | reduce_exit | stay_out (mục 5.6)
  universe_tier TEXT NOT NULL DEFAULT 'A',  -- A (VN30) | B (ngoài VN30, đạt ngưỡng), mục 4.7
  holding_state TEXT DEFAULT 'unknown',     -- holding | none | unknown (người dùng tự khai)
  coverage      JSONB,                      -- trạng thái từng thành phần (ok/partial/missing) và weight_coverage
  config_hash   TEXT,                       -- cấu hình model đã tạo ra dự báo này (mục 5.7)
  signal_type  TEXT NOT NULL,          -- loại luận điểm: valuation | earnings | trend | flow | mixed
  entry_zone   NUMRANGE,
  stop_loss    NUMERIC,
  target       NUMERIC,
  horizon_days INTEGER,
  confidence   NUMERIC,
  created_at   TIMESTAMPTZ DEFAULT now(),
  status       TEXT DEFAULT 'open'     -- open | closed; kết quả chi tiết ở prediction_outcomes
);

CREATE TABLE feedback (
  id           BIGSERIAL PRIMARY KEY,
  run_id       TEXT REFERENCES runs(run_id),   -- report mà feedback nhắm tới (nếu có)
  channel      TEXT NOT NULL,                  -- telegram
  message      TEXT NOT NULL,
  created_at   TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE skill_proposals (
  id            BIGSERIAL PRIMARY KEY,
  feedback_id   BIGINT REFERENCES feedback(id),
  skill_name    TEXT NOT NULL,
  diff          TEXT NOT NULL,                 -- bản sửa trong skills-staging/
  status        TEXT NOT NULL DEFAULT 'pending', -- pending | approved | rejected | reverted
  regression_ok BOOLEAN,                       -- kết quả kiểm thử hồi quy
  commit_ref    TEXT,                          -- commit Git sau khi duyệt
  created_at    TIMESTAMPTZ DEFAULT now(),
  decided_at    TIMESTAMPTZ
);
```

### 7.3. Lược đồ dữ liệu thị trường (gợi ý)

```sql
CREATE TABLE tickers (                   -- danh mục toàn thị trường (chỉ định danh); dữ liệu chi tiết chỉ có cho VN30 + mã đã xác nhận
  ticker TEXT PRIMARY KEY, name TEXT, exchange TEXT, sector TEXT,
  industry_group TEXT,                   -- bank | securities | insurance | real_estate | other (bộ chỉ số theo ngành, mục 5.4.1)
  listed_date DATE, delisted_date DATE
);

CREATE TABLE index_membership (          -- lịch sử thành viên VN30 (point-in-time)
  index_code TEXT, ticker TEXT REFERENCES tickers, valid_from DATE, valid_to DATE,
  PRIMARY KEY (index_code, ticker, valid_from)
);

CREATE TABLE prices_daily (              -- giá thô theo VND đầy đủ (đã chuẩn hóa đơn vị, mục 8); giá điều chỉnh tính khi đọc
  ticker TEXT, trade_date DATE,
  open NUMERIC, high NUMERIC, low NUMERIC, close NUMERIC,
  volume BIGINT, value NUMERIC,
  source TEXT NOT NULL, fetched_at TIMESTAMPTZ NOT NULL,
  PRIMARY KEY (ticker, trade_date)
);

CREATE TABLE price_adjustments (         -- cổ tức, tách/gộp, phát hành thêm
  ticker TEXT, ex_date DATE, kind TEXT, factor NUMERIC,
  PRIMARY KEY (ticker, ex_date, kind)
);

CREATE TABLE foreign_flow_daily (
  ticker TEXT, trade_date DATE,
  buy_value NUMERIC, sell_value NUMERIC, net_value NUMERIC, room_left NUMERIC,
  source TEXT NOT NULL, fetched_at TIMESTAMPTZ NOT NULL,
  PRIMARY KEY (ticker, trade_date)
);

CREATE TABLE fundamentals_quarterly (
  ticker TEXT, period TEXT,              -- ví dụ '2026Q2'
  report_type TEXT,                      -- self_prepared | reviewed | audited (bản tự lập, soát xét, kiểm toán)
  version INTEGER,                       -- tăng khi báo cáo được điều chỉnh
  metrics JSONB NOT NULL, published_date DATE,   -- nếu nguồn không có ngày công bố, dùng fetched_at làm mốc thận trọng
  source TEXT NOT NULL, fetched_at TIMESTAMPTZ NOT NULL,
  PRIMARY KEY (ticker, period, report_type, version)
);

CREATE TABLE corporate_events (
  id BIGSERIAL PRIMARY KEY, ticker TEXT, event_type TEXT, event_date DATE,
  payload JSONB, source_url TEXT, fetched_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE news_items (                -- tăng nhanh nhất, phân vùng theo tháng
  id BIGSERIAL, published_at TIMESTAMPTZ NOT NULL,
  tickers TEXT[], source TEXT, url TEXT, url_hash TEXT,
  title TEXT, summary TEXT, sentiment NUMERIC, fetched_at TIMESTAMPTZ NOT NULL,
  PRIMARY KEY (id, published_at)
) PARTITION BY RANGE (published_at);

CREATE TABLE prediction_outcomes (       -- một dòng cho mỗi mốc chấm điểm
  prediction_id BIGINT REFERENCES predictions(id),
  horizon_days INTEGER,                  -- 20 / 60 / 120 / 250 phiên
  graded_at TIMESTAMPTZ NOT NULL,
  filled BOOLEAN,                        -- vùng vào có được khớp không (mục 10.2)
  entry_price NUMERIC,                   -- giá vào theo quy tắc chấm điểm, đã điều chỉnh
  exit_reason TEXT,                      -- horizon | stop | target | not_filled
  ret NUMERIC, excess_vs_vn30 NUMERIC, thesis_status TEXT,
  PRIMARY KEY (prediction_id, horizon_days)
);

CREATE TABLE retention_log (             -- nhật ký job dọn/lưu trữ dữ liệu
  id BIGSERIAL PRIMARY KEY, run_at TIMESTAMPTZ DEFAULT now(),
  object_name TEXT, action TEXT, rows_affected BIGINT, archive_ref TEXT, dry_run BOOLEAN
);

CREATE TABLE watchlist_extra (           -- mã ngoài VN30, chỉ thêm sau khi người dùng xác nhận (mục 4.7)
  ticker TEXT PRIMARY KEY REFERENCES tickers,
  confirmed_at TIMESTAMPTZ NOT NULL,     -- thời điểm người dùng xác nhận tải bổ sung
  added_by TEXT,                         -- ID Telegram trong allowlist
  last_interaction_at TIMESTAMPTZ,       -- dùng cho quy tắc tạm dừng sau nhiều ngày không tương tác
  status TEXT NOT NULL DEFAULT 'active'  -- active | paused | removed
);

CREATE TABLE theses (                    -- luận điểm phiên bản hóa (mục 5.8)
  id BIGSERIAL PRIMARY KEY, ticker TEXT REFERENCES tickers,
  version INTEGER NOT NULL, summary TEXT NOT NULL,
  pillars JSONB NOT NULL,                -- các trụ cột, có chỉ số đo được
  invalidation_rules JSONB NOT NULL,     -- điều kiện vô hiệu hóa, code kiểm được
  status TEXT NOT NULL DEFAULT 'active', -- active | weakened | invalidated
  valid_from TIMESTAMPTZ NOT NULL, valid_to TIMESTAMPTZ,
  created_by_run TEXT REFERENCES runs(run_id)
);

CREATE TABLE positions (                 -- vị thế tự khai qua /dangiu (mục 5.8)
  ticker TEXT PRIMARY KEY REFERENCES tickers,
  avg_cost NUMERIC,                      -- giá vốn (tùy chọn)
  declared_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  declared_by TEXT NOT NULL              -- ID Telegram trong allowlist
);

CREATE TABLE trading_calendar (          -- nguồn duy nhất cho mọi phép tính theo phiên
  trade_date DATE PRIMARY KEY, is_trading_day BOOLEAN NOT NULL, note TEXT
);

CREATE TABLE jobs (                      -- hàng đợi job và chống chạy trùng (mục 4.8)
  job_id TEXT PRIMARY KEY, kind TEXT NOT NULL,
  dedupe_key TEXT NOT NULL UNIQUE,       -- ví dụ 'scheduled_post:2026-09-29'
  status TEXT NOT NULL,                  -- queued | running | done | failed | expired
  attempts INTEGER DEFAULT 0, run_id TEXT,
  scheduled_for TIMESTAMPTZ, started_at TIMESTAMPTZ, finished_at TIMESTAMPTZ, error TEXT
);

CREATE TABLE data_quality_log (          -- kết quả kiểm tra chất lượng dữ liệu (mục 4.8)
  id BIGSERIAL PRIMARY KEY, run_id TEXT, ticker TEXT,
  check_name TEXT NOT NULL, result TEXT NOT NULL,   -- pass | warn | fail
  detail JSONB, created_at TIMESTAMPTZ DEFAULT now()
);

CREATE UNIQUE INDEX one_open_prediction  -- không ghi dự báo trùng khi nhãn/luận điểm không đổi (mục 10.1)
  ON predictions (ticker, action_label, COALESCE(thesis_id, 0)) WHERE status = 'open';
```

Thứ tự tạo bảng do migration đảm nhiệm (các khóa ngoại giữa `predictions`, `theses`, `runs`, `tickers` được tạo sau khi đủ bảng). Bảng `trading_calendar` cần nạp trước mỗi năm và kiểm tra lại khi sàn công bố lịch nghỉ.

Ghi chú: không lưu toàn văn bài báo, chỉ lưu tiêu đề, URL, tóm tắt do hệ thống tạo và sentiment. Cách này giảm dung lượng và giảm rủi ro về điều khoản sử dụng nguồn tin. Chỉ báo kỹ thuật là dữ liệu suy ra, không cần bảng lưu dài hạn (tính khi cần hoặc cache ngắn hạn).

### 7.4. Vòng đời dữ liệu: giữ, nén, lưu trữ, xóa

Dữ liệu được chia theo mức độ "không thể tạo lại" và tốc độ mất giá trị. Các mốc dưới đây là **giá trị mặc định gợi ý**, đặt trong cấu hình để chỉnh sau khi có dữ liệu thực tế.

| Nhóm | Bảng/tệp | Chính sách |
|---|---|---|
| Không thể tạo lại | `predictions`, `prediction_outcomes`, `runs` (metadata), `theses`, `positions`, `feedback`, `skill_proposals` | **Giữ vĩnh viễn**, dung lượng nhỏ, là bằng chứng đo hiệu quả; sao lưu ưu tiên cao nhất |
| Dữ liệu thị trường nhỏ | `prices_daily`, `foreign_flow_daily`, `fundamentals_quarterly`, `corporate_events`, `tickers`, `index_membership`, `price_adjustments`, `trading_calendar` | **Giữ vĩnh viễn** (vài nghìn dòng/năm); có bản sao riêng vì nguồn vnstock có thể đổi hoặc giới hạn lịch sử |
| Tin tức | `news_items` | Giữ đầy đủ 24 tháng. Sau đó chỉ giữ bản ghi gắn với dự báo/luận điểm; phần còn lại xuất ra lưu trữ (Parquet hoặc jsonl.gz) rồi xóa |
| Snapshot JSON và `report_md` | trong `runs` hoặc tệp tham chiếu | Giữ đầy đủ 90 ngày, **và** giữ đến khi dự báo liên quan chấm xong mốc cuối. Sau đó nén và lưu trữ; xóa bản gốc sau 24 tháng |
| Dữ liệu suy ra | ảnh chart PNG, cache chỉ báo | Chart giữ 90 ngày rồi xóa (vẽ lại được từ giá); cache chỉ báo tối đa 400 ngày |
| Nhật ký vận hành | `llm_calls` (chi phí/token, độ trễ), `jobs`, `data_quality_log`, log | Chi tiết 90 ngày rồi tổng hợp theo tháng; prompt/phản hồi thô lưu tệp và xóa sau 90 ngày, riêng dữ liệu của thí nghiệm đang chạy được giữ đến khi kết thúc |

Cơ chế thực hiện:

1. **Phân vùng theo thời gian** cho bảng tăng nhanh (`news_items`, và snapshot nếu lưu trong DB), theo tháng. Bỏ phân vùng cũ (`DETACH`/`DROP`) nhanh hơn `DELETE` hàng loạt và không gây phình bảng. Các bảng chuỗi thời gian nhỏ (`prices_daily`...) không cần phân vùng.
2. **Job dọn dữ liệu chạy ngoài Hermes** (cron hệ thống hoặc `pg_cron`, không dùng LLM), hằng tuần, ngoài giờ giao dịch, bằng role `retention_job`. Mỗi lần chạy theo thứ tự: chạy thử (`dry_run`) và ghi số dòng sẽ xử lý → xuất ra lưu trữ → kiểm tra số dòng và checksum → mới xóa/tách phân vùng → ghi `retention_log`.
3. **Không xóa dữ liệu còn được tham chiếu:** bỏ qua snapshot/tin gắn với dự báo chưa chấm xong mốc cuối hoặc luận điểm còn hiệu lực.
4. **Nén và lưu trữ lạnh:** dữ liệu quá hạn xuất sang Parquet/jsonl.gz trên đĩa VPS hoặc kho ngoài, có thể nạp lại khi cần điều tra.
5. **Sao lưu:** `pg_dump` hằng ngày, giữ 7 bản ngày, 4 bản tuần, 12 bản tháng, có một bản ngoài VPS; thử khôi phục mỗi quý. Chạy sao lưu trước lần dọn dữ liệu đầu tiên.
6. **Theo dõi dung lượng:** cảnh báo qua Telegram khi đĩa hoặc DB vượt ngưỡng.

Với chính sách trên, dung lượng ước tính ở mức vài GB sau nhiều năm với VN30; VPS đĩa 20-40 GB là đủ (ước lượng thô, cần đo lại sau vài tuần vận hành thật).

### 7.5. Loại thông tin cũ khỏi ngữ cảnh của agent

"Hết giá trị tham khảo" không chỉ là chuyện xóa dữ liệu; còn phải tránh đưa thông tin cũ vào report. Các quy tắc áp dụng ở tầng truy vấn, không phụ thuộc việc dữ liệu đã bị xóa hay chưa:

- **Cửa sổ thời gian khi nạp vào prompt:** tin tức mặc định 90 ngày gần nhất (trung/dài hạn); tin cũ hơn chỉ đưa vào nếu gắn với luận điểm đang theo dõi.
- **Sự kiện đã qua** (`event_date` < hôm nay) không xuất hiện ở mục "sự kiện sắp tới".
- **Luận điểm được phiên bản hóa** (bảng `theses`, mục 5.8): khi có luận điểm mới thì luận điểm cũ bị đóng (`valid_to`), report chỉ dùng bản đang hiệu lực và nêu bản cũ khi cần giải thích thay đổi.
- **Hit-rate tính trên cửa sổ trượt** (ví dụ 12-24 tháng) và luôn kèm số mẫu, tránh kết luận từ ít quan sát hoặc từ chế độ thị trường đã đổi.
- **Cờ dữ liệu cũ:** mọi dữ liệu có `as_of` cũ hơn phiên gần nhất bị gắn cờ theo cổng Phase 4 (mục 3).

---

## 8. Đặc thù thị trường Việt Nam (cấu hình trong `vn-rules.yaml`)

Các quy định này có thể thay đổi, **không hard-code**, cần xác nhận với công ty chứng khoán/sở giao dịch:

- Biên độ dao động: HOSE ±7%, HNX ±10%, UPCoM ±15%. Stop-loss phải tính đến việc kẹt sàn không thoát được.
- Chỉ mua bán trong nước, bán khống hầu như không khả dụng. Kịch bản giảm thiên về đứng ngoài hoặc hạ tỷ trọng.
- Chu kỳ thanh toán: mua ngày T, cổ phiếu về tài khoản chậm nhất 13h00 ngày T+2, bán được từ phiên chiều T+2 (thường gọi T+2.5). **Có thể thay đổi khi hệ thống KRX vận hành đầy đủ; cần xác minh nguồn chính thức.**
- Lô chẵn 100 cổ phiếu, các phiên ATO/ATC.
- Nhóm cổ phiếu bị cảnh báo/kiểm soát/hạn chế giao dịch, danh mục margin, room ngoại: cần lọc.
- Thanh khoản: giới hạn khối lượng gợi ý theo % thanh khoản trung bình.
- Cờ cảnh báo "hạ tầng thanh toán bất thường": từng có sự cố bù trừ toàn thị trường ngày 27/02/2026 khiến cổ phiếu về tài khoản trễ.
- **Đơn vị giá:** nhiều nguồn dữ liệu VN trả giá theo nghìn đồng. Chuẩn hóa về VND đầy đủ ở lớp `providers/`, có unit test và kiểm tra tự động (mục 4.8), vì sai đơn vị làm lệch 1.000 lần các ngưỡng thanh khoản, R:R và khối lượng gợi ý. Cần kiểm tra thực tế với vnstock.
- **Cổ tức bằng cổ phiếu, cổ phiếu thưởng, phát hành thêm:** khá phổ biến; giá thô giảm mà không phải do thị trường. Lưu sự kiện điều chỉnh (`price_adjustments`), quy đổi vùng vào/cắt lỗ/mục tiêu đã ghi khi qua ngày GDKHQ, và chấm điểm theo lợi suất đã điều chỉnh (mục 10.3).
- **Giờ đóng cửa và thời điểm dữ liệu chốt:** khung giờ giao dịch và giờ đóng cửa cần xác nhận với sàn, nhất là khi hệ thống KRX thay đổi; dữ liệu khối ngoại và một số chỉ số có thể cập nhật muộn hơn giá (mục 4.2).
- **Cơ cấu lại VN30 định kỳ:** mã có thể vào hoặc ra khỏi rổ; VN30 là chỉ số giá (không tính cổ tức). Quy tắc xử lý ở mục 10.4.
- **Nhóm ngân hàng, chứng khoán, bảo hiểm** có cấu trúc báo cáo tài chính khác doanh nghiệp sản xuất; dùng bộ chỉ số riêng (mục 5.4.1).

---

## 9. Guardrails và an toàn

- Không cấp API key sàn/môi giới, không có quyền đặt lệnh ở giai đoạn này.
- Nhãn hành động do code sinh; LLM và cổng Phase 4 chỉ được hạ nhãn, không được nâng nhãn; ngưỡng chỉ chỉnh thủ công trong Git (mục 5.6).
- Mã ngoài VN30 phải qua cổng độ phủ dữ liệu (mục 4.7): thiếu dữ liệu thì trả lời "không đủ dữ liệu để đánh giá", không điền chỗ trống, không âm thầm chia lại trọng số; mọi báo cáo hiển thị `weight_coverage`.
- Chạy Hermes trong Docker, giữ chế độ duyệt lệnh; bật tool riêng theo nền tảng (cron, chat).
- Allowlist người dùng cho gateway; giới hạn tần suất `depth="full"`.
- Skill lõi trong Git, so sánh thư mục `skills/` định kỳ để phát hiện skill tự sinh làm hành vi trôi. Sửa skill qua chat chỉ đi theo vòng đề xuất → duyệt → kiểm thử hồi quy → commit (mục 4.6); không dùng `/yolo`.
- Bộ nhớ Hermes chỉ dùng cho sở thích/cấu hình (watchlist, khung thời gian), không lưu "niềm tin" về thị trường.
- Đặt secret trong `.env`; thử bằng `cron run` trước khi bật tự động, vì cron có thể chạy trong môi trường hạn chế.
- Kiểm tra điều khoản sử dụng/bản quyền của mọi nguồn dữ liệu.
- **Giấy phép vnstock (license-2026.09):** tách quyền dùng phần mềm khỏi quyền dùng dữ liệu của nguồn bên thứ ba; điều kiện truy cập, lưu trữ, hiển thị và phân phối dữ liệu áp dụng theo từng nguồn phía sau (cần tự kiểm tra). Bản Cộng đồng dành cho học tập, nghiên cứu và dự án cá nhân; phân phối lại phần mềm và vận hành dịch vụ cung cấp dữ liệu thô cho bên thứ ba cần thỏa thuận riêng. Cấm né hạn mức (nhiều tài khoản, giả mạo thiết bị) và cấm tạo tải bất thường. Dùng cá nhân thì không vướng; nếu định chia sẻ hoặc thu phí, hỏi support@vnstocks.com và thiết kế lớp `providers/` để thay được nguồn. Gói đang dùng là Cộng đồng, phù hợp với mục đích cá nhân của dự án.

---

## 10. Đánh giá hiệu quả

- **Forward test là bằng chứng chính:** ghi dự báo thật từ ngày chạy, chấm dần theo thời gian.
- **Không tin backtest dùng LLM trên dữ liệu cũ:** model có thể đã nhớ kết quả; chỉ dặn prompt "tránh look-ahead" là rào chắn yếu. Nếu cần backtest, phần quy tắc tất định (không LLM) mới backtest được đáng tin.
- **Chỉ số theo dõi:** hit-rate theo `signal_type`, R:R thực tế, độ lệch giữa `cron` và `on_demand`, tỷ lệ report bị cổng Phase 4 chặn, chi phí/token mỗi report, độ trễ.
- **Mốc đánh giá theo nhiều khung:** vì dự báo trung/dài hạn, chấm ở các mốc 20, 60, 120 phiên (và 250 phiên khi đủ dữ liệu), so với VN-Index/VN30 (lợi suất vượt trội), không chỉ đúng/sai tuyệt đối. Song song chấm theo **luận điểm**: KQKD công bố có đúng kỳ vọng đã nêu không, các điều kiện vô hiệu hóa có bị kích hoạt không.
- **Kỳ vọng thực tế:** mốc 20 phiên có kết quả sau khoảng 1 tháng, mốc 60/120 phiên tích lũy dần theo quý. Chỉ mở rộng phạm vi khi số liệu đủ tin cậy.

### 10.1. Khi nào ghi một dự báo (tránh đếm trùng)

- Không ghi dự báo mới mỗi ngày cho cùng một quan điểm. Chỉ ghi khi (a) mã được phân tích lần đầu, (b) `action_label` đổi, (c) luận điểm đổi phiên bản, hoặc (d) làm mới theo chu kỳ dài (mặc định gợi ý 120 phiên) nếu quan điểm vẫn giữ.
- Chỉ mục duy nhất `one_open_prediction` chặn bản ghi trùng; cột `trigger` ghi lý do.
- Hit-rate tính trên các dự báo độc lập này, kèm số mẫu; không tính các lần cron hằng ngày lặp lại cùng một quan điểm.

### 10.2. Quy tắc khớp lệnh khi chấm điểm

- **Giá vào:** giá mở cửa phiên giao dịch kế tiếp sau thời điểm sinh dự báo (không dùng giá đóng cửa mà phân tích đã dựa vào); không dùng thông tin sau thời điểm đó.
- **Vùng vào:** nếu giá không chạm vùng vào trong cửa sổ chờ (mặc định gợi ý 20 phiên) thì ghi `not_filled` và chấm riêng, không tính là đúng hay sai.
- **Thoát:** chạm cắt lỗ trước thì tính thoát ở cắt lỗ (có xét khả năng kẹt sàn, mục 8); chạm mục tiêu thì thoát ở mục tiêu; không thì thoát ở cuối mốc đánh giá.
- **Chi phí:** trừ phí giao dịch và thuế bán, đặt trong cấu hình.
- Nhãn không có lệnh (Theo dõi, Đứng ngoài) được chấm bằng lợi suất vượt trội của mã so với VN30 trong cùng mốc, để biết hệ thống có bỏ lỡ cơ hội hay không.

### 10.3. Điều chỉnh sự kiện doanh nghiệp

Mọi mức giá đã ghi (vùng vào, cắt lỗ, mục tiêu) được quy đổi theo hệ số điều chỉnh khi qua ngày GDKHQ. Lợi suất chấm điểm là tổng lợi suất (giá điều chỉnh, cộng cổ tức tiền mặt nếu có dữ liệu).

### 10.4. Mã vào hoặc rời VN30

- Mã rời VN30 chuyển sang nhóm B nhưng các dự báo đang mở vẫn được chấm tiếp đến hết mốc.
- VN30 là chỉ số giá, không gồm cổ tức, nên lợi suất vượt trội có thể lệch nhẹ với các mã chi trả cổ tức cao; ghi chú trong báo cáo thống kê, hoặc dùng chỉ số lợi suất tổng nếu có nguồn.

### 10.5. Cỡ mẫu và nhóm B

Với dự báo chỉ ghi khi đổi nhận định (10.1) và mốc chấm 20/60/120 phiên, số mẫu tích lũy chậm; ngưỡng `allow_buy_label_min_graded_samples: 30` cho nhóm B có thể rất lâu mới đạt. Cần quyết định (mục 14): chấp nhận nhãn mua gần như không mở cho nhóm B, hoặc hạ ngưỡng, hoặc dùng thống kê chung của nhóm A cho nhóm B kèm trần độ tin cậy.

---

## 11. Lộ trình triển khai

| Giai đoạn | Việc chính | Tiêu chí hoàn thành |
|---|---|---|
| **0. Nền tảng** | Cài Hermes, `hermes doctor`; nối Telegram qua gateway; dựng repo, Docker, DB; đăng ký API key vnstock, đo hạn mức thực tế, kiểm tra phạm vi hàm (khối ngoại, tin/CBTT) và điều khoản của các nguồn phía sau; dựng bộ mẫu vàng và `models.yaml`, chạy thử so sánh Sonnet 5.5/Opus 5.5/Haiku 4.5, kiểm tra Hermes với các thay đổi của dòng 5.5 (mục 5.7); kiểm tra cấu trúc trả về và đơn vị giá của vnstock, cấu hình bộ nhớ tự động của Hermes, khóa API và hạn mức chi tiêu trong Claude Console (mục 4.8) | Nhắn tin cho bot và nhận trả lời; có bảng ghi nhận hạn mức và phạm vi dữ liệu thực tế; có bảng so sánh model trên bộ mẫu vàng |
| **1. Phase 0 + on-demand** | Dựng PostgreSQL, role `mcp_ro`/`pipeline_rw`/`retention_job`, sao lưu hằng ngày (mục 7.1); pipeline Python + snapshot JSON + bảng dữ liệu thị trường, `runs`, `predictions`; hàm sinh nhãn hành động bằng code (mục 5.6); cổng độ phủ dữ liệu cho mã ngoài VN30 (mục 4.7); kiểm tra chất lượng dữ liệu, `trading_calendar`, `theses`, `positions` (mục 4.8, 5.8); `run_analysis` chế độ `on_demand`, chạy bằng CLI cho một mã | Chạy ổn định cho vài mã, số liệu khớp nguồn, chạy lại pipeline không tạo bản ghi trùng, khôi phục thử từ bản sao lưu thành công, kiểm tra chất lượng dữ liệu bắt được dữ liệu lỗi giả lập |
| **2. Nối chat** | SOUL.md + skill `vn-stock-analyze`; định tuyến ý định; trả lời 2 bước; nhóm tool chỉ đọc; lệnh `/gopy /duyet /tuchoi /hoantac`; `skills-staging/` và kiểm thử hồi quy trên snapshot (mục 4.6); thử nghiệm tách skill lõi chỉ đọc khỏi skill agent tự sinh | Hỏi đáp một mã và hỏi về dữ liệu đã lấy qua Telegram; một đề xuất sửa skill đi trọn vòng duyệt → kiểm thử → commit |
| **3. Cron** | Bọc cùng hàm vào cron sau phiên (VN30); hàng đợi `jobs` và worker gửi Telegram, advisory lock, chạy bù và chống gửi trùng, chờ dữ liệu cuối ngày đủ mới chạy (mục 4.2, 4.8); cache snapshot | Báo cáo sau phiên tự chạy nhiều ngày liên tiếp, kể cả sau khi VPS khởi động lại, không trùng, không thiếu |
| **4. Chấm điểm** | Job chấm dự báo theo nhiều mốc (20/60/120 phiên) và theo luận điểm, ghi vào `prediction_outcomes` theo quy tắc khớp lệnh và điều chỉnh sự kiện doanh nghiệp (mục 10.1-10.4); nạp thống kê vào lần chạy sau; job dọn/lưu trữ dữ liệu chạy thử (`dry_run`) rồi bật thật (mục 7.4) | Có kết quả mốc 20 phiên sau khoảng 1 tháng; mốc 60/120 phiên tích lũy dần; `retention_log` có bản ghi chạy thử đúng như dự kiến |
| **5. Mở rộng LLM** | Vai tin tức/CBTT, vĩ mô hiện thực bằng tool MCP theo `models.yaml`; Bull/Bear cho mã lọt lưới; cổng Phase 4 đầy đủ; chạy shadow test model thách đấu (mục 5.7) | Report có phần mâu thuẫn và kịch bản rõ; có số liệu chi phí và chất lượng theo từng cấu hình |
| **6. Nâng cao** | Đọc chart bằng vision, quét thị trường, cảnh báo intraday, cron trước phiên/tuần | Theo nhu cầu |
| **7. (Tùy chọn)** | Paper trading, đối chiếu danh mục thật | Chỉ khi forward test đạt mức chấp nhận được |

Thứ tự 1-2-3 (on-demand trước, cron sau) giúp gỡ lỗi dễ hơn: mỗi lần thử chỉ một mã, còn cron về sau chỉ là vòng lặp gọi lại cùng hàm.

---

## 12. Cấu trúc thư mục đề xuất

```
~/.hermes/                          # (hoặc profile Hermes đóng gói bằng Git)
├── SOUL.md
├── skills/
│   ├── vn-stock-analyze/SKILL.md
│   ├── vn-market-daily/SKILL.md
│   ├── vn-news-digest/SKILL.md
│   ├── vn-chart-review/SKILL.md
│   └── vn-report-verify/SKILL.md
├── skills-staging/                 # vùng agent được ghi đề xuất sửa skill (mục 4.6)
└── config/vn-rules.yaml            # biên độ, lô, chu kỳ thanh toán, giới hạn rủi ro

vn-market-mcp/                      # dự án riêng, chạy bằng Docker
├── server.py                       # khai báo tool MCP
├── pipeline/                       # Phase 0, run_analysis, cache, khóa
├── worker/                         # hàng đợi job, gửi Telegram, chạy bù (mục 4.8)
├── quality/                        # kiểm tra chất lượng dữ liệu (mục 4.8)
├── providers/                      # vnstock, API sàn, CBTT...
├── rules/                          # cổng Phase 4, risk math (tất định)
├── llm/                            # các vai LLM gọi Claude API, schemas/, prompts/ (mục 5.7)
├── config/models.yaml              # ánh xạ vai sang model, effort, chế độ, giới hạn chi phí (mục 5.7)
├── grading/                        # job chấm điểm, thống kê hit-rate
├── retention/                      # job dọn/lưu trữ dữ liệu, sao lưu (mục 7.4)
├── db/                             # migration SQL
└── tests/, evals/
```

---

## 13. Rủi ro và cách giảm thiểu

| Rủi ro | Giảm thiểu |
|---|---|
| LLM bịa/nhớ sai số liệu | Số chỉ đến từ code; đối chiếu report với snapshot bằng code |
| Dữ liệu cũ hoặc nguồn lỗi | `as_of` bắt buộc, cổng fail closed, gắn cờ |
| Thiên kiến từ câu hỏi dẫn dắt | Quy tắc mục 4.5, ghi `source` để phát hiện lệch |
| Skill/bộ nhớ Hermes tự đổi hành vi | Skill lõi trong Git, bộ nhớ chỉ lưu cấu hình |
| Prompt injection từ tin tức/web dẫn tới sửa skill hoặc thao tác ngoài ý muốn | Subagent tin tức không có tool ghi; chỉ tin nhắn từ ID trong allowlist mới khởi tạo đề xuất; duyệt thủ công + kiểm thử hồi quy (mục 4.6) |
| Sửa skill qua chat làm report cron đổi âm thầm | Skill lõi chỉ đọc, đề xuất qua `skills-staging/`, commit Git kèm changelog, có `/hoantac` |
| Backtest ảo do LLM đã nhớ dữ liệu | Forward test, phần tất định mới backtest |
| Chi phí token tăng | Inject dữ liệu gọn, Bull/Bear chỉ cho mã lọt lưới, model rẻ cho tin tức, giới hạn `depth="full"` |
| Người dùng hỏi mã ngoài VN30 nhưng dữ liệu thiếu, LLM điền khoảng trống bằng trí nhớ | Cổng độ phủ dữ liệu, `weight_coverage` hiển thị, đối chiếu số bằng code, nhóm B bị chặn nhãn "Ưu tiên mua" và trần độ tin cậy (mục 4.7) |
| Dữ liệu phình dần, hoặc dữ liệu cũ gây nhiễu report | PostgreSQL, phân vùng bảng tăng nhanh, job dọn/lưu trữ có `dry_run` và kiểm tra checksum, cửa sổ thời gian khi nạp vào prompt (mục 7.4, 7.5) |
| Mất dữ liệu không thể tạo lại (dự báo, kết quả chấm điểm) | Sao lưu hằng ngày ra ngoài VPS, thử khôi phục định kỳ, chỉ role `retention_job` được xóa |
| Model đổi, ngừng hỗ trợ hoặc không tương thích tham số (ví dụ Haiku 4.5, thay đổi của dòng 5.5) | `config/models.yaml` với `fallback`, `validation.forbid_params`, không hard-code tên model, thử nghiệm ở giai đoạn 0 (mục 5.7) |
| Chi phí LLM vượt dự kiến | Trần chi phí theo lần chạy và theo ngày, ghi `llm_calls`, Batch API và prompt caching, cảnh báo qua Telegram |
| Đếm trùng dự báo làm hit-rate ảo; giá vào và khớp lệnh không thực tế | Ghi dự báo khi đổi nhận định, chỉ mục duy nhất, quy tắc khớp lệnh (mục 10.1-10.2) |
| Cổ tức bằng cổ phiếu và điều chỉnh giá làm lệch cắt lỗ, chấm điểm | `price_adjustments`, quy đổi mức giá và lợi suất tổng (mục 8, 10.3) |
| Lỗi dữ liệu âm thầm khiến cả rổ ra "Đứng ngoài" | Kiểm tra chất lượng dữ liệu, cảnh báo phân biệt lỗi hệ thống với nhận định (mục 4.8) |
| Cron lỡ hoặc gửi trùng; timeout khi tổng hợp lâu | Hàng đợi `jobs`, worker riêng, advisory lock, chạy bù (mục 4.2, 4.8) |
| Nhãn "Nắm giữ/Giảm" thiếu căn cứ hoặc nhảy giữa các ngày | Bảng `positions` và `theses` với điều kiện vô hiệu hóa do code kiểm (mục 5.8) |
| Chấm cơ bản sai cho nhóm ngân hàng và tài chính | Bộ chỉ số theo ngành (mục 5.4.1) |
| Quy định thị trường đổi (KRX, chu kỳ thanh toán) | Đặt trong `vn-rules.yaml`, xác minh định kỳ |
| Vi phạm giấy phép dữ liệu | Lớp `providers/` thay được nguồn; ưu tiên API chính thức |
| Người dùng hiểu report là lời khuyên | Tuyên bố miễn trừ cuối mỗi report; không dùng từ "chắc chắn" |

### 13.1. Rà soát thiết kế ngày 29/09/2026

Rà soát toàn bộ plan v2.8 tìm bẫy kỹ thuật và lỗ hổng logic. Kết quả và nơi xử lý:

| # | Vấn đề | Xử lý tại |
|---|---|---|
| A1 | Dự báo trùng lặp làm hit-rate ảo | 10.1, chỉ mục `one_open_prediction` |
| A2 | Giá vào lấy giá đóng cửa, thiếu quy tắc khớp lệnh và phí | 10.2 |
| A3 | Cổ tức bằng cổ phiếu/phát hành thêm làm lệch mức giá đã ghi | 8, 10.3, `price_adjustments` |
| A4 | Thiếu bảng và quy tắc cho luận điểm, điều kiện vô hiệu hóa | 5.8, bảng `theses` |
| A5 | `confidence` và "nguồn đồng thuận" chưa được định nghĩa | 5.4.1 |
| A6 | Điểm tổng hợp chưa có cách chuẩn hóa | 5.4.1 |
| A7 | Nhóm ngân hàng/tài chính cần bộ chỉ số riêng | 5.4.1, `tickers.industry_group` |
| A8 | BCTC bị ghi đè, look-ahead do ngày công bố | 7.3 (`report_type`, `version`, `published_date`) |
| A9 | Đơn vị giá (nghìn đồng) | 4.8, 8; cần kiểm tra thực tế ở giai đoạn 0 |
| A10 | Regime chỉ cộng điểm, không chặn nhãn mua | 5.4.1 (cổng regime), 5.6 |
| A11 | "Đang nắm giữ" không được lưu giữa các phiên | 5.8, bảng `positions`, `/dangiu` |
| A12 | Cơ cấu lại VN30; VN30 là chỉ số giá | 8, 10.4 |
| B1 | Hermes (LLM) điều phối Phase làm luồng không tất định | 3 ("Ai điều phối?"), nguyên tắc 9 |
| B2 | Lệnh MCP chạy lâu bị timeout | 4.4, 4.8 (`jobs`, worker) |
| B3 | Batch API không đảm bảo thời gian xong | 4.8, 5.7 |
| B4 | Dữ liệu cuối ngày chưa chốt lúc 15:15 | 4.2 |
| B5 | Lỗi dữ liệu âm thầm, phân biệt lỗi hệ thống với "Đứng ngoài" | 4.2, 4.8, `data_quality_log` |
| B6 | Cron lỡ hoặc chạy lại, gửi trùng | 4.2, 4.8, `jobs` |
| B7 | Khóa đồng thời trong bộ nhớ không đủ | 4.4 (advisory lock) |
| B8 | Thiếu lịch giao dịch làm nguồn duy nhất | 4.2, bảng `trading_calendar` |
| B9 | Chi phí của chính Hermes chưa tính | 4.8, 5.7.7; đo ở giai đoạn 0 |
| B10 | Bộ nhớ tự động của Hermes trái nguyên tắc 8 | 4.8; kiểm tra cấu hình ở giai đoạn 0 |
| B11 | Giới hạn độ dài và escape ký tự của Telegram | 4.8 |
| B12 | Đối chiếu số bằng regex dễ sai với số kiểu Việt | 5.7.3 (văn bản LLM không chứa chữ số) |
| B13 | Bí mật, khóa API, hạn mức chi tiêu | 4.8, lộ trình giai đoạn 0 |
| C1 | Mục 3 và 5.2 còn mô tả subagent Hermes | 3, 5.2 (đã sửa) |
| C2 | `style="swing"` và `max_age_minutes=15` kiểu lướt sóng | 4.1, 4.4 (đã sửa) |
| C3 | `bias` trùng `action_label`; `signal_type` kiểu lướt sóng | 7.2 (`trigger`, `thesis_id`, `signal_type` mới) |
| C4 | Ngưỡng 30 mẫu cho nhóm B gần như không đạt được | 10.5; câu hỏi mở ở mục 14 |

Các điểm cần thử thực tế ở giai đoạn 0 (chưa xác minh được từ tài liệu): đơn vị giá và cấu trúc trả về của vnstock, giờ đóng cửa và thời điểm dữ liệu chốt, cấu hình bộ nhớ tự động của Hermes, tương thích của Hermes với dòng Claude 5.5.

---

## 14. Quyết định đã chốt và câu hỏi còn mở

### Đã chốt (29/09/2026)

| Hạng mục | Quyết định | Hệ quả thiết kế |
|---|---|---|
| Phong cách | **Trung/dài hạn** | Cơ bản/định giá là lõi (trọng số 45%); chạy chính sau phiên và theo tuần; cập nhật theo mùa KQKD; stop-loss theo ATR khung tuần; chấm điểm nhiều mốc (mục 10) |
| Nguồn dữ liệu | **API cá nhân của vnstock, gói Cộng đồng (60 request/phút)** | Chỉ dùng cá nhân, không chia sẻ dữ liệu cho người khác; cache cục bộ trong DB để giảm số lần gọi; kiểm tra hạn mức và phạm vi dữ liệu (BCTC, khối ngoại, tin/CBTT) trước khi viết pipeline; nếu thiếu tin/CBTT thì bổ sung nguồn khác và kiểm tra điều khoản |
| Phạm vi dữ liệu | **VN30 nạp sẵn**; mã ngoài VN30 chỉ tải bổ sung khi người dùng quan tâm **và xác nhận**. Không nạp toàn thị trường | Pipeline nhẹ (~360 request nạp ban đầu, ~30 request/ngày), thanh khoản cao giảm rủi ro kẹt sàn/không thoát lệnh; danh sách VN30 rà soát định kỳ nên đặt trong config; mã bổ sung đi qua flow xác nhận và giới hạn ở mục 4.7; độ rộng thị trường tính theo chỉ số và trong nhóm VN30/mã đã theo dõi |
| Nơi chạy | **VPS** | Docker Compose (Hermes, MCP server, DB); đặt múi giờ container là Asia/Ho_Chi_Minh; MCP chỉ bind localhost, không mở cổng dịch vụ ra internet; SSH bằng khóa, tường lửa, sao lưu DB định kỳ; gọi LLM qua API (không chạy model cục bộ) |
| Kênh nhận | **Chỉ Telegram** | Allowlist ID người dùng; tin nhắn giới hạn 4096 ký tự nên report gửi dạng tóm tắt trước, chi tiết theo lệnh (ví dụ `/chitiet HPG`); chart gửi dạng ảnh; không cần cầu nối Zalo/Email |

### Còn mở

1. Xác nhận phân bổ model ở mục 5.7 (Sonnet 5.5 mặc định, Opus 5.5 cho synthesis đầy đủ và vĩ mô hằng tuần) và ngân sách LLM (ước tính nền khoảng $60-130/tháng, chưa tính chat); chốt lại sau khi chạy kiểm thử ở giai đoạn 0.
2. Danh mục: đề xuất dùng vị thế tự khai qua `/dangiu` (mục 5.8) thay vì kết nối tài khoản môi giới; cần bạn xác nhận. Nếu không khai báo vị thế, hệ thống dùng nhãn thay thế cho `holding_state = unknown` (mục 5.6).
3. Kiểm tra thực tế vnstock có cung cấp khối ngoại, tự doanh, margin hay không (cây hàm công bố không liệt kê hàm riêng); nếu không có thì cần nguồn khác cho phần Flow.
4. Xác nhận dùng PostgreSQL và các mốc lưu giữ mặc định ở mục 7.4 (tin tức 24 tháng, snapshot/chart 90 ngày, snapshot lưu trữ thêm đến khi chấm xong mốc cuối rồi xóa bản gốc sau 24 tháng).
5. Nhóm B và cỡ mẫu (mục 10.5): chấp nhận nhãn "Ưu tiên mua" gần như không mở cho mã ngoài VN30, hạ ngưỡng số mẫu, hay dùng thống kê chung của nhóm A kèm trần độ tin cậy?
6. Xác nhận các mặc định mới: cửa sổ chờ vùng vào 20 phiên, chu kỳ làm mới dự báo 120 phiên, ngưỡng cache snapshot 30 phút trong phiên (mục 4.4, 10.1, 10.2), và quy tắc `regime = risk_off` chặn nhãn mua (mục 5.4.1).

---

## 15. Tài liệu tham khảo

**Hermes Agent**
- Hermes Agent (Nous Research): https://github.com/NousResearch/hermes-agent
- Tài liệu chính thức: https://hermes-agent.nousresearch.com/docs
- Gói Hermes nghiên cứu giao dịch (cổ phiếu Mỹ, không đặt lệnh, có cron): https://github.com/tradermonty/hermes-trading-research-agent-work-package
- Skill phân tích thị trường cho Hermes (cổ phiếu Indonesia, crypto, forex): https://github.com/jagres0039/hermes-market-skills

**Dự án tương tự**
- TradingAgents (multi-agent, LangGraph): https://github.com/TauricResearch/TradingAgents
- Pipeline swing-trading thị trường VN (PydanticAI, tự chấm điểm, MCP chỉ đọc): https://github.com/pt-hieu/trade-bot
- Multi-agent giao dịch dựa trên giá cho thị trường VN (LangGraph + vnstock): https://github.com/DungPhanHoangg05/A-Multi-Agent-Large-Language-Model-Approach-to-Price-Driven-Stock-Trading-in-the-Vietnamese-Stock-Ma
- Bot Telegram giao dịch/quét dòng tiền VN: https://github.com/Ninhvu2101/VNStock-TradingBot-247
- VN Stock Advisor (CrewAI): https://github.com/abcxyz91/vn_stock_advisor
- Blueprint context database cho agent phân tích cổ phiếu VN: https://github.com/phanngoc/vn-stock-market-agent

**Dữ liệu và đánh giá**
- vnstock (lưu ý giấy phép cá nhân): https://github.com/vnstock-hq
- Look-ahead bias và memorization trong LLM tài chính: Look-Ahead-Bench (Benhenda, 2026, arXiv:2601.13770) và các nghiên cứu liên quan
- Giấy phép vnstock (license-2026.09): https://vnstocks.com/onboard/giay-phep-su-dung

**Claude API (đã tra cứu ngày 29/09/2026)**
- Claude Opus 5.5: https://platform.claude.com/docs/en/models/opus-5-5/overview
- Migrating to Claude Sonnet 5.5: https://platform.claude.com/docs/en/models/sonnet-5-5/migration-guide
- Giá: https://platform.claude.com/docs/en/about-claude/pricing
- Structured outputs: https://platform.claude.com/docs/en/build-with-claude/structured-outputs
- Web search tool: https://docs.claude.com/en/docs/agents-and-tools/tool-use/web-search-tool
- Claude Haiku 4.5 (vòng đời): https://platform.claude.com/docs/en/models/haiku-4-5/overview
