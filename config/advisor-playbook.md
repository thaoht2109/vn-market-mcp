# Playbook của cố vấn

Nguyên tắc chung cho cố vấn độc lập (SKILL bước 2b), viết lại bằng lời của dự án từ các khung phân tích và
quản trị danh mục phổ biến. Mỗi lần dùng, cố vấn nhận mục "Chung" và mục đúng ngành của mã (không có thì mục
"Khác"). Mỗi góc nhìn lưu kèm phiên bản của file này (8 ký tự đầu sha256) để so phiên bản nào đánh giá đúng
hơn (`python -m ops.backtest_score`). Các ngưỡng tỷ trọng là lựa chọn của người dùng: sửa ở đây.

## Chung

Khung ra quyết định:
- Bắt đầu từ rủi ro: trước khi nói lợi nhuận, xác định mốc giá làm quan điểm sai và mức lỗ chấp nhận được.
- Cơ bản và định giá trả lời "mua mã nào"; xu hướng giá và dòng tiền trả lời "mua lúc nào". Khi hai bên mâu thuẫn (cơ bản tốt, xu hướng giảm), ưu tiên chờ xu hướng xác nhận hơn là mua đón.
- Không bình quân giá xuống khi giá dưới MA50 và xu hướng giảm. Chỉ mua thêm vào vị thế đang lãi khi xu hướng tăng được xác nhận.
- Đang lãi: bảo vệ lợi nhuận bằng cách nâng mức cắt lỗ lên theo hỗ trợ gần nhất hoặc MA20, thay vì chốt toàn bộ ngay khi chưa có tín hiệu xấu.
- Đang lỗ gần mức cắt lỗ: giữ kỷ luật cắt lỗ. "Cơ bản vẫn tốt" không phải lý do bỏ cắt lỗ khi xu hướng đã gãy.
- Chưa nắm giữ: không có tín hiệu rõ thì đứng ngoài cũng là một quyết định; không cần lúc nào cũng có hành động.

Quản trị danh mục (ngưỡng tham khảo):
- Một mã không quá 20% danh mục; một ngành không quá 40%.
- Rủi ro mỗi vị thế (khoảng cách tới mức cắt lỗ nhân tỷ trọng) không quá 2% tổng danh mục.
- Thị trường chung ở trạng thái rủi ro cao: giữ nhiều tiền mặt hơn, giải ngân từng phần nhỏ.
- Đã giữ mã cùng ngành: mua thêm mã cùng ngành làm tăng rủi ro tập trung, cần nói rõ.

Đặc thù thị trường Việt Nam:
- Biên độ dao động mỗi phiên: HOSE 7%, HNX 10%, UPCoM 15%. Chu kỳ thanh toán T+2: cổ phiếu mua hôm nay chỉ bán được sau 2 phiên, nên cắt lỗ không thực hiện được ngay khi vừa mua.
- VN-Index thủng MA200 kèm khối lượng lớn ở chiều giảm: rủi ro bán giải chấp ký quỹ lan rộng; tránh mở vị thế mới bằng vay ký quỹ.
- Mùa công bố báo cáo tài chính (tháng 1, 4, 7, 10): giá thường đi trước kỳ vọng; P/E tính trên quý cũ có thể đã lỗi thời.
- Cổ tức bằng cổ phiếu và phát hành thêm làm pha loãng lợi nhuận trên mỗi cổ phiếu; so P/E giữa các kỳ cần lưu ý điều này.
- Khối ngoại bán ròng kéo dài ở mã vốn hóa lớn thường tạo áp lực giá; mua ròng vài phiên chưa đủ thành xu hướng.
- Mã thanh khoản thấp khó thoát hàng khi cần: giảm tỷ trọng tương ứng.

## Banks

- Định giá chính là P/B so với lịch sử của chính mã và so với ngành, luôn đi kèm ROE: theo kinh nghiệm, ROE từ 15% trở lên mới xứng với P/B trên 1,5 lần. P/E ít ý nghĩa hơn với ngân hàng.
- Chất lượng tài sản: nợ xấu dưới 2% là tốt; xu hướng nợ xấu qua các quý quan trọng hơn con số của một quý.
- NIM và CASA: CASA cao giúp chi phí vốn thấp và ít nhạy khi lãi suất tăng.
- Lãi suất liên ngân hàng tăng nhanh là bất lợi cho NIM và thanh khoản hệ thống.
- Tăng trưởng tín dụng cao hơn ngành trong khi nợ xấu tăng: rủi ro chất lượng tăng trưởng.

## Financial Services

- Công ty chứng khoán: lợi nhuận theo thanh khoản thị trường và dư nợ ký quỹ, là cổ phiếu chu kỳ biến động mạnh hơn thị trường.
- Mua khi thị trường chung yếu là đi ngược chu kỳ; cần thanh khoản thị trường hồi phục xác nhận.
- P/B là thước đo định giá chính.

## Real Estate

- Lợi nhuận ghi nhận theo bàn giao, không đều giữa các quý: P/E theo quý dễ gây hiểu sai.
- Rủi ro chính: pháp lý dự án, nợ vay và lãi suất. Nợ/vốn cao kèm lãi suất tăng là tổ hợp rủi ro lớn.

## Basic Resources

- Thép: lợi nhuận theo chu kỳ giá thép cán nóng và quặng sắt; P/E thấp ở đỉnh chu kỳ có thể là bẫy.
- Biên lợi nhuận gộp thu hẹp nhiều quý liên tiếp là dấu hiệu chu kỳ đi xuống.

## Chemicals

- Cao su, hóa chất: chịu ảnh hưởng giá hàng hóa thế giới. Lợi nhuận từ chuyển đổi đất khu công nghiệp là bất thường, không lặp lại đều.

## Food & Beverage

- Nhóm phòng thủ: tăng trưởng ổn định, định giá thường cao hơn thị trường, nên so với lịch sử của chính mã.
- Biên lợi nhuận gộp nhạy với giá nguyên liệu; USD tăng làm đắt nguyên liệu nhập khẩu.

## Retail

- Theo sức mua tiêu dùng; theo dõi biên lợi nhuận gộp và tăng trưởng doanh thu của hệ thống cửa hàng hiện có.

## Oil & Gas

- Theo giá dầu Brent/WTI; lọc dầu phụ thuộc chênh lệch giữa giá sản phẩm xăng dầu và giá dầu thô.

## Utilities

- Phân phối khí: giá khí neo theo giá dầu, sản lượng tiêu thụ quyết định lợi nhuận; cổ tức tiền mặt đều.

## Travel & Leisure

- Hàng không: nhạy với giá nhiên liệu và tỷ giá (nhiều chi phí bằng USD), nợ thuê tàu bay lớn.

## Khác

- Công nghệ: định giá theo tăng trưởng lợi nhuận dài hạn; P/E cao hơn thị trường có thể hợp lý khi tăng trưởng được duy trì; rủi ro từ thị trường xuất khẩu phần mềm và tỷ giá.
- Xây dựng, hạ tầng: theo tiến độ giải ngân đầu tư công; dòng tiền và khoản phải thu là rủi ro chính.
- Ngành chưa có khung riêng: dùng mục "Chung" và nói rõ là chưa có tiêu chí riêng cho ngành.
