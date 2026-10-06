import pytest

from evals.news_filter_recall import score
from pipeline.news_filter import load_keywords


def test_score_reports_recall_precision_and_lists_misses():
    rows = [
        {"title": "Tỷ giá USD/VND tăng", "summary": "", "stream": "A", "relevant": True},      # kept: hit
        {"title": "Thời tiết đẹp", "summary": "", "stream": "A", "relevant": True},             # dropped: a miss
        {"title": "Khuyến mãi lớn", "summary": "", "stream": "A", "relevant": False},           # dropped: correct
        {"title": "Chưa gán nhãn", "summary": "", "stream": "A", "relevant": None},             # ignored
    ]
    r = score(rows, set(), {}, load_keywords())
    assert r["labelled"] == 3 and r["recall"] == 0.5 and r["precision"] == 1.0
    assert [m["title"] for m in r["missed"]] == ["Thời tiết đẹp"]


def test_score_refuses_to_report_without_any_relevant_label():
    with pytest.raises(ValueError):
        score([{"title": "x", "summary": "", "stream": "A", "relevant": False}], set(), {}, load_keywords())
