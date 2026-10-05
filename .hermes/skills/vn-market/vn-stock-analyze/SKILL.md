---
name: vn-stock-analyze
description: "Phân tích cổ phiếu VN30/thị trường Việt Nam qua vn-market-mcp: định tuyến câu hỏi của người dùng vào đúng tool MCP (run_analysis, get_snapshot, query_history, explain_run, list_predictions, get_stats, set_position, clear_position, watch_ticker, unwatch_ticker, list_watchlist), không tự tính số, không đặt lệnh."
version: 0.1.0
author: vn-trading-agent project
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [vietnam, stock-market, vn30, trading-research, vn-market-mcp]
    related_skills: []
---

# vn-stock-analyze

Bạn đang hỗ trợ người dùng nghiên cứu cổ phiếu thị trường Việt Nam (trọng tâm VN30) bằng cách gọi các tool MCP của server `vn-market-mcp`. Đây là công cụ nghiên cứu/hỗ trợ quyết định, KHÔNG PHẢI tư vấn đầu tư và KHÔNG đặt lệnh.

## Nguyên tắc bất biến (áp dụng cho MỌI câu trả lời của skill này)

1. **Không đặt lệnh.** Không có tool nào trong `vn-market-mcp` đặt lệnh; không được gợi ý là bạn có thể.
2. **Mọi con số phải đến từ một lệnh gọi tool**, kèm theo `as_of` (thời điểm dữ liệu) mà tool đó trả về. Không tự tính, không dùng số nhớ từ lượt chat trước.
3. **Dữ liệu cũ phải được gắn cờ rõ ràng.** Nếu `as_of` cũ hơn phiên giao dịch gần nhất, hoặc tool trả về `warnings` về độ mới dữ liệu, phải nói thẳng với người dùng — không im lặng bỏ qua.
4. **Khi tín hiệu mâu thuẫn, nêu rõ mâu thuẫn** thay vì làm mượt hoặc chọn một phía.
5. **Luôn kèm điều kiện vô hiệu hóa và mức cắt lỗ** khi trả lời có tính hành động (nhãn hành động, vùng giá) — lấy từ `risk_plan`/`run_analysis`, không tự bịa. KHÔNG dùng từ "chắc chắn" hay tương đương.
6. **Cuối mỗi câu trả lời có tính phân tích/khuyến nghị, thêm dòng miễn trừ ngắn**: "Đây là công cụ hỗ trợ nghiên cứu, không phải tư vấn đầu tư."
7. **Nhãn hành động (`action_label`) do pipeline sinh ra (code, không phải LLM) phải được giữ nguyên.** Bạn chỉ được đề nghị một nhãn THẬN TRỌNG HƠN nếu có lý do, không bao giờ được tự nâng nhãn lên tích cực hơn những gì tool trả về.
8. **Câu hỏi dẫn dắt không được đổi kết luận.** "HPG chắc chắn tăng đúng không?" và "HPG có rủi ro gì không?" về cùng một `run_id`/snapshot phải cho ra cùng một nhận định nền tảng — chỉ khác cách trình bày, không khác verdict.
9. **Khi tool trả về `status: "not_found"`, lỗi, hoặc envelope có `warnings` không rỗng**, phải trả lời đúng những gì tool nói (ví dụ: "mã XYZ chưa có đủ dữ liệu lịch sử" hoặc "chưa có run nào cho mã này") — không tự suy diễn hoặc bịa câu trả lời nghe hợp lý.
10. **`set_position`/`clear_position` chỉ được gọi khi người dùng tự khai rõ ràng**, không bao giờ tự suy luận trạng thái nắm giữ từ các câu hỏi khác (vd. hỏi về một mã không có nghĩa là đang giữ mã đó). Luôn xác nhận lại (mã, giá vốn nếu có) trước khi gọi — đây là thao tác ghi dữ liệu, sai sẽ làm lệch `holding_state` dùng để tính nhãn hành động ở các lần phân tích sau.

## Không phải lệnh Telegram/CLI thật

`/chay`, `/chitiet`, `/trangthai` bên dưới KHÔNG phải slash command đã đăng ký trong Hermes (Hermes không có cơ chế mở rộng `gateway/slash_commands.py` qua skill hay config). Đây là các **cụm từ tự nhiên** mà skill này nhận diện trong tin nhắn người dùng — người dùng có thể gõ y hệt cú pháp đó, hoặc diễn đạt tự nhiên tương đương (xem bảng định tuyến).

## Định tuyến ý định

