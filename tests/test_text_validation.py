import pytest

from llm.text_validation import has_disallowed_digits, lookup_evidence_ref


@pytest.mark.parametrize(
    "text",
    [
        "BID nằm dưới MA20, MA50 và MA200, MACD vẫn âm.",
        "RSI14 đang trung tính, ATR14 phản ánh biến động thấp.",
        "Báo cáo quý 2026 cho thấy triển vọng tích cực.",
        "Luận điểm dựa trên {{technical.ma200}} và {{technical.rsi14}}.",
    ],
)
def test_has_disallowed_digits_allows_indicator_names_years_and_placeholders(text):
    assert has_disallowed_digits(text) is False


@pytest.mark.parametrize(
    "text",
    [
        "Giá đã tăng 5% trong tuần qua.",
        "RSI14 đang ở mức 62.",  # indicator name OK, but the literal value isn't
        "Mục tiêu giá 84500 đồng.",
    ],
)
def test_has_disallowed_digits_still_flags_fabricated_numbers(text):
    assert has_disallowed_digits(text) is True


def test_lookup_evidence_ref_resolves_dotted_path():
    snapshot = {"technical": {"rsi14": 62.5}}
    assert lookup_evidence_ref(snapshot, "technical.rsi14") is True
    assert lookup_evidence_ref(snapshot, "technical.missing") is False
