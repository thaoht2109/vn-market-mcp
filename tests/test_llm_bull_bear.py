import pytest

from llm.bull_bear import BullBearValidationError, run_bear_case, run_bull_case
from llm.config import ModelsConfig
from llm.providers import ChatResult
from tests.conftest import insert_ticker

SNAPSHOT = {"ticker": "BBTEST", "technical": {"rsi14": 61.2}, "fundamental": {"roe": 18.5}}

VALID_BULL = {
    "arguments": [{"text": "Động lượng kỹ thuật đang ủng hộ xu hướng tăng", "evidence_ref": "technical.rsi14"}],
    "key_risk_to_thesis": "Dòng tiền có thể đảo chiều nếu vĩ mô xấu đi",
}


class FakeChatClient:
    def __init__(self, results):
        self._results = list(results)

    def create(self, **kwargs):
        return self._results.pop(0)


def _result(tool_input):
    return ChatResult(tool_input=tool_input, input_tokens=100, output_tokens=50, cached_tokens=0)


@pytest.fixture
def run_row(db_conn):
    insert_ticker(db_conn, "BBTEST")
    db_conn.execute(
        "INSERT INTO runs (run_id, mode, tickers, style, depth, as_of, snapshot_ref)"
        " VALUES ('bb-run', 'on_demand', ARRAY['BBTEST'], 'long', 'quick', now(), 'snapshots/x.json')"
    )
    db_conn.commit()
    yield "bb-run"
    db_conn.execute("DELETE FROM llm_calls WHERE run_id = 'bb-run'")
    db_conn.execute("DELETE FROM runs WHERE run_id = 'bb-run'")
    db_conn.execute("DELETE FROM tickers WHERE ticker = 'BBTEST'")
    db_conn.commit()


def test_run_bull_case_accepts_valid_output(db_conn, run_row):
    cfg = ModelsConfig.load()
    client = FakeChatClient([_result(VALID_BULL)])
    result = run_bull_case(db_conn, cfg, SNAPSHOT, run_row, clients={"deepseek": client})
    assert result.arguments[0]["evidence_ref"] == "technical.rsi14"


def test_run_bear_case_accepts_valid_output(db_conn, run_row):
    cfg = ModelsConfig.load()
    client = FakeChatClient([_result(VALID_BULL)])  # same schema, reused content is fine for this test
    result = run_bear_case(db_conn, cfg, SNAPSHOT, run_row, clients={"deepseek": client})
    assert result.key_risk_to_thesis == VALID_BULL["key_risk_to_thesis"]


def test_run_bull_case_rejects_nonexistent_evidence_ref(db_conn, run_row):
    bad = {**VALID_BULL, "arguments": [{"text": "Thanh khoản tốt", "evidence_ref": "technical.made_up"}]}
    cfg = ModelsConfig.load()
    client = FakeChatClient([_result(bad)])
    with pytest.raises(BullBearValidationError, match="evidence_ref"):
        run_bull_case(db_conn, cfg, SNAPSHOT, run_row, clients={"deepseek": client})


def test_run_bull_case_rejects_digit_in_free_text(db_conn, run_row):
    bad = {**VALID_BULL, "key_risk_to_thesis": "RSI đạt 61.2 có thể đảo chiều"}
    cfg = ModelsConfig.load()
    client = FakeChatClient([_result(bad)])
    with pytest.raises(BullBearValidationError, match="digit"):
        run_bull_case(db_conn, cfg, SNAPSHOT, run_row, clients={"deepseek": client})
