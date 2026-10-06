# SBV spike — kết quả (2026-10-06)

## Kết luận

**Khả thi.** Hai trang HTML tĩnh của cổng mới `sbv.gov.vn` chứa đủ số liệu có nhãn nhận diện được: tỷ giá trung tâm USD/VND mới nhất và lãi suất tái cấp vốn / tái chiết khấu (kèm lãi suất liên ngân hàng). Không cần JavaScript, không cần cookie.

## Cách truy cập

- Host chuẩn là `https://sbv.gov.vn` (`www.sbv.gov.vn` chuyển hướng sang đó; cần cho phép cả hai host trong sandbox).
- WAF của SBV chặn một số User-Agent: `Mozilla/5.0` trần bị trả trang "Request Rejected" (HTTP 200, 244 byte); User-Agent `vn-market-mcp/1.0 (personal research tool)` và chuỗi giống trình duyệt đầy đủ đều qua (HTTP 200, ~420 KB). Collector dùng chuỗi tự nhận diện của dự án, **và phải coi phản hồi 200 nhỏ bất thường / chứa "Request Rejected" là lỗi nguồn** (cùng cơ chế "200 nhưng không hợp lệ" của bộ thu RSS).
- URL (percent-encoded, đã kiểm tra trả 200):
  - Tỷ giá: `https://sbv.gov.vn/t%E1%BB%B7-gi%C3%A1` (→ `/vi/tỷ-giá`)
  - Lãi suất: `https://sbv.gov.vn/l%C3%A3i-su%E1%BA%A5t1` (→ `/vi/lãi-suất1`)

## Dữ liệu tìm được

Fixture: `tests/fixtures/sbv/ty-gia.html`, `tests/fixtures/sbv/lai-suat.html`.

| Chỉ tiêu | Trang | Đoạn nhận diện (văn bản sau khi bỏ thẻ) | Ngày | Tần suất cập nhật |
|---|---|---|---|---|
| Tỷ giá trung tâm USD/VND | `tỷ-giá` | `áp dụng cho ngày 05/10/2026 như sau: Tỷ giá trung tâm Tỷ giá 1 Đô la Mỹ = 25.643 VND` (số văn bản `416/TB-NHNN`) | trong câu "áp dụng cho ngày dd/mm/yyyy" | Theo ngày làm việc (thông báo mới mỗi ngày) |
| Tỷ giá tham khảo mua/bán USD | `tỷ-giá` | bảng `1 USD Đô la Mỹ 24.411,00 26.875,00` (mua, bán) | tiêu đề bảng `05/10/2026` | Theo ngày |
| Lãi suất tái cấp vốn | `lãi-suất1` | `Lãi suất tái cấp vốn 4,500% 1123/QĐ-NHNN ngày 16/06/2023 19/03/2023` | cột "Ngày áp dụng" (19/03/2023) | Hiếm khi đổi (theo quyết định) |
| Lãi suất tái chiết khấu | `lãi-suất1` | `Lãi suất tái chiết khấu 3,000% 1123/QĐ-NHNN ...` | như trên | Hiếm khi đổi |
| Lãi suất liên ngân hàng (qua đêm, 1 tuần, ...) | `lãi-suất1` | `Ngày áp dụng: 02/10/2026 ... Qua đêm 2,73 ...` | "Ngày áp dụng" | Theo ngày (trễ 1 ngày làm việc) |

Định dạng số: dấu chấm phân cách hàng nghìn, dấu phẩy thập phân (`25.643`, `4,500%`, `2,73`). Giá trị ở cột kỳ hạn dài của liên ngân hàng có chú thích `(*)`/`(**)` là tham chiếu ngày cũ hơn.

## Đề xuất cho plan SBV

1. Migration: bảng `macro_indicators(indicator, period DATE, value NUMERIC, unit, source, source_url, published_at, fetched_at)` với khóa `(indicator, period, source)`.
2. `pipeline/collectors/sbv.py`: tải hai trang, bỏ `<script>/<style>` và thẻ, rồi regex trên văn bản: `áp dụng cho ngày (\d{2}/\d{2}/\d{4}).*?1 Đô la Mỹ = ([\d.]+) VND`, `Lãi suất tái cấp vốn ([\d,]+)%`, `Lãi suất tái chiết khấu ([\d,]+)%`, và dòng "Qua đêm" của bảng liên ngân hàng. Test trên hai fixture này.
3. Job `collect_sbv` 09:00 và 17:00 giờ VN, thứ 2–6, cùng cơ chế hàng đợi và `source_health` như `collect_rss`; thêm `sbv` vào danh sách nguồn kiểm tra im lặng.
4. Khối `indicators` trong `get_macro_context`; skill Hermes thêm luật số liệu chính thức thắng số liệu trong bài báo.
5. Thông báo tỷ giá trung tâm công bố vào buổi sáng; lần chạy 09:00 có thể chưa thấy ngày mới, lần 17:00 là mốc đảm bảo.
