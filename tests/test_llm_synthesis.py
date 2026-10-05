import pytest

from llm.config import ModelsConfig
from llm.providers import ChatResult
from llm.synthesis import SynthesisValidationError, run_synthesis_daily
from tests.conftest import insert_ticker

SNAPSHOT = {
    "ticker": "SYNTEST",
    "technical": {"rsi14": 61.2, "trend": "up"},
    "fundamental": {"roe": 18.5},
}


class FakeChatClient:
    def __init__(self, results):
        self._results = list(results)

    def create(self, **kwargs):
        return self._results.pop(0)


def _result(tool_input):
    return ChatResult(tool_input=tool_input, input_tokens=100, output_tokens=50, cached_tokens=0)


VALID_OUTPUT = {
    "thesis_summary": "Xu hướng tăng được hỗ trợ bởi dòng tiền và định giá hợp lý",
    "supporting_points": [{"text": "Chỉ báo động lượng đang ủng hộ xu hướng tăng", "evidence_ref": "technical.rsi14"}],
    "contradictions": [],
    "scenarios": [{"name": "base", "description": "Tiếp tục tích lũy trong biên độ hiện tại"}],
    "invalidation_rules": [{"condition": "ROE giảm liên tục nhiều quý", "evidence_ref": "fundamental.roe"}],
    "label_recommendation": {"downgrade_to": None, "reason": ""},
}


@pytest.fixture
def run_row(db_conn):
    insert_ticker(db_conn, "SYNTEST")
    db_conn.execute(
        "INSERT INTO runs (run_id, mode, tickers, style, depth, as_of, snapshot_ref)"
        " VALUES ('syn-run', 'on_demand', ARRAY['SYNTEST'], 'long', 'quick', now(), 'snapshots/x.json')"
    )
    db_conn.commit()
    yield "syn-run"
    db_conn.execute("DELETE FROM llm_calls WHERE run_id = 'syn-run'")
    db_conn.execute("DELETE FROM runs WHERE run_id = 'syn-run'")
    db_conn.execute("DELETE FROM tickers WHERE ticker = 'SYNTEST'")
    db_conn.commit()


def _run(conn, cfg, snapshot, run_id, output):
    client = FakeChatClient([_result(output)])
    return run_synthesis_daily(conn, cfg, snapshot, run_id, clients={"deepseek": client})


def test_run_synthesis_daily_accepts_valid_output(db_conn, run_row):
    cfg = ModelsConfig.load()
    result = _run(db_conn, cfg, SNAPSHOT, run_row, VALID_OUTPUT)
    assert result.downgrade_to is None
    assert result.supporting_points[0]["evidence_ref"] == "technical.rsi14"
    assert result.llm_meta.provider == "deepseek"


def test_run_synthesis_daily_rejects_digit_in_free_text(db_conn, run_row):
    bad = {**VALID_OUTPUT, "thesis_summary": "RSI đạt 61.2 điểm cho thấy xu hướng tăng"}
    cfg = ModelsConfig.load()
    with pytest.raises(SynthesisValidationError, match="digit"):
        _run(db_conn, cfg, SNAPSHOT, run_row, bad)


def test_run_synthesis_daily_rejects_nonexistent_evidence_ref(db_conn, run_row):
    bad = {**VALID_OUTPUT, "supporting_points": [{"text": "Thanh khoản cải thiện rõ rệt", "evidence_ref": "technical.made_up_field"}]}
    cfg = ModelsConfig.load()
    with pytest.raises(SynthesisValidationError, match="evidence_ref"):
        _run(db_conn, cfg, SNAPSHOT, run_row, bad)


def test_run_synthesis_daily_rejects_upgrade_recommendation(db_conn, run_row):
    bad = {**VALID_OUTPUT, "label_recommendation": {"downgrade_to": "buy_accumulate", "reason": "x"}}
    cfg = ModelsConfig.load()
    with pytest.raises(SynthesisValidationError, match="downgrade"):
        _run(db_conn, cfg, SNAPSHOT, run_row, bad)


def test_run_synthesis_daily_allows_year_digits(db_conn, run_row):
    ok = {**VALID_OUTPUT, "thesis_summary": "Luận điểm được củng cố từ báo cáo năm 2026"}
    cfg = ModelsConfig.load()
    result = _run(db_conn, cfg, SNAPSHOT, run_row, ok)
    assert "2026" in result.thesis_summary
