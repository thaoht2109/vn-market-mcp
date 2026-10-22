---
name: verify-live
description: Kiểm tra thật (chỉ đọc) vnstock API và DB production của vn-market-mcp. Dùng sau khi sửa lỗi liên quan tới provider/pipeline/dữ liệu, sau mỗi lần deploy, trước khi tạo PR cho thay đổi đụng tới dữ liệu, hoặc khi người dùng hỏi dữ liệu có cũ/sai không.
---

# verify-live

Unit test mock provider theo đúng giả định của code, nên không bắt được giả định sai về API ngoài
(vnstock bỏ qua `end`, `public_date` là giờ VN) hay job "done" mà không ghi dữ liệu. Skill này kiểm tra
trên hệ thống thật. Mọi truy cập DB chạy trong phiên `READ ONLY` do Postgres ép buộc.

## Khi nào chạy

- Sau khi sửa lỗi trong `providers/`, `pipeline/`, `ops/worker.py`, `ops/scheduler.py`: chạy TRƯỚC khi tạo PR.
- Sau khi deploy pipeline: chạy lại để xác nhận container mới vẫn đúng.
- Khi nghi dữ liệu cũ/sai: chạy với `--date` của phiên nghi ngờ.

## Cách chạy

Code đã deploy (đã merge và build lại pipeline):

```bash
docker exec vn-market-mcp-pipeline-worker-1 python -m ops.verify_live [--date YYYY-MM-DD] [--ticker FPT]
```

Code chưa deploy (đang trên nhánh): chép thư mục `ops/` lên trước `PYTHONPATH`, không đụng vào `/app`:

```bash
docker exec vn-market-mcp-pipeline-worker-1 sh -c 'rm -rf /tmp/ov && mkdir -p /tmp/ov'
docker cp ops vn-market-mcp-pipeline-worker-1:/tmp/ov/ops   # thêm providers/ pipeline/ nếu cũng sửa
docker exec -w /tmp/ov -e PYTHONPATH=/tmp/ov:/app vn-market-mcp-pipeline-worker-1 python -m ops.verify_live
```

`-w /tmp/ov` là bắt buộc: `python -m` đặt thư mục hiện tại lên đầu `sys.path`. Lệnh docker cần
`dangerouslyDisableSandbox: true` (sandbox chặn docker socket).

## Đọc kết quả

| Kiểm tra | FAIL nghĩa là |
|---|---|
| `db.freshness` | thiếu giá VN30, thiếu `market_regime_daily`, hoặc `trading_calendar` sắp hết |
| `db.jobs` | có job `failed`/`expired` trong ngày: xem cột `error` trong bảng `jobs` |
| `db.news_future` | tin có `published_at` ở tương lai: lỗi múi giờ |
| `db.regime_matches_index` | close VN-Index trong DB lệch > 0,5% so với vnstock (bỏ qua khi phiên chưa đóng) |
| `vnstock.end_respected` | provider trả bar sau `end`: bộ lọc trong `_history` hỏng |
| `vnstock.news_time` | provider trả tin ở tương lai |

WARN không chặn: `vnstock.raw_ignores_end` chỉ báo thư viện vẫn bỏ qua `end` (khi PASS thì có thể bỏ
bộ lọc trong provider); `news_time` 0 tin là không kiểm chứng được (vnstock nuốt lỗi mạng, xem log).

## Quy tắc

- Không bao giờ chạy lệnh ghi vào DB production trong lúc "kiểm tra". Cần backfill thì hỏi người dùng trước.
- Có FAIL sau khi sửa lỗi thì chưa được báo "đã sửa": tìm nguyên nhân, sửa, chạy lại.
- Báo kết quả nguyên văn (dòng PASS/WARN/FAIL) cho người dùng, kèm phiên đã kiểm tra.
- Thêm kiểm tra mới vào `ops/verify_live.py` mỗi khi phát hiện một giả định mới về dữ liệu/API ngoài.
