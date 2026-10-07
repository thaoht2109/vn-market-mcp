from datetime import date, datetime, timezone

from pipeline.stock_report import render_stock_report

SNAP = {
    "technical": {"trend": "sideways", "ma20": 32407.5, "ma50": 31376.0, "ma200": 32268.55, "rsi14": 53.4,
                  "macd": 261.3, "macd_signal": 307.7, "bollinger_upper": 33370.6, "bollinger_lower": 31444.3,
                  "atr14": 728.6, "support": [27800.0], "resistance": [35200.0], "volume_avg20": 15164635.0,
                  "volume_anomaly": False},
    "fundamental": {"industry_group": "Banks", "metrics": {"pe": 8.6, "pb": 1.31, "roe": 0.1476, "roa": 0.0227,
                    "nim": 0.0363, "npl_ratio": 0.0108, "casa_ratio": 0.35, "credit_growth": 0.0633}},
    "risk_plan": {"entry_zone": [31885.7, 32614.3], "stop_loss": 30792.9, "target": 35164.3, "rr": 2.0,
                  "suggested_volume": 765100, "warnings": []},
    "confidence": 0.667, "weight_coverage": 0.7, "action_label": "watch", "warnings": [],
    "market": {"close": 1737.71, "change_pct": -0.66, "ma200": 1795.65, "rsi14": 35.7, "regime": "risk_off"},
}


def _render(**over):
    kw = dict(ticker="TCB", name="Techcombank", as_of=datetime(2026, 10, 2, 8, 40, tzinfo=timezone.utc),
              close=32250.0, prev_close=33000.0, volume=14_180_000.0, in_session=False, fund_period="2026-Q2",
              fund_history=[("2026-Q2", {"pe": 8.6, "pb": 1.31, "roe": 0.1476}), ("2026-Q1", {"pe": 9.0, "pb": 1.33, "roe": 0.14}),
                            ("2025-Q4", {"pe": 9.5, "pb": 1.4, "roe": 0.13}), ("2025-Q3", {"pe": 9.9, "pb": 1.5, "roe": 0.12})],
              flow_rows=[(date(2026, 10, 2), 3e9, 8e9, -5e9), (date(2026, 10, 1), 6e9, 4e9, 2e9)])
    kw.update(over)
    return render_stock_report(SNAP, **kw)


def test_report_carries_every_indicator_with_its_figure():
    text = _render()
    for part in ("**TCB — Techcombank**", "15:40 02/10/2026", "**Kết luận:** theo dõi, chưa giải ngân",
                 "thị trường chung đang yếu", "**1. Kỹ thuật**", "dưới MA20 32.408 (-0,5%)",
                 "trên MA50 31.376 (+2,8%)", "MACD 261,3 dưới đường tín hiệu 307,7", "**2. Cơ bản & định giá**",
                 "Kỳ 2026-Q2", "P/B theo quý: 2026-Q2 1,31 | 2026-Q1 1,33", "(thấp nhất kỳ lưu)", "02/10: mua 3,0 tỷ, bán 8,0 tỷ, ròng -5,0 tỷ",
                 "bán ròng 3,0 tỷ đồng", "ATR(14) 729đ/phiên", "Khối lượng 14.180.000 cp = 94%", "RSI(14) 53,4", "chênh -46,4",
                 "NIM 3,63%", "nợ xấu 1,08%", "Khối lượng tham chiếu", "VN-Index 1.737,71 (-0,66%)",
                 "dưới MA200 1.795,65", "yếu cùng chiều thị trường", "Cắt lỗ 30.793 (-4,5%)", "lãi/rủi ro 2,0:1",
                 "**6. Rủi ro & điều kiện sai**"):
        assert part in text, part
    assert "tạm tính" not in text


def test_in_session_flag_and_missing_market_and_flow():
    snap = {**SNAP, "market": None}
    text = render_stock_report(snap, ticker="TCB", name=None, as_of=datetime(2026, 10, 2, 3, 0, tzinfo=timezone.utc),
                               close=32250.0, prev_close=None, volume=None, in_session=True, fund_period=None,
                               fund_history=[], flow_rows=[])
    assert "giá tạm tính" in text and "Chưa có dữ liệu VN-Index" in text and "**3. Dòng tiền khối ngoại**" not in text


def test_macro_line_and_coverage_names_what_is_missing():
    snap = {**SNAP, "weight_coverage": 0.85,
            "components": {"technical": 40.0, "flow": 55.0, "news_events": None,
                           "fundamental_valuation": 60.0, "sector_macro": 47.5},
            "macro": {"interbank_overnight": {"value": 2.05, "period": "2026-10-05", "score": 79.0},
                      "cpi_yoy": {"value": 5.08, "period": "2026-09-01", "score": 38.0},
                      "breadth_ma50": {"value": 0.4667, "period": "2026-10-07", "score": 46.7}}}
    text = render_stock_report(snap, ticker="TCB", name=None, as_of=datetime(2026, 10, 7, 8, 40, tzinfo=timezone.utc),
                               close=32250.0, prev_close=None, volume=None, in_session=False, fund_period=None,
                               fund_history=[], flow_rows=[])
    assert "mới phản ánh ~85% yếu tố (thiếu tin tức)" in text
    assert "Vĩ mô 47,5/100 (trung tính): lãi suất liên ngân hàng qua đêm 2,05% (05/10)" in text
    assert "CPI so với cùng kỳ 5,08% (tháng 09/2026)" in text and "47% mã VN30 trên MA50" in text
