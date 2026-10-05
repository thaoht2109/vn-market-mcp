"""Deterministic per-ticker report (≤500 words) built straight from stored data.

Replaces having the chat LLM re-reason the same numbers on every question: this
is a pure function of the snapshot + a few DB rows, so Hermes only relays it.
Every judgement is a rule over a figure printed next to it.
"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

_VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")
_STANCE = {
    "stay_out": "đứng ngoài", "watch": "theo dõi, chưa giải ngân",
    "buy_accumulate": "tích lũy dần", "reduce_exit": "giảm tỷ trọng",
}
_TREND = {"up": "tăng", "down": "giảm", "sideways": "đi ngang"}
DISCLAIMER = "Đây là công cụ hỗ trợ nghiên cứu, không phải tư vấn đầu tư."
COMMENTARY_TITLE = "**Nhận định**"


def _n(x: float, d: int = 0) -> str:
    """Vietnamese number format: 32.250 / 8,6"""
    return f"{x:,.{d}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _pct(x: float, d: int = 1, sign: bool = True) -> str:
    return f"{x:+.{d}f}%".replace(".", ",") if sign else f"{x:.{d}f}%".replace(".", ",")


def _vs(close: float, level: float, name: str, d: int = 0) -> str:
    return f"{'trên' if close >= level else 'dưới'} {name} {_n(level, d)} ({_pct((close / level - 1) * 100)})"


def _technical(s: dict, close: float, prev: float | None, volume: float | None) -> list[str]:
    t = s["technical"]
    lines = []
    day = f" ({_pct((close / prev - 1) * 100, 2)} so với phiên trước {_n(prev)})" if prev else ""
    lines.append(f"Giá đóng cửa {_n(close)}{day}; xu hướng kỹ thuật {_TREND.get(t['trend'], t['trend'])}.")

    mas = [("MA20", t["ma20"]), ("MA50", t["ma50"])] + ([("MA200", t["ma200"])] if t.get("ma200") else [])
    above = sum(close >= v for _, v in mas)
    meaning = ("xu hướng tăng được xác nhận" if above == len(mas)
               else "xu hướng yếu cả ngắn lẫn trung hạn" if above == 0
               else "tín hiệu lẫn lộn, chưa có hướng rõ")
    lines.append("Giá " + ", ".join(_vs(close, v, n) for n, v in mas) + f" → {meaning}.")

    rsi, macd, sig = t["rsi14"], t["macd"], t["macd_signal"]
    rsi_txt = ("quá bán" if rsi < 30 else "yếu" if rsi < 45 else "trung tính" if rsi < 55
               else "mạnh" if rsi < 70 else "quá mua, dễ điều chỉnh")
    lines.append(f"RSI(14) {_n(rsi, 1)} ({rsi_txt}).")
    lines.append(f"MACD {_n(macd, 1)} {'trên' if macd > sig else 'dưới'} đường tín hiệu {_n(sig, 1)} "
                 f"(chênh {_n(macd - sig, 1)}) → {'đà cải thiện' if macd > sig else 'đà đang yếu dần'}"
                 f"; MACD {'trên' if macd > 0 else 'dưới'} 0.")

    lo, hi = t["bollinger_lower"], t["bollinger_upper"]
    pos = (close - lo) / (hi - lo) * 100 if hi > lo else 50
    bb = "sát biên trên, dư địa tăng ngắn hạn hẹp" if pos > 85 else "sát biên dưới" if pos < 15 else "giữa dải, chưa căng"
    lines.append(f"Dải Bollinger {_n(lo)}–{_n(hi)}; giá ở {_n(pos)}% dải ({bb}).")
    atr = t["atr14"]
    lines.append(f"Biên độ dao động bình quân ATR(14) {_n(atr)}đ/phiên (~{_n(atr / close * 100, 1)}% giá).")

    if volume and t.get("volume_avg20"):
        r = volume / t["volume_avg20"] * 100
        vol = "thanh khoản yếu" if r < 70 else "dòng tiền tăng" if r > 130 else "thanh khoản bình thường"
        lines.append(f"Khối lượng {_n(volume)} cp = {_n(r)}% bình quân 20 phiên {_n(t['volume_avg20'])} ({vol}"
                     f"{'; đột biến' if t.get('volume_anomaly') else ''}).")
    sup, res = t["support"][0], t["resistance"][0]
    lines.append(f"Hỗ trợ {_n(sup)} ({_pct((sup / close - 1) * 100)}), kháng cự {_n(res)} "
                 f"({_pct((res / close - 1) * 100)}).")
    return lines


_FUND_LABELS = (("pe", "P/E", 1, 1), ("pb", "P/B", 2, 1), ("roe", "ROE", 1, 100), ("roa", "ROA", 2, 100),
                ("gross_margin", "biên lợi nhuận gộp", 1, 100), ("debt_to_equity", "nợ/vốn", 2, 1),
                ("nim", "NIM", 2, 100), ("casa_ratio", "CASA", 1, 100), ("npl_ratio", "nợ xấu", 2, 100),
                ("credit_growth", "tăng trưởng tín dụng", 1, 100))


def _fmt_metric(key: str, v: float) -> str:
    _, label, d, mult = next(x for x in _FUND_LABELS if x[0] == key)
    return f"{label} {_n(v * mult, d)}{'%' if mult == 100 else ''}"


def _fundamental(s: dict, period: str | None, history: list[tuple[str, dict]]) -> list[str]:
    m = {k: v for k, v in s["fundamental"]["metrics"].items() if v}
    keys = [k for k, *_ in _FUND_LABELS if k in m]
    out = [(f"Kỳ {period}: " if period else "") + ", ".join(_fmt_metric(k, m[k]) for k in keys) + "."]
    if "roe" in m:
        out[0] += f" ROE {'≥15% → sinh lời tốt' if m['roe'] >= 0.15 else '10–15% → khá' if m['roe'] >= 0.10 else '<10% → thấp'}."
    for key in ("pe", "pb", "roe"):
        series = [(p, h[key]) for p, h in history if h.get(key)]
        if len(series) >= 2 and key in m:
            line = " | ".join(f"{p} {_n(v * (100 if key == 'roe' else 1), 2 if key == 'pb' else 1)}"
                              f"{'%' if key == 'roe' else ''}" for p, v in series)
            vals = [v for _, v in series]
            tag = (" (thấp nhất kỳ lưu)" if m[key] <= min(vals) else " (cao nhất kỳ lưu)" if m[key] >= max(vals) else "")
            out.append(f"{_fmt_metric(key, 0).split(' ')[0]} theo quý: {line}{tag}")
    return out


def _flow(rows: list[tuple]) -> list[str]:
    """rows: (trade_date, buy_value, sell_value, net_value), newest first."""
    if not rows:
        return []
    out = [f"{d:%d/%m}: mua {_n(float(b or 0) / 1e9, 1)} tỷ, bán {_n(float(sl or 0) / 1e9, 1)} tỷ, "
           f"ròng {_n(float(n or 0) / 1e9, 1)} tỷ" for d, b, sl, n in rows]
    net = sum(float(n or 0) for *_, n in rows)
    out.append(f"Tổng {len(rows)} phiên: khối ngoại {'mua ròng' if net > 0 else 'bán ròng'} {_n(abs(net) / 1e9, 1)} tỷ đồng.")
    return out


def _market(s: dict, close: float, news_rows: list[tuple]) -> list[str]:
    mk = s.get("market") or {}
    headlines = [f"Tin {d:%d/%m}: {title}" + (f" ({src})" if src else "") for d, title, src in news_rows]
    if mk.get("close") is None:
        return ["Chưa có dữ liệu VN-Index trong lần chạy này."] + headlines
    idx = mk["close"]
    out = [f"VN-Index {_n(idx, 2)}" + (f" ({_pct(mk['change_pct'], 2)})" if mk.get("change_pct") is not None else "")
           + f"; xu hướng {_TREND.get(mk.get('trend'), mk.get('trend'))}."]
    mas = [(n, mk[k]) for n, k in (("MA20", "ma20"), ("MA50", "ma50"), ("MA200", "ma200")) if mk.get(k)]
    out.append("VN-Index " + ", ".join(_vs(idx, v, n, 2) for n, v in mas) + f"; RSI(14) {_n(mk['rsi14'], 1)}.")
    risk_off = mk["regime"] == "risk_off"
    out.append("Chế độ thị trường: " + ("rủi ro cao (VN-Index dưới MA200, xu hướng giảm) → hạn chế mở mua mới."
                                        if risk_off else "chưa ở trạng thái rủi ro cao."))
    t = s["technical"]
    if risk_off:
        out.append("Mã " + ("đang đi ngược thị trường (giữ trên MA20)." if close >= t["ma20"]
                            else "yếu cùng chiều thị trường (dưới MA20)."))
    return out + headlines


def render_stock_report(snapshot: dict, *, ticker: str, name: str | None, as_of: datetime, close: float,
                        prev_close: float | None, volume: float | None, in_session: bool,
                        fund_period: str | None, fund_history: list[tuple[str, dict]],
                        flow_rows: list[tuple], news_rows: list[tuple] = ()) -> str:
    """Complete data report (no length cap). The AI "Nhận định" is appended by the caller."""
    s = snapshot
    t, plan, mk = s["technical"], s["risk_plan"], s.get("market") or {}
    when = as_of.astimezone(_VN_TZ).strftime("%H:%M %d/%m/%Y")
    out = [f"**{ticker}{' — ' + name if name else ''}** · dữ liệu chốt {when}"
           + (" (giá tạm tính, phiên đang giao dịch)" if in_session else "")]

    label = s.get("action_label")
    stance = _STANCE.get(label, "chưa đủ dữ liệu để kết luận")
    if mk.get("regime") == "risk_off" and label in ("watch", "stay_out"):
        reason = "thị trường chung đang yếu (VN-Index dưới MA200) nên chưa nâng lên mức mua"
    elif t["trend"] == "down":
        reason = "xu hướng kỹ thuật đang giảm"
    else:
        reason = "tín hiệu kỹ thuật và định giá chưa đủ đồng thuận để giải ngân"
    conf = s.get("confidence") or 0
    conf_txt = "cao" if conf >= 0.75 else "trung bình" if conf >= 0.5 else "thấp"
    wc = s.get("weight_coverage") or 1
    cov = f", mới phản ánh ~{round(wc * 100)}% yếu tố (thiếu tin tức/vĩ mô)" if wc < 0.95 else ""
    score = s.get("composite_score")
    out.append(f"**Kết luận:** {stance} — {reason}. Độ tin cậy {conf_txt} ({_n(conf, 2)}){cov}"
               + (f"; điểm tổng hợp {_n(score, 1)}/100." if score is not None else "."))

    def block(title: str, lines: list[str]) -> None:
        if lines:
            out.append(f"**{title}**\n" + "\n".join("- " + l for l in lines))

    block("1. Kỹ thuật", _technical(s, close, prev_close, volume))
    block("2. Cơ bản & định giá", _fundamental(s, fund_period, fund_history))
    block("3. Dòng tiền khối ngoại", _flow(flow_rows))
    block("4. Thị trường chung (VN-Index) & tin tức", _market(s, close, list(news_rows)))

    stop_pct = (plan["stop_loss"] / close - 1) * 100
    tgt_pct = (plan["target"] / close - 1) * 100
    lo, hi = plan["entry_zone"][0], plan["entry_zone"][-1]
    plan_lines = [
        f"Vùng mua {_n(lo)}–{_n(hi)}.",
        f"Cắt lỗ {_n(plan['stop_loss'])} ({_pct(stop_pct)}), đặt cách 2 lần ATR(14) ~{_n(t['atr14'])}đ để tránh bị quét bởi nhiễu thường ngày.",
        f"Mục tiêu {_n(plan['target'])} ({_pct(tgt_pct)}); tỷ lệ lãi/rủi ro {_n(plan['rr'], 1)}:1.",
    ]
    if plan.get("suggested_volume"):
        plan_lines.append(f"Khối lượng tham chiếu {_n(plan['suggested_volume'])} cp "
                          f"(~{_n(plan['suggested_volume'] * close / 1e9, 2)} tỷ đồng theo giá hiện tại).")
    block("5. Kế hoạch rủi ro", plan_lines)

    risks = []
    roe = (s["fundamental"]["metrics"].get("roe") or 0) * 100
    weak = t["trend"] == "down" or close < t["ma50"] or t["macd"] < t["macd_signal"]
    if roe >= 15 and weak:
        risks.append(f"Trái chiều: cơ bản tốt (ROE {_n(roe, 1)}%) nhưng kỹ thuật yếu (MACD dưới tín hiệu hoặc giá dưới MA50).")
    elif roe < 10 and not weak:
        risks.append(f"Trái chiều: kỹ thuật tích cực nhưng nền tảng sinh lời thấp (ROE {_n(roe, 1)}%).")
    atr_pct = t["atr14"] / close * 100
    if abs(stop_pct) < 2.5 * atr_pct:
        risks.append(f"Cắt lỗ chỉ cách {_n(abs(stop_pct), 1)}% trong khi biên độ mỗi phiên ~{_n(atr_pct, 1)}% "
                     "→ dễ bị quét rồi giá quay lại.")
    for w in list(s.get("warnings") or []) + list(plan.get("warnings") or []):
        risks.append(str(w))
    risks.append(f"Vô hiệu khi đóng cửa dưới {_n(plan['stop_loss'])} (cắt lỗ) hoặc dưới hỗ trợ {_n(t['support'][0])}; "
                 f"xác nhận tăng khi vượt {_n(t['resistance'][0])} kèm khối lượng trên bình quân 20 phiên.")
    block("6. Rủi ro & điều kiện sai", risks)
    return "\n\n".join(out)
