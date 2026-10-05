from pipeline.report import render_placeholders, render_synthesis_report


def test_render_placeholders_fills_dotted_path_from_snapshot():
    snapshot = {"technical": {"rsi14": 62.5}, "composite_score": 71}
    text = "RSI {{technical.rsi14}} và điểm tổng hợp {{composite_score}}."
    assert render_placeholders(text, snapshot) == "RSI 62.50 và điểm tổng hợp 71."


def test_render_placeholders_unknown_ref_becomes_question_mark():
    assert render_placeholders("Giá trị {{no.such.field}}.", {}) == "Giá trị ?."


def test_render_placeholders_rounds_float_to_2dp():
    snapshot = {"composite_score": 69.30846223839855}
    assert render_placeholders("Điểm {{composite_score}}.", snapshot) == "Điểm 69.31."


def test_render_synthesis_report_returns_none_without_synthesis():
    assert render_synthesis_report({}) is None


def test_render_synthesis_report_fills_summary_and_points():
    snapshot = {
        "composite_score": 80,
        "synthesis": {
            "thesis_summary": "Điểm tổng hợp {{composite_score}} cho thấy xu hướng tích cực.",
            "supporting_points": [{"text": "ROE tốt ({{fundamental.roe}}).", "evidence_ref": "fundamental.roe"}],
            "contradictions": [],
        },
        "fundamental": {"roe": 0.21},
    }
    report = render_synthesis_report(snapshot)
    assert "Điểm tổng hợp 80 cho thấy xu hướng tích cực." in report
    assert "ROE tốt (0.21)." in report
    assert "Mâu thuẫn:" not in report