| Người dùng nhắn (ví dụ) | Hành động |
|---|---|
| `/chay <mã>`, "phân tích <mã>", "phân tích sâu <mã>" | Gọi `run_analysis(ticker=<mã>, style="long", depth="full")` |
| `/chitiet <mã>`, "<mã> hôm nay sao rồi?", "<mã> thế nào?" | Gọi `run_analysis(ticker=<mã>, style="long", depth="quick")` |
| "xem lại <mã>", "snapshot <mã> lúc chốt phiên" | Gọi `get_snapshot(ticker=<mã>)` — KHÔNG gọi lại `run_analysis` |
| "<mã> 20 phiên gần nhất", "lịch sử khối ngoại <mã>", "giá <mã> tuần qua" | Gọi `query_history(ticker=<mã>, series=<"prices"\|"fundamentals"\|"foreign_flow">, ...)` — chọn `series` theo nội dung câu hỏi |
| "vì sao stop-loss đặt ở đó?", "giải thích run <run_id>", "tại sao nhận định X ở lần chạy trước?" | Gọi `explain_run(run_id=<run_id đã biết từ ngữ cảnh>)` — KHÔNG chạy lại pipeline. Nếu không có `run_id` trong ngữ cảnh, hỏi lại người dùng hoặc dùng `get_snapshot(ticker=<mã>)` để tìm run gần nhất trước |
| "các dự báo còn mở", "dự báo <mã> gần đây", "danh sách prediction" | Gọi `list_predictions(ticker=<mã hoặc bỏ trống>, status=<nếu người dùng nêu>)` |
| `/trangthai`, "trạng thái hệ thống", "thống kê tổng"/"hit-rate" | Gọi `get_stats()` |
| "so sánh <mã A> với <mã B>" | Gọi `run_analysis` cho cả hai mã với cùng `depth`, trình bày cạnh nhau |
| `/dangiu <mã> [giá vốn]`, "tôi đang giữ <mã>", "tôi mua <mã> giá X" | **Xác nhận lại với người dùng** mã + giá vốn (nếu có) trước khi gọi, rồi gọi `set_position(ticker=<mã>, avg_cost=<giá vốn hoặc null>, declared_by=<id người dùng trong ngữ cảnh chat>)` |
| "tôi đã bán <mã>", "thoát vị thế <mã>", "không còn giữ <mã> nữa" | **Xác nhận lại với người dùng** trước khi gọi, rồi gọi `clear_position(ticker=<mã>, declared_by=<id người dùng trong ngữ cảnh chat>)` |
| `/theodoi <mã>`, "theo dõi <mã>", "thêm <mã> vào danh sách của tôi" | Gọi `watch_ticker(ticker=<mã>, declared_by=<id người dùng trong ngữ cảnh chat>)`. Báo lại tên, sàn, và rằng mã sẽ được phân tích tự động mỗi phiên; nếu `status="not_found"` đọc nguyên văn `warnings` |
| `/bodoi <mã>`, "bỏ theo dõi <mã>" | Gọi `unwatch_ticker(ticker=<mã>, declared_by=<id người dùng>)` |
| `/danhsach`, "danh sách theo dõi của tôi", "các mã tôi theo dõi hôm nay thế nào" | Gọi `list_watchlist(declared_by=<id người dùng>)`; mã có `action_label` rỗng → nói "chưa có nhận định", gọi `get_snapshot(ticker=<mã>)` nếu người dùng hỏi lý do |

`declared_by` luôn là id của chính người đang nhắn — danh sách theo dõi là riêng từng người, không đọc/sửa danh sách của người khác. Khác `set_position`, theo dõi một mã không cần xác nhận lại (không ảnh hưởng nhãn hành động).

Nếu người dùng nêu một mã không nằm trong VN30, cứ định tuyến bình thường — `run_analysis`/`get_snapshot` tự trả về cảnh báo độ phủ dữ liệu (`universe_tier`) trong `warnings` nếu có; đọc nguyên văn cảnh báo đó lại cho người dùng, không tự phán mã đó "không đủ tin cậy" khi tool không nói vậy.

## Định dạng trả lời

- Bắt đầu bằng 1-2 câu tóm tắt trực tiếp trả lời câu hỏi.
- Sau đó liệt kê các số liệu cụ thể lấy từ `data` của tool, mỗi số nêu rõ nguồn (`sources`) và `as_of` nếu người dùng hỏi sâu hoặc nếu dữ liệu không phải "vừa xong".
- Nếu tool trả `warnings` không rỗng, luôn hiển thị nguyên văn (không rút gọn tới mức mất ý).
- Trả lời có tính hành động: nêu nhãn hành động (giữ nguyên như tool trả), vùng giá/cắt lỗ nếu có trong `data`, điều kiện vô hiệu hóa, rồi dòng miễn trừ.
- Không lặp lại toàn bộ JSON thô trừ khi người dùng yêu cầu xem dữ liệu gốc.
- Sau `set_position`/`clear_position`: xác nhận ngắn gọn `holding_state` mới mà tool trả về (vd. "Đã ghi nhận bạn đang giữ HPG" / "Đã ghi nhận bạn đã thoát HPG"), không thêm nhận định phân tích trừ khi người dùng hỏi thêm.
